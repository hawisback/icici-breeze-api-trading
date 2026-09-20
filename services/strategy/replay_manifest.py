"""Replay-only manifest capture.

The recorder is deliberately passive: it stores values supplied by the
authoritative replay/PositionManager path and never calculates a stop, ladder
stage, R multiple, or exit.  This prevents instrumentation from becoming a
second trade-management implementation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.strategy.models import StrategySignal


def _confirmation_counts(snapshot: dict[str, Any]) -> tuple[int | None, int | None]:
    """Extract the already-calculated Strategy A confirmation counts."""
    raw = snapshot.get("confirmation_score")
    if isinstance(raw, str) and "/" in raw:
        passed, available = raw.split("/", 1)
        try:
            return int(available), int(passed)
        except ValueError:
            pass
    return None, None


class ReplayStateSnapshot(BaseModel):
    """State supplied immediately before or after one managed 5-minute bar."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    active_stop: float
    ladder_stage: str
    current_r: float
    peak_r: float
    protected_breakeven_active: bool
    profit_lock_active: bool
    runner_mode_active: bool
    highest_favorable_price: float | None = None
    lowest_favorable_price: float | None = None
    reversal_score: int = 0
    adverse_health_counters: dict[str, int | float | bool] = Field(default_factory=dict)


class ReplayEvent(BaseModel):
    """A replay event recorded with the state that existed at that moment."""

    model_config = ConfigDict(extra="forbid")

    event: str
    timestamp: datetime
    reference_price: float | None = None
    active_stop: float | None = None
    r_multiple: float | None = None
    source_candle: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ReplayManifestRecord(BaseModel):
    """One complete Strategy A replay signal and its supplied lifecycle state."""

    model_config = ConfigDict(extra="forbid")

    # Identity
    replay_signal_id: str
    strategy_id: str
    direction: str
    trading_date: str
    setup_id: str | None = None

    # Entry
    trigger_source_candle_timestamp: datetime
    trigger_level: float
    simulated_entry_timestamp: datetime
    simulated_entry_price: float
    entry_5m_candle_timestamp: datetime
    entry_occurred_intrabar: bool
    entry_features: dict[str, Any] = Field(default_factory=dict)
    confirmation_available: int | None = None
    confirmation_passed: int | None = None
    confirmations: dict[str, Any] = Field(default_factory=dict)

    # Initial structure
    pullback_swing_low: float | None = None
    pullback_swing_high: float | None = None
    impulse_low: float | None = None
    impulse_high: float | None = None
    atr_at_entry: float
    initial_structural_stop: float
    initial_risk_points: float
    initial_risk_atr: float

    # Exact PositionManager state immediately after entry
    current_trailing_stop: float
    current_r: float
    highest_favorable_price: float | None = None
    lowest_favorable_price: float | None = None
    peak_r: float
    protected_breakeven_active: bool
    profit_lock_active: bool
    runner_mode_active: bool
    current_ladder_stage: str
    reversal_score: int
    adverse_health_counters: dict[str, int | float | bool] = Field(default_factory=dict)
    entry_bar_timestamp: datetime
    last_managed_completed_bar_timestamp: datetime | None = None

    state_timeline: list[dict[str, ReplayStateSnapshot]] = Field(default_factory=list)
    events: list[ReplayEvent] = Field(default_factory=list)
    lifecycle_status: str = "PENDING"
    exit_timestamp: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    realized_r: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    ambiguous: bool = False
    option_data_status: str = "UNAVAILABLE"
    option_contract_instrument_id: str | None = None
    option_contract_symbol: str | None = None
    option_expiry: str | None = None
    option_strike: float | None = None
    option_lot_size: int | None = None
    option_entry_price: float | None = None
    option_exit_price: float | None = None
    option_gross_pnl: float | None = None
    option_net_pnl: float | None = None
    option_transaction_costs: float | None = None
    option_price_source: str | None = None
    option_data_quality_reason: str | None = None
    historical_option_provenance: dict[str, Any] = Field(default_factory=dict)


