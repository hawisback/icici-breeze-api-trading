"""Chronological execution-parity state for historical Day Replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass
class ChronologicalExecutionState:
    active_positions: list[HistoricalManagedReplayPosition] = field(
        default_factory=list
    )
    daily_entries: int = 0
    completed_positions: int = 0
    entry_evaluation_suppressed_cycles: int = 0
    last_loss_exit_time: datetime | None = None
    chronology_indeterminate: bool = False
    chronology_block_reason: str | None = None


class ChronologicalReplayExecutor:
    """Advance accepted replay positions in the same time direction as signals.

    This class intentionally enforces only the active-position capacity needed
    for chronological execution parity. Daily loss/trade limits, cooldown
    gates, sizing, and capital/risk budgets are wired in the next increment.
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
                if (
                    record.realized_r is not None
                    and float(record.realized_r) < 0
                ):
                    self.state.last_loss_exit_time = exit_time
            elif record.lifecycle_status in {"AMBIGUOUS", "UNRESOLVED"}:
                # Once we cannot know whether exposure remains, taking another
                # trade would be optimistic. Block new entries for the session.
                self._mark_indeterminate(record)

        self.state.active_positions = survivors
        return closed

    def can_accept_entry(self) -> bool:
        return bool(
            not self.state.chronology_indeterminate
            and len(self.state.active_positions)
            < self.risk_config.max_concurrent_positions
        )

    def note_entry_evaluation_suppressed(self) -> None:
        self.state.entry_evaluation_suppressed_cycles += 1

    def accept_signal(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        """Confirm one production-priority signal and make it active."""
        if not self.can_accept_entry():
            raise RuntimeError("chronological replay cannot accept new exposure")

        record = self.registry.confirm_entry(signal, context)
        position = self.lifecycle_replayer.start_record(record)
        if position is not None:
            self.state.active_positions.append(position)
            self.state.daily_entries += 1
            return record

        # Intrabar entry resolution may determine that no fill occurred, or it
        # may be unknowable. Only the latter blocks future entries.
        if record.lifecycle_status in {"AMBIGUOUS", "UNRESOLVED"}:
            self._mark_indeterminate(record)
        elif record.lifecycle_status == "RESOLVED":
            self.state.daily_entries += 1
            self.state.completed_positions += 1
        return record

    def finalize_session(self) -> None:
        for position in self.state.active_positions:
            self.lifecycle_replayer.finalize_record(position)
            if position.record.lifecycle_status in {"AMBIGUOUS", "UNRESOLVED"}:
                self._mark_indeterminate(position.record)
        self.state.active_positions = []

    def metadata(self) -> dict[str, Any]:
        return {
            "daily_entries": self.state.daily_entries,
            "completed_positions": self.state.completed_positions,
            "active_positions_at_end": len(self.state.active_positions),
            "entry_evaluation_suppressed_cycles": (
                self.state.entry_evaluation_suppressed_cycles
            ),
            "last_loss_exit_time": (
                self.state.last_loss_exit_time.isoformat()
                if self.state.last_loss_exit_time
                else None
            ),
            "chronology_indeterminate": self.state.chronology_indeterminate,
            "chronology_block_reason": self.state.chronology_block_reason,
            "max_concurrent_positions": self.risk_config.max_concurrent_positions,
        }
