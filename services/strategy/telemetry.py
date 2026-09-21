"""Structured Strategy A paper/shadow telemetry and replay comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field


class StrategyAEvaluationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: str
    futures_contract: str
    completed_candle_timestamp: str
    ema20: float | None = None
    ema50: float | None = None
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    atr: float | None = None
    vwap: float | None = None
    active_support: float | None = None
    active_resistance: float | None = None
    trend_result: str | None = None
    confluence_result: bool | None = None
    confirmation_result: bool | None = None
    trigger: float | None = None
    structural_stop: float | None = None
    underlying_r: float | None = None
    room_to_opposing_sr: float | None = None
    strategy_state: str
    rejection_or_invalidation_reason: str | None = None
    option_contract: str | None = None
    expiry: str | None = None
    quote_timestamp: str | None = None
    quote_freshness_seconds: float | None = None
    delta: float | None = None
    gamma: float | None = None
    delta_source: str = "UNAVAILABLE"
    gamma_source: str = "UNAVAILABLE"
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    position_size: int | None = None
    lot_size: int | None = None
    lots: int | None = None
    risk_budget: float | None = None
    estimated_option_loss_at_structural_stop: float | None = None
    entry_fill: float | None = None
    option_entry_fill: float | None = None
    slippage_points: float | None = None
    partial_exit_quantity: int | None = None
    final_exit_quantity: int | None = None
    transaction_costs: float | None = None
    execution_order_count: int | None = None
    management_event: str | None = None
    exit_reason: str | None = None
    realized_r: float | None = None
    option_pnl: float | None = None


class StrategyATelemetryStore:
    def __init__(self) -> None:
        self.records: list[StrategyAEvaluationRecord] = []

    def append(self, record: StrategyAEvaluationRecord) -> None:
        self.records.append(record)

    def summary(self) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for record in self.records:
            if record.rejection_or_invalidation_reason:
                reasons[record.rejection_or_invalidation_reason] = reasons.get(record.rejection_or_invalidation_reason, 0) + 1
        events = [record.management_event or "" for record in self.records]
        sessions = {record.timestamp[:10] for record in self.records if record.timestamp}
        evaluations = sum(event in ("EVALUATED", "REJECTED", "SETUP_CREATED") or event == "" for event in events)
        setups = sum(event == "SETUP_CREATED" for event in events)
        armed = sum(event == "ARMED" for event in events)
        triggers = sum(event in ("TRIGGERED", "ENTERED") for event in events)
        expired = sum(event == "EXPIRED" for event in events)
        selection_attempts = sum(event in ("CONTRACT_SELECTED", "CONTRACT_SELECTION_REJECTED") for event in events)
        selection_successes = sum(event == "CONTRACT_SELECTED" for event in events)
        stale = sum("STALE" in (record.rejection_or_invalidation_reason or "").upper() or event == "STALE_QUOTE_REJECTED" for record, event in zip(self.records, events))
        spread = sum("SPREAD" in (record.rejection_or_invalidation_reason or "").upper() or "LIQUIDITY" in (record.rejection_or_invalidation_reason or "").upper() for record in self.records)
        sizing = sum(event in ("SIZING_REJECTED",) for event in events)
        entries = sum(event in ("ENTRY_OPENED", "ENTERED") for event in events)
        partials = sum(event == "PARTIAL_EXIT" for event in events)
        forced = sum(event == "FORCED_EXIT" or record.exit_reason == "SESSION_FORCE_SQUARE_OFF_1515" for record, event in zip(self.records, events))
        structural = sum(event == "CLOSED" and "STRUCTURAL" in (record.exit_reason or "") for record, event in zip(self.records, events))
        trailing = sum(event == "CLOSED" and "TRAILING" in (record.exit_reason or "") for record, event in zip(self.records, events))
        realized = [record.realized_r for record in self.records if record.realized_r is not None]
        option_pnl = [record.option_pnl for record in self.records if record.option_pnl is not None]
        slippages = [record.slippage_points for record in self.records if record.slippage_points is not None]
        summary = {
            "sessions": len(sessions), "evaluations": evaluations, "setups": setups,
            "setups_per_session": setups / len(sessions) if sessions else 0.0,
            "armed_count": armed, "trigger_count": triggers,
            "trigger_frequency": triggers / evaluations if evaluations else 0.0,
            "expired_setup_count": expired, "rejection_distribution": dict(sorted(reasons.items())),
            "option_selection_attempts": selection_attempts, "option_selection_successes": selection_successes,
            "option_selection_success_rate": selection_successes / selection_attempts if selection_attempts else 0.0,
            "stale_quote_rejections": stale, "stale_quote_rate": stale / selection_attempts if selection_attempts else 0.0,
            "spread_liquidity_rejections": spread, "spread_liquidity_rejection_rate": spread / selection_attempts if selection_attempts else 0.0,
            "sizing_rejections": sizing, "sizing_rejection_rate": sizing / selection_attempts if selection_attempts else 0.0,
            "entries": entries, "partial_exits": partials, "forced_exits": forced,
            "structural_stop_exits": structural, "trailing_stop_exits": trailing,
            "realized_r_values": realized, "average_realized_r": sum(realized) / len(realized) if realized else 0.0,
            "option_pnl_total": sum(option_pnl) if option_pnl else None,
            "average_slippage": sum(slippages) / len(slippages) if slippages else 0.0,
            "runtime_replay_discrepancy_count": sum(event == "RUNTIME_REPLAY_DISCREPANCY" for event in events),
        }
        # Preserve the old key as a compatibility alias while making the
        # denominator explicitly selection attempts rather than setups.
        summary["stale_chain_rate"] = summary["stale_quote_rate"]
        return summary

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row.model_dump(mode="json"), sort_keys=True) for row in self.records) + ("\n" if self.records else ""), encoding="utf-8")


def compare_telemetry_to_replay(runtime: Iterable[StrategyAEvaluationRecord], replay: Iterable[StrategyAEvaluationRecord]) -> list[dict[str, Any]]:
    def identity(row: StrategyAEvaluationRecord) -> tuple[str, str, str]:
        return row.timestamp, row.futures_contract, row.completed_candle_timestamp
    left = {identity(row): row for row in runtime}
    right = {identity(row): row for row in replay}
    differences: list[dict[str, Any]] = []
    fields = ("strategy_state", "management_event", "futures_contract", "trigger", "entry_fill", "structural_stop", "underlying_r", "rejection_or_invalidation_reason", "option_contract", "exit_reason")
    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        if a is None or b is None:
            differences.append({"identity": key, "runtime_present": a is not None, "replay_present": b is not None})
            continue
        changed = {field: (getattr(a, field), getattr(b, field)) for field in fields if getattr(a, field) != getattr(b, field)}
        if changed:
            differences.append({"identity": key, "fields": changed})
    return differences