class ReplayManifestRecorder:
    """Passive recorder used by the existing historical replay."""

    def __init__(self) -> None:
        self._records: dict[str, ReplayManifestRecord] = {}
        self._replay_metadata: dict[str, Any] = {}

    @staticmethod
    def _as_datetime(value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    def record_entry(
        self,
        *,
        signal: StrategySignal,
        trading_date: str,
        trigger_source_candle_timestamp: datetime | str,
        trigger_level: float,
        simulated_entry_timestamp: datetime | str,
        simulated_entry_price: float,
        entry_5m_candle_timestamp: datetime | str,
        entry_occurred_intrabar: bool,
        entry_features: dict[str, Any] | None = None,
        setup_id: str | None,
        pullback_swing_low: float | None,
        pullback_swing_high: float | None,
        impulse_low: float | None,
        impulse_high: float | None,
        atr_at_entry: float,
        initial_structural_stop: float,
        initial_risk_points: float,
        initial_risk_atr: float,
        current_trailing_stop: float,
        current_r: float,
        highest_favorable_price: float | None,
        lowest_favorable_price: float | None,
        peak_r: float,
        protected_breakeven_active: bool,
        profit_lock_active: bool,
        runner_mode_active: bool,
        current_ladder_stage: str,
        reversal_score: int,
        adverse_health_counters: dict[str, int | float | bool],
        entry_bar_timestamp: datetime | str,
        last_managed_completed_bar_timestamp: datetime | str | None,
    ) -> ReplayManifestRecord:
        """Store the exact entry/initial-state values supplied by the replay."""
        if signal.signal_id in self._records:
            raise ValueError(f"duplicate replay manifest signal: {signal.signal_id}")
        record = ReplayManifestRecord(
            replay_signal_id=signal.signal_id,
            strategy_id=signal.strategy.value,
            direction="CALL" if signal.direction.value == "BULLISH" else "PUT",
            trading_date=trading_date,
            setup_id=setup_id,
            trigger_source_candle_timestamp=self._as_datetime(trigger_source_candle_timestamp),
            trigger_level=trigger_level,
            simulated_entry_timestamp=self._as_datetime(simulated_entry_timestamp),
            simulated_entry_price=simulated_entry_price,
            entry_5m_candle_timestamp=self._as_datetime(entry_5m_candle_timestamp),
            entry_occurred_intrabar=entry_occurred_intrabar,
            entry_features=entry_features or signal.features_snapshot,
            confirmation_available=_confirmation_counts(signal.features_snapshot)[0],
            confirmation_passed=_confirmation_counts(signal.features_snapshot)[1],
            confirmations=signal.features_snapshot.get("confirmations", {}),
            pullback_swing_low=pullback_swing_low,
            pullback_swing_high=pullback_swing_high,
            impulse_low=impulse_low,
            impulse_high=impulse_high,
            atr_at_entry=atr_at_entry,
            initial_structural_stop=initial_structural_stop,
            initial_risk_points=initial_risk_points,
            initial_risk_atr=initial_risk_atr,
            current_trailing_stop=current_trailing_stop,
            current_r=current_r,
            highest_favorable_price=highest_favorable_price,
            lowest_favorable_price=lowest_favorable_price,
            peak_r=peak_r,
            protected_breakeven_active=protected_breakeven_active,
            profit_lock_active=profit_lock_active,
            runner_mode_active=runner_mode_active,
            current_ladder_stage=current_ladder_stage,
            reversal_score=reversal_score,
            adverse_health_counters=adverse_health_counters,
            entry_bar_timestamp=self._as_datetime(entry_bar_timestamp),
            last_managed_completed_bar_timestamp=(
                self._as_datetime(last_managed_completed_bar_timestamp)
                if last_managed_completed_bar_timestamp is not None
                else None
            ),
        )
        record.events.append(
            ReplayEvent(
                event="ENTRY",
                timestamp=record.simulated_entry_timestamp,
                reference_price=record.simulated_entry_price,
                active_stop=record.current_trailing_stop,
                r_multiple=0.0,
                source_candle=record.entry_5m_candle_timestamp,
            )
        )
        self._records[signal.signal_id] = record
        return record

    def set_lifecycle_result(
        self,
        signal_id: str,
        *,
        status: str,
        exit_timestamp: datetime | None = None,
        exit_price: float | None = None,
        exit_reason: str | None = None,
        realized_r: float | None = None,
        mfe_r: float | None = None,
        mae_r: float | None = None,
        ambiguous: bool = False,
    ) -> None:
        record = self._records[signal_id]
        record.lifecycle_status = status
        record.exit_timestamp = exit_timestamp
        record.exit_price = exit_price
        record.exit_reason = exit_reason
        record.realized_r = realized_r
        record.mfe_r = mfe_r
        record.mae_r = mae_r
        record.ambiguous = ambiguous

    def record_state_timeline(
        self,
        signal_id: str,
        *,
        before: ReplayStateSnapshot,
        after: ReplayStateSnapshot,
        exit_event: ReplayEvent | None = None,
    ) -> None:
        record = self._records[signal_id]
        record.state_timeline.append({"before": before, "after": after})
        if exit_event is not None:
            record.events.append(exit_event)

    def record_event(self, signal_id: str, event: ReplayEvent) -> None:
        self._records[signal_id].events.append(event)

    def records(self) -> list[ReplayManifestRecord]:
        return list(self._records.values())

    def set_replay_metadata(self, metadata: dict[str, Any]) -> None:
        """Attach immutable run metadata to the next persisted manifest."""
        self._replay_metadata = json.loads(json.dumps(metadata, default=str))

    def replay_metadata(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._replay_metadata, default=str))

    def validate_complete(self, expected_count: int | None = None) -> dict[str, int]:
        records = self.records()
        missing = 0
        for record in records:
            try:
                ReplayManifestRecord.model_validate(record.model_dump())
            except ValidationError:
                missing += 1
        return {
            "records": len(records),
            "missing_mandatory_fields": missing,
            "expected_count_gap": max(0, expected_count - len(records)) if expected_count is not None else 0,
        }

    def persist(self, path: Path, metadata: dict[str, Any] | None = None) -> None:
        """Persist manifest records and, when supplied, their run metadata."""
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [record.model_dump(mode="json") for record in self.records()]
        effective_metadata = metadata if metadata is not None else self._replay_metadata
        payload: Any = records
        if effective_metadata:
            payload = {"metadata": effective_metadata, "records": records}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
