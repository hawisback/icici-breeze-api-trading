"""Completed-bar Trend Pullback Continuation with live-price breakout confirmation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.strategy.features import FeatureEngine
from services.strategy.models import (
    OptionType, StrategyName, StrategySignal, StrategyTriggerDiagnostics,
    TradeDirection, TriggerCondition,
)

IST = timezone(timedelta(hours=5, minutes=30))

EXPECTED_5M_SECONDS = 300
EXPECTED_15M_SECONDS = 900
CONTIGUITY_TOLERANCE_SECONDS = 5


def validate_candle_contiguity(previous: Any, current: Any, interval: str = "5m") -> tuple[bool, float]:
    """Validate interval between candle start times with tolerance.

    5m candles: expected 300s, tolerance ±5s
    15m candles: expected 900s, tolerance ±5s
    Returns (is_contiguous, actual_seconds).
    """
    expected = EXPECTED_15M_SECONDS if "15" in str(interval) else EXPECTED_5M_SECONDS
    actual_seconds = (current.start_time - previous.start_time).total_seconds()
    is_contiguous = abs(actual_seconds - expected) <= CONTIGUITY_TOLERANCE_SECONDS
    return is_contiguous, actual_seconds


class TrendPullbackStrategy:
    """Shared execution/diagnostic decision path with serializable setup state."""

    @staticmethod
    def validate_candle_contiguity(previous: Any, current: Any, interval: str = "5m") -> tuple[bool, float]:
        return validate_candle_contiguity(previous, current, interval)

    def __init__(
        self,
        adx_threshold: float = 18.0,
        rvol_threshold: float = 1.20,
        min_confirmation_score: int = 2,
        ema_slope_threshold: float = 0.10,
        breakout_buffer_atr: float = 0.02,
        breakout_confirm_polls: int = 2,
        min_impulse_atr: float = 0.80,
        min_pullback_depth: float = 0.08,
        max_pullback_depth: float = 0.70,
        retest_tolerance_atr: float = 0.45,
        min_available_confirmations: int = 3,
    ) -> None:
        self.adx_threshold = adx_threshold
        self.rvol_threshold = rvol_threshold
        self.min_confirmation_score = min_confirmation_score
        self.ema_slope_threshold = ema_slope_threshold
        self.breakout_buffer_atr = breakout_buffer_atr
        self.breakout_confirm_polls = breakout_confirm_polls
        self.min_impulse_atr = min_impulse_atr
        self.min_pullback_depth = min_pullback_depth
        self.max_pullback_depth = max_pullback_depth
        self.retest_tolerance_atr = retest_tolerance_atr
        self.min_available_confirmations = min_available_confirmations
        self.state = {d.value: {} for d in TradeDirection}

    def export_state(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_state(self, state: dict[str, Any]) -> None:
        self.state = {d.value: dict(state.get(d.value, {})) for d in TradeDirection}

    def reset(self, at: Optional[datetime] = None) -> None:
        for direction in TradeDirection:
            old = self.state[direction.value]
            self.state[direction.value] = {
                "session": at.astimezone(IST).date().isoformat() if at else old.get("session"),
                "after": at.isoformat() if at else old.get("after"),
                "consumed": old.get("consumed"),
                "cooldown": old.get("cooldown"),
                "confirm_count": 0,
            }

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        self.state[direction.value] = {
            "session": at.astimezone(IST).date().isoformat(),
            "after": at.isoformat(),
            "cooldown": at.isoformat(),
            "confirm_count": 0,
        }

    @staticmethod
    def _impulse(bars, atr, bullish, after=None, min_impulse_atr=0.80):
        """Latest confirmed pivot pair, with extremes in chronological order."""
        lows = [i for i in range(1, len(bars)-1)
                if bars[i].low < bars[i-1].low and bars[i].low <= bars[i+1].low]
        highs = [i for i in range(1, len(bars)-1)
                 if bars[i].high > bars[i-1].high and bars[i].high >= bars[i+1].high]
        starts, ends = (lows, highs) if bullish else (highs, lows)
        for end in reversed(ends):
            if after and bars[end].end_time <= datetime.fromisoformat(after):
                continue
            for start in reversed(starts):
                if start >= end:
                    continue
                low = bars[start].low if bullish else bars[end].low
                high = bars[end].high if bullish else bars[start].high
                if high - low >= (min_impulse_atr * atr - 1e-9):
                    leg = bars[start:end+1]
                    if min(c.low for c in leg) < low or max(c.high for c in leg) > high:
                        continue
                    return {
                        "start": bars[start].end_time.isoformat(),
                        "end": bars[end].end_time.isoformat(),
                        "low": low,
                        "high": high,
                        "height": high - low,
                        "source": "PIVOT",
                    }
        return None

    @staticmethod
    def _fallback_impulse(bars, atr, bullish, after=None, min_impulse_atr=0.80):
        """Chronological swing fallback from the latest 6 completed 5-minute bars."""
        if len(bars) < 6:
            return None
        recent = bars[-6:]
        swing_low_index = min(range(len(recent)), key=lambda i: (recent[i].low, i if bullish else -i))
        swing_high_index = max(range(len(recent)), key=lambda i: (recent[i].high, i if bullish else -i))

        if bullish:
            if swing_low_index >= swing_high_index:
                return None
            start_bar = recent[swing_low_index]
            end_bar = recent[swing_high_index]
            swing_low = start_bar.low
            swing_high = end_bar.high
        else:
            if swing_high_index >= swing_low_index:
                return None
            start_bar = recent[swing_high_index]
            end_bar = recent[swing_low_index]
            swing_low = end_bar.low
            swing_high = start_bar.high

        impulse_size = swing_high - swing_low
        if impulse_size < (min_impulse_atr * atr - 1e-9):
            return None

        if after and end_bar.end_time <= datetime.fromisoformat(after):
            return None

        return {
            "start": start_bar.end_time.isoformat(),
            "end": end_bar.end_time.isoformat(),
            "low": swing_low,
            "high": swing_high,
            "height": impulse_size,
            "source": "FALLBACK",
        }

    def _decision(self, direction, features, bars, macro, futures, overrides):
        bullish = direction == TradeDirection.BULLISH
        sign = 1 if bullish else -1
        opt = OptionType.CALL if bullish else OptionType.PUT
        state = self.state[direction.value]
        summary, conditions = {}, []
        live_price = features.spot_price

        def condition(id, name, passed, current, target):
            conditions.append(TriggerCondition(
                id=id,
                name=name,
                status="PASSED" if passed else "PENDING",
                current_value=str(current),
                target_threshold=str(target),
                gap_description=name,
            ))

        def finish(phase, reason, signal=None, trigger_level=None):
            passed = sum(c.status == "PASSED" for c in conditions)
            state["phase"] = phase
            entry_level = trigger_level
            if entry_level is None and bars:
                entry_level = bars[-1].high if bullish else bars[-1].low
            summary["primary_blocker"] = reason
            diagnostic = StrategyTriggerDiagnostics(
                strategy=StrategyName.TREND_PULLBACK,
                strategy_label=f"Trend Pullback ({opt.value})",
                direction=direction,
                option_type=opt,
                phase_state=phase,
                overall_status="READY_TO_TRIGGER" if signal else "WAITING",
                passed_count=passed,
                total_count=len(conditions),
                ready_pct=round(100 * passed / len(conditions), 1) if conditions else 0,
                key_blocker=reason,
                current_spot=live_price,
                phase_summary=summary,
                conditions=conditions,
                target_entry_level=entry_level,
            )
            return signal, diagnostic

        def setting(name, default):
            value = getattr(overrides, name, None) if overrides else None
            return default if value is None else value

        now = features.timestamp
        valid = (features.data_ready and len(bars) >= 6 and bool(macro) and bool(futures)
                 and features.atr_5m > 0 and features.futures_atr_5m > 0)
        if valid:
            valid = (all(c.source in ("BREEZE", "LIVE") and c.end_time <= now for c in bars + macro + futures)
                     and 0 <= (now - bars[-1].end_time).total_seconds() < 300
                     and 0 <= (now - macro[-1].end_time).total_seconds() < 900
                     and futures[-1].end_time == bars[-1].end_time)
        if not valid:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
            if bars:
                state["after"] = bars[-1].end_time.isoformat()
            return finish("SEARCH_REGIME", features.data_reason or "Awaiting complete real spot/futures data")

        trigger_bar = bars[-1]
        session = trigger_bar.start_time.astimezone(IST).date().isoformat()
        if state.get("session") != session:
            state.clear()
            state["session"] = session

        if state.get("cooldown") and trigger_bar.end_time <= datetime.fromisoformat(state["cooldown"]):
            return finish("WAIT_FOR_IMPULSE", "Awaiting a completed candle after exit")

        if state.get("consumed") == trigger_bar.end_time.isoformat():
            return finish("TRIGGERED", "Setup already consumed for this bar")

        atr = features.atr_5m
        adx_target = setting("adx_threshold", self.adx_threshold)

        if bullish:
            ema_direction_ok = features.ema20_15m > features.ema50_15m
            price_direction_ok = macro[-1].close > features.ema20_15m
            slope_support = (features.ema20_slope_15m > 0) or (features.ema20_slope_15m == 0.0 and features.ema20_slope_norm_15m > 0)
            di_support = features.plus_di_15m > features.minus_di_15m
            adx_support = features.adx_15m >= adx_target
        else:
            ema_direction_ok = features.ema20_15m < features.ema50_15m
            price_direction_ok = macro[-1].close < features.ema20_15m
            slope_support = (features.ema20_slope_15m < 0) or (features.ema20_slope_15m == 0.0 and features.ema20_slope_norm_15m < 0)
            di_support = features.minus_di_15m > features.plus_di_15m
            adx_support = features.adx_15m >= adx_target

        support_score = int(bool(slope_support)) + int(bool(di_support)) + int(bool(adx_support))
        macro_regime_ok = bool(ema_direction_ok and price_direction_ok and support_score >= 1)

        condition(
            "macro_regime",
            "15m macro regime",
            macro_regime_ok,
            f"EMA dir: {ema_direction_ok}, Price: {price_direction_ok}, Support: {support_score}/3 (ADX: {features.adx_15m:.1f})",
            "EMA direction + Price pos + Support score >= 1/3",
        )
        summary["regime"] = {
            "status": "QUALIFIED" if macro_regime_ok else "WAITING",
            "macro_ema_direction": ema_direction_ok,
            "macro_price_direction": price_direction_ok,
            "macro_slope_support": bool(slope_support),
            "macro_di_support": bool(di_support),
            "macro_adx_support": bool(adx_support),
            "macro_support_score": support_score,
            "macro_regime_pass": macro_regime_ok,
            "direction_score": f"{support_score}/3",
            "adx": features.adx_15m,
        }
        summary["macro_ema_direction"] = ema_direction_ok
        summary["macro_price_direction"] = price_direction_ok
        summary["macro_slope_support"] = bool(slope_support)
        summary["macro_di_support"] = bool(di_support)
        summary["macro_adx_support"] = bool(adx_support)
        summary["macro_support_score"] = support_score
        summary["macro_regime_pass"] = macro_regime_ok

        if not macro_regime_ok:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
            state["after"] = trigger_bar.end_time.isoformat()
            return finish("SEARCH_REGIME", "Macro regime is not qualified")

        window = [c for c in bars[-15:] if c.start_time.astimezone(IST).date().isoformat() == session]
        impulse = state.get("impulse")
        min_impulse_val = setting("min_impulse_atr", self.min_impulse_atr)
        if impulse is None:
            impulse = self._impulse(window, atr, bullish, state.get("after"), min_impulse_val)
            if impulse is None:
                impulse = self._fallback_impulse(window, atr, bullish, state.get("after"), min_impulse_val)
            if impulse:
                state["impulse"] = impulse

        # Reset confirm count if setup changed
        if impulse:
            impulse_key = f"{impulse['start']}_{impulse['end']}_{impulse['low']}_{impulse['high']}"
            if state.get("active_setup_key") != impulse_key:
                state["active_setup_key"] = impulse_key
                state["confirm_count"] = 0
        else:
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0

        imp_src = impulse.get("source", "PIVOT") if impulse else "NONE"
        imp_size = impulse["height"] if impulse else 0.0
        imp_atr_ratio = round(imp_size / atr, 2) if impulse and atr > 0 else 0.0
        imp_start = impulse["start"] if impulse else None
        imp_end = impulse["end"] if impulse else None

        condition(
            "chronological_impulse",
            "Chronological impulse",
            bool(impulse),
            f"{imp_src}: {imp_size:.2f} ({imp_atr_ratio:.2f} ATR)" if impulse else "None",
            f">= {min_impulse_val:.2f} ATR",
        )
        summary["impulse"] = {
            "found": bool(impulse),
            "source": imp_src,
            "impulse_source": imp_src,
            "impulse_size": imp_size,
            "impulse_atr_ratio": imp_atr_ratio,
            "impulse_start": imp_start,
            "impulse_end": imp_end,
            "height_atr": imp_atr_ratio,
        }
        summary["impulse_source"] = imp_src
        summary["impulse_size"] = imp_size
        summary["impulse_atr_ratio"] = imp_atr_ratio
        summary["impulse_start"] = imp_start
        summary["impulse_end"] = imp_end

        if not impulse:
            return finish("WAIT_FOR_IMPULSE", "Waiting for a chronological impulse")

        start, end = datetime.fromisoformat(impulse["start"]), datetime.fromisoformat(impulse["end"])
        pb = [c for c in bars if c.end_time > end]
        age = len(pb)
        summary["pullback"] = {"state": "ACTIVE", "bars": age, "depth_pct": 0, "retest": "None", "retest_distance_atr": {}}
        if not pb:
            return finish("PULLBACK_ACTIVE", "Waiting for at least two pullback bars")

        extreme = min(c.low for c in pb) if bullish else max(c.high for c in pb)
        depth = (impulse["high"] - extreme if bullish else extreme - impulse["low"]) / impulse["height"]
        summary["pullback"]["depth_pct"] = round(depth * 100, 1)

        min_depth = setting("min_pullback_depth", self.min_pullback_depth)
        max_depth = setting("max_pullback_depth", self.max_pullback_depth)

        invalid = (
            age > 9
            or depth > (max_depth + 1e-6)
            or (trigger_bar.low < impulse["low"] if bullish else trigger_bar.high > impulse["high"])
        )
        if invalid:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
            state["after"] = trigger_bar.end_time.isoformat()
            return finish("WAIT_FOR_IMPULSE", "Setup reset: duration, depth or impulse structure invalid")

        sequence = [c for c in bars if c.end_time >= end]
        candle_contiguity_pass = True
        candle_interval_seconds = None

        for a, b in zip(sequence, sequence[1:]):
            ok, secs = validate_candle_contiguity(a, b, "5m")
            candle_interval_seconds = secs
            if not ok:
                candle_contiguity_pass = False
                break

        if not candle_contiguity_pass:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
            state["after"] = trigger_bar.end_time.isoformat()
            summary["candle_contiguity_pass"] = False
            summary["candle_interval_seconds"] = candle_interval_seconds
            condition(
                "candle_contiguity",
                "Candle contiguity",
                False,
                f"{candle_interval_seconds:.1f}s",
                "300s ± 5s",
            )
            return finish("WAIT_FOR_IMPULSE", f"Setup reset: incomplete candle sequence ({candle_interval_seconds:.1f}s)")

        if candle_interval_seconds is None and len(bars) >= 2:
            ok, secs = validate_candle_contiguity(bars[-2], bars[-1], "5m")
            candle_interval_seconds = secs
            candle_contiguity_pass = ok
        elif candle_interval_seconds is None:
            candle_interval_seconds = 300.0

        summary["candle_contiguity_pass"] = candle_contiguity_pass
        summary["candle_interval_seconds"] = candle_interval_seconds
        condition(
            "candle_contiguity",
            "Candle contiguity",
            candle_contiguity_pass,
            f"{candle_interval_seconds:.1f}s",
            "300s ± 5s",
        )

        retest_tol = setting("retest_tolerance_atr", self.retest_tolerance_atr)
        retests = []
        retest_distances = {}
        if abs(extreme - features.ema9_5m) <= (retest_tol * atr + 1e-9):
            retests.append("EMA9")
            retest_distances["EMA9"] = round(abs(extreme - features.ema9_5m) / atr, 3)
        if abs(extreme - features.ema20_5m) <= (retest_tol * atr + 1e-9):
            retests.append("EMA20")
            retest_distances["EMA20"] = round(abs(extreme - features.ema20_5m) / atr, 3)

        aligned = {c.end_time: c for c in futures}
        leg = [c for c in bars if start <= c.end_time <= end]
        if not leg or not all(c.end_time in aligned for c in leg + pb):
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
            state["after"] = trigger_bar.end_time.isoformat()
            return finish("WAIT_FOR_IMPULSE", "Missing aligned futures bars for this setup")

        for c in pb:
            f = aligned[c.end_time]
            history = [x for x in futures if x.end_time <= f.end_time]
            vwap = FeatureEngine.calculate_futures_vwap(history)
            fatr = FeatureEngine.calculate_atr(history)
            distance = max(f.low - vwap, vwap - f.high, 0)
            if vwap > 0 and fatr > 0 and distance <= (retest_tol * fatr + 1e-9):
                retests.append("Futures VWAP")
                retest_distances["Futures VWAP"] = round(distance / fatr, 3)
                break

        prior = [c for c in bars if c.end_time <= start]
        levels = [prior[i].high if bullish else prior[i].low for i in range(1, len(prior)-1)
                  if (prior[i].high > prior[i-1].high and prior[i].high >= prior[i+1].high if bullish
                      else prior[i].low < prior[i-1].low and prior[i].low <= prior[i+1].low)]
        for level in levels:
            if impulse["low"] < level < impulse["high"] and abs(extreme - level) <= (retest_tol * atr + 1e-9):
                retests.append("Prior breakout level")
                retest_distances["Prior breakout level"] = round(abs(extreme - level) / atr, 3)
                break

        pb_ok = (2 <= age <= 9 and (min_depth - 1e-6) <= depth <= (max_depth + 1e-6) and bool(retests))
        condition(
            "pullback_depth_retest",
            "Pullback duration, depth and retest",
            pb_ok,
            f"{age} bars; {depth:.1%}; {retests}",
            f"2-9 bars; {min_depth:.0%}-{max_depth:.0%}; retest within {retest_tol:.2f} ATR",
        )
        summary["pullback"].update(
            state="QUALIFIED" if pb_ok else "ACTIVE",
            retest=", ".join(retests) or "None",
            retest_distance_atr=retest_distances,
        )
        if not pb_ok:
            state["confirm_count"] = 0
            return finish("PULLBACK_ACTIVE", "Waiting for controlled pullback and reference retest")

        # Iteration 1 — Live Price Breakout Trigger with Buffer & Polling Confirmation
        previous_completed_bar = bars[-1]
        buffer_atr = setting("breakout_buffer_atr", self.breakout_buffer_atr)
        required_polls = setting("breakout_confirm_polls", self.breakout_confirm_polls)

        if bullish:
            trigger_price = round(previous_completed_bar.high + (buffer_atr * atr), 2)
            breakout_condition = (live_price > trigger_price)
        else:
            trigger_price = round(previous_completed_bar.low - (buffer_atr * atr), 2)
            breakout_condition = (live_price < trigger_price)

        confirm_count = state.get("confirm_count", 0)
        if breakout_condition:
            confirm_count += 1
        else:
            confirm_count = 0
        state["confirm_count"] = confirm_count

        breakout_confirmed = (confirm_count >= required_polls)

        condition(
            "breakout_trigger",
            "Live price breakout confirmation",
            breakout_confirmed,
            f"Live {live_price:.2f} vs Trigger {trigger_price:.2f} (polls {confirm_count}/{required_polls})",
            f"{'>' if bullish else '<'} {trigger_price:.2f} for {required_polls} polls",
        )
        summary["trigger"] = {
            "breakout_trigger_price": trigger_price,
            "live_price": live_price,
            "breakout_condition": breakout_condition,
            "breakout_confirm_count": confirm_count,
            "required_polls": required_polls,
            "confirmed": breakout_confirmed,
            "previous_completed_bar_end": previous_completed_bar.end_time.isoformat(),
        }

        if not breakout_confirmed:
            reason = (
                f"Waiting for live price breakout ({live_price:.2f} vs {trigger_price:.2f})"
                if not breakout_condition
                else f"Breakout pending confirmation (poll {confirm_count}/{required_polls})"
            )
            return finish("WAIT_FOR_TRIGGER", reason, trigger_level=trigger_price)

        # Iteration 3 Part A — Ternary Confirmation Handling (True / False / None)
        leg_volume = sum(aligned[c.end_time].volume for c in leg) / len(leg) if leg else 0
        pb_volume = sum(aligned[c.end_time].volume for c in pb) / len(pb) if pb else 0
        ratio = pb_volume / leg_volume if leg_volume > 0 else None

        # Supertrend
        st = features.supertrend_direction
        if st and st in ("BULLISH", "BEARISH"):
            supertrend_val = (st == direction.value)
        else:
            supertrend_val = None

        # Futures VWAP
        if features.futures_vwap > 0 and features.futures_price > 0:
            vwap_val = (sign * (features.futures_price - features.futures_vwap) > 0)
        else:
            vwap_val = None

        # Derivatives score
        deriv = features.bull_derivatives_score if bullish else features.bear_derivatives_score
        deriv_target = setting("bull_derivatives_score" if bullish else "bear_derivatives_score", 2)
        if deriv is None:
            deriv_val = None
        else:
            deriv_val = (deriv >= deriv_target)

        # Futures OI buildup
        buildup = features.futures_buildup
        if buildup is None or buildup in ("UNKNOWN", "UNAVAILABLE", "NONE", ""):
            futures_oi_val = None
        elif buildup in ("LONG_BUILDUP", "SHORT_COVERING"):
            futures_oi_val = True if bullish else False
        elif buildup in ("SHORT_BUILDUP", "LONG_UNWINDING"):
            futures_oi_val = False if bullish else True
        else:
            futures_oi_val = False

        # RVOL
        if features.rvol_5m is None or features.rvol_5m <= 0:
            rvol_val = None
        else:
            rvol_val = (features.rvol_5m >= setting("rvol_threshold", self.rvol_threshold))

        # Pullback volume dry-up
        if ratio is None:
            volume_dryup_val = None
        else:
            volume_dryup_val = (ratio < 0.80)

        confirmations = {
            "supertrend": supertrend_val,
            "vwap": vwap_val,
            "derivatives": deriv_val,
            "futures_oi": futures_oi_val,
            "rvol": rvol_val,
            "volume_dryup": volume_dryup_val,
        }

        available_confirmations = [v for v in confirmations.values() if v is not None]
        passed_confirmations = [v for v in available_confirmations if v is True]

        required_passes = setting("min_confirmation_score", self.min_confirmation_score)
        min_available = setting("min_available_confirmations", self.min_available_confirmations)

        conf_ok = (len(available_confirmations) >= min_available and len(passed_confirmations) >= required_passes)

        condition(
            "confirmation_score",
            "Entry confirmations",
            conf_ok,
            f"{len(passed_confirmations)} passed / {len(available_confirmations)} available",
            f">={required_passes} passed and >={min_available} available",
        )
        summary["confirmation"] = {
            "passed_confirmation_count": len(passed_confirmations),
            "available_confirmation_count": len(available_confirmations),
            "required_passes": required_passes,
            "min_available": min_available,
            "confirmations": confirmations,
        }

        # Iteration 3 Part B — Remove Minimum 0.45 ATR Risk Rejection Floor
        raw_stop = extreme - sign * 0.15 * atr
        risk = sign * (live_price - raw_stop)
        stop = raw_stop
        max_r = setting("max_initial_r_atr", 1.60) * atr

        risk_ok = (0 < risk <= max_r + 1e-9)

        condition(
            "risk_r_band",
            "Structural initial R",
            risk_ok,
            round(risk / atr, 2) if atr > 0 else 0,
            f"0 < R <= {max_r / atr:.2f} ATR",
        )
        summary["risk"] = {
            "initial_risk_atr": round(risk / atr, 2) if atr > 0 else 0,
            "initial_r_points": risk,
            "stop": stop,
            "max_r": max_r,
        }

        if not conf_ok:
            return finish(
                "TRIGGERED",
                f"Confirmation score {len(passed_confirmations)}/{len(available_confirmations)} below required {required_passes} (min {min_available} available)",
                trigger_level=trigger_price,
            )

        if not risk_ok:
            return finish(
                "RISK_AND_CONTRACT_CHECK",
                f"Structural risk {risk/atr:.2f} ATR outside allowed band (0 < R <= {max_r/atr:.2f} ATR)",
                trigger_level=trigger_price,
            )

        signal = StrategySignal(
            signal_id=f"SIG-A-{direction.value}-{int(trigger_bar.end_time.timestamp())}",
            strategy=StrategyName.TREND_PULLBACK,
            direction=direction,
            option_type=opt,
            timestamp=features.timestamp,
            spot_reference_price=live_price,
            structural_stop=stop,
            r_points=risk,
            derivatives_score=deriv or 0.0,
            features_snapshot={
                "entry_reference_spot": live_price,
                "pullback_low": extreme if bullish else None,
                "pullback_high": extreme if not bullish else None,
                "impulse_low": impulse["low"],
                "impulse_high": impulse["high"],
                "impulse_source": imp_src,
                "impulse_size": imp_size,
                "pullback_bars": age,
                "pullback_depth": depth,
                "pullback_vol_ratio": ratio,
                "confirmation_score": f"{len(passed_confirmations)}/{len(available_confirmations)}",
                "confirmations": confirmations,
                "direction_score": f"{support_score}/3",
                "atr": atr,
                "r_initial": risk,
                "breakout_trigger_price": trigger_price,
                "candle_contiguity_pass": candle_contiguity_pass,
                "candle_interval_seconds": candle_interval_seconds,
            },
        )
        return finish("READY_TO_TRIGGER", "Setup qualified; checking contract and risk limits", signal, trigger_level=trigger_price)

    def evaluate(self, features, candles_5m, candles_15m, futures_candles=None, overrides=None):
        for direction in TradeDirection:
            signal, _ = self._decision(direction, features, candles_5m, candles_15m, futures_candles or [], overrides)
            if signal:
                self.state[direction.value]["consumed"] = signal.timestamp.isoformat()
                self.state[direction.value]["after"] = signal.timestamp.isoformat()
                self.state[direction.value].pop("impulse", None)
                self.state[direction.value].pop("active_setup_key", None)
                self.state[direction.value]["confirm_count"] = 0
                return signal
        return None

    def diagnose(self, features, candles_5m, candles_15m, overrides=None, futures_candles=None):
        preview = deepcopy(self)
        return [
            preview._decision(d, features, candles_5m, candles_15m, futures_candles or [], overrides)[1]
            for d in TradeDirection
        ]
