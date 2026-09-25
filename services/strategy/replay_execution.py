"""Chronological execution-parity state for historical Day Replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from libs.contracts.models import Candle
from services.strategy.models import (
    RiskConfig,
    StrategyName,
    StrategySignal,
    TradeDirection,
)
from services.strategy.replay_lifecycle import (
    HistoricalManagedReplayPosition,
    HistoricalPositionManagerReplayer,
)
from services.strategy.replay_manifest import ReplayManifestRecord
from services.strategy.replay_registry import (
    ReplayBarContext,
    ReplayStrategyRegistry,
)
from services.strategy.risk_gates import (
    RiskGateDecision,
    check_daily_loss_limits,
    check_daily_trade_limit,
    check_loss_cooldown,
    check_position_capacity,
    check_strategy_trade_limits,
)
from services.strategy.replay_contract_selection import (
    ReplayContractSelectionDecision,
)
from services.strategy.replay_sizing import ReplaySizingDecision


@dataclass
class ChronologicalExecutionState:
    active_positions: list[HistoricalManagedReplayPosition] = field(
        default_factory=list
    )
    daily_entries: int = 0
    daily_entries_by_strategy: dict[str, int] = field(default_factory=dict)
    failed_entries_by_strategy: dict[str, int] = field(default_factory=dict)
    completed_positions: int = 0
    realized_r_total: float = 0.0
    realized_net_pnl_total: float = 0.0
    realized_net_pnl_complete: bool = True
    entry_evaluation_suppressed_cycles: int = 0
    entry_gate_block_counts: dict[str, int] = field(default_factory=dict)
    last_loss_exit_time: datetime | None = None
    loss_cooldown_until: datetime | None = None
    chronology_indeterminate: bool = False
    chronology_block_reason: str | None = None
    positions_open_at_session_end: int = 0
    rejected_opportunities: list[dict[str, Any]] = field(default_factory=list)


class ChronologicalReplayExecutor:
    """Advance accepted replay positions in the same time direction as signals.

    Numeric production risk gates are evaluated from the same shared helpers
    as the live service. Loss cooldown and failure counts use underlying
    realized R; the daily percentage-loss gate additionally uses chronological
    estimated executable net P&L when all prior resolved trades are priced.
    """

    def __init__(
        self,
        *,
        lifecycle_replayer: HistoricalPositionManagerReplayer,
        registry: ReplayStrategyRegistry,
        risk_config: RiskConfig,
    ) -> None:
        self.lifecycle_replayer = lifecycle_replayer
        self.registry = registry
        self.risk_config = risk_config
        self.state = ChronologicalExecutionState()

    @staticmethod
    def _record_direction(record: ReplayManifestRecord) -> TradeDirection:
        return (
            TradeDirection.BULLISH
            if record.direction == "CALL"
            else TradeDirection.BEARISH
        )

    def _mark_indeterminate(self, record: ReplayManifestRecord) -> None:
        self.state.chronology_indeterminate = True
        self.state.chronology_block_reason = (
            f"{record.replay_signal_id}:{record.lifecycle_status}:"
            f"{record.exit_reason or 'UNKNOWN'}"
        )

    def manage_completed_bar(
        self,
        bar: Candle,
        running: list[Candle],
    ) -> list[ReplayManifestRecord]:
        """Manage all positions that were already open before this bar."""
        closed: list[ReplayManifestRecord] = []
        survivors: list[HistoricalManagedReplayPosition] = []
        for position in self.state.active_positions:
            active = self.lifecycle_replayer.advance_record(
                position,
                bar,
                running,
            )
            if active:
                survivors.append(position)
                continue

            record = position.record
            closed.append(record)
            if record.lifecycle_status == "RESOLVED":
                self.state.completed_positions += 1
                exit_time = record.exit_timestamp or bar.end_time
                self.registry.notify_exit(
                    StrategyName(record.strategy_id),
                    self._record_direction(record),
                    exit_time,
                )
                if record.realized_r is not None:
                    self.state.realized_r_total += float(record.realized_r)
                if (
                    record.realized_r is not None
                    and float(record.realized_r) < 0
                ):
                    self.state.last_loss_exit_time = exit_time
                    self.state.loss_cooldown_until = exit_time + timedelta(
                        minutes=self.risk_config.cooldown_after_loss_min
                    )
                    self.state.failed_entries_by_strategy[record.strategy_id] = (
                        self.state.failed_entries_by_strategy.get(
                            record.strategy_id,
                            0,
                        )
                        + 1
                    )
            elif record.lifecycle_status in {"AMBIGUOUS", "UNRESOLVED"}:
                # Once we cannot know whether exposure remains, taking another
                # trade would be optimistic. Block new entries for the session.
                self._mark_indeterminate(record)

        self.state.active_positions = survivors
        return closed

    def global_entry_gate(self, at: datetime) -> RiskGateDecision:
        if self.state.chronology_indeterminate:
            return RiskGateDecision(
                False,
                "CHRONOLOGY_INDETERMINATE",
                {"reason": self.state.chronology_block_reason},
            )
        for decision in (
            check_position_capacity(
                self.risk_config,
                active_count=len(self.state.active_positions),
            ),
            check_loss_cooldown(
                self.risk_config,
                at=at,
                last_loss_exit_time=self.state.last_loss_exit_time,
            ),
            check_daily_loss_limits(
                self.risk_config,
                realized_r_total=self.state.realized_r_total,
                net_pnl_total=(
                    self.state.realized_net_pnl_total
                    if self.state.realized_net_pnl_complete
                    else None
                ),
                account_equity=self.risk_config.account_equity,
            ),
            check_daily_trade_limit(
                self.risk_config,
                daily_count=self.state.daily_entries,
            ),
        ):
            if not decision.allowed:
                return decision
        return RiskGateDecision(True)

    def strategy_entry_gate(
        self,
        signal: StrategySignal,
    ) -> RiskGateDecision:
        strategy_id = signal.strategy.value
        return check_strategy_trade_limits(
            self.risk_config,
            strategy_trade_count=self.state.daily_entries_by_strategy.get(
                strategy_id,
                0,
            ),
            strategy_failure_count=self.state.failed_entries_by_strategy.get(
                strategy_id,
                0,
            ),
        )

    def can_accept_entry(self, at: datetime | None = None) -> bool:
        if at is None:
            return bool(
                not self.state.chronology_indeterminate
                and len(self.state.active_positions)
                < self.risk_config.max_concurrent_positions
            )
        return self.global_entry_gate(at).allowed

    def record_rejection(
        self,
        *,
        at: datetime,
        status: str,
        strategy: StrategyName | None = None,
        signal_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.state.rejected_opportunities.append({
            "timestamp": at.isoformat(),
            "status": status,
            "strategy": strategy.value if strategy is not None else None,
            "signal_id": signal_id,
            "details": details or {},
        })

    def note_entry_evaluation_suppressed(
        self,
        status: str | None = None,
    ) -> None:
        self.state.entry_evaluation_suppressed_cycles += 1
        if status:
            self.state.entry_gate_block_counts[status] = (
                self.state.entry_gate_block_counts.get(status, 0) + 1
            )

    def accept_signal(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
        *,
        sizing: ReplaySizingDecision,
        selection: ReplayContractSelectionDecision | None = None,
    ) -> ReplayManifestRecord:
        """Confirm one production-priority signal and make it active."""
        global_gate = self.global_entry_gate(context.bar.end_time)
        if not global_gate.allowed:
            raise RuntimeError(
                f"chronological replay entry blocked: {global_gate.status}"
            )
        strategy_gate = self.strategy_entry_gate(signal)
        if not strategy_gate.allowed:
            raise RuntimeError(
                f"chronological replay strategy entry blocked: {strategy_gate.status}"
            )
        if sizing.status != "APPLIED" or not sizing.lots or not sizing.quantity:
            raise RuntimeError(
                "chronological replay cannot accept a sizing-rejected signal"
            )

        record = self.registry.confirm_entry(signal, context)
        if selection is not None:
            selection_meta = selection.to_manifest_metadata()
            context.session.recorder.set_contract_selection_result(
                record.replay_signal_id,
                desired_method=selection_meta["desired_method"],
                actual_method=selection_meta["actual_method"],
                evidence_status=selection_meta["evidence_status"],
                production_rules_applied=selection_meta[
                    "production_rules_applied"
                ],
                snapshot_id=selection_meta["snapshot_id"],
                snapshot_timestamp=selection.snapshot_timestamp,
                unsupported_evidence=selection_meta[
                    "unsupported_evidence"
                ],
                rejection_reason=selection_meta["rejection_reason"],
                provenance=selection_meta["provenance"],
            )
        context.session.recorder.set_sizing_result(
            record.replay_signal_id,
            **sizing.to_manifest_kwargs(),
        )
        position = self.lifecycle_replayer.start_record(record)
        if position is not None:
            self.state.active_positions.append(position)
            self.state.daily_entries += 1
            self.state.daily_entries_by_strategy[record.strategy_id] = (
                self.state.daily_entries_by_strategy.get(record.strategy_id, 0) + 1
            )
            return record

        # Intrabar entry resolution may determine that no fill occurred, or it
        # may be unknowable. Only the latter blocks future entries.
        if record.lifecycle_status in {"AMBIGUOUS", "UNRESOLVED"}:
            self._mark_indeterminate(record)
        elif record.lifecycle_status == "RESOLVED":
            self.state.daily_entries += 1
            self.state.daily_entries_by_strategy[record.strategy_id] = (
                self.state.daily_entries_by_strategy.get(record.strategy_id, 0) + 1
            )
            self.state.completed_positions += 1
            exit_time = record.exit_timestamp or context.bar.end_time
            self.registry.notify_exit(
                StrategyName(record.strategy_id),
                self._record_direction(record),
                exit_time,
            )
            if record.realized_r is not None:
                self.state.realized_r_total += float(record.realized_r)
            if (
                record.realized_r is not None
                and float(record.realized_r) < 0
            ):
                self.state.last_loss_exit_time = exit_time
                self.state.loss_cooldown_until = exit_time + timedelta(
                    minutes=self.risk_config.cooldown_after_loss_min
                )
                self.state.failed_entries_by_strategy[record.strategy_id] = (
                    self.state.failed_entries_by_strategy.get(
                        record.strategy_id,
                        0,
                    )
                    + 1
                )
        return record

    def apply_execution_economics(
        self,
        record: ReplayManifestRecord,
    ) -> None:
        if record.lifecycle_status != "RESOLVED":
            return
        if record.simulated_net_pnl is None:
            self.state.realized_net_pnl_complete = False
            return
        self.state.realized_net_pnl_total += float(record.simulated_net_pnl)

    def finalize_session(self) -> None:
        self.state.positions_open_at_session_end = len(
            self.state.active_positions
        )
        for position in self.state.active_positions:
            self.lifecycle_replayer.finalize_record(position)
            if position.record.lifecycle_status in {"AMBIGUOUS", "UNRESOLVED"}:
                self._mark_indeterminate(position.record)
        self.state.active_positions = []

    def metadata(self) -> dict[str, Any]:
        return {
            "daily_entries": self.state.daily_entries,
            "daily_entries_by_strategy": dict(
                sorted(self.state.daily_entries_by_strategy.items())
            ),
            "failed_entries_by_strategy": dict(
                sorted(self.state.failed_entries_by_strategy.items())
            ),
            "completed_positions": self.state.completed_positions,
            "realized_r_total": round(self.state.realized_r_total, 4),
            "realized_net_pnl_total": (
                round(self.state.realized_net_pnl_total, 2)
                if self.state.realized_net_pnl_complete
                else None
            ),
            "realized_net_pnl_complete": (
                self.state.realized_net_pnl_complete
            ),
            "active_positions_at_end": self.state.positions_open_at_session_end,
            "entry_evaluation_suppressed_cycles": (
                self.state.entry_evaluation_suppressed_cycles
            ),
            "entry_gate_block_counts": dict(
                sorted(self.state.entry_gate_block_counts.items())
            ),
            "last_loss_exit_time": (
                self.state.last_loss_exit_time.isoformat()
                if self.state.last_loss_exit_time
                else None
            ),
            "loss_cooldown_until": (
                self.state.loss_cooldown_until.isoformat()
                if self.state.loss_cooldown_until
                else None
            ),
            "chronology_indeterminate": self.state.chronology_indeterminate,
            "chronology_block_reason": self.state.chronology_block_reason,
            "max_concurrent_positions": self.risk_config.max_concurrent_positions,
            "risk_gate_basis": {
                "daily_loss": (
                    "UNDERLYING_REALIZED_R_AND_ESTIMATED_EXECUTABLE_NET_PNL"
                ),
                "loss_cooldown": "UNDERLYING_REALIZED_R_NEGATIVE_EXIT",
                "daily_trade_count": "ACCEPTED_REPLAY_ENTRIES",
                "strategy_failure_count": "UNDERLYING_REALIZED_R_NEGATIVE_EXIT",
            },
            "rejected_opportunities": list(self.state.rejected_opportunities),
        }
