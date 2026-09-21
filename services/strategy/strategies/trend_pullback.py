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
    canonical_active_futures_stream,
    completed_futures_candles,
    resolve_completed_futures_contract,
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

    def __init__(
        self,
        config: StrategyTunablesConfig | None = None,
        *,
        allow_session_bypass: bool = False,
        **legacy_kwargs: Any,
    ) -> None:
        self.config = config or StrategyTunablesConfig()
        self.allow_session_bypass = bool(allow_session_bypass)
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
        self.active_contract_id: str | None = None
        self.last_event: StrategyEvent | None = None
        self._setup_confirmation_key: str | None = None

    @property
    def state(self) -> dict[str, Any]:
        return {"snapshot": self.snapshot.model_dump(mode="json"), "last_processed_candle": self.last_processed_candle.isoformat() if self.last_processed_candle else None, "active_contract_id": self.active_contract_id}

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
        self.active_contract_id = state.get("active_contract_id")

    def reset(self, at: Optional[datetime] = None) -> None:
        self.snapshot = StrategyStateSnapshot()
        self.last_processed_candle = None
        self.active_contract_id = None
        self.last_event = StrategyEvent(event="RESET", timestamp=at or datetime.now(timezone.utc))
        self._setup_confirmation_key = None

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        self.snapshot = StrategyStateSnapshot().transition(StrategyState.COOLDOWN, cooldown_until=at + timedelta(minutes=10))
        self.last_event = StrategyEvent(event="COOLDOWN", timestamp=at, reason="POSITION_EXIT")

    def confirm_entry(self, at: datetime) -> None:
        """Confirm ENTERED only after the downstream Strategy A trade exists."""
        if self.snapshot.state is not StrategyState.ARMED or self.snapshot.setup is None:
            raise ValueError("Strategy A entry confirmation requires an ARMED setup")
        self.snapshot = self.snapshot.transition(StrategyState.ENTERED, entry_timestamp=at)
        self.last_event = StrategyEvent(
            event="ENTERED",
            timestamp=at,
            reason="OPTION_EXECUTION_CONFIRMED",
        )

    def on_execution_rejected(self, at: datetime, reason: str) -> None:
        """Consume a triggered attempt without leaving a phantom ENTERED state."""
        if self.snapshot.state not in {
            StrategyState.SETUP,
            StrategyState.ARMED,
            StrategyState.ENTERED,
        }:
            return
        self.snapshot = StrategyStateSnapshot().transition(
            StrategyState.COOLDOWN,
            cooldown_until=at + timedelta(minutes=10),
        )
        self._setup_confirmation_key = None
        self.last_event = StrategyEvent(
            event="EXECUTION_REJECTED",
            timestamp=at,
            reason=reason,
        )

    @staticmethod
    def _time_minutes(value: datetime) -> int:
        local = value.astimezone(IST)
        return local.hour * 60 + local.minute

    def _entry_allowed(self, timestamp: datetime, overrides: ThresholdOverrides | None) -> bool:
        # Production Strategy A cannot bypass its 09:45-14:45 contract.
        # Historical simulation opts in explicitly via allow_session_bypass.
        if self.allow_session_bypass and overrides and overrides.bypass_entry_window:
            return True
        start_h, start_m = map(int, self.config.entry_session_start.split(":"))
        end_h, end_m = map(int, self.config.entry_session_end.split(":"))
        minutes = self._time_minutes(timestamp)
        return start_h * 60 + start_m <= minutes <= end_h * 60 + end_m

    def _forced_exit(self, timestamp: datetime) -> bool:
        hour, minute = map(int, self.config.forced_exit_time.split(":"))
        return self._time_minutes(timestamp) >= hour * 60 + minute

    def _features_for_input(self, futures_candles: Sequence[Candle], as_of: datetime | None) -> tuple[list[Candle], FuturesFeatureSnapshot]:
        selection_time = as_of or (max(c.end_time for c in futures_candles) if futures_candles else None)
        if selection_time is None:
            raise ValueError("no futures timestamp available")
        raw = canonical_active_futures_stream(futures_candles, as_of=selection_time, interval="15m")
        if not raw:
            raw = aggregate_completed_15m(futures_candles, as_of=as_of)
            raw = canonical_active_futures_stream(raw, as_of=selection_time, interval="15m") if raw else []
        if not raw:
            raise ValueError("no completed futures 15m candles")
        if as_of is not None:
            # Require the latest expected completed 15m bar. Around a quarter-hour
            # boundary allow two minutes for the newly completed broker candle to
            # arrive; after that, the previous bar is stale and must not advance
            # the Strategy A state machine.
            local = selection_time.astimezone(IST)
            boundary_local = local.replace(
                minute=(local.minute // 15) * 15,
                second=0,
                microsecond=0,
            )
            expected_end = boundary_local.astimezone(timezone.utc)
            if (local - boundary_local).total_seconds() <= 120:
                expected_end -= timedelta(minutes=15)
            # Strategy A stops accepting entries at 14:45 and forcibly exits
            # at 15:15. After that point a later wall-clock quarter must not
            # manufacture a stale-data failure for an otherwise valid session.
            session_validation_end = local.replace(
                hour=15, minute=15, second=0, microsecond=0
            ).astimezone(timezone.utc)
            if expected_end > session_validation_end:
                expected_end = session_validation_end
            if raw[-1].end_time < expected_end:
                raise ValueError(
                    "STALE_FUTURES_DATA: "
                    f"latest={raw[-1].end_time.isoformat()} "
                    f"expected_at_least={expected_end.isoformat()}"
                )
        return raw, FuturesFeatureEngine.build(raw, as_of=as_of)

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
            structural_extreme = min(feature.low, feature.support) if feature.support is not None else feature.low
            stop = structural_extreme - self.config.structural_stop_buffer_atr * feature.atr14
            opposing = feature.resistance if feature.resistance and feature.resistance > trigger else None
        else:
            trigger = feature.low - self.config.trigger_buffer_atr * feature.atr14
            structural_extreme = max(feature.high, feature.resistance) if feature.resistance is not None else feature.high
            stop = structural_extreme + self.config.structural_stop_buffer_atr * feature.atr14
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
        initial_r = abs(entry_price - setup.structural_stop)
        return StrategySignal(
            signal_id=f"STRATEGY-A-{setup.direction.value}-{feature.contract_id}-{int(feature.candle_timestamp.timestamp())}",
            strategy=StrategyName.TREND_PULLBACK,
            direction=direction,
            option_type=option,
            timestamp=feature.candle_timestamp,
            # Compatibility field retained for shared APIs; Strategy A uses
            # the explicit futures trigger/open fill below everywhere risk is
            # calculated.
            spot_reference_price=feature.close,
            underlying_entry_price=entry_price,
            structural_stop=setup.structural_stop,
            r_points=initial_r,
            derivatives_score=0.0,
            features_snapshot={
                "futures_contract": feature.contract_id,
                "completed_candle_timestamp": feature.candle_timestamp.isoformat(),
                "ema20": feature.ema20, "ema50": feature.ema50,
                "adx14": feature.adx14, "plus_di14": feature.plus_di14,
                "minus_di14": feature.minus_di14, "atr14": feature.atr14,
                "session_vwap": feature.session_vwap,
                "support": feature.support, "resistance": feature.resistance,
                "trend": feature.trend, "confluence_result": True,
                "confirmation_result": True,
                "trigger": setup.trigger_price, "entry_price": entry_price,
                "underlying_entry_price": entry_price, "bar_close": feature.close,
                "structural_extreme": (min(feature.low, feature.support) if setup.direction is StrategyDirection.CALL and feature.support is not None else max(feature.high, feature.resistance) if setup.direction is StrategyDirection.PUT and feature.resistance is not None else feature.low if setup.direction is StrategyDirection.CALL else feature.high),
                "structural_stop": setup.structural_stop, "underlying_r": initial_r,
                "confluence_references": list(setup.confluence_references),
                "state": StrategyState.ENTERED.value,
            },
        )

    def _diagnostic(self, feature: FuturesFeatureSnapshot, direction: StrategyDirection, reason: str, setup: StrategySetup | None = None) -> StrategyTriggerDiagnostics:
        option = OptionType.CALL if direction is StrategyDirection.CALL else OptionType.PUT
        trend_ok, trend_reason = self._trend_ok(feature, direction)
        confirmation_ok, confirmation_reason = self._confirmation_ok(feature, direction)
        confluence_ok, references, level, confluence_reason = self._confluence(feature, direction)
        active_setup = setup if setup is not None and setup.direction is direction else None
        prospective_setup = active_setup
        risk_reason: str | None = None
        if prospective_setup is None and trend_ok and confirmation_ok and confluence_ok and level is not None:
            prospective_setup, risk_reason = self._build_setup(feature, direction, references, level)
        risk_applicable = trend_ok and confirmation_ok and confluence_ok and level is not None
        risk_ok = prospective_setup is not None if risk_applicable else False
        range_points = max(0.0, feature.high - feature.low)
        body_ratio = abs(feature.close - feature.open) / range_points if range_points > 0 else 0.0
        range_atr = range_points / feature.atr14 if feature.atr14 > 0 else 0.0
        target = prospective_setup.trigger_price if prospective_setup else None
        distance = abs(feature.close - target) if target is not None else None
        conditions = [
            TriggerCondition(
                id="trend",
                name="Futures trend regime",
                current_value=feature.trend,
                target_threshold="directional EMA20/EMA50 + DI + ADX",
                status="PASSED" if trend_ok else "PENDING",
                gap_description=trend_reason,
            ),
            TriggerCondition(
                id="confluence",
                name="Pullback confluence",
                current_value=str(level) if level is not None else "NONE",
                target_threshold="confirmed S/R + EMA20 or session VWAP",
                status="PASSED" if confluence_ok else "PENDING",
                gap_description=confluence_reason,
            ),
            TriggerCondition(
                id="confirmation",
                name="Confirmation candle",
                current_value=f"body={body_ratio:.2f}; range={range_atr:.2f} ATR",
                target_threshold=(
                    f"body>={self.config.confirmation_min_body_ratio:.2f}; "
                    f"directional close; range<={self.config.confirmation_max_range_atr:.2f} ATR"
                ),
                status="PASSED" if confirmation_ok else "PENDING",
                gap_description=confirmation_reason,
            ),
            TriggerCondition(
                id="risk",
                name="Structural risk",
                current_value=(
                    f"R={prospective_setup.initial_underlying_r:.2f} pts"
                    if prospective_setup is not None else "NOT_ESTABLISHED"
                ),
                target_threshold=(
                    f"{self.config.minimum_stop_distance_atr:.2f}-{self.config.maximum_stop_distance_atr:.2f} ATR "
                    f"stop; room>={self.config.minimum_room_to_opposing_sr_r:.2f}R"
                ),
                status="PASSED" if risk_ok else "PENDING",
                gap_description=(risk_reason or "STRUCTURAL_RISK_OK" if risk_applicable else "WAITING_FOR_SETUP_PREREQUISITES"),
            ),
        ]
        # Keep the public TriggerCondition status contract binary
        # (PASSED/PENDING). Structural risk is displayed as pending until its
        # prerequisites exist, but it is not included in readiness arithmetic
        # before trend/confirmation/confluence have qualified.
        applicable = conditions if risk_applicable else [condition for condition in conditions if condition.id != "risk"]
        passed = sum(condition.status == "PASSED" for condition in applicable)
        total = len(applicable)
        return StrategyTriggerDiagnostics(
            strategy=StrategyName.TREND_PULLBACK,
            strategy_label="NIFTY Futures Trend-Pullback Confluence",
            direction=TradeDirection.BULLISH if direction is StrategyDirection.CALL else TradeDirection.BEARISH,
            option_type=option,
            overall_status="READY_TO_TRIGGER" if self.snapshot.state is StrategyState.ARMED else "WAITING",
            passed_count=passed, total_count=total, ready_pct=round(passed / total * 100, 2) if total else 0.0,
            key_blocker=reason, target_entry_level=target, current_spot=feature.close,
            distance_pts=distance,
            phase_state=self.snapshot.state.value,
            phase_summary={
                "futures_contract": feature.contract_id,
                "completed_candle_timestamp": feature.candle_timestamp.isoformat(),
                "ema20": feature.ema20, "ema50": feature.ema50, "adx": feature.adx14,
                "plus_di": feature.plus_di14, "minus_di": feature.minus_di14,
                "atr": feature.atr14, "vwap": feature.session_vwap,
                "active_support": feature.support, "active_resistance": feature.resistance,
                "rejection_reason": reason,
                "strategy_a_v2": {
                    "data": {
                        "contract": feature.contract_id,
                        "completed_candle_timestamp": feature.candle_timestamp.isoformat(),
                        "close": feature.close,
                    },
                    "trend": {
                        "passed": trend_ok, "reason": trend_reason,
                        "ema20": feature.ema20, "ema50": feature.ema50,
                        "adx": feature.adx14, "plus_di": feature.plus_di14, "minus_di": feature.minus_di14,
                    },
                    "confluence": {
                        "passed": confluence_ok, "reason": confluence_reason,
                        "references": references, "level": level,
                        "support": feature.support, "resistance": feature.resistance,
                        "vwap": feature.session_vwap,
                    },
                    "confirmation": {
                        "passed": confirmation_ok, "reason": confirmation_reason,
                        "body_ratio": body_ratio, "range_atr": range_atr,
                    },
                    "trigger": {
                        "state": self.snapshot.state.value,
                        "trigger_price": target,
                        "distance_pts": distance,
                    },
                    "risk": {
                        "passed": risk_ok if risk_applicable else None,
                        "reason": risk_reason or ("STRUCTURAL_RISK_OK" if risk_ok else "WAITING_FOR_SETUP_PREREQUISITES"),
                        "structural_stop": prospective_setup.structural_stop if prospective_setup else None,
                        "initial_r_points": prospective_setup.initial_underlying_r if prospective_setup else None,
                    },
                },
            },
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
            message = str(exc)
            reason = "STALE_FUTURES_DATA" if message.startswith("STALE_FUTURES_DATA:") else "INCOMPLETE_FUTURES_DATA"
            self.last_event = StrategyEvent(
                event="REJECTED",
                timestamp=as_of or datetime.now(timezone.utc),
                reason=reason,
                details={"error": message},
            )
            return None
        if self.active_contract_id is not None and feature.contract_id != self.active_contract_id:
            previous_contract = self.active_contract_id
            previous_state = self.snapshot.state.value
            self.snapshot = StrategyStateSnapshot()
            self._setup_confirmation_key = None
            self.last_processed_candle = feature.candle_timestamp
            self.active_contract_id = feature.contract_id
            self.last_event = StrategyEvent(event="ROLLOVER_RESET", timestamp=feature.candle_timestamp, reason="FUTURES_ROLLOVER_RESET", details={"previous_contract": previous_contract, "new_contract": feature.contract_id, "previous_state": previous_state})
            return None
        self.active_contract_id = feature.contract_id
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
                # The futures thesis has triggered, but option execution has not
                # been accepted yet. Service/replay confirms ENTERED only after
                # the corresponding trade/manifest is persisted.
                self.last_event = StrategyEvent(
                    event="TRIGGERED",
                    timestamp=feature.candle_timestamp,
                    reason="TRIGGER_CROSSED",
                    details={"entry_price": entry_price},
                )
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
        except ValueError as exc:
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
            message = str(exc)
            reason = (
                "STALE_FUTURES_DATA" if message.startswith("STALE_FUTURES_DATA:")
                else "FUTURES_DATA_UNAVAILABLE"
            )
            return [self._diagnostic(feature, direction, reason, self.snapshot.setup) for direction in (StrategyDirection.CALL, StrategyDirection.PUT)]
        result = []
        for direction in (StrategyDirection.CALL, StrategyDirection.PUT):
            trend_ok, trend_reason = self._trend_ok(feature, direction)
            conf_ok, conf_reason = self._confirmation_ok(feature, direction)
            confluence_ok, references, level, confluence_reason = self._confluence(feature, direction)
            prospective_setup = None
            risk_reason = None
            if trend_ok and conf_ok and confluence_ok and level is not None:
                prospective_setup, risk_reason = self._build_setup(feature, direction, references, level)
            if not trend_ok:
                reason = trend_reason
            elif not conf_ok:
                reason = conf_reason
            elif not confluence_ok:
                reason = confluence_reason
            elif prospective_setup is None:
                reason = risk_reason or "STRUCTURAL_RISK_REJECTED"
            else:
                reason = "READY"
            setup_for_direction = (
                self.snapshot.setup
                if self.snapshot.setup is not None and self.snapshot.setup.direction is direction
                else prospective_setup
            )
            result.append(self._diagnostic(feature, direction, reason, setup_for_direction))
        return result
