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
        breakout_confirm_polls: int = 1,
        min_impulse_atr: float = 0.70,
        min_pullback_depth: float = 0.08,
        max_pullback_depth: float = 0.70,
        call_pullback_min_depth: Optional[float] = None,
        call_pullback_max_depth: Optional[float] = None,
        put_pullback_min_depth: Optional[float] = None,
        put_pullback_max_depth: Optional[float] = None,
        retest_tolerance_atr: float = 0.45,
        min_available_confirmations: int = 2,
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
        # The legacy global arguments remain supported for isolated callers and
        # old tests. Production passes the directional configuration below.
        self.call_pullback_min_depth = call_pullback_min_depth if call_pullback_min_depth is not None else min_pullback_depth
        self.call_pullback_max_depth = call_pullback_max_depth if call_pullback_max_depth is not None else max_pullback_depth
        self.put_pullback_min_depth = put_pullback_min_depth if put_pullback_min_depth is not None else min_pullback_depth
        self.put_pullback_max_depth = put_pullback_max_depth if put_pullback_max_depth is not None else max_pullback_depth
        self.retest_tolerance_atr = retest_tolerance_atr
        self.min_available_confirmations = min_available_confirmations
        self.state = {d.value: {} for d in TradeDirection}

    def export_state(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_state(self, state: dict[str, Any]) -> None:
        restored = {}
        for direction in TradeDirection:
            current = dict(state.get(direction.value, {}))
            # Older persisted state used `after` for several unrelated reset
            # events. Do not promote that ambiguous value into a new regime
            # cutoff; only the explicit lifecycle field is authoritative.
            if "regime_qualified_since" not in current:
                current.pop("setup_cutoff", None)
                current.pop("after", None)
            else:
                current["setup_cutoff"] = current.get("regime_qualified_since")
                current["after"] = current.get("setup_cutoff")
            restored[direction.value] = current
        self.state = restored

    def reset(self, at: Optional[datetime] = None) -> None:
        for direction in TradeDirection:
            old = self.state[direction.value]
            self.state[direction.value] = {
                "session": at.astimezone(IST).date().isoformat() if at else old.get("session"),
                "consumed": old.get("consumed"),
                "cooldown": old.get("cooldown"),
                "confirm_count": 0,
            }
            # Keep the legacy field for compatibility with older state
            # consumers, but it is not used as the regime cutoff anymore.
            if at is not None:
                self.state[direction.value]["after"] = at.isoformat()

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        self.state[direction.value] = {
            "session": at.astimezone(IST).date().isoformat(),
            "cooldown": at.isoformat(),
            "confirm_count": 0,
            "after": at.isoformat(),
        }

    @staticmethod
    def _impulse(bars, atr, bullish, after=None, min_impulse_atr=0.70, rejection_reasons=None):
        """Latest confirmed pivot pair, with extremes in chronological order."""
        def reject(category, detail):
            if rejection_reasons is not None:
                item = {"category": category, "detail": detail}
                if item not in rejection_reasons:
                    rejection_reasons.append(item)

        lows = [i for i in range(1, len(bars)-1)
                if bars[i].low < bars[i-1].low and bars[i].low <= bars[i+1].low]
        highs = [i for i in range(1, len(bars)-1)
                 if bars[i].high > bars[i-1].high and bars[i].high >= bars[i+1].high]
        starts, ends = (lows, highs) if bullish else (highs, lows)
        cutoff = datetime.fromisoformat(after) if after else None
        for end in reversed(ends):
            if cutoff and bars[end].end_time < cutoff:
                reject("cutoff relationship", f"candidate ended at {bars[end].end_time.isoformat()} before setup cutoff {cutoff.isoformat()}")
                continue
            for start in reversed(starts):
                if start >= end:
                    continue
                low = bars[start].low if bullish else bars[end].low
                high = bars[end].high if bullish else bars[start].high
                if high - low >= (min_impulse_atr * atr - 1e-9):
                    leg = bars[start:end+1]
                    if min(c.low for c in leg) < low or max(c.high for c in leg) > high:
                        reject("chronology", f"candidate {bars[start].end_time.isoformat()} -> {bars[end].end_time.isoformat()} was not a clean directional leg")
                        continue
                    return {
                        "start": bars[start].end_time.isoformat(),
                        "end": bars[end].end_time.isoformat(),
                        "low": low,
                        "high": high,
                        "height": high - low,
                        "source": "PIVOT",
                    }
                else:
                    reject("magnitude", f"candidate {bars[start].end_time.isoformat()} -> {bars[end].end_time.isoformat()} was {high - low:.2f} pts, below {min_impulse_atr * atr:.2f} pts")
        if not starts or not ends:
            reject("direction", "no confirmed pivot sequence in the requested directional order")
        elif not rejection_reasons:
            reject("direction", "confirmed pivots did not form the requested directional order")
        return None

    @staticmethod
    def _fallback_impulse(bars, atr, bullish, after=None, min_impulse_atr=0.70, rejection_reasons=None):
        """Chronological swing fallback from the latest 6 completed 5-minute bars."""
        def reject(category, detail):
            if rejection_reasons is not None:
                item = {"category": category, "detail": detail}
                if item not in rejection_reasons:
                    rejection_reasons.append(item)

        if len(bars) < 6:
            reject("age", f"fallback requires 6 completed candles, received {len(bars)}")
            return None
        recent = bars[-6:]
        swing_low_index = min(range(len(recent)), key=lambda i: (recent[i].low, i if bullish else -i))
        swing_high_index = max(range(len(recent)), key=lambda i: (recent[i].high, i if bullish else -i))

        if bullish:
            if swing_low_index >= swing_high_index:
                reject("direction", "bullish fallback requires the swing low to occur before the swing high")
                return None
            start_bar = recent[swing_low_index]
            end_bar = recent[swing_high_index]
            swing_low = start_bar.low
            swing_high = end_bar.high
        else:
            if swing_high_index >= swing_low_index:
                reject("direction", "bearish fallback requires the swing high to occur before the swing low")
                return None
            start_bar = recent[swing_high_index]
            end_bar = recent[swing_low_index]
            swing_low = end_bar.low
            swing_high = start_bar.high

        impulse_size = swing_high - swing_low
        if impulse_size < (min_impulse_atr * atr - 1e-9):
            reject("magnitude", f"fallback swing was {impulse_size:.2f} pts, below {min_impulse_atr * atr:.2f} pts")
            return None

        cutoff = datetime.fromisoformat(after) if after else None
        if cutoff and end_bar.end_time < cutoff:
            reject("cutoff relationship", f"fallback ended at {end_bar.end_time.isoformat()} before setup cutoff {cutoff.isoformat()}")
            return None

        return {
            "start": start_bar.end_time.isoformat(),
            "end": end_bar.end_time.isoformat(),
            "low": swing_low,
            "high": swing_high,
            "height": impulse_size,
            "source": "FALLBACK",
        }

    def _decision(
        self,
        direction,
        features,
        bars,
        macro,
        futures,
        overrides,
        is_diagnose: bool = False,
        replay_context: Optional[dict[str, Any]] = None,
    ):
        bullish = direction == TradeDirection.BULLISH
        sign = 1 if bullish else -1
        opt = OptionType.CALL if bullish else OptionType.PUT
        state = self.state[direction.value]
        summary, conditions = {}, []
        replay_context = replay_context or {}
        live_price = replay_context.get("live_price", features.spot_price)
        first_failure: Optional[tuple[str, str, Optional[float]]] = None

        def condition(id, name, passed, current, target, gap=None, applicable=True, status_override=None):
            conditions.append(TriggerCondition(
                id=id,
                name=name,
                status=status_override or ("PASSED" if passed else ("PENDING" if applicable else "N/A")),
                current_value=str(current),
                target_threshold=str(target),
                gap_description=str(gap) if gap is not None else str(name),
            ))

        def finish(phase, reason, signal=None, trigger_level=None):
            applicable_conditions = [c for c in conditions if c.status != "N/A"]
            passed = sum(c.status in ("PASSED", "PASS") for c in applicable_conditions)
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
                total_count=len(applicable_conditions),
                ready_pct=round(100 * passed / len(applicable_conditions), 1) if applicable_conditions else 0,
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

        def factor_status(value):
            if value is True:
                return "PASS"
            if value is False:
                return "FAIL"
            return "UNAVAILABLE"

        def add_confirmation_conditions(confirmations, deriv_score=None, deriv_target=1.0):
            labels = {
                "supertrend": ("Supertrend confirmation", "Directional 5m Supertrend"),
                "vwap": ("Futures VWAP confirmation", "Futures price on directional side of VWAP"),
                "derivatives": ("Derivatives confirmation", "Directional derivatives score threshold"),
                "futures_oi": ("Futures OI/buildup confirmation", "Directional futures price/OI buildup"),
                "rvol": ("Futures RVOL confirmation", "RVOL threshold"),
                "volume_dryup": ("Pullback volume dry-up confirmation", "Pullback/impulse volume ratio"),
            }
            for key, value in confirmations.items():
                status = factor_status(value)
                name, target = labels[key]
                current = status
                if key == "derivatives":
                    current = "UNAVAILABLE" if deriv_score is None else f"{deriv_score:+.1f}"
                    target = f">= {deriv_target:+.1f}"
                condition(
                    f"confirmation_{key}",
                    name,
                    value is True,
                    current,
                    target,
                    "Futures data unavailable for this factor" if status == "UNAVAILABLE" else status,
                    status_override=status,
                )

        now = features.timestamp
        allowed_sources = ("BREEZE", "KITE", "LIVE")
        valid_futures = sorted(
            [
                c for c in futures
                if c.source in allowed_sources
                and bool(c.instrument_id)
                and c.interval == "5m"
                and c.start_time < c.end_time
                and c.end_time <= now
            ],
            key=lambda c: c.end_time,
        )
        latest_underlying = bars[-1].end_time if bars else None
        latest_futures = valid_futures[-1].end_time if valid_futures else None
        futures_fresh = bool(
            latest_futures
            and 0 <= (now - latest_futures).total_seconds() < EXPECTED_5M_SECONDS
        )
        summary["futures"] = {
            "candles_requested": 0,
            "candles_available": len(valid_futures),
            "candles_aligned": 0,
            "alignment_coverage": 0.0,
            "latest_completed_futures_timestamp": latest_futures.isoformat() if latest_futures else None,
            "latest_underlying_timestamp": latest_underlying.isoformat() if latest_underlying else None,
            "fresh": futures_fresh,
            "confirmation_factors_unavailable": [
                "futures_vwap_confirmation",
                "futures_oi",
                "futures_rvol",
                "pullback_volume_dryup",
                "pullback_futures_vwap_retest",
            ],
            "factor_statuses": {
                "futures_vwap_confirmation": "UNAVAILABLE",
                "futures_oi": "UNAVAILABLE",
                "futures_rvol": "UNAVAILABLE",
                "pullback_volume_dryup": "UNAVAILABLE",
                "pullback_futures_vwap_retest": "UNAVAILABLE",
            },
        }
        valid = (features.data_ready and len(bars) >= 6 and bool(macro) and bool(futures)
                 and features.atr_5m > 0 and features.futures_atr_5m > 0)
        if valid:
            valid = (all(c.source in allowed_sources and c.end_time <= now for c in bars + macro)
                     and bool(valid_futures)
                     and 0 <= (now - bars[-1].end_time).total_seconds() < 300
                     and 0 <= (now - macro[-1].end_time).total_seconds() < 900
                     and futures_fresh)
        if not valid and not is_diagnose:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
            return finish("SEARCH_REGIME", features.data_reason or "Awaiting complete real spot/futures data")
        if not valid and is_diagnose:
            return finish("SEARCH_REGIME", features.data_reason or "Awaiting complete real spot/futures data")

        if not bars:
            return finish("SEARCH_REGIME", "Awaiting sufficient market candle history")

        trigger_bar = bars[-1]
        session = trigger_bar.start_time.astimezone(IST).date().isoformat()
        if state.get("session") != session:
            state.clear()
            state["session"] = session

        if state.get("cooldown") and trigger_bar.end_time <= datetime.fromisoformat(state["cooldown"]):
            if not is_diagnose:
                return finish("WAIT_FOR_IMPULSE", "Awaiting a completed candle after exit")
            first_failure = first_failure or ("WAIT_FOR_IMPULSE", "Awaiting a completed candle after exit", None)

        if state.get("consumed") == trigger_bar.end_time.isoformat():
            if not is_diagnose:
                return finish("TRIGGERED", "Setup already consumed for this bar")
            first_failure = first_failure or ("TRIGGERED", "Setup already consumed for this bar", None)

        atr = max(1.0, features.atr_5m if features.atr_5m > 0 else 15.0)
        adx_target = setting("adx_threshold", self.adx_threshold)

        if bullish:
            ema_direction_ok = features.ema20_15m > features.ema50_15m
            price_direction_ok = (macro[-1].close if macro else live_price) > features.ema20_15m
            slope_support = (features.ema20_slope_15m > 0) or (features.ema20_slope_15m == 0.0 and features.ema20_slope_norm_15m > 0)
            di_support = features.plus_di_15m > features.minus_di_15m
            adx_support = features.adx_15m >= adx_target
        else:
            ema_direction_ok = features.ema20_15m < features.ema50_15m
            price_direction_ok = (macro[-1].close if macro else live_price) < features.ema20_15m
            slope_support = (features.ema20_slope_15m < 0) or (features.ema20_slope_15m == 0.0 and features.ema20_slope_norm_15m < 0)
            di_support = features.minus_di_15m > features.plus_di_15m
            adx_support = features.adx_15m >= adx_target

        support_score = int(bool(slope_support)) + int(bool(di_support)) + int(bool(adx_support))
        macro_regime_ok = bool(ema_direction_ok and price_direction_ok and support_score >= 1)

        previous_cutoff = state.get("regime_qualified_since")
        if macro_regime_ok:
            if previous_cutoff is None:
                # Anchor the setup to the start of the 15m regime candle that
                # first qualifies. This remains stable across 5m polls while
                # the same macro candle/regime is being observed.
                previous_cutoff = (macro[-1].start_time if macro else trigger_bar.end_time).isoformat()
                state["regime_qualified_since"] = previous_cutoff
                state["setup_cutoff"] = previous_cutoff
                if "after" not in state:
                    state["after"] = previous_cutoff  # legacy read-only alias
                cutoff_event = "CREATED"
            else:
                state["setup_cutoff"] = previous_cutoff
                state["after"] = previous_cutoff  # legacy read-only alias
                cutoff_event = "PRESERVED"
            state["setup_cutoff_event"] = cutoff_event
        else:
            cutoff_event = (
                "INVALIDATED"
                if previous_cutoff or state.get("setup_cutoff_event") == "INVALIDATED"
                else "NONE"
            )
            state.pop("regime_qualified_since", None)
            state.pop("setup_cutoff", None)
            state.pop("after", None)
            if cutoff_event == "INVALIDATED":
                state["setup_cutoff_event"] = cutoff_event

        macro_gap = (
            f"15m regime qualified (Support: {support_score}/3, ADX: {features.adx_15m:.1f})"
            if macro_regime_ok
            else f"Waiting for 15m trend alignment (EMA dir: {ema_direction_ok}, Price: {price_direction_ok}, Support: {support_score}/3, ADX: {features.adx_15m:.1f} vs {adx_target:.1f})"
        )
        condition(
            "macro_regime",
            "15m macro regime",
            macro_regime_ok,
            f"EMA {features.ema20_15m:.1f}/{features.ema50_15m:.1f}, Price {macro[-1].close if macro else live_price:.1f}, ADX {features.adx_15m:.1f}",
            "EMA direction + Price pos + Support score >= 1/3",
            macro_gap,
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
            "qualified": macro_regime_ok,
            "qualified_since": previous_cutoff if macro_regime_ok else None,
            "setup_cutoff": previous_cutoff if macro_regime_ok else None,
            "cutoff_event": cutoff_event,
        }
        summary["macro_ema_direction"] = ema_direction_ok
        summary["macro_price_direction"] = price_direction_ok
        summary["macro_slope_support"] = bool(slope_support)
        summary["macro_di_support"] = bool(di_support)
        summary["macro_adx_support"] = bool(adx_support)
        summary["macro_support_score"] = support_score
        summary["macro_regime_pass"] = macro_regime_ok
        summary["setup_direction"] = direction.value
        summary["macro_regime_qualified"] = macro_regime_ok
        summary["regime_first_qualified_at"] = previous_cutoff if macro_regime_ok else None
        summary["setup_cutoff"] = previous_cutoff if macro_regime_ok else None
        summary["setup_cutoff_event"] = cutoff_event
        if macro_regime_ok and previous_cutoff:
            cutoff_dt = datetime.fromisoformat(previous_cutoff)
            summary["setup_age_seconds"] = max(0.0, (trigger_bar.end_time - cutoff_dt).total_seconds())
            summary["completed_5m_candles_since_cutoff"] = sum(
                1 for candle in bars if candle.end_time > cutoff_dt
            )
        else:
            summary["setup_age_seconds"] = None
            summary["completed_5m_candles_since_cutoff"] = 0

        if not macro_regime_ok:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
            if not is_diagnose:
                return finish("SEARCH_REGIME", "Macro regime is not qualified")
            first_failure = first_failure or ("SEARCH_REGIME", "Macro regime is not qualified", None)

        session_bars = [c for c in bars if c.start_time.astimezone(IST).date().isoformat() == session]
        setup_cutoff = state.get("setup_cutoff")
        cutoff_time = datetime.fromisoformat(setup_cutoff) if setup_cutoff else None
        pre_cutoff = [c for c in session_bars if cutoff_time and c.end_time < cutoff_time]
        post_cutoff = [c for c in session_bars if not cutoff_time or c.end_time >= cutoff_time]
        included_pre_cutoff = pre_cutoff[-4:] if cutoff_time else []
        # Preserve the existing recent-candle horizon after the cutoff while
        # adding only the explicitly allowed pre-cutoff context.
        window = (included_pre_cutoff + post_cutoff[-15:]) if cutoff_time else session_bars[-15:]
        window = sorted(window, key=lambda c: c.end_time)
        pivot_rejections, fallback_rejections = [], []
        impulse = state.get("impulse")
        min_impulse_val = setting("min_impulse_atr", self.min_impulse_atr)
        if impulse is None:
            impulse = self._impulse(
                window, atr, bullish, setup_cutoff, min_impulse_val,
                rejection_reasons=pivot_rejections,
            )
            if impulse is None:
                impulse = self._fallback_impulse(
                    window, atr, bullish, setup_cutoff, min_impulse_val,
                    rejection_reasons=fallback_rejections,
                )
            if impulse:
                state["impulse"] = impulse

        search_rejections = pivot_rejections + fallback_rejections
        excluded_stale_count = max(0, len(session_bars) - len(window))
        if excluded_stale_count:
            search_rejections.append({
                "category": "age/staleness",
                "detail": f"{excluded_stale_count} older session candle(s) excluded outside the bounded impulse search window",
            })
        search_start = window[0].end_time.isoformat() if window else None
        impulse_crossed_cutoff = bool(
            impulse and cutoff_time
            and datetime.fromisoformat(impulse["start"]) < cutoff_time <= datetime.fromisoformat(impulse["end"])
        )
        summary["impulse_search"] = {
            "setup_cutoff_timestamp": setup_cutoff,
            "search_start_timestamp": search_start,
            "included_pre_cutoff": bool(included_pre_cutoff),
            "pre_cutoff_candles_included": len(included_pre_cutoff),
            "post_cutoff_candles_searched": len(post_cutoff[-15:]),
            "search_candle_count": len(window),
            "max_pre_cutoff_candles": 4,
            "rejection_reasons": search_rejections,
        }

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

        best_move = 0.0
        if window:
            lows = [c.low for c in window]
            highs = [c.high for c in window]
            best_move = max(0.0, max(highs) - min(lows))

        imp_gap = (
            f"Impulse active ({imp_atr_ratio:.2f} ATR >= {min_impulse_val:.2f} ATR)"
            if impulse
            else (
                "No qualifying chronological impulse: "
                + "; ".join(f"{item['category']}: {item['detail']}" for item in search_rejections[:3])
            )
            if search_rejections
            else (
                f"No qualifying chronological impulse: direction/chronology: "
                f"bounded range spans {best_move:.1f} pts ({best_move / atr:.2f} ATR) "
                "but no directional candidate was found"
                if best_move >= min_impulse_val * atr
                else f"No qualifying chronological impulse: magnitude: need "
                     f"{min_impulse_val * atr - best_move:.1f} pts "
                     f"({max(0.0, min_impulse_val - best_move / atr):.2f} ATR) more directional move"
            )
        )

        condition(
            "chronological_impulse",
            "Chronological impulse",
            bool(impulse),
            f"{imp_src}: {imp_size:.2f} pts ({imp_atr_ratio:.2f} ATR)" if impulse else f"Max swing: {best_move:.1f} pts ({best_move/atr:.2f} ATR)",
            f">= {min_impulse_val:.2f} ATR ({min_impulse_val * atr:.1f} pts)",
            imp_gap,
        )
        summary["impulse"] = {
            "found": bool(impulse),
            "status": "PASS" if impulse else "FAIL",
            "source": imp_src,
            "impulse_source": imp_src,
            "impulse_size": imp_size,
            "atr": atr,
            "impulse_atr_ratio": imp_atr_ratio,
            "required_atr_multiple": min_impulse_val,
            "impulse_start": imp_start,
            "impulse_end": imp_end,
            "height_atr": imp_atr_ratio,
            "impulse_direction": direction.value,
            "impulse_points": imp_size,
            "impulse_atr_multiple": imp_atr_ratio,
            "impulse_crossed_setup_cutoff": impulse_crossed_cutoff,
        }
        summary["impulse_source"] = imp_src
        summary["impulse_size"] = imp_size
        summary["impulse_atr_ratio"] = imp_atr_ratio
        summary["impulse_start"] = imp_start
        summary["impulse_end"] = imp_end
        summary["impulse_direction"] = direction.value
        summary["impulse_points"] = imp_size
        summary["impulse_atr_multiple"] = imp_atr_ratio
        summary["impulse_crossed_setup_cutoff"] = impulse_crossed_cutoff
        summary["impulse_rejection_reason"] = (
            "; ".join(f"{item['category']}: {item['detail']}" for item in search_rejections[:3])
            if not impulse and search_rejections
            else None
        )

        if not impulse:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0

            # These checks use data that is already available before an
            # impulse exists. A missing setup is a dependency state, not a
            # missing Kite-data state.
            continuity_window = window if len(window) >= 2 else bars[-15:]
            intervals = [
                validate_candle_contiguity(a, b, "5m")
                for a, b in zip(continuity_window, continuity_window[1:])
            ]
            if intervals:
                candle_contiguity_pass = all(ok for ok, _ in intervals)
                candle_interval_seconds = intervals[-1][1]
                summary["candle_contiguity_pass"] = candle_contiguity_pass
                summary["candle_interval_seconds"] = candle_interval_seconds
            else:
                candle_contiguity_pass = False
                candle_interval_seconds = None

            st = features.supertrend_direction
            supertrend_val = (st == direction.value) if st in ("BULLISH", "BEARISH") else None
            if futures_fresh and features.futures_vwap > 0 and features.futures_price > 0:
                vwap_val = sign * (features.futures_price - features.futures_vwap) > 0
            else:
                vwap_val = None
            deriv = features.bull_derivatives_score if bullish else features.bear_derivatives_score
            deriv_target = setting("bull_derivatives_score" if bullish else "bear_derivatives_score", 1.0)
            deriv_val = None if deriv is None else deriv >= deriv_target
            buildup = features.futures_buildup if futures_fresh and len(valid_futures) >= 2 else None
            if buildup is None or buildup in ("UNKNOWN", "UNAVAILABLE", "NONE", ""):
                futures_oi_val = None
            elif buildup in ("LONG_BUILDUP", "SHORT_COVERING"):
                futures_oi_val = bullish
            elif buildup in ("SHORT_BUILDUP", "LONG_UNWINDING"):
                futures_oi_val = not bullish
            else:
                futures_oi_val = False
            rvol_val = (
                None if not futures_fresh or features.rvol_5m is None or features.rvol_5m <= 0
                else features.rvol_5m >= setting("rvol_threshold", self.rvol_threshold)
            )
            pre_impulse_confirmations = {
                "supertrend": supertrend_val,
                "vwap": vwap_val,
                "derivatives": deriv_val,
                "futures_oi": futures_oi_val,
                "rvol": rvol_val,
            }
            available_confirmations = [v for v in pre_impulse_confirmations.values() if v is not None]
            passed_confirmations = [v for v in available_confirmations if v is True]
            required_passes = setting("min_confirmation_score", self.min_confirmation_score)
            summary["confirmation"] = {
                "passed_confirmation_count": len(passed_confirmations),
                "available_confirmation_count": len(available_confirmations),
                "required_passes": required_passes,
                "min_available": 2,
                "confirmations": pre_impulse_confirmations,
                "statuses": {key: factor_status(value) for key, value in pre_impulse_confirmations.items()},
            }
            add_confirmation_conditions(pre_impulse_confirmations, deriv, deriv_target)
            summary["derivatives"] = {
                "bull_score": features.bull_derivatives_score,
                "bear_score": features.bear_derivatives_score,
                "active_score": deriv,
                "required_threshold": deriv_target,
                "status": factor_status(deriv_val),
                "components": features.derivatives_score_components,
            }
            summary["futures"]["factor_statuses"] = {
                "futures_vwap_confirmation": factor_status(vwap_val),
                "futures_oi": factor_status(futures_oi_val),
                "futures_rvol": factor_status(rvol_val),
                "pullback_volume_dryup": "UNAVAILABLE",
                "pullback_futures_vwap_retest": "UNAVAILABLE",
            }
            summary["futures"]["confirmation_factors_unavailable"] = [
                key for key, value in summary["futures"]["factor_statuses"].items()
                if value == "UNAVAILABLE"
            ]
            condition(
                "pullback_depth_retest",
                "Pullback duration, depth and retest",
                False,
                "No impulse",
                "1-9 bars; 30%-80%; retest within 0.35 ATR",
                "Waiting for valid impulse to measure pullback",
            )
            condition(
                "candle_contiguity",
                "Candle contiguity",
                candle_contiguity_pass,
                f"{candle_interval_seconds:.1f}s" if candle_interval_seconds is not None else "N/A",
                "300s ± 5s",
                "All available completed candles are contiguous"
                if candle_contiguity_pass
                else "One or more available candle intervals are outside 300s ± 5s",
            )
            condition(
                "breakout_trigger",
                "Live price breakout confirmation",
                False,
                f"Live ₹{live_price:.2f}",
                "Awaiting impulse breakout level",
                "Waiting for valid impulse",
            )
            condition(
                "confirmation_score",
                "Entry confirmations",
                False,
                f"{len(passed_confirmations)} passed / {len(available_confirmations)} available",
                f">={setting('min_confirmation_score', self.min_confirmation_score)} passed",
                "Waiting for valid impulse",
            )
            condition(
                "risk_r_band",
                "Structural initial R",
                False,
                "Awaiting impulse/pullback structure",
                "<= 1.60 ATR",
                "Waiting for valid impulse",
            )
            blocker = first_failure[1] if first_failure else "Waiting for a chronological impulse"
            phase = first_failure[0] if first_failure else "WAIT_FOR_IMPULSE"
            return finish(phase, blocker)

        start, end = datetime.fromisoformat(impulse["start"]), datetime.fromisoformat(impulse["end"])
        pb = [c for c in bars if c.end_time > end]
        age = len(pb)
        duration_status = "WAITING" if age == 0 else ("PASS" if age <= 9 else "INVALID")
        if bullish:
            configured_min_depth = setting("min_pullback_depth", self.call_pullback_min_depth)
            configured_max_depth = setting("max_pullback_depth", self.call_pullback_max_depth)
            configured_range = f"{configured_min_depth:.0%}\u2013{configured_max_depth:.0%}"
        else:
            configured_min_depth = setting("min_pullback_depth", self.put_pullback_min_depth)
            configured_max_depth = setting("max_pullback_depth", self.put_pullback_max_depth)
            configured_range = f"{configured_min_depth:.0%}\u2013<{configured_max_depth:.0%}"
        summary["pullback"] = {
            "state": "ACTIVE",
            "bars": age,
            "required_bars": "1-9",
            "duration_status": duration_status,
            "depth_pct": 0,
            "depth_status": "WAITING",
            "depth_range": configured_range,
            "max_depth_inclusive": bullish,
            "retest": "None",
            "retest_status": "WAITING",
            "structure_preservation_status": "WAITING",
            "retest_distance_atr": {},
        }
        if not pb:
            if is_diagnose and first_failure:
                return finish(first_failure[0], first_failure[1])
            return finish("PULLBACK_ACTIVE", "Waiting for at least one pullback bar")

        extreme = min(c.low for c in pb) if bullish else max(c.high for c in pb)
        depth = (impulse["high"] - extreme if bullish else extreme - impulse["low"]) / impulse["height"]
        summary["pullback"]["depth_pct"] = round(depth * 100, 1)

        if bullish:
            min_depth = setting("min_pullback_depth", self.call_pullback_min_depth)
            max_depth = setting("max_pullback_depth", self.call_pullback_max_depth)
            max_depth_exclusive = False
        else:
            min_depth = setting("min_pullback_depth", self.put_pullback_min_depth)
            max_depth = setting("max_pullback_depth", self.put_pullback_max_depth)
            max_depth_exclusive = True
        # Preserve the legacy CALL tolerance. The frozen PUT candidate uses
        # exact boundary semantics: minimum inclusive, maximum exclusive.
        depth_below_min = depth < min_depth if max_depth_exclusive else depth < (min_depth - 1e-6)
        depth_above_max = depth >= max_depth if max_depth_exclusive else depth > (max_depth + 1e-6)
        depth_in_range = not depth_below_min and not depth_above_max
        structure_preserved = not (
            trigger_bar.low < impulse["low"] if bullish else trigger_bar.high > impulse["high"]
        )
        summary["pullback"].update(
            depth_status=("INVALID" if depth_above_max else
                          "PASS" if depth_in_range else "WAITING"),
            depth_range=(f"{min_depth:.0%}\u2013<{max_depth:.0%}" if max_depth_exclusive else f"{min_depth:.0%}\u2013{max_depth:.0%}"),
            max_depth_inclusive=not max_depth_exclusive,
            structure_preservation_status="PASS" if structure_preserved else "INVALID",
        )

        invalid = (
            age > 9
            or depth_above_max
            or not structure_preserved
        )
        if invalid:
            state.pop("impulse", None)
            state.pop("active_setup_key", None)
            state["confirm_count"] = 0
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

        aligned = {c.end_time: c for c in valid_futures}
        leg = [c for c in bars if start <= c.end_time <= end]
        required_underlying = leg + pb
        aligned_required = [c for c in required_underlying if c.end_time in aligned]
        summary["futures"].update(
            {
                "candles_requested": len(required_underlying),
                "candles_aligned": len(aligned_required),
                "alignment_coverage": round(
                    len(aligned_required) / len(required_underlying), 3
                ) if required_underlying else 0.0,
            }
        )

        for c in pb:
            f = aligned.get(c.end_time)
            if f is None:
                continue
            history = [x for x in valid_futures if x.end_time <= f.end_time]
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

        futures_vwap_retest = any(name == "Futures VWAP" for name in retests)
        futures_vwap_retest_available = any(c.end_time in aligned for c in pb)
        pb_ok = 1 <= age <= 9 and depth_in_range and bool(retests)
        pb_gap = (
            f"Pullback qualified ({age} bars, {depth:.1%}, retest: {', '.join(retests)})"
            if pb_ok
            else f"Waiting for controlled pullback ({age} bars, depth {depth:.1%}, retests: {', '.join(retests) or 'None'})"
        )
        condition(
            "pullback_depth_retest",
            "Pullback duration, depth and retest",
            pb_ok,
            f"{age} bars; {depth:.1%}; {retests}",
            (f"1-9 bars; {min_depth:.0%}\u2013<{max_depth:.0%}; retest within {retest_tol:.2f} ATR"
             if max_depth_exclusive else
             f"1-9 bars; {min_depth:.0%}\u2013{max_depth:.0%}; retest within {retest_tol:.2f} ATR"),
            pb_gap,
        )
        summary["pullback"].update(
            state="QUALIFIED" if pb_ok else "ACTIVE",
            duration_status="PASS" if 1 <= age <= 9 else "INVALID",
            depth_status=("INVALID" if depth_above_max else
                          "PASS" if depth_in_range else "WAITING"),
            depth_range=(f"{min_depth:.0%}\u2013<{max_depth:.0%}" if max_depth_exclusive else f"{min_depth:.0%}\u2013{max_depth:.0%}"),
            max_depth_inclusive=not max_depth_exclusive,
            retest=", ".join(retests) or "None",
            retest_status="PASS" if retests else "WAITING",
            structure_preservation_status="PASS" if structure_preserved else "INVALID",
            retest_distance_atr=retest_distances,
        )
        summary["futures"]["factor_statuses"]["pullback_futures_vwap_retest"] = factor_status(
            futures_vwap_retest if futures_vwap_retest_available else None
        )
        if not pb_ok:
            state["confirm_count"] = 0
            if not is_diagnose:
                return finish("PULLBACK_ACTIVE", "Waiting for controlled pullback and reference retest")
            first_failure = first_failure or ("PULLBACK_ACTIVE", "Waiting for controlled pullback and reference retest", None)

        # Iteration 1 — Live Price Breakout Trigger with Buffer & Polling Confirmation
        previous_completed_bar = bars[-1]
        buffer_atr = setting("breakout_buffer_atr", self.breakout_buffer_atr)
        required_polls = setting("breakout_confirm_polls", self.breakout_confirm_polls)

        replay_triggered = bool(replay_context.get("triggered"))
        replay_trigger_price = replay_context.get("trigger_price")
        if replay_triggered and replay_trigger_price is not None:
            trigger_price = float(replay_trigger_price)
            breakout_condition = True
        elif bullish:
            trigger_price = round(previous_completed_bar.high + (buffer_atr * atr), 2)
            breakout_condition = (live_price > trigger_price)
        else:
            trigger_price = round(previous_completed_bar.low - (buffer_atr * atr), 2)
            breakout_condition = (live_price < trigger_price)

        confirm_count = state.get("confirm_count", 0)
        if replay_triggered:
            confirm_count = required_polls
        elif breakout_condition:
            confirm_count += 1
        else:
            confirm_count = 0
        state["confirm_count"] = confirm_count

        breakout_confirmed = (confirm_count >= required_polls)

        trig_gap = (
            f"Breakout confirmed (+{abs(live_price - trigger_price):.1f} pts beyond trigger)"
            if breakout_confirmed
            else f"Live spot is {abs(live_price - trigger_price):.1f} pts away from trigger level ₹{trigger_price:.2f}"
        )
        condition(
            "breakout_trigger",
            "Live price breakout confirmation",
            breakout_confirmed,
            f"Live ₹{live_price:.2f} vs Trigger ₹{trigger_price:.2f} (polls {confirm_count}/{required_polls})",
            f"{'>' if bullish else '<'} ₹{trigger_price:.2f} for {required_polls} polls",
            trig_gap,
        )
        summary["trigger"] = {
            "direction": "CALL" if bullish else "PUT",
            "breakout_reference_level": previous_completed_bar.high if bullish else previous_completed_bar.low,
            "atr_buffer_multiple": buffer_atr,
            "atr_buffer_points": round(buffer_atr * atr, 2),
            "breakout_trigger_price": trigger_price,
            "live_price": live_price,
            "breakout_condition": breakout_condition,
            "breakout_confirm_count": confirm_count,
            "current_polls_satisfied": confirm_count,
            "required_polls": required_polls,
            "confirmed": breakout_confirmed,
            "status": "PASS" if breakout_confirmed else "WAITING",
            "trigger_timestamp": (
                replay_context.get("trigger_timestamp")
                if replay_triggered and breakout_confirmed
                else features.timestamp.isoformat() if breakout_confirmed else None
            ),
            "previous_completed_bar_end": previous_completed_bar.end_time.isoformat(),
        }
        if replay_triggered:
            summary["trigger"].update(
                {
                    "replay_trigger": True,
                    "trigger_source_candle_timestamp": replay_context.get("source_candle_timestamp"),
                    "historical_candle_timestamp": replay_context.get("historical_candle_timestamp"),
                    "simulated_trigger_price": trigger_price,
                }
            )

        if not breakout_confirmed:
            reason = (
                f"Waiting for live price breakout ({live_price:.2f} vs {trigger_price:.2f})"
                if not breakout_condition
                else f"Breakout pending confirmation (poll {confirm_count}/{required_polls})"
            )
            if not is_diagnose:
                return finish("WAIT_FOR_TRIGGER", reason, trigger_level=trigger_price)
            first_failure = first_failure or ("WAIT_FOR_TRIGGER", reason, trigger_price)

        # Iteration 3 Part A — Ternary Confirmation Handling (True / False / None)
        leg_futures = [aligned[c.end_time] for c in leg if c.end_time in aligned]
        pb_futures = [aligned[c.end_time] for c in pb if c.end_time in aligned]
        futures_volume_complete = len(leg_futures) == len(leg) and len(pb_futures) == len(pb)
        leg_volume = sum(c.volume for c in leg_futures) / len(leg_futures) if futures_volume_complete and leg_futures else 0
        pb_volume = sum(c.volume for c in pb_futures) / len(pb_futures) if futures_volume_complete and pb_futures else 0
        ratio = pb_volume / leg_volume if leg_volume > 0 else None

        # Supertrend
        st = features.supertrend_direction
        if st and st in ("BULLISH", "BEARISH"):
            supertrend_val = (st == direction.value)
        else:
            supertrend_val = None

        # Futures VWAP
        if futures_fresh and features.futures_vwap > 0 and features.futures_price > 0:
            vwap_val = (sign * (features.futures_price - features.futures_vwap) > 0)
        else:
            vwap_val = None

        # Derivatives score
        deriv = features.bull_derivatives_score if bullish else features.bear_derivatives_score
        deriv_target = setting("bull_derivatives_score" if bullish else "bear_derivatives_score", 1.0)
        if deriv is None:
            deriv_val = None
        else:
            deriv_val = (deriv >= deriv_target)

        # Futures OI buildup
        buildup = features.futures_buildup if futures_fresh and len(valid_futures) >= 2 else None
        if buildup is None or buildup in ("UNKNOWN", "UNAVAILABLE", "NONE", ""):
            futures_oi_val = None
        elif buildup in ("LONG_BUILDUP", "SHORT_COVERING"):
            futures_oi_val = True if bullish else False
        elif buildup in ("SHORT_BUILDUP", "LONG_UNWINDING"):
            futures_oi_val = False if bullish else True
        else:
            futures_oi_val = False

        # RVOL
        if not futures_fresh or features.rvol_5m is None or features.rvol_5m <= 0:
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

        summary["futures"]["factor_statuses"].update(
            {
                "futures_vwap_confirmation": factor_status(vwap_val),
                "futures_oi": factor_status(futures_oi_val),
                "futures_rvol": factor_status(rvol_val),
                "pullback_volume_dryup": factor_status(volume_dryup_val),
            }
        )
        summary["futures"]["confirmation_factors_unavailable"] = [
            key for key, value in summary["futures"]["factor_statuses"].items()
            if value == "UNAVAILABLE"
        ]
        add_confirmation_conditions(confirmations, deriv, deriv_target)

        available_confirmations = [v for v in confirmations.values() if v is not None]
        passed_confirmations = [v for v in available_confirmations if v is True]

        required_passes = setting("min_confirmation_score", self.min_confirmation_score)
        min_available = setting("min_available_confirmations", self.min_available_confirmations)

        conf_ok = (len(available_confirmations) >= min_available and len(passed_confirmations) >= required_passes)

        conf_gap = (
            f"Confirmation PASS: {len(passed_confirmations)} of {len(available_confirmations)} available confirmations passed."
            if conf_ok
            else (
                f"Confirmation FAIL: {len(available_confirmations)} available confirmations; "
                f"{min_available} required and {required_passes} passed required."
                if len(available_confirmations) < min_available
                else f"Confirmation FAIL: {len(passed_confirmations)} of {len(available_confirmations)} available confirmations passed; {required_passes} required."
            )
        )
        condition(
            "confirmation_score",
            "Entry confirmations",
            conf_ok,
            f"{len(passed_confirmations)} passed / {len(available_confirmations)} available",
            f">={required_passes} passed and >={min_available} available",
            conf_gap,
        )
        summary["confirmation"] = {
            "passed_confirmation_count": len(passed_confirmations),
            "available_confirmation_count": len(available_confirmations),
            "required_passes": required_passes,
            "min_available": min_available,
            "required_available": min_available,
            "required_passed": required_passes,
            "confirmations": confirmations,
            "statuses": {key: factor_status(value) for key, value in confirmations.items()},
            "reason": conf_gap,
        }
        summary["derivatives"] = {
            "bull_score": features.bull_derivatives_score,
            "bear_score": features.bear_derivatives_score,
            "active_score": deriv,
            "required_threshold": deriv_target,
            "status": factor_status(deriv_val),
            "components": features.derivatives_score_components,
        }

        # Iteration 3 Part B — Remove Minimum 0.45 ATR Risk Rejection Floor
        raw_stop = extreme - sign * 0.15 * atr
        risk = sign * (live_price - raw_stop)
        stop = raw_stop
        max_r = setting("max_initial_r_atr", 1.60) * atr

        risk_ok = (0 < risk <= max_r + 1e-9)

        risk_gap = (
            f"Risk {risk/atr:.2f} ATR within allowed (0, {max_r/atr:.2f}] ATR band ({risk:.1f} pts)"
            if risk_ok
            else f"Risk {risk/atr:.2f} ATR ({risk:.1f} pts) outside allowed (0, {max_r/atr:.2f}] ATR"
        )
        condition(
            "risk_r_band",
            "Structural initial R",
            risk_ok,
            f"{round(risk / atr, 2) if atr > 0 else 0:.2f} ATR ({risk:.1f} pts)",
            f"0 < R <= {max_r / atr:.2f} ATR ({max_r:.1f} pts)",
            risk_gap,
        )
        summary["risk"] = {
            "initial_risk_atr": round(risk / atr, 2) if atr > 0 else 0,
            "initial_r_points": risk,
            "stop": stop,
            "max_r": max_r,
        }

        if not conf_ok:
            reason = conf_gap
            if not is_diagnose:
                return finish("TRIGGERED", reason, trigger_level=trigger_price)
            first_failure = first_failure or ("TRIGGERED", reason, trigger_price)

        if not risk_ok:
            reason = f"Structural risk {risk/atr:.2f} ATR outside allowed band (0 < R <= {max_r/atr:.2f} ATR)"
            if not is_diagnose:
                return finish("RISK_AND_CONTRACT_CHECK", reason, trigger_level=trigger_price)
            first_failure = first_failure or ("RISK_AND_CONTRACT_CHECK", reason, trigger_price)

        if first_failure:
            return None, finish(first_failure[0], first_failure[1], trigger_level=first_failure[2])[1]

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
                self.state[direction.value].pop("impulse", None)
                self.state[direction.value].pop("active_setup_key", None)
                self.state[direction.value]["confirm_count"] = 0
                return signal
        return None

    def evaluate_replay_trigger(
        self,
        direction,
        features,
        candles_5m,
        candles_15m,
        futures_candles=None,
        overrides=None,
        *,
        trigger_price: float,
        trigger_timestamp: str,
        source_candle_timestamp: str,
        historical_candle_timestamp: str,
    ):
        """Evaluate a historical intrabar trigger without changing live evaluation."""
        replay_features = features.model_copy(update={"spot_price": trigger_price})
        signal, diagnostic = self._decision(
            direction,
            replay_features,
            candles_5m,
            candles_15m,
            futures_candles or [],
            overrides,
            replay_context={
                "triggered": True,
                "trigger_price": trigger_price,
                "live_price": trigger_price,
                "trigger_timestamp": trigger_timestamp,
                "source_candle_timestamp": source_candle_timestamp,
                "historical_candle_timestamp": historical_candle_timestamp,
            },
        )
        if signal:
            self.state[direction.value]["consumed"] = signal.timestamp.isoformat()
            self.state[direction.value].pop("impulse", None)
            self.state[direction.value].pop("active_setup_key", None)
            self.state[direction.value]["confirm_count"] = 0
        return signal, diagnostic

    def diagnose(self, features, candles_5m, candles_15m, overrides=None, futures_candles=None):
        preview = deepcopy(self)
        return [
            preview._decision(d, features, candles_5m, candles_15m, futures_candles or [], overrides, is_diagnose=True)[1]
            for d in TradeDirection
        ]
