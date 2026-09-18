"""Completed-bar Trend Pullback Continuation, specification revision 2."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from services.strategy.features import FeatureEngine
from services.strategy.models import (
    OptionType, StrategyName, StrategySignal, StrategyTriggerDiagnostics,
    TradeDirection, TriggerCondition,
)

IST = timezone(timedelta(hours=5, minutes=30))


class TrendPullbackStrategy:
    """Shared execution/diagnostic decision path with serializable setup state."""

    def __init__(self, adx_threshold=20.0, rvol_threshold=1.20,
                 min_confirmation_score=2, ema_slope_threshold=0.10):
        self.adx_threshold = adx_threshold
        self.rvol_threshold = rvol_threshold
        self.min_confirmation_score = min_confirmation_score
        self.ema_slope_threshold = ema_slope_threshold
        self.state = {d.value: {} for d in TradeDirection}

    def export_state(self):
        return deepcopy(self.state)

    def restore_state(self, state):
        self.state = {d.value: dict(state.get(d.value, {})) for d in TradeDirection}

    def reset(self, at=None):
        for direction in TradeDirection:
            old = self.state[direction.value]
            self.state[direction.value] = {
                "session": at.astimezone(IST).date().isoformat() if at else old.get("session"),
                "after": at.isoformat() if at else old.get("after"),
                "consumed": old.get("consumed"), "cooldown": old.get("cooldown"),
            }

    def on_exit(self, direction, at):
        self.state[direction.value] = {
            "session": at.astimezone(IST).date().isoformat(),
            "after": at.isoformat(), "cooldown": at.isoformat()}

    @staticmethod
    def _impulse(bars, atr, bullish, after=None):
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
                if high-low >= atr:
                    leg = bars[start:end+1]
                    if min(c.low for c in leg) < low or max(c.high for c in leg) > high:
                        continue
                    return {"start": bars[start].end_time.isoformat(),
                            "end": bars[end].end_time.isoformat(),
                            "low": low, "high": high, "height": high-low}
        return None

    def _decision(self, direction, features, bars, macro, futures, overrides):
        bullish = direction == TradeDirection.BULLISH
        sign = 1 if bullish else -1
        opt = OptionType.CALL if bullish else OptionType.PUT
        state = self.state[direction.value]
        summary, conditions = {}, []

        def condition(id, name, passed, current, target):
            conditions.append(TriggerCondition(id=id, name=name, status="PASSED" if passed else "PENDING",
                              current_value=str(current), target_threshold=str(target), gap_description=name))

        def finish(phase, reason, signal=None):
            passed = sum(c.status == "PASSED" for c in conditions)
            state["phase"] = phase
            diagnostic = StrategyTriggerDiagnostics(
                strategy=StrategyName.TREND_PULLBACK, strategy_label=f"Trend Pullback ({opt.value})",
                direction=direction, option_type=opt, phase_state=phase,
                overall_status="READY_TO_TRIGGER" if signal else "WAITING",
                passed_count=passed, total_count=len(conditions),
                ready_pct=round(100*passed/len(conditions), 1) if conditions else 0,
                key_blocker=reason, current_spot=features.spot_price,
                phase_summary=summary, conditions=conditions,
                target_entry_level=(bars[-2].high if bullish else bars[-2].low) if len(bars) > 1 else None)
            return signal, diagnostic

        def setting(name, default):
            value = getattr(overrides, name, None) if overrides else None
            return default if value is None else value

        now = features.timestamp
        valid = (features.data_ready and len(bars) >= 6 and bool(macro) and bool(futures)
                 and features.atr_5m > 0 and features.futures_atr_5m > 0)
        if valid:
            valid = (all(c.source in ("BREEZE", "LIVE") and c.end_time <= now for c in bars + macro + futures)
                     and 0 <= (now-bars[-1].end_time).total_seconds() < 300
                     and 0 <= (now-macro[-1].end_time).total_seconds() < 900
                     and futures[-1].end_time == bars[-1].end_time)
        if not valid:
            state.pop("impulse", None)
            if bars:
                state["after"] = bars[-1].end_time.isoformat()
            return finish("SEARCH_REGIME", features.data_reason or "Awaiting complete real spot/futures data")
        trigger, previous = bars[-1], bars[-2]
        session = trigger.start_time.astimezone(IST).date().isoformat()
        if state.get("session") != session:
            state.clear()
            state["session"] = session
        if state.get("cooldown") and trigger.end_time <= datetime.fromisoformat(state["cooldown"]):
            return finish("WAIT_FOR_IMPULSE", "Awaiting a completed candle after exit")
        if state.get("consumed") == trigger.end_time.isoformat():
            return finish("TRIGGERED", "Trigger candle already consumed")

        atr = features.atr_5m
        slope = setting("ema_slope_threshold", self.ema_slope_threshold)
        score = sum((sign*(features.ema20_15m-features.ema50_15m) > 0,
                     sign*features.ema20_slope_norm_15m >= slope,
                     sign*(macro[-1].close-features.ema20_15m) > 0,
                     sign*(features.plus_di_15m-features.minus_di_15m) > 0))
        regime = features.adx_15m >= setting("adx_threshold", self.adx_threshold) and score >= 3
        condition("macro_regime", "15m macro regime", regime, f"{score}/4; ADX {features.adx_15m}", "3/4 plus ADX threshold")
        summary["regime"] = {"status": "QUALIFIED" if regime else "WAITING", "direction_score": f"{score}/4", "adx": features.adx_15m}
        if not regime:
            state.pop("impulse", None)
            state["after"] = trigger.end_time.isoformat()
            return finish("SEARCH_REGIME", "Macro regime is not qualified")

        window = [c for c in bars[-15:] if c.start_time.astimezone(IST).date().isoformat() == session]
        impulse = state.get("impulse")
        if impulse is None:
            impulse = self._impulse(window, atr, bullish, state.get("after"))
            if impulse:
                state["impulse"] = impulse
        condition("chronological_impulse", "Chronological impulse", bool(impulse), impulse or "None", ">= 1 ATR")
        summary["impulse"] = {"found": bool(impulse), "height_atr": impulse["height"]/atr if impulse else 0}
        if not impulse:
            return finish("WAIT_FOR_IMPULSE", "Waiting for a chronological impulse")
        start, end = datetime.fromisoformat(impulse["start"]), datetime.fromisoformat(impulse["end"])
        pb = [c for c in bars[:-1] if c.end_time > end]
        age = len(pb)
        summary["pullback"] = {"state": "ACTIVE", "bars": age, "depth_pct": 0, "retest": "None"}
        if not pb:
            return finish("PULLBACK_ACTIVE", "Waiting for at least two pullback bars")
        extreme = min(c.low for c in pb) if bullish else max(c.high for c in pb)
        depth = (impulse["high"]-extreme if bullish else extreme-impulse["low"])/impulse["height"]
        summary["pullback"]["depth_pct"] = round(depth*100, 1)
        invalid = (age > 9 or depth > .65 or
                   (trigger.low < impulse["low"] if bullish else trigger.high > impulse["high"]))
        if invalid:
            state.pop("impulse", None)
            state["after"] = trigger.end_time.isoformat()
            return finish("WAIT_FOR_IMPULSE", "Setup reset: duration, depth or impulse structure invalid")
        sequence = [c for c in bars if c.end_time >= end]
        if any(b.start_time != a.end_time for a, b in zip(sequence, sequence[1:])):
            state.pop("impulse", None)
            state["after"] = trigger.end_time.isoformat()
            return finish("WAIT_FOR_IMPULSE", "Setup reset: incomplete candle sequence")
        retests = []
        if abs(extreme-features.ema9_5m) <= .35*atr:
            retests.append("EMA9")
        if abs(extreme-features.ema20_5m) <= .35*atr:
            retests.append("EMA20")
        aligned = {c.end_time: c for c in futures}
        leg = [c for c in bars if start <= c.end_time <= end]
        if not leg or not all(c.end_time in aligned for c in leg + pb + [trigger]):
            state.pop("impulse", None)
            state["after"] = trigger.end_time.isoformat()
            return finish("WAIT_FOR_IMPULSE", "Missing aligned futures bars for this setup")
        for c in pb:
            f = aligned[c.end_time]
            history = [x for x in futures if x.end_time <= f.end_time]
            vwap = FeatureEngine.calculate_futures_vwap(history)
            fatr = FeatureEngine.calculate_atr(history)
            distance = max(f.low-vwap, vwap-f.high, 0)
            if vwap > 0 and fatr > 0 and distance <= .35*fatr:
                retests.append("Futures VWAP")
                break
        prior = [c for c in bars if c.end_time <= start]
        levels = [prior[i].high if bullish else prior[i].low for i in range(1, len(prior)-1)
                  if (prior[i].high > prior[i-1].high and prior[i].high >= prior[i+1].high if bullish
                      else prior[i].low < prior[i-1].low and prior[i].low <= prior[i+1].low)]
        if any(impulse["low"] < level < impulse["high"] and abs(extreme-level) <= .35*atr for level in levels):
            retests.append("Prior breakout level")
        pb_ok = 2 <= age <= 9 and .10 <= depth <= .65 and bool(retests)
        condition("pullback_depth_retest", "Pullback duration, depth and retest", pb_ok, f"{age} bars; {depth:.1%}; {retests}", "2-9 bars; 10-65%; retest")
        summary["pullback"].update(state="QUALIFIED" if pb_ok else "ACTIVE", retest=", ".join(retests) or "None")
        if not pb_ok:
            return finish("PULLBACK_ACTIVE", "Waiting for controlled pullback and reference retest")
        leg_volume = sum(aligned[c.end_time].volume for c in leg)/len(leg)
        pb_volume = sum(aligned[c.end_time].volume for c in pb)/len(pb)
        ratio = pb_volume/leg_volume if leg_volume > 0 else None
        candle_range = trigger.high-trigger.low
        price_trigger = trigger.close > previous.high if bullish else trigger.close < previous.low
        momentum = (sign*(trigger.close-features.ema9_5m) > 0 or sign*(features.rsi_5m-50) > 0 or
                    (candle_range > 0 and sign*(trigger.close-trigger.open) > 0 and
                     abs(trigger.close-trigger.open)/candle_range >= .40 and
                     (trigger.close-trigger.low if bullish else trigger.high-trigger.close)/candle_range >= .65))
        exhaustion = 0 < candle_range <= 1.85*atr
        condition("trigger_candle_breakout", "Completed close breaks previous high/low", price_trigger, trigger.close, previous.high if bullish else previous.low)
        condition("momentum_confirmation", "Momentum and exhaustion safety", momentum and exhaustion, candle_range/atr, "1 of 3 momentum; range <=1.85 ATR")
        summary["trigger"] = {"waiting_for": "Previous high" if bullish else "Previous low", "gap_pts": max(0, sign*((previous.high if bullish else previous.low)-trigger.close))}
        if not (price_trigger and momentum and exhaustion):
            return finish("WAIT_FOR_TRIGGER", "Waiting for resumption close" if exhaustion else "Trigger range exceeds 1.85 ATR")
        deriv = features.bull_derivatives_score if bullish else features.bear_derivatives_score
        deriv_target = setting("bull_derivatives_score" if bullish else "bear_derivatives_score", 2)
        points = sum((features.supertrend_direction == direction.value,
                      sign*(features.futures_price-features.futures_vwap) > 0,
                      deriv >= deriv_target,
                      features.futures_buildup in (("LONG_BUILDUP", "SHORT_COVERING") if bullish else ("SHORT_BUILDUP", "LONG_UNWINDING")),
                      features.rvol_5m >= setting("rvol_threshold", self.rvol_threshold),
                      ratio is not None and ratio < .80))
        required = setting("min_confirmation_score", self.min_confirmation_score)
        condition("confirmation_score", "Entry confirmations", points >= required, points, required)
        summary["confirmation"] = {"score": points, "required": required}
        raw_stop = extreme-sign*.15*atr
        risk = max(sign*(trigger.close-raw_stop), .45*atr)
        stop = trigger.close-sign*risk
        risk_ok = risk <= 1.60*atr
        condition("risk_r_band", "Structural initial R", risk_ok, risk/atr, ".45-1.60 ATR")
        summary["risk"] = {"initial_r_atr": risk/atr, "stop": stop}
        if points < required:
            return finish("TRIGGERED", f"Confirmation score {points}/6 below {required}")
        if not risk_ok:
            return finish("RISK_AND_CONTRACT_CHECK", "Structural risk exceeds 1.60 ATR")
        signal = StrategySignal(
            signal_id=f"SIG-A-{direction.value}-{int(trigger.end_time.timestamp())}",
            strategy=StrategyName.TREND_PULLBACK, direction=direction, option_type=opt,
            timestamp=trigger.end_time, spot_reference_price=trigger.close,
            structural_stop=stop, r_points=risk, derivatives_score=deriv,
            features_snapshot={"entry_reference_spot": trigger.close,
                               "pullback_low": extreme if bullish else None,
                               "pullback_high": extreme if not bullish else None,
                               "impulse_low": impulse["low"], "impulse_high": impulse["high"],
                               "pullback_bars": age, "pullback_depth": depth,
                               "pullback_vol_ratio": ratio, "confirmation_score": f"{points}/6",
                               "direction_score": f"{score}/4", "atr": atr, "r_initial": risk})
        return finish("READY_TO_TRIGGER", "Setup qualified; checking contract and risk limits", signal)

    def evaluate(self, features, candles_5m, candles_15m, futures_candles=None, overrides=None):
        for direction in TradeDirection:
            signal, _ = self._decision(direction, features, candles_5m, candles_15m, futures_candles or [], overrides)
            if signal:
                self.state[direction.value]["consumed"] = signal.timestamp.isoformat()
                self.state[direction.value]["after"] = signal.timestamp.isoformat()
                self.state[direction.value].pop("impulse", None)
                return signal
        return None

    def diagnose(self, features, candles_5m, candles_15m, overrides=None, futures_candles=None):
        preview = deepcopy(self)
        return [preview._decision(d, features, candles_5m, candles_15m, futures_candles or [], overrides)[1]
                for d in TradeDirection]
