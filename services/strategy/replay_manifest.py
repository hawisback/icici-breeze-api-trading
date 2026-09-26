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
    """Extract available/effective counts from Strategy A or B snapshots."""
    ratio = snapshot.get("confirmation_ratio")
    if isinstance(ratio, str) and "/" in ratio:
        effective, available = ratio.split("/", 1)
        try:
            return int(available), int(effective)
        except ValueError:
            pass

    score = snapshot.get("confirmation_score")
    if isinstance(score, str) and "/" in score:
        passed, available = score.split("/", 1)
        try:
            return int(available), int(passed)
        except ValueError:
            pass

    effective = snapshot.get("effective_confirmation_score", score)
    available = snapshot.get("available_confirmation_count")
    if isinstance(effective, (int, float)) and not isinstance(effective, bool) and isinstance(available, int):
        return available, int(effective)
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
    raw_confirmation_score: int | float | None = None
    effective_confirmation_score: int | float | None = None
    oi_wall_penalty: int | float | None = None
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
    box_high: float | None = None
    box_low: float | None = None
    atr_at_lock: float | None = None
    consecutive_inside_box_closes: int | None = None

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

    # Execution-parity sizing. These fields describe the quantity decision
    # made from historical evidence; they never imply an executable fill.
    sizing_status: str = "NOT_APPLIED"
    sizing_method: str | None = None
    sizing_price_basis: str | None = None
    sizing_account_equity: float | None = None
    sizing_risk_per_trade_pct: float | None = None
    sizing_risk_budget: float | None = None
    sizing_option_loss_per_lot: float | None = None
    sizing_delta_proxy: float | None = None
    sizing_delta_source: str | None = None
    sizing_contract_instrument_id: str | None = None
    sizing_contract_symbol: str | None = None
    sizing_contract_expiry: str | None = None
    sizing_contract_strike: float | None = None
    sizing_contract_lot_size: int | None = None
    sizing_entry_reference_price: float | None = None
    sizing_entry_mark: float | None = None
    replay_lots: int | None = None
    replay_quantity: int | None = None
    sizing_rejection_reason: str | None = None

    # Contract selection evidence. Exact production-rule parity is claimed only
    # when an exact point-in-time chain snapshot supported the decision.
    contract_selection_desired_method: str | None = None
    contract_selection_actual_method: str | None = None
    contract_selection_evidence_status: str | None = None
    contract_selection_production_rules_applied: bool = False
    contract_selection_snapshot_id: str | None = None
    contract_selection_snapshot_timestamp: datetime | None = None
    contract_selection_unsupported_evidence: list[str] = Field(default_factory=list)
    contract_selection_rejection_reason: str | None = None
    contract_selection_provenance: dict[str, Any] = Field(default_factory=dict)

    # Historical marks remain separate from estimated executable fills.
    simulated_entry_fill_price: float | None = None
    simulated_exit_fill_price: float | None = None
    simulated_entry_fill_method: str | None = None
    simulated_exit_fill_method: str | None = None
    simulated_entry_fill_basis: str | None = None
    simulated_exit_fill_basis: str | None = None
    simulated_fill_quote_equivalent: bool = False
    simulated_gross_pnl: float | None = None
    simulated_slippage_cost: float | None = None
    simulated_transaction_costs: float | None = None
    simulated_net_pnl: float | None = None
    simulated_cost_breakdown: dict[str, Any] = Field(default_factory=dict)
    simulated_execution_provenance: dict[str, Any] = Field(default_factory=dict)

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
        box_high: float | None = None,
        box_low: float | None = None,
        atr_at_lock: float | None = None,
        consecutive_inside_box_closes: int | None = None,
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
            raw_confirmation_score=signal.features_snapshot.get("raw_confirmation_score"),
            effective_confirmation_score=signal.features_snapshot.get("effective_confirmation_score"),
            oi_wall_penalty=signal.features_snapshot.get("oi_wall_penalty"),
            confirmations=(
                signal.features_snapshot.get("confirmations")
                or signal.features_snapshot.get("confirmation_factors", {})
            ),
            pullback_swing_low=pullback_swing_low,
            pullback_swing_high=pullback_swing_high,
            impulse_low=impulse_low,
            impulse_high=impulse_high,
            atr_at_entry=atr_at_entry,
            initial_structural_stop=initial_structural_stop,
            initial_risk_points=initial_risk_points,
            initial_risk_atr=initial_risk_atr,
            box_high=box_high,
            box_low=box_low,
            atr_at_lock=atr_at_lock,
            consecutive_inside_box_closes=consecutive_inside_box_closes,
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

    def set_contract_selection_result(
        self,
        signal_id: str,
        *,
        desired_method: str,
        actual_method: str,
        evidence_status: str,
        production_rules_applied: bool,
        snapshot_id: str | None,
        snapshot_timestamp: datetime | None,
        unsupported_evidence: list[str],
        rejection_reason: str | None,
        provenance: dict[str, Any],
    ) -> ReplayManifestRecord:
        record = self._records[signal_id]
        record.contract_selection_desired_method = desired_method
        record.contract_selection_actual_method = actual_method
        record.contract_selection_evidence_status = evidence_status
        record.contract_selection_production_rules_applied = (
            production_rules_applied
        )
        record.contract_selection_snapshot_id = snapshot_id
        record.contract_selection_snapshot_timestamp = snapshot_timestamp
        record.contract_selection_unsupported_evidence = list(
            unsupported_evidence
        )
        record.contract_selection_rejection_reason = rejection_reason
        record.contract_selection_provenance = dict(provenance)
        return record

    def set_execution_estimate(
        self,
        signal_id: str,
        *,
        entry_fill_price: float | None,
        exit_fill_price: float | None,
        entry_method: str | None,
        exit_method: str | None,
        entry_basis: str | None,
        exit_basis: str | None,
        quote_equivalent: bool,
        gross_pnl: float | None,
        slippage_cost: float | None,
        transaction_costs: float | None,
        net_pnl: float | None,
        cost_breakdown: dict[str, Any],
        provenance: dict[str, Any],
    ) -> ReplayManifestRecord:
        record = self._records[signal_id]
        record.simulated_entry_fill_price = entry_fill_price
        record.simulated_exit_fill_price = exit_fill_price
        record.simulated_entry_fill_method = entry_method
        record.simulated_exit_fill_method = exit_method
        record.simulated_entry_fill_basis = entry_basis
        record.simulated_exit_fill_basis = exit_basis
        record.simulated_fill_quote_equivalent = quote_equivalent
        record.simulated_gross_pnl = gross_pnl
        record.simulated_slippage_cost = slippage_cost
        record.simulated_transaction_costs = transaction_costs
        record.simulated_net_pnl = net_pnl
        record.simulated_cost_breakdown = dict(cost_breakdown)
        record.simulated_execution_provenance = dict(provenance)
        return record

    def set_sizing_result(
        self,
        signal_id: str,
        *,
        status: str,
        method: str | None,
        price_basis: str | None,
        account_equity: float | None,
        risk_per_trade_pct: float | None,
        risk_budget: float | None,
        option_loss_per_lot: float | None,
        delta_proxy: float | None,
        delta_source: str | None,
        contract_instrument_id: str | None,
        contract_symbol: str | None,
        contract_expiry: str | None,
        contract_strike: float | None,
        contract_lot_size: int | None,
        entry_reference_price: float | None,
        entry_mark: float | None,
        lots: int | None,
        quantity: int | None,
        rejection_reason: str | None = None,
    ) -> ReplayManifestRecord:
        record = self._records[signal_id]
        record.sizing_status = status
        record.sizing_method = method
        record.sizing_price_basis = price_basis
        record.sizing_account_equity = account_equity
        record.sizing_risk_per_trade_pct = risk_per_trade_pct
        record.sizing_risk_budget = risk_budget
        record.sizing_option_loss_per_lot = option_loss_per_lot
        record.sizing_delta_proxy = delta_proxy
        record.sizing_delta_source = delta_source
        record.sizing_contract_instrument_id = contract_instrument_id
        record.sizing_contract_symbol = contract_symbol
        record.sizing_contract_expiry = contract_expiry
        record.sizing_contract_strike = contract_strike
        record.sizing_contract_lot_size = contract_lot_size
        record.sizing_entry_reference_price = entry_reference_price
        record.sizing_entry_mark = entry_mark
        record.replay_lots = lots
        record.replay_quantity = quantity
        record.sizing_rejection_reason = rejection_reason
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
