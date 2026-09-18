"""Strategy B: immutable compression box and completed-bar breakout decisions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from services.strategy.models import (
    CompressionBox, OptionType, StrategyName, StrategySignal,
    StrategyTriggerDiagnostics, TradeDirection, TriggerCondition,
)
IST = timezone(timedelta(hours=5, minutes=30))


class VolatilityBreakoutStrategy:
    def __init__(self, rvol_threshold=1.20, adx_threshold=20.0,
                 min_confirmation_score=3, bb_width_percentile_threshold=25.0,
                 box_max_height_atr=1.30, lookback_bars=8, max_age_bars=8,
                 breakout_buffer_atr=0.05, max_extension_atr=0.75,
                 entry_start="09:25", entry_end="14:45"):
        self.rvol_threshold = rvol_threshold
        self.adx_threshold = adx_threshold  # compatibility; not a B gate
        self.min_confirmation_score = min_confirmation_score
        self.bb_width_threshold = bb_width_percentile_threshold
        self.box_max_height_atr = box_max_height_atr
        self.lookback_bars = lookback_bars
        self.max_age_bars = max_age_bars
        self.breakout_buffer_atr = breakout_buffer_atr
        self.max_extension_atr = max_extension_atr
        self.entry_start, self.entry_end = entry_start, entry_end
        self.locked_box = None
        self.last_bar = self.after = self.session = self.fingerprint = self.consumed = None

    def export_state(self):
        return {"box": self.locked_box.model_dump(mode="json") if self.locked_box else None,
                **{k:getattr(self,k) for k in ("last_bar","after","session","fingerprint","consumed")}}

    def restore_state(self, state):
        self.locked_box = CompressionBox.model_validate(state["box"]) if state.get("box") else None
        for name in ("last_bar","after","session","fingerprint","consumed"):
            setattr(self, name, state.get(name))

    def reset(self, at=None):
        self.locked_box = None
        if at is not None:
            self.after = at.isoformat()

    def _decision(self, features, bars, overrides=None):
        def setting(key, default):
            value = getattr(overrides, key, None)
            return default if value is None else value
        bb_target = setting("bb_width_percentile", self.bb_width_threshold)
        height_target = setting("box_max_height_atr", self.box_max_height_atr)
        required = setting("strat_b_min_confirmation", self.min_confirmation_score)
        rvol_target = setting("rvol_threshold", self.rvol_threshold)
        conditions, summary = [], {}

        def condition(key, name, passed, current, target):
            conditions.append(TriggerCondition(id=key,name=name,status="PASSED" if passed else "PENDING",
                current_value=str(current),target_threshold=str(target),gap_description=""))

        def finish(phase, reason, signal=None, direction=None):
            if self.locked_box and "box" not in summary:
                box = self.locked_box
                summary["box"] = {"state":"LOCKED","high":box.box_high,"low":box.box_low,
                                  "height":box.box_height,"bars":box.bars_active,"max_bars":box.max_bars}
                summary["compression"] = {"status":"PASSED","bb_width":box.bb_width_at_lock,
                                          "height_atr":box.box_height/box.atr_at_lock}
            diags = []
            for d in TradeDirection:
                visible = conditions if direction in (None,d) else []
                passed = sum(c.status == "PASSED" for c in visible)
                ready = signal is not None and signal.direction == d
                diags.append(StrategyTriggerDiagnostics(
                    strategy=StrategyName.VOLATILITY_BREAKOUT,
                    strategy_label=f"Volatility Breakout ({'CALL' if d == TradeDirection.BULLISH else 'PUT'})",
                    direction=d,option_type=OptionType.CALL if d == TradeDirection.BULLISH else OptionType.PUT,
                    overall_status="READY_TO_TRIGGER" if ready else "WAITING",
                    phase_state=phase if direction in (None,d) else "WAITING_FOR_BREAKOUT",
                    key_blocker=reason if direction in (None,d) else "Waiting for breakout in this direction",
                    current_spot=features.spot_price,passed_count=passed,total_count=len(visible),
                    ready_pct=100*passed/len(visible) if visible else 0,
                    conditions=deepcopy(visible),phase_summary=deepcopy(summary)))
            return signal, diags

        now = features.timestamp
        if (not features.breakout_data_ready or not bars or features.atr_5m <= 0
                or any(c.source not in ("BREEZE","LIVE") or c.interval != "5m"
                       or c.end_time > now or c.end_time-c.start_time != timedelta(minutes=5)
                       or not 0 < c.low <= min(c.open,c.close) <= max(c.open,c.close) <= c.high for c in bars)
                or any(b.start_time <= a.start_time for a,b in zip(bars,bars[1:]))
                or not 0 <= (now-bars[-1].end_time).total_seconds() < 300):
            self.reset(now)
            return finish("RESET","Missing, stale or invalid real completed spot/futures data")
        trigger = bars[-1]
        clock = trigger.end_time.astimezone(IST)
        session = clock.date().isoformat()
        if self.session != session:
            self.locked_box = None
            self.last_bar = None
            self.session = session
        fingerprint = repr((bb_target,height_target,required,rvol_target,self.lookback_bars,
            self.max_age_bars,self.breakout_buffer_atr,self.max_extension_atr,self.entry_start,self.entry_end,
            setting("bull_derivatives_score",2),setting("bear_derivatives_score",2)))
        if self.fingerprint is not None and self.fingerprint != fingerprint:
            self.reset(trigger.end_time)
        self.fingerprint = fingerprint
        if not setting("bypass_entry_window",False) and not self.entry_start <= clock.strftime("%H:%M") <= self.entry_end:
            self.reset(trigger.end_time)
            return finish("RESET","Outside Strategy B entry window")
        stamp = trigger.end_time.isoformat()
        if self.last_bar and trigger.end_time <= datetime.fromisoformat(self.last_bar):
            return finish("WAITING_FOR_BREAKOUT","Completed candle already evaluated")
        self.last_bar = stamp
        window = [c for c in bars if c.start_time.astimezone(IST).date().isoformat() == session
                  and (not self.after or c.end_time > datetime.fromisoformat(self.after))][-self.lookback_bars:]
        if self.locked_box:
            box = self.locked_box
            anchor = datetime.fromisoformat(box.created_bar_time)
            active = [c for c in bars if c.end_time > anchor]
            if (not active or active[0].start_time != anchor
                    or any(b.start_time != a.end_time for a,b in zip(active,active[1:]))):
                self.reset(trigger.end_time)
                return finish("RESET","Missing completed bars since box lock")
            box.bars_active = len(active)
            if box.bars_active > box.max_bars:
                self.reset(trigger.end_time)
                return finish("RESET","Box expired after maximum completed-bar age")
        else:
            if len(window) < self.lookback_bars or any(b.start_time != a.end_time for a,b in zip(window,window[1:])):
                return finish("SEARCHING_COMPRESSION","Waiting for a fresh contiguous compression window")
            atr = features.atr_5m
            high, low = max(c.high for c in window), min(c.low for c in window)
            condition("compression_bb","Historical BB width percentile",features.bb_width_percentile <= bb_target,features.bb_width_percentile,bb_target)
            condition("compression_height","Compression height in ATR",0 < high-low <= height_target*atr,(high-low)/atr,height_target)
            if any(c.high-c.low > 4*atr for c in window):
                return finish("SEARCHING_COMPRESSION","Suspicious candle cannot define box")
            if not all(c.status == "PASSED" for c in conditions):
                return finish("SEARCHING_COMPRESSION","Compression width or height not qualified")
            self.locked_box = CompressionBox(box_high=high,box_low=low,box_height=high-low,
                atr_at_lock=atr,bb_width_at_lock=features.bb_width_percentile,locked_at=trigger.end_time,
                created_bar_time=stamp,bars_active=0,max_bars=self.max_age_bars,is_locked=True)
            summary["box"] = {"state":"LOCKED","high":high,"low":low,"height":high-low,"bars":0,"max_bars":self.max_age_bars}
            return finish("BOX_LOCKED","Box frozen; waiting for a subsequent completed breakout candle")

        box, atr = self.locked_box, self.locked_box.atr_at_lock
        summary["box"] = {"state":"LOCKED","high":box.box_high,"low":box.box_low,
                          "height":box.box_height,"bars":box.bars_active,"max_bars":box.max_bars}
        summary["compression"] = {"status":"PASSED","bb_width":box.bb_width_at_lock,"height_atr":box.box_height/atr}
        bullish = trigger.close > box.box_high+self.breakout_buffer_atr*atr
        bearish = trigger.close < box.box_low-self.breakout_buffer_atr*atr
        if not (bullish or bearish):
            if trigger.high-trigger.low > 4*atr:
                self.reset(trigger.end_time)
                return finish("RESET","Structural expansion invalidated box")
            return finish("WAITING_FOR_BREAKOUT","Waiting for completed close beyond frozen box and buffer")
        direction = TradeDirection.BULLISH if bullish else TradeDirection.BEARISH
        sign = 1 if bullish else -1
        edge = box.box_high if bullish else box.box_low
        extension = sign*(trigger.close-edge)
        condition("breakout_trigger","Completed buffered breakout",True,trigger.close,edge+sign*self.breakout_buffer_atr*atr)
        condition("anti_chase_extension","Anti-chase extension",extension <= self.max_extension_atr*atr,extension/atr,self.max_extension_atr)
        if extension > self.max_extension_atr*atr or trigger.high-trigger.low > 4*atr:
            self.reset(trigger.end_time)
            return finish("RESET","BREAKOUT_OVEREXTENDED or suspicious expansion",direction=direction)
        spread = max(trigger.high-trigger.low,1e-12)
        deriv = features.breakout_bull_derivatives_score if bullish else features.breakout_bear_derivatives_score
        factors = {
            "rvol":features.rvol_5m >= rvol_target,
            "body":sign*(trigger.close-trigger.open) > 0 and abs(trigger.close-trigger.open)/spread >= .45,
            "close_location":(trigger.close-trigger.low if bullish else trigger.high-trigger.close)/spread >= .70,
            "vwap":features.futures_vwap > 0 and sign*(features.futures_price-features.futures_vwap) > 0,
            "derivatives":deriv >= setting("bull_derivatives_score" if bullish else "bear_derivatives_score",2),
            "futures_oi":features.futures_buildup in (("LONG_BUILDUP","SHORT_COVERING") if bullish else ("SHORT_BUILDUP","LONG_UNWINDING"))}
        wall = features.bullish_oi_wall if bullish else features.bearish_oi_wall
        points = sum(factors.values()) - int(wall)
        condition("confirmation_score","Combined confirmations (wall penalty included)",points >= required,points,required)
        summary["confirmation"] = {"score":points,"required":required,"factors":factors,"wall_penalty":int(wall)}
        stop = edge-sign*.25*atr
        risk = sign*(trigger.close-stop)
        condition("risk_band","Frozen structural R",0 < risk <= 1.20*atr,risk,1.20*atr)
        summary["risk"] = {"initial_r":risk,"stop":stop,"max_r":1.20*atr}
        # A completed breakout attempt consumes this box even if rejected.
        self.reset(trigger.end_time)
        if points < required:
            return finish("CONFIRMATION_FAILED","Breakout confirmation insufficient; box abandoned",direction=direction)
        if not 0 < risk <= 1.20*atr:
            return finish("RISK_REJECTED","Initial R outside allowed band",direction=direction)
        key = f"SIG-B-{direction.value}-{int(datetime.fromisoformat(box.created_bar_time).timestamp())}-{int(trigger.end_time.timestamp())}"
        if self.consumed == key:
            return finish("RESET","Signal already consumed",direction=direction)
        self.consumed = key
        signal = StrategySignal(signal_id=key,strategy=StrategyName.VOLATILITY_BREAKOUT,
            direction=direction,option_type=OptionType.CALL if bullish else OptionType.PUT,
            timestamp=trigger.end_time,spot_reference_price=trigger.close,structural_stop=stop,
            r_points=risk,derivatives_score=deriv,features_snapshot={
                "box_high":box.box_high,"box_low":box.box_low,"atr_at_lock":atr,
                "box_created_time":box.created_bar_time,"confirmation_score":points,
                "confirmation_factors":factors,"wall_penalty":int(wall),"entry_reference_spot":trigger.close})
        return finish("READY_TO_TRIGGER","Breakout qualified; checking contract and execution risk",signal,direction)

    def evaluate(self, features, candles_5m, candles_15m=None, futures_candles=None, overrides=None):
        return self._decision(features,candles_5m,overrides)[0]

    def diagnose(self, features, candles_5m, overrides=None):
        return deepcopy(self)._decision(features,candles_5m,overrides)[1]
