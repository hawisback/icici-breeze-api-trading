"""Deterministic NIFTY futures Trend-Pullback Confluence state machine.

Strategy A decisions are made only from completed 15-minute futures candles.
The legacy 5-minute/spot/score-based evaluator is intentionally not retained.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from libs.contracts.models import Candle
from services.strategy.futures_signal import (
    IST,
    FuturesFeatureEngine,
    FuturesFeatureSnapshot,
    aggregate_completed_15m,
    completed_futures_candles,
)
from services.strategy.models import (
    OptionType,
    StrategyDirection,
    StrategyName,
    StrategySetup,
    StrategySignal,
    StrategyState,
    StrategyStateSnapshot,
    StrategyTriggerDiagnostics,
    StrategyTunablesConfig,
    ThresholdOverrides,
    TradeDirection,
    TriggerCondition,
)


EXPECTED_5M_SECONDS = 300
EXPECTED_15M_SECONDS = 900
CONTIGUITY_TOLERANCE_SECONDS = 5


class StrategyEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event: str
    timestamp: datetime
    reason: str | None = None
    details: dict[str, Any] = {}


def validate_candle_contiguity(previous: Any, current: Any, interval: str = "5m") -> tuple[bool, float]:
    expected = EXPECTED_15M_SECONDS if "15" in str(interval) else EXPECTED_5M_SECONDS
    actual = (current.start_time - previous.start_time).total_seconds()
    return abs(actual - expected) <= CONTIGUITY_TOLERANCE_SECONDS, actual


class TrendPullbackStrategy:
    """Single deterministic Strategy A evaluator.

    The `candles_5m`/`candles_15m` arguments remain in the public method for
    compatibility, but are ignored for Strategy A.  `futures_candles` is the
    only signal input; 5m futures are aggregated only when three completed,
    contiguous bars are available.
    """

    def __init__(self, config: StrategyTunablesConfig | None = None, **legacy_kwargs: Any) -> None:
        self.config = config or StrategyTunablesConfig()
        mapping = {
            "adx_threshold": "adx_threshold",
            "breakout_buffer_atr": "trigger_buffer_atr",
            "min_impulse_atr": "minimum_stop_distance_atr",
            "retest_tolerance_atr": "confluence_distance_atr",
        }
        updates = {target: legacy_kwargs[name] for name, target in mapping.items() if name in legacy_kwargs and legacy_kwargs[name] is not None}
        if updates:
            self.config = self.config.model_copy(update=updates)
        self.snapshot = StrategyStateSnapshot()
        self.last_processed_candle: datetime | None = None
        self.last_event: StrategyEvent | None = None
        self._setup_confirmation_key: str | None = None

    @property
    def state(self) -> dict[str, Any]:
        return {"snapshot": self.snapshot.model_dump(mode="json"), "last_processed_candle": self.last_processed_candle.isoformat() if self.last_processed_candle else None}

    @property
    def adx_threshold(self) -> float:
        return self.config.adx_threshold

    @property
    def breakout_buffer_atr(self) -> float:
        return self.config.trigger_buffer_atr

    def export_state(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_state(self, state: dict[str, Any]) -> None:
        payload = state.get("snapshot", state)
        try:
            self.snapshot = StrategyStateSnapshot.model_validate(payload)
        except Exception:
            self.snapshot = StrategyStateSnapshot()
        raw = state.get("last_processed_candle")
        self.last_processed_candle = datetime.fromisoformat(raw) if raw else None

    def reset(self, at: Optional[datetime] = None) -> None:
        self.snapshot = StrategyStateSnapshot()
        self.last_processed_candle = None
        self.last_event = StrategyEvent(event="RESET", timestamp=at or datetime.now(timezone.utc))
        self._setup_confirmation_key = None

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        self.snapshot = StrategyStateSnapshot().transition(StrategyState.COOLDOWN, cooldown_until=at + timedelta(minutes=10))
        self.last_event = StrategyEvent(event="COOLDOWN", timestamp=at, reason="POSITION_EXIT")

    @staticmethod
    def _time_minutes(value: datetime) -> int:
        local = value.astimezone(IST)
        return local.hour * 60 + local.minute

    def _entry_allowed(self, timestamp: datetime, overrides: ThresholdOverrides | None) -> bool:
        if overrides and overrides.bypass_entry_window:
            return True
        start_h, start_m = map(int, self.config.entry_session_start.split(":"))
        end_h, end_m = map(int, self.config.entry_session_end.split(":"))
        minutes = self._time_minutes(timestamp)
        return start_h * 60 + start_m <= minutes <= end_h * 60 + end_m

    def _forced_exit(self, timestamp: datetime) -> bool:
        hour, minute = map(int, self.config.forced_exit_time.split(":"))
        return self._time_minutes(timestamp) >= hour * 60 + minute

    def _features_for_input(self, futures_candles: Sequence[Candle], as_of: datetime | None) -> tuple[list[Candle], FuturesFeatureSnapshot]:
        raw = completed_futures_candles(futures_candles, as_of=as_of, interval="15m")
        if not raw:
            raw = aggregate_completed_15m(futures_candles, as_of=as_of)
        if not raw:
            raise ValueError("no completed futures 15m candles")
        contract = raw[-1].instrument_id
        same_contract = [c for c in raw if c.instrument_id == contract]
        return same_contract, FuturesFeatureEngine.build(same_contract, as_of=as_of)

    def _trend_ok(self, feature: FuturesFeatureSnapshot, direction: StrategyDirection) -> tuple[bool, str]:
        if feature.atr14 <= 0:
            return False, "ATR_UNAVAILABLE"
        aligned = (
            feature.ema20 > feature.ema50
            and feature.plus_di14 > feature.minus_di14
            and feature.adx14 >= self.config.adx_threshold
            and abs(feature.ema20 - feature.ema50) >= self.config.ema_separation_min_atr * feature.atr14
        )
        if direction is StrategyDirection.PUT:
            aligned = (
                feature.ema20 < feature.ema50
                and feature.minus_di14 > feature.plus_di14
                and feature.adx14 >= self.config.adx_threshold
                and abs(feature.ema20 - feature.ema50) >= self.config.ema_separation_min_atr * feature.atr14
            )
        return (True, "TREND_CONFIRMED") if aligned else (False, "TREND_REGIME_NOT_CONFIRMED")

    def _confirmation_ok(self, feature: FuturesFeatureSnapshot, direction: StrategyDirection) -> tuple[bool, str]:
        range_ = feature.high - feature.low
        if range_ <= 0:
            return False, "ZERO_RANGE_CONFIRMATION"
        body_ratio = abs(feature.close - feature.open) / range_
        if body_ratio + 1e-12 < self.config.confirmation_min_body_ratio:
            return False, "CONFIRMATION_BODY_TOO_WEAK"
        if direction is StrategyDirection.CALL:
            directional = feature.close > feature.open
            close_ok = feature.close >= feature.high - self.config.confirmation_close_location_pct * range_
        else:
            directional = feature.close < feature.open
            close_ok = feature.close <= feature.low + self.config.confirmation_close_location_pct * range_
        if not directional:
            return False, "CONFIRMATION_DIRECTION_MISMATCH"
        if not close_ok:
            return False, "CONFIRMATION_CLOSE_LOCATION"
        if range_ > self.config.confirmation_max_range_atr * feature.atr14 + 1e-9:
            return False, "CONFIRMATION_RANGE_TOO_LARGE"
        return True, "CONFIRMATION_CONFIRMED"

    def _confluence(self, feature: FuturesFeatureSnapshot, direction: StrategyDirection) -> tuple[bool, list[str], float | None, str]:
        if feature.atr14 <= 0:
            return False, [], None, "ATR_UNAVAILABLE"
        zone = self.config.sr_zone_atr * feature.atr14
        distance = self.config.confluence_distance_atr * feature.atr14
        level = feature.support if direction is StrategyDirection.CALL else feature.resistance
        if level is None:
            return False, [], None, "NO_CONFIRMED_SR"
        bar_touches_sr = feature.low - zone <= level <= feature.high + zone
        ema = abs(feature.close - feature.ema20) <= distance or abs(feature.low - feature.ema20) <= distance or abs(feature.high - feature.ema20) <= distance
        vwap = abs(feature.close - feature.session_vwap) <= distance or abs(feature.low - feature.session_vwap) <= distance or abs(feature.high - feature.session_vwap) <= distance
        references = ["CONFIRMED_SR"]
        if ema:
            references.append("EMA20")
        if vwap:
            references.append("SESSION_VWAP")
        if not bar_touches_sr:
            return False, references, level, "PRICE_NOT_IN_SR_ZONE"
        if not (ema or vwap):
            return False, references, level, "NO_EMA_OR_VWAP_CONFLUENCE"
        return True, references, level, "CONFLUENCE_CONFIRMED"

    def _build_setup(self, feature: FuturesFeatureSnapshot, direction: StrategyDirection, references: list[str], level: float) -> tuple[StrategySetup | None, str | None]:
        if direction is StrategyDirection.CALL:
            trigger = feature.high + self.config.trigger_buffer_atr * feature.atr14
            stop = feature.low - self.config.structural_stop_buffer_atr * feature.atr14
            opposing = feature.resistance if feature.resistance and feature.resistance > trigger else None
        else:
            trigger = feature.low - self.config.trigger_buffer_atr * feature.atr14
            stop = feature.high + self.config.structural_stop_buffer_atr * feature.atr14
            opposing = feature.support if feature.support and feature.support < trigger else None
        risk = abs(trigger - stop)
        risk_atr = risk / feature.atr14 if feature.atr14 > 0 else 0.0
        if risk_atr < self.config.minimum_stop_distance_atr - 1e-9:
            return None, "STRUCTURAL_R_BELOW_MINIMUM"
        if risk_atr > self.config.maximum_stop_distance_atr + 1e-9:
            return None, "STRUCTURAL_R_ABOVE_MAXIMUM"
        if opposing is not None and abs(opposing - trigger) / risk < self.config.minimum_room_to_opposing_sr_r - 1e-9:
            return None, "INSUFFICIENT_ROOM_TO_OPPOSING_SR"
        expiry = feature.candle_timestamp + timedelta(minutes=15 * self.config.trigger_validity_bars)
        return StrategySetup(
            direction=direction,
            setup_timestamp=feature.candle_timestamp,
            confirmation_bar_timestamp=feature.candle_timestamp,
            confirmation_high=feature.high,
            confirmation_low=feature.low,
            trigger_price=trigger,
            structural_stop=stop,
            initial_underlying_r=risk,
            relevant_support_resistance_level=level,
            confluence_references=tuple(references),
            setup_expiry_timestamp=expiry,
            setup_expiry_bar_index=feature.bar_index + self.config.trigger_validity_bars,
        ), None

    def _signal(self, setup: StrategySetup, feature: FuturesFeatureSnapshot, entry_price: float) -> StrategySignal:
        direction = TradeDirection.BULLISH if setup.direction is StrategyDirection.CALL else TradeDirection.BEARISH
        option = OptionType.CALL if setup.direction is StrategyDirection.CALL else OptionType.PUT
        return StrategySignal(
            signal_id=f"STRATEGY-A-{setup.direction.value}-{feature.contract_id}-{int(feature.candle_timestamp.timestamp())}",
            strategy=StrategyName.TREND_PULLBACK,
            direction=direction,
            option_type=option,
            timestamp=feature.candle_timestamp,
            spot_reference_price=feature.close,
            structural_stop=setup.structural_stop,
            r_points=setup.initial_underlying_r,
            derivatives_score=0.0,
            features_snapshot={
                "futures_contract": feature.contract_id,
                "completed_candle_timestamp": feature.candle_timestamp.isoformat(),
                "ema20": feature.ema20, "ema50": feature.ema50,
                "adx14": feature.adx14, "plus_di14": feature.plus_di14,
                "minus_di14": feature.minus_di14, "atr14": feature.atr14,
                "session_vwap": feature.session_vwap,
                "support": feature.support, "resistance": feature.resistance,
                "trigger": setup.trigger_price, "entry_price": entry_price,
                "structural_stop": setup.structural_stop, "underlying_r": setup.initial_underlying_r,
                "confluence_references": list(setup.confluence_references),
                "state": StrategyState.ENTERED.value,
            },
        )

    def _diagnostic(self, feature: FuturesFeatureSnapshot, direction: StrategyDirection, reason: str, setup: StrategySetup | None = None) -> StrategyTriggerDiagnostics:
        option = OptionType.CALL if direction is StrategyDirection.CALL else OptionType.PUT
        conditions = [
            TriggerCondition(id="trend", name="Futures trend regime", current_value=feature.trend, target_threshold="directional EMA/DI/ADX", status="PASSED" if reason == "TREND_CONFIRMED" else "PENDING", gap_description=reason),
            TriggerCondition(id="sr", name="Confirmed support/resistance", current_value=str(feature.support if direction is StrategyDirection.CALL else feature.resistance), target_threshold="confirmed pivot", status="PASSED" if (feature.support if direction is StrategyDirection.CALL else feature.resistance) is not None else "PENDING", gap_description=reason),
        ]
        target = setup.trigger_price if setup else None
        passed = sum(c.status == "PASSED" for c in conditions)
        return StrategyTriggerDiagnostics(
            strategy=StrategyName.TREND_PULLBACK,
            strategy_label="NIFTY Futures Trend-Pullback Confluence",
            direction=TradeDirection.BULLISH if direction is StrategyDirection.CALL else TradeDirection.BEARISH,
            option_type=option,
            overall_status="READY_TO_TRIGGER" if self.snapshot.state is StrategyState.ARMED else "WAITING",
            passed_count=passed, total_count=len(conditions), ready_pct=round(passed / len(conditions) * 100, 2),
            key_blocker=reason, target_entry_level=target, current_spot=feature.close,
            distance_pts=abs(feature.close - target) if target is not None else None,
            phase_state=self.snapshot.state.value,
            phase_summary={"futures_contract": feature.contract_id, "completed_candle_timestamp": feature.candle_timestamp.isoformat(), "ema20": feature.ema20, "ema50": feature.ema50, "adx": feature.adx14, "plus_di": feature.plus_di14, "minus_di": feature.minus_di14, "atr": feature.atr14, "vwap": feature.session_vwap, "active_support": feature.support, "active_resistance": feature.resistance, "rejection_reason": reason},
            conditions=conditions,
        )

    def evaluate(self, features: Any, candles_5m: Sequence[Candle], candles_15m: Sequence[Candle], futures_candles: Sequence[Candle] | None = None, overrides: ThresholdOverrides | None = None) -> StrategySignal | None:
        source = list(futures_candles or [])
        as_of = getattr(features, "timestamp", None)
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            as_of = None
        try:
            _, feature = self._features_for_input(source, as_of)
        except ValueError as exc:
            self.last_event = StrategyEvent(event="REJECTED", timestamp=as_of or datetime.now(timezone.utc), reason="INCOMPLETE_FUTURES_DATA", details={"error": str(exc)})
            return None
        if self.last_processed_candle == feature.candle_timestamp:
            self.last_event = StrategyEvent(event="DUPLICATE_IGNORED", timestamp=feature.candle_timestamp, reason="DUPLICATE_COMPLETED_CANDLE")
            return None
        self.last_processed_candle = feature.candle_timestamp
        if self._forced_exit(feature.candle_timestamp) and self.snapshot.state is not StrategyState.FLAT:
            self.snapshot = StrategyStateSnapshot()
            self.last_event = StrategyEvent(event="FORCED_EXIT", timestamp=feature.candle_timestamp, reason="FORCED_EXIT_1515")
            return None

        if self.snapshot.state is StrategyState.ENTERED:
            self.last_event = StrategyEvent(event="ACTIVE_POSITION", timestamp=feature.candle_timestamp, reason="ENTRY_ALREADY_CONSUMED")
            return None

        if self.snapshot.state in (StrategyState.SETUP, StrategyState.ARMED) and self.snapshot.setup:
            setup = self.snapshot.setup
            if feature.bar_index > setup.setup_expiry_bar_index:
                self.snapshot = StrategyStateSnapshot()
                self.last_event = StrategyEvent(event="EXPIRED", timestamp=feature.candle_timestamp, reason="TRIGGER_EXPIRED_TWO_BARS")
                return None
            if (setup.direction is StrategyDirection.CALL and feature.close <= setup.structural_stop) or (setup.direction is StrategyDirection.PUT and feature.close >= setup.structural_stop):
                self.snapshot = StrategyStateSnapshot()
                self.last_event = StrategyEvent(event="INVALIDATED", timestamp=feature.candle_timestamp, reason="STRUCTURAL_STOP_BREACHED")
                return None
            if self.snapshot.state is StrategyState.SETUP:
                self.snapshot = self.snapshot.transition(StrategyState.ARMED)
            gap_triggered = feature.open >= setup.trigger_price if setup.direction is StrategyDirection.CALL else feature.open <= setup.trigger_price
            triggered = gap_triggered or (feature.high >= setup.trigger_price if setup.direction is StrategyDirection.CALL else feature.low <= setup.trigger_price)
            entry_price = feature.open if gap_triggered else setup.trigger_price
            chase = entry_price - setup.trigger_price if setup.direction is StrategyDirection.CALL else setup.trigger_price - entry_price
            if triggered and chase <= self.config.maximum_chase_atr * feature.atr14 + 1e-9 and self._entry_allowed(feature.candle_timestamp, overrides):
                signal = self._signal(setup, feature, entry_price)
                self.snapshot = self.snapshot.transition(StrategyState.ENTERED, entry_timestamp=feature.candle_timestamp)
                self.last_event = StrategyEvent(event="ENTERED", timestamp=feature.candle_timestamp, reason="TRIGGER_CROSSED", details={"entry_price": entry_price})
                return signal
            if triggered and chase > self.config.maximum_chase_atr * feature.atr14 + 1e-9:
                self.snapshot = StrategyStateSnapshot()
                self.last_event = StrategyEvent(event="REJECTED", timestamp=feature.candle_timestamp, reason="MAXIMUM_CHASE_EXCEEDED")
            elif triggered and not self._entry_allowed(feature.candle_timestamp, overrides):
                self.last_event = StrategyEvent(event="REJECTED", timestamp=feature.candle_timestamp, reason="ENTRY_SESSION_CLOSED")
            return None

        if self.snapshot.state is StrategyState.COOLDOWN:
            if self.snapshot.cooldown_until and feature.candle_timestamp < self.snapshot.cooldown_until:
                return None
            self.snapshot = StrategyStateSnapshot()
        if not self._entry_allowed(feature.candle_timestamp, overrides):
            self.last_event = StrategyEvent(event="REJECTED", timestamp=feature.candle_timestamp, reason="SETUP_SESSION_CLOSED")
            return None
        for direction in (StrategyDirection.CALL, StrategyDirection.PUT):
            trend_ok, trend_reason = self._trend_ok(feature, direction)
            if not trend_ok:
                continue
            conf_ok, conf_reason = self._confirmation_ok(feature, direction)
            if not conf_ok:
                continue
            confluence_ok, references, level, confluence_reason = self._confluence(feature, direction)
            if not confluence_ok or level is None:
                continue
            setup, risk_reason = self._build_setup(feature, direction, references, level)
            if setup is None:
                continue
            key = f"{direction.value}:{feature.candle_timestamp.isoformat()}"
            if key == self._setup_confirmation_key:
                continue
            self._setup_confirmation_key = key
            self.snapshot = self.snapshot.transition(StrategyState.SETUP, setup=setup)
            self.last_event = StrategyEvent(event="SETUP_CREATED", timestamp=feature.candle_timestamp, reason="SETUP_QUALIFIED", details={"direction": direction.value, "confirmation_reason": conf_reason, "confluence_reason": confluence_reason})
            return None
        self.last_event = StrategyEvent(event="REJECTED", timestamp=feature.candle_timestamp, reason="NO_VALID_SETUP")
        return None

    def evaluate_replay_trigger(self, direction: TradeDirection, features: Any, candles_5m: Sequence[Candle], candles_15m: Sequence[Candle], futures_candles: Sequence[Candle] | None = None, overrides: ThresholdOverrides | None = None, **_: Any) -> tuple[StrategySignal | None, StrategyTriggerDiagnostics]:
        signal = self.evaluate(features, candles_5m, candles_15m, futures_candles=futures_candles, overrides=overrides)
        _, feature = self._features_for_input(list(futures_candles or []), getattr(features, "timestamp", None))
        return signal, self._diagnostic(feature, StrategyDirection.CALL if direction is TradeDirection.BULLISH else StrategyDirection.PUT, self.last_event.reason if self.last_event else "NO_DECISION", self.snapshot.setup)

    def diagnose(self, features: Any, candles_5m: Sequence[Candle], candles_15m: Sequence[Candle], overrides: ThresholdOverrides | None = None, futures_candles: Sequence[Candle] | None = None) -> list[StrategyTriggerDiagnostics]:
        try:
            _, feature = self._features_for_input(list(futures_candles or []), getattr(features, "timestamp", None))
        except ValueError:
            timestamp = getattr(features, "timestamp", None)
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
                timestamp = datetime.now(timezone.utc)
            feature = FuturesFeatureSnapshot(
                contract_id="UNAVAILABLE",
                candle_timestamp=timestamp,
                candle_start=timestamp,
                open=0.0,
                high=0.0,
                low=0.0,
                close=0.0,
                ema20=0.0,
                ema50=0.0,
                adx14=0.0,
                plus_di14=0.0,
                minus_di14=0.0,
                atr14=0.0,
                session_vwap=0.0,
                bar_index=0,
            )
            return [self._diagnostic(feature, direction, "FUTURES_DATA_UNAVAILABLE", self.snapshot.setup) for direction in (StrategyDirection.CALL, StrategyDirection.PUT)]
        result = []
        for direction in (StrategyDirection.CALL, StrategyDirection.PUT):
            trend_ok, trend_reason = self._trend_ok(feature, direction)
            conf_ok, conf_reason = self._confirmation_ok(feature, direction)
            confluence_ok, _, _, confluence_reason = self._confluence(feature, direction)
            reason = "READY" if trend_ok and conf_ok and confluence_ok else next((r for r, ok in ((trend_reason, trend_ok), (conf_reason, conf_ok), (confluence_reason, confluence_ok)) if not ok), "NO_VALID_SETUP")
            result.append(self._diagnostic(feature, direction, reason, self.snapshot.setup))
        return result
