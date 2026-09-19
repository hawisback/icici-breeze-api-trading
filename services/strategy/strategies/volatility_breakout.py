"""Strategy B: Volatility compression breakout with live price trigger and multi-factor confirmation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.strategy.features import FeatureEngine
from services.strategy.models import (
    CompressionBox, OptionType, StrategyName, StrategySignal,
    StrategyTriggerDiagnostics, TradeDirection, TriggerCondition,
)

IST = timezone(timedelta(hours=5, minutes=30))


class VolatilityBreakoutStrategy:
    """Strategy B implementation with immutable compression box, live price breakout,
    2-poll confirmation, relaxed anti-chase, ternary confirmation model, and structural risk gate.
    """

    def __init__(
        self,
        rvol_threshold: float = 1.20,
        adx_threshold: float = 20.0,
        min_confirmation_score: int = 2,
        min_available_confirmations: int = 3,
        bb_width_percentile_threshold: float = 35.0,
        bb_width_percentile_lookback: int = 60,
        box_max_height_atr: float = 1.50,
        lookback_bars: int = 8,
        max_age_bars: int = 12,
        breakout_buffer_atr: float = 0.03,
        breakout_confirm_polls: int = 2,
        max_extension_atr: float = 0.90,
        entry_start: str = "09:25",
        entry_end: str = "14:45",
    ) -> None:
        self.rvol_threshold = rvol_threshold
        self.adx_threshold = adx_threshold  # compatibility
        self.min_confirmation_score = min_confirmation_score
        self.min_available_confirmations = min_available_confirmations
        self.bb_width_threshold = bb_width_percentile_threshold
        self.bb_width_percentile_threshold = bb_width_percentile_threshold
        self.bb_percentile_lookback = bb_width_percentile_lookback
        self.box_max_height_atr = box_max_height_atr
        self.lookback_bars = lookback_bars
        self.max_age_bars = max_age_bars
        self.breakout_buffer_atr = breakout_buffer_atr
        self.breakout_confirm_polls = breakout_confirm_polls
        self.max_extension_atr = max_extension_atr
        self.entry_start = entry_start
        self.entry_end = entry_end
        self.locked_box: Optional[CompressionBox] = None
        self.breakout_confirm_count: int = 0
        self.confirm_direction: Optional[TradeDirection] = None
        self.last_bar: Optional[str] = None
        self.after: Optional[str] = None
        self.session: Optional[str] = None
        self.fingerprint: Optional[str] = None
        self.consumed: Optional[str] = None

    def export_state(self) -> dict[str, Any]:
        return {
            "box": self.locked_box.model_dump(mode="json") if self.locked_box else None,
            "breakout_confirm_count": self.breakout_confirm_count,
            "confirm_direction": self.confirm_direction.value if self.confirm_direction else None,
            **{k: getattr(self, k) for k in ("last_bar", "after", "session", "fingerprint", "consumed")}
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        self.locked_box = CompressionBox.model_validate(state["box"]) if state.get("box") else None
        self.breakout_confirm_count = state.get("breakout_confirm_count", 0)
        cd = state.get("confirm_direction")
        self.confirm_direction = TradeDirection(cd) if cd else None
        for name in ("last_bar", "after", "session", "fingerprint", "consumed"):
            setattr(self, name, state.get(name))

    def reset(self, at: Optional[datetime] = None) -> None:
        self.locked_box = None
        self.breakout_confirm_count = 0
        self.confirm_direction = None
        if at is not None:
            self.after = at.isoformat()

    def _decision(self, features, bars, overrides=None, is_diagnose: bool = False):
        def setting(key, default):
            value = getattr(overrides, key, None)
            return default if value is None else value

        bb_target = setting("bb_width_percentile", self.bb_width_threshold)
        height_target = setting("box_max_height_atr", self.box_max_height_atr)
        required_conf = setting("strat_b_min_confirmation", self.min_confirmation_score)
        min_available = setting("strat_b_min_available_confirmations", self.min_available_confirmations)
        rvol_target = setting("rvol_threshold", self.rvol_threshold)
        max_age = setting("strat_b_box_max_age_bars", self.max_age_bars)
        buf_atr = setting("strat_b_breakout_buffer_atr", setting("breakout_buffer_atr", self.breakout_buffer_atr))
        max_ext = setting("strat_b_max_extension_atr", setting("breakout_max_extension_atr", self.max_extension_atr))
        required_polls = setting("strat_b_breakout_confirm_polls", setting("breakout_confirm_polls", self.breakout_confirm_polls))

        conditions_bull: list[TriggerCondition] = []
        conditions_bear: list[TriggerCondition] = []
        summary: dict[str, Any] = {}
        live_price = features.spot_price

        def condition(key, name, passed, current, target, gap=None, for_dir=None, applicable=True):
            c = TriggerCondition(
                id=key,
                name=name,
                status="PASSED" if passed else ("PENDING" if applicable else "N/A"),
                current_value=str(current),
                target_threshold=str(target),
                gap_description=str(gap) if gap is not None else str(name),
            )
            if for_dir is None or for_dir == TradeDirection.BULLISH:
                conditions_bull.append(c)
            if for_dir is None or for_dir == TradeDirection.BEARISH:
                conditions_bear.append(c)

        def finish(phase, reason, signal=None, direction=None):
            summary["primary_blocker"] = reason.split(":")[0].strip()
            summary["blocker_detail"] = reason
            if self.locked_box and "box" not in summary:
                box = self.locked_box
                summary["box"] = {
                    "state": "LOCKED", "high": box.box_high, "low": box.box_low,
                    "height": box.box_height, "bars": box.bars_active, "max_bars": box.max_bars
                }
                summary["compression"] = {
                    "status": "PASSED", "bb_width": box.bb_width_at_lock,
                    "height_atr": box.box_height / box.atr_at_lock if box.atr_at_lock > 0 else 0,
                    "bb_percentile_lookback": self.bb_percentile_lookback,
                }
            diags = []
            for d in TradeDirection:
                visible = conditions_bull if d == TradeDirection.BULLISH else conditions_bear
                applicable = [c for c in visible if c.status != "N/A"]
                passed_cnt = sum(c.status == "PASSED" for c in applicable)
                ready = signal is not None and signal.direction == d
                target_level = None
                atr_ref = self.locked_box.atr_at_lock if self.locked_box else (features.atr_5m if features and features.atr_5m > 0 else 15.0)
                if self.locked_box:
                    buf_val = buf_atr * atr_ref
                    target_level = round(self.locked_box.box_high + buf_val, 2) if d == TradeDirection.BULLISH else round(self.locked_box.box_low - buf_val, 2)
                elif bars and atr_ref > 0:
                    win = bars[-self.lookback_bars:]
                    h = max(c.high for c in win)
                    l = min(c.low for c in win)
                    buf_val = buf_atr * atr_ref
                    target_level = round(h + buf_val, 2) if d == TradeDirection.BULLISH else round(l - buf_val, 2)

                diags.append(StrategyTriggerDiagnostics(
                    strategy=StrategyName.VOLATILITY_BREAKOUT,
                    strategy_label=f"Volatility Breakout ({'CALL' if d == TradeDirection.BULLISH else 'PUT'})",
                    direction=d,
                    option_type=OptionType.CALL if d == TradeDirection.BULLISH else OptionType.PUT,
                    overall_status="READY_TO_TRIGGER" if ready else "WAITING",
                    phase_state=phase if (direction in (None, d)) else "WAITING_FOR_BREAKOUT",
                    key_blocker=reason if (direction in (None, d)) else "Waiting for breakout in this direction",
                    current_spot=live_price,
                    passed_count=passed_cnt,
                    total_count=len(applicable),
                    ready_pct=round(100 * passed_cnt / len(applicable), 1) if applicable else 0,
                    conditions=deepcopy(visible),
                    phase_summary=deepcopy(summary),
                    target_entry_level=target_level,
                ))
            return signal, diags

        now = features.timestamp
        if (not features.breakout_data_ready or not bars or features.atr_5m <= 0
                or any(c.source not in ("BREEZE", "KITE", "LIVE") or c.interval != "5m"
                       or c.end_time > now or abs((c.end_time - c.start_time).total_seconds() - 300) > 5
                       or not 0 < c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in bars)
                or any(b.start_time <= a.start_time for a, b in zip(bars, bars[1:]))
                or not 0 <= (now - bars[-1].end_time).total_seconds() < 300):
            if not is_diagnose:
                self.reset(now)
                return finish("RESET", "Missing, stale or invalid real completed spot/futures data")
            elif not bars or features.atr_5m <= 0:
                return finish("RESET", "Awaiting sufficient market candle history")
            else:
                return finish("RESET", "Missing, stale or invalid real completed spot/futures data")

        trigger = bars[-1]
        clock = trigger.end_time.astimezone(IST)
        session = clock.date().isoformat()
        if self.session != session:
            self.locked_box = None
            self.last_bar = None
            self.session = session
            self.breakout_confirm_count = 0
            self.confirm_direction = None

        fingerprint = repr((
            bb_target, height_target, required_conf, min_available, rvol_target,
            self.lookback_bars, max_age, buf_atr, max_ext, required_polls,
            self.entry_start, self.entry_end,
            setting("bull_derivatives_score", 2), setting("bear_derivatives_score", 2),
        ))
        if self.fingerprint is not None and self.fingerprint != fingerprint:
            self.reset(trigger.end_time)
        self.fingerprint = fingerprint

        if not setting("bypass_entry_window", False) and not self.entry_start <= clock.strftime("%H:%M") <= self.entry_end:
            self.reset(trigger.end_time)
            return finish("RESET", "Outside Strategy B entry window")

        stamp = trigger.end_time.isoformat()

        # Step 1: Manage Active Box Aging and Expiry
        if self.locked_box:
            box = self.locked_box
            anchor = datetime.fromisoformat(box.created_bar_time)
            active = [c for c in bars if c.end_time > anchor]
            if (active and (abs((active[0].start_time - anchor).total_seconds()) > 5
                    or any(abs((b.start_time - a.start_time).total_seconds() - 300) > 5 for a, b in zip(active, active[1:])))):
                self.reset(trigger.end_time)
                return finish("RESET", "Missing completed bars since box lock")

            box.bars_active = len(active)
            if box.bars_active > box.max_bars:
                self.reset(trigger.end_time)
                return finish("RESET", "BOX_EXPIRED: Box expired after maximum completed-bar age")
        else:
            # Step 2: Establish Consolidation Box
            window = [c for c in bars if c.start_time.astimezone(IST).date().isoformat() == session
                      and (not self.after or c.end_time > datetime.fromisoformat(self.after))][-self.lookback_bars:]

            # These diagnostics can be calculated before a valid box exists.
            # A missing setup is a dependency state, not missing Kite data.
            closes = [c.close for c in bars]
            bb_pct = features.bb_width_percentile
            if bb_pct is None:
                bb_pct = FeatureEngine.calculate_bb_percentile(closes, history=self.bb_percentile_lookback)
            bb_current = f"{bb_pct:.1f}%ile" if bb_pct is not None else f"Insufficient history ({len(closes)}/40 candles)"

            candidate = window[-self.lookback_bars:]
            if candidate:
                candidate_high = max(c.high for c in candidate)
                candidate_low = min(c.low for c in candidate)
                candidate_height = candidate_high - candidate_low
                candidate_height_atr = candidate_height / features.atr_5m if features.atr_5m > 0 else 0.0
                height_current = f"{candidate_height_atr:.2f} ATR ({candidate_height:.1f} pts; {len(candidate)}/{self.lookback_bars} bars)"
            else:
                candidate_height_atr = None
                height_current = "No eligible candles after setup cutoff"

            def preview_confirmations(direction):
                is_bull = direction == TradeDirection.BULLISH
                sign = 1 if is_bull else -1
                spread = max(trigger.high - trigger.low, 1e-12)
                values = {
                    "rvol": None if features.rvol_5m is None or features.rvol_5m <= 0 else features.rvol_5m >= rvol_target,
                    "body_strength": bool(sign * (trigger.close - trigger.open) > 0 and abs(trigger.close - trigger.open) / spread >= 0.45),
                    "close_location": bool((trigger.close - trigger.low if is_bull else trigger.high - trigger.close) / spread >= 0.70),
                    "vwap": None if features.futures_vwap <= 0 or features.futures_price <= 0 else sign * (features.futures_price - features.futures_vwap) > 0,
                    "derivatives": None,
                    "futures_oi": None,
                }
                drv = features.breakout_bull_derivatives_score if is_bull else features.breakout_bear_derivatives_score
                if drv is not None:
                    values["derivatives"] = drv >= setting("bull_derivatives_score" if is_bull else "bear_derivatives_score", 2.0)
                buildup = features.futures_buildup
                if buildup not in (None, "UNKNOWN", "UNAVAILABLE", "NONE", ""):
                    if buildup in ("LONG_BUILDUP", "SHORT_COVERING"):
                        values["futures_oi"] = is_bull
                    elif buildup in ("SHORT_BUILDUP", "LONG_UNWINDING"):
                        values["futures_oi"] = not is_bull
                    else:
                        values["futures_oi"] = False
                available = [v for v in values.values() if v is not None]
                passed = [v for v in available if v is True]
                return len(passed), len(available)

            preview_bull = preview_confirmations(TradeDirection.BULLISH)
            preview_bear = preview_confirmations(TradeDirection.BEARISH)
            summary["bb_width_percentile"] = round(bb_pct, 1) if bb_pct is not None else None
            summary["bb_percentile_lookback"] = self.bb_percentile_lookback
            summary["compression_pass"] = False
            summary["candidate_box_bars"] = len(candidate)
            summary["candidate_box_height_atr"] = round(candidate_height_atr, 2) if candidate else None

            if len(window) < self.lookback_bars or any(abs((b.start_time - a.start_time).total_seconds() - 300) > 5 for a, b in zip(window, window[1:])):
                condition("compression_bb", "Historical BB width percentile", False, bb_current, f"<={bb_target:.1f}%ile", f"Current BB width is {bb_current}; waiting for a fresh contiguous compression window")
                condition("compression_height", "Compression height in ATR", False, height_current, f"<={height_target:.2f} ATR", "Waiting for a fresh contiguous compression window with enough bars")
                condition("box_age_bars", "Consolidation box age", False, "0 bars", f"<={max_age} bars", "No active box locked")
                condition("breakout_trigger", "Live price breakout confirmation", False, f"Live ₹{live_price:.2f}", "Awaiting box lock", "No active box locked")
                condition("anti_chase_extension", "Anti-chase extension", False, "Awaiting box lock", f"<={max_ext:.2f} ATR", "No active box locked")
                condition("confirmation_score", "Entry confirmations", False, f"CALL {preview_bull[0]} passed / {preview_bull[1]} available; PUT {preview_bear[0]} passed / {preview_bear[1]} available", f">={required_conf} passed", "Confirmation evidence is available; final score is evaluated after box lock")
                condition("risk_band", "Structural initial R", False, "Awaiting box lock", "<= 1.20 ATR", "No active box locked")
                return finish("SEARCHING_COMPRESSION", "NO_COMPRESSION: Waiting for a fresh contiguous compression window")

            atr = features.atr_5m
            high, low = max(c.high for c in window), min(c.low for c in window)
            box_height = high - low
            box_height_atr = box_height / atr if atr > 0 else 0.0

            # BB width percentile lookback
            closes = [c.close for c in bars]
            if len(closes) >= 40 and features.bb_width_percentile is None:
                bb_pct = FeatureEngine.calculate_bb_percentile(closes, history=self.bb_percentile_lookback)
            else:
                bb_pct = features.bb_width_percentile

            bb_ok = (bb_pct <= (bb_target + 1e-9))
            height_ok = (0 < box_height <= (height_target * atr + 1e-9))

            bb_gap = (
                f"BB width percentile compressed at {bb_pct:.1f}%ile <= {bb_target:.1f}%ile"
                if bb_ok
                else f"BB percentile {bb_pct:.1f}%ile > target {bb_target:.1f}%ile (gap: +{bb_pct - bb_target:.1f}%)"
            )
            height_gap = (
                f"Box height {box_height_atr:.2f} ATR <= {height_target:.2f} ATR ({box_height:.1f} pts)"
                if height_ok
                else f"Box height {box_height_atr:.2f} ATR > max {height_target:.2f} ATR (gap: +{box_height_atr - height_target:.2f} ATR)"
            )

            condition("compression_bb", "Historical BB width percentile", bb_ok, f"{bb_pct:.1f}%ile", f"<={bb_target:.1f}%ile", bb_gap)
            condition("compression_height", "Compression height in ATR", height_ok, f"{box_height_atr:.2f} ATR ({box_height:.1f} pts)", f"<={height_target:.2f} ATR", height_gap)
            condition("box_age_bars", "Consolidation box age", bb_ok and height_ok, "0 bars", f"<={max_age} bars", "Box qualified for lock" if (bb_ok and height_ok) else "Awaiting compression qualification")

            summary["bb_width_percentile"] = round(bb_pct, 1)
            summary["bb_percentile_lookback"] = self.bb_percentile_lookback
            summary["compression_pass"] = bb_ok and height_ok
            summary["box_high"] = high
            summary["box_low"] = low
            summary["box_height"] = box_height
            summary["box_height_atr"] = round(box_height_atr, 2)
            summary["box_age_bars"] = 0
            summary["box_frozen"] = False

            call_trigger = round(high + buf_atr * atr, 2)
            put_trigger = round(low - buf_atr * atr, 2)
            call_gap = f"Live spot ₹{live_price:.2f} is {call_trigger - live_price:.2f} pts below trigger ₹{call_trigger:.2f}"
            put_gap = f"Live spot ₹{live_price:.2f} is {live_price - put_trigger:.2f} pts above trigger ₹{put_trigger:.2f}"
            condition("breakout_trigger", "Live price breakout confirmation", False, f"Live ₹{live_price:.2f}", f"> ₹{call_trigger:.2f}", call_gap, for_dir=TradeDirection.BULLISH)
            condition("breakout_trigger", "Live price breakout confirmation", False, f"Live ₹{live_price:.2f}", f"< ₹{put_trigger:.2f}", put_gap, for_dir=TradeDirection.BEARISH)
            condition("anti_chase_extension", "Anti-chase extension", False, "Awaiting box lock", f"<={max_ext:.2f} ATR", "Awaiting box lock")
            condition("confirmation_score", "Entry confirmations", False, f"CALL {preview_bull[0]} passed / {preview_bull[1]} available; PUT {preview_bear[0]} passed / {preview_bear[1]} available", f">={required_conf} passed", "Confirmation evidence is available; final score is evaluated after box lock")
            condition("risk_band", "Structural initial R", False, "Awaiting box lock", "<= 1.20 ATR", "Awaiting box lock")

            if any(c.high - c.low > 4 * atr for c in window):
                return finish("SEARCHING_COMPRESSION", "SUSPICIOUS_EXPANSION: Suspicious candle cannot define box")
            if not bb_ok:
                return finish("SEARCHING_COMPRESSION", f"NO_COMPRESSION: BB width percentile {bb_pct:.1f} > threshold {bb_target:.1f}")
            if not height_ok:
                return finish("SEARCHING_COMPRESSION", f"BOX_TOO_LARGE: Box height {box_height_atr:.2f} ATR > max {height_target:.2f} ATR")

            self.locked_box = CompressionBox(
                box_high=high, box_low=low, box_height=box_height,
                atr_at_lock=atr, bb_width_at_lock=bb_pct, locked_at=trigger.end_time,
                created_bar_time=stamp, bars_active=0, max_bars=max_age, is_locked=True
            )
            self.breakout_confirm_count = 0
            self.confirm_direction = None
            summary["box"] = {
                "state": "LOCKED", "high": high, "low": low, "height": box_height,
                "bars": 0, "max_bars": max_age
            }
            summary["box_frozen"] = True
            return finish("BOX_LOCKED", "BOX_LOCKED: Box frozen; waiting for live price breakout")

        # Step 3: Active Frozen Box Diagnostics & Live Breakout Evaluation
        box = self.locked_box
        atr = box.atr_at_lock
        summary["box"] = {
            "state": "LOCKED", "high": box.box_high, "low": box.box_low,
            "height": box.box_height, "bars": box.bars_active, "max_bars": box.max_bars
        }
        summary["compression"] = {
            "status": "PASSED", "bb_width": box.bb_width_at_lock,
            "height_atr": round(box.box_height / atr, 2) if atr > 0 else 0,
            "bb_percentile_lookback": self.bb_percentile_lookback,
        }
        summary["bb_width_percentile"] = box.bb_width_at_lock
        summary["bb_percentile_lookback"] = self.bb_percentile_lookback
        summary["compression_pass"] = True
        summary["box_high"] = box.box_high
        summary["box_low"] = box.box_low
        summary["box_height"] = box.box_height
        summary["box_height_atr"] = round(box.box_height / atr, 2) if atr > 0 else 0
        summary["box_age_bars"] = box.bars_active
        summary["box_frozen"] = True

        condition("compression_bb", "Historical BB width percentile", True, f"{box.bb_width_at_lock:.1f}%ile", f"<={bb_target:.1f}%ile", f"Box locked at {box.bb_width_at_lock:.1f}%ile BB width")
        condition("compression_height", "Compression height in ATR", True, f"{round(box.box_height / atr, 2) if atr > 0 else 0:.2f} ATR ({box.box_height:.1f} pts)", f"<={height_target:.2f} ATR", f"Box height within target {height_target:.2f} ATR")
        condition("box_age_bars", "Consolidation box age", box.bars_active <= box.max_bars, f"{box.bars_active} bars", f"<={box.max_bars} bars", f"Active consolidation box ({box.bars_active}/{box.max_bars} bars)")

        call_trigger = round(box.box_high + buf_atr * atr, 2)
        put_trigger = round(box.box_low - buf_atr * atr, 2)

        bullish = (live_price > call_trigger)
        bearish = (live_price < put_trigger)

        # Check abnormal intrabar candle expansion
        if trigger.high - trigger.low > 4 * atr:
            self.reset(trigger.end_time)
            return finish("RESET", "STRUCTURAL_EXPANSION: Abnormal candle expansion invalidated box")

        spread = max(trigger.high - trigger.low, 1e-12)

        def eval_confirmations(d: TradeDirection):
            is_bull = (d == TradeDirection.BULLISH)
            s = 1 if is_bull else -1
            drv = features.breakout_bull_derivatives_score if is_bull else features.breakout_bear_derivatives_score

            # 1. RVOL
            if features.rvol_5m is None or features.rvol_5m <= 0:
                rv = None
            else:
                rv = bool(features.rvol_5m >= rvol_target)

            # 2. Candle Body Strength
            if spread <= 0:
                bs = None
            else:
                bs = bool(s * (trigger.close - trigger.open) > 0 and (abs(trigger.close - trigger.open) / spread) >= 0.45)

            # 3. Close Location
            if spread <= 0:
                cl = None
            else:
                loc = (trigger.close - trigger.low if is_bull else trigger.high - trigger.close) / spread
                cl = bool(loc >= 0.70)

            # 4. Futures VWAP
            if features.futures_vwap <= 0 or features.futures_price <= 0:
                vw = None
            else:
                vw = bool(s * (features.futures_price - features.futures_vwap) > 0)

            # 5. Derivatives Score
            if drv is None:
                dv = None
            else:
                target_d = setting("bull_derivatives_score" if is_bull else "bear_derivatives_score", 2.0)
                dv = bool(drv >= target_d)

            # 6. Futures OI Buildup
            bld = features.futures_buildup
            if bld is None or bld in ("UNKNOWN", "UNAVAILABLE", "NONE", ""):
                oi = None
            elif bld in ("LONG_BUILDUP", "SHORT_COVERING"):
                oi = True if is_bull else False
            elif bld in ("SHORT_BUILDUP", "LONG_UNWINDING"):
                oi = False if is_bull else True
            else:
                oi = False

            conf_dict = {
                "rvol": rv,
                "body_strength": bs,
                "close_location": cl,
                "vwap": vw,
                "derivatives": dv,
                "futures_oi": oi,
            }
            avail = [v for v in conf_dict.values() if v is not None]
            psd = [v for v in avail if v is True]
            return conf_dict, avail, psd

        if not (bullish or bearish):
            self.breakout_confirm_count = 0
            self.confirm_direction = None
            summary["breakout_confirm_count"] = 0
            summary["live_price"] = live_price

            call_gap = f"Live spot ₹{live_price:.2f} is {call_trigger - live_price:.2f} pts below trigger ₹{call_trigger:.2f}"
            put_gap = f"Live spot ₹{live_price:.2f} is {live_price - put_trigger:.2f} pts above trigger ₹{put_trigger:.2f}"

            condition("breakout_trigger", "Live price breakout confirmation", False, f"Live ₹{live_price:.2f}", f"> ₹{call_trigger:.2f}", call_gap, for_dir=TradeDirection.BULLISH)
            condition("breakout_trigger", "Live price breakout confirmation", False, f"Live ₹{live_price:.2f}", f"< ₹{put_trigger:.2f}", put_gap, for_dir=TradeDirection.BEARISH)

            # CALL
            call_ext = max(0.0, live_price - box.box_high)
            call_ext_atr = round(call_ext / atr, 2) if atr > 0 else 0
            condition("anti_chase_extension", "Anti-chase extension", True, f"{call_ext_atr:.2f} ATR", f"<={max_ext:.2f} ATR", f"Extension within {max_ext:.2f} ATR limit", for_dir=TradeDirection.BULLISH)
            conf_dict_bull, avail_bull, psd_bull = eval_confirmations(TradeDirection.BULLISH)
            conf_ok_bull = (len(avail_bull) >= min_available and len(psd_bull) >= required_conf)
            condition("confirmation_score", "Entry confirmations", conf_ok_bull, f"{len(psd_bull)} passed / {len(avail_bull)} available", f">={required_conf} passed of >={min_available} available", f"{len(psd_bull)} passed / {len(avail_bull)} available", for_dir=TradeDirection.BULLISH)
            call_stop = round(box.box_high - 0.25 * atr, 2)
            call_risk = round(call_trigger - call_stop, 2)
            condition("risk_band", "Structural initial R", bool(0 < call_risk <= 1.20 * atr + 1e-9), f"{round(call_risk / atr, 2) if atr > 0 else 0:.2f} ATR", "<= 1.20 ATR", f"Initial R at trigger: {round(call_risk / atr, 2) if atr > 0 else 0:.2f} ATR", for_dir=TradeDirection.BULLISH)

            # PUT
            put_ext = max(0.0, box.box_low - live_price)
            put_ext_atr = round(put_ext / atr, 2) if atr > 0 else 0
            condition("anti_chase_extension", "Anti-chase extension", True, f"{put_ext_atr:.2f} ATR", f"<={max_ext:.2f} ATR", f"Extension within {max_ext:.2f} ATR limit", for_dir=TradeDirection.BEARISH)
            conf_dict_bear, avail_bear, psd_bear = eval_confirmations(TradeDirection.BEARISH)
            conf_ok_bear = (len(avail_bear) >= min_available and len(psd_bear) >= required_conf)
            condition("confirmation_score", "Entry confirmations", conf_ok_bear, f"{len(psd_bear)} passed / {len(avail_bear)} available", f">={required_conf} passed of >={min_available} available", f"{len(psd_bear)} passed / {len(avail_bear)} available", for_dir=TradeDirection.BEARISH)
            put_stop = round(box.box_low + 0.25 * atr, 2)
            put_risk = round(put_stop - put_trigger, 2)
            condition("risk_band", "Structural initial R", bool(0 < put_risk <= 1.20 * atr + 1e-9), f"{round(put_risk / atr, 2) if atr > 0 else 0:.2f} ATR", "<= 1.20 ATR", f"Initial R at trigger: {round(put_risk / atr, 2) if atr > 0 else 0:.2f} ATR", for_dir=TradeDirection.BEARISH)

            return finish("WAITING_FOR_BREAKOUT", "WAITING_FOR_BREAKOUT: Live price within consolidation boundaries")

        breakout_dir = TradeDirection.BULLISH if bullish else TradeDirection.BEARISH
        other_dir = TradeDirection.BEARISH if bullish else TradeDirection.BULLISH
        trigger_price = call_trigger if bullish else put_trigger
        sign = 1 if bullish else -1
        edge = box.box_high if bullish else box.box_low

        # Opposite direction diagnostic populating
        other_trigger = put_trigger if bullish else call_trigger
        other_gap = (
            f"Live spot ₹{live_price:.2f} is {live_price - other_trigger:.2f} pts above trigger ₹{other_trigger:.2f}"
            if bullish else
            f"Live spot ₹{live_price:.2f} is {other_trigger - live_price:.2f} pts below trigger ₹{other_trigger:.2f}"
        )
        condition("breakout_trigger", "Live price breakout confirmation", False, f"Live ₹{live_price:.2f}", f"{'<' if bullish else '>'} ₹{other_trigger:.2f}", other_gap, for_dir=other_dir)
        condition("anti_chase_extension", "Anti-chase extension", True, "0.00 ATR", f"<={max_ext:.2f} ATR", "Within extension limit", for_dir=other_dir)
        _, o_avail, o_psd = eval_confirmations(other_dir)
        condition("confirmation_score", "Entry confirmations", (len(o_avail) >= min_available and len(o_psd) >= required_conf), f"{len(o_psd)} passed / {len(o_avail)} available", f">={required_conf} passed of >={min_available} available", f"{len(o_psd)} passed / {len(o_avail)} available", for_dir=other_dir)
        o_stop = round((box.box_low + 0.25 * atr) if bullish else (box.box_high - 0.25 * atr), 2)
        o_risk = round(abs(live_price - o_stop), 2)
        condition("risk_band", "Structural initial R", bool(0 < o_risk <= 1.20 * atr + 1e-9), f"{round(o_risk / atr, 2) if atr > 0 else 0:.2f} ATR", "<= 1.20 ATR", f"Risk {round(o_risk / atr, 2) if atr > 0 else 0:.2f} ATR", for_dir=other_dir)

        # Step 4: Anti-Chase Extension Check (Change 6)
        extension = sign * (live_price - edge)
        ext_ok = (extension <= max_ext * atr + 1e-9)
        ext_gap = f"Live extension {extension/atr:.2f} ATR <= limit {max_ext:.2f} ATR" if ext_ok else f"Live extension {extension/atr:.2f} ATR exceeds limit {max_ext:.2f} ATR"
        condition("anti_chase_extension", "Anti-chase extension", ext_ok, f"{round(extension / atr, 2):.2f} ATR", f"<={max_ext:.2f} ATR", ext_gap, for_dir=breakout_dir)
        summary["extension"] = {
            "breakout_extension_atr": round(extension / atr, 2),
            "max_extension_atr": max_ext,
            "passed": ext_ok,
        }
        summary["breakout_extension_atr"] = round(extension / atr, 2)

        if not ext_ok:
            self.reset(trigger.end_time)
            return finish(
                "RESET",
                f"BREAKOUT_OVEREXTENDED: Live extension {extension/atr:.2f} ATR exceeds limit {max_ext:.2f} ATR",
                direction=breakout_dir,
            )

        # Step 5: Breakout Polling Confirmation (Change 5)
        if self.confirm_direction == breakout_dir:
            self.breakout_confirm_count += 1
        else:
            self.confirm_direction = breakout_dir
            self.breakout_confirm_count = 1

        confirmed = (self.breakout_confirm_count >= required_polls)
        trig_gap = (
            f"Breakout confirmed (+{abs(live_price - trigger_price):.2f} pts beyond trigger)"
            if confirmed else
            f"Live price {live_price:.2f} beyond trigger (poll {self.breakout_confirm_count}/{required_polls})"
        )
        condition(
            "breakout_trigger",
            "Live price breakout confirmation",
            confirmed,
            f"Live ₹{live_price:.2f} vs Trigger ₹{trigger_price:.2f} (polls {self.breakout_confirm_count}/{required_polls})",
            f"{'>' if bullish else '<'} ₹{trigger_price:.2f} for {required_polls} polls",
            trig_gap,
            for_dir=breakout_dir,
        )
        summary["trigger"] = {
            "breakout_trigger_price": trigger_price,
            "live_price": live_price,
            "breakout_confirm_count": self.breakout_confirm_count,
            "required_polls": required_polls,
            "confirmed": confirmed,
        }
        summary["direction"] = breakout_dir.value
        summary["breakout_trigger_price"] = trigger_price
        summary["live_price"] = live_price
        summary["breakout_confirm_count"] = self.breakout_confirm_count

        if not confirmed:
            return finish(
                "WAITING_FOR_BREAKOUT",
                f"BREAKOUT_NOT_CONFIRMED: Live price {live_price:.2f} beyond trigger (poll {self.breakout_confirm_count}/{required_polls})",
                direction=breakout_dir,
            )

        # Step 6: Multi-Factor Ternary Confirmation Model (Changes 7 & 8)
        deriv = features.breakout_bull_derivatives_score if bullish else features.breakout_bear_derivatives_score
        confirmations, available_confirmations, passed_confirmations = eval_confirmations(breakout_dir)

        # OI Wall check (Change 9: Informational only; no score penalty)
        oi_wall = bool(features.bullish_oi_wall if bullish else features.bearish_oi_wall)

        conf_ok = (len(available_confirmations) >= min_available and len(passed_confirmations) >= required_conf)
        conf_gap = (
            f"Confirmations qualified ({len(passed_confirmations)} passed / {len(available_confirmations)} available)"
            if conf_ok else
            f"Need {required_conf - len(passed_confirmations)} more confirmations ({len(passed_confirmations)}/{required_conf})"
        )
        condition(
            "confirmation_score",
            "Entry confirmations",
            conf_ok,
            f"{len(passed_confirmations)} passed / {len(available_confirmations)} available (OI wall: {oi_wall})",
            f">={required_conf} passed of >={min_available} available",
            conf_gap,
            for_dir=breakout_dir,
        )

        summary["confirmation"] = {
            "score": len(passed_confirmations),
            "available": len(available_confirmations),
            "required": required_conf,
            "min_available": min_available,
            "factors": confirmations,
            "oi_wall_detected": oi_wall,
        }
        summary["available_confirmation_count"] = len(available_confirmations)
        summary["passed_confirmation_count"] = len(passed_confirmations)
        summary["rvol_pass"] = confirmations.get("rvol")
        summary["body_strength_pass"] = confirmations.get("body_strength")
        summary["close_location_pass"] = confirmations.get("close_location")
        summary["vwap_pass"] = confirmations.get("vwap")
        summary["derivatives_pass"] = confirmations.get("derivatives")
        summary["futures_oi_pass"] = confirmations.get("futures_oi")
        summary["oi_wall_detected"] = oi_wall

        if len(available_confirmations) < min_available:
            self.reset(trigger.end_time)
            return finish(
                "CONFIRMATION_FAILED",
                f"INSUFFICIENT_CONFIRMATION_DATA: Available confirmations {len(available_confirmations)} < required {min_available}",
                direction=breakout_dir,
            )

        if len(passed_confirmations) < required_conf:
            self.reset(trigger.end_time)
            return finish(
                "CONFIRMATION_FAILED",
                f"CONFIRMATION_SCORE_LOW: Passed confirmations {len(passed_confirmations)} < required {required_conf}",
                direction=breakout_dir,
            )

        # Step 7: Structural Stop & Initial Risk Gate (Change 10)
        stop = round(edge - sign * 0.25 * atr, 2)
        risk = round(sign * (live_price - stop), 2)
        max_risk = 1.20 * atr

        risk_ok = bool(0 < risk <= (max_risk + 1e-9))
        risk_gap = (
            f"Structural risk {risk/atr:.2f} ATR within allowed (0, 1.20] ATR band ({risk:.1f} pts)"
            if risk_ok else
            f"Structural risk {risk/atr:.2f} ATR outside allowed (0, 1.20] ATR band"
        )
        condition("risk_band", "Structural initial R", risk_ok, f"{round(risk / atr, 2):.2f} ATR ({risk:.1f} pts)", "0 < R <= 1.20 ATR", risk_gap, for_dir=breakout_dir)
        summary["risk"] = {
            "initial_r": risk,
            "initial_risk_atr": round(risk / atr, 2) if atr > 0 else 0,
            "stop": stop,
            "max_r": max_risk,
        }
        summary["initial_risk_atr"] = round(risk / atr, 2) if atr > 0 else 0

        if not risk_ok:
            self.reset(trigger.end_time)
            return finish(
                "RISK_REJECTED",
                f"RISK_TOO_HIGH: Structural risk {risk/atr:.2f} ATR outside allowed (0, 1.20] ATR band",
                direction=breakout_dir,
            )

        # Step 8: Signal Emitted
        key = f"SIG-B-{breakout_dir.value}-{int(datetime.fromisoformat(box.created_bar_time).timestamp())}-{int(trigger.end_time.timestamp())}"
        if self.consumed == key:
            return finish("RESET", "Signal already consumed", direction=breakout_dir)
        self.consumed = key

        signal = StrategySignal(
            signal_id=key,
            strategy=StrategyName.VOLATILITY_BREAKOUT,
            direction=breakout_dir,
            option_type=OptionType.CALL if bullish else OptionType.PUT,
            timestamp=trigger.end_time,
            spot_reference_price=live_price,
            structural_stop=stop,
            r_points=risk,
            derivatives_score=deriv or 0.0,
            features_snapshot={
                "box_high": box.box_high,
                "box_low": box.box_low,
                "atr_at_lock": atr,
                "box_created_time": box.created_bar_time,
                "confirmation_score": len(passed_confirmations),
                "confirmation_ratio": f"{len(passed_confirmations)}/{len(available_confirmations)}",
                "confirmation_factors": confirmations,
                "oi_wall_detected": oi_wall,
                "entry_reference_spot": live_price,
                "breakout_trigger_price": trigger_price,
                "breakout_extension_atr": round(extension / atr, 2),
            },
        )
        self.reset(trigger.end_time)
        return finish("READY_TO_TRIGGER", "Breakout qualified; checking contract and execution risk", signal, breakout_dir)

    def evaluate(self, features, candles_5m, candles_15m=None, futures_candles=None, overrides=None):
        return self._decision(features, candles_5m, overrides)[0]

    def diagnose(self, features, candles_5m, overrides=None):
        return deepcopy(self)._decision(features, candles_5m, overrides, is_diagnose=True)[1]
