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
    delta: float | None = None
    gamma: float | None = None
    delta_source: str = "UNAVAILABLE"
    gamma_source: str = "UNAVAILABLE"
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    position_size: int | None = None
    entry_fill: float | None = None
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
        setups = entries = 0
        for record in self.records:
            if record.rejection_or_invalidation_reason:
                reasons[record.rejection_or_invalidation_reason] = reasons.get(record.rejection_or_invalidation_reason, 0) + 1
            if record.management_event == "SETUP_CREATED": setups += 1
            if record.management_event == "ENTERED": entries += 1
        return {"evaluations": len(self.records), "setups": setups, "entries": entries, "rejection_distribution": dict(sorted(reasons.items())), "option_selection_success_rate": (entries / setups if setups else 0.0), "stale_chain_rate": sum(r.rejection_or_invalidation_reason == "STALE_OPTION_QUOTE" for r in self.records) / len(self.records) if self.records else 0.0}

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row.model_dump(mode="json"), sort_keys=True) for row in self.records) + ("\n" if self.records else ""), encoding="utf-8")


def compare_telemetry_to_replay(runtime: Iterable[StrategyAEvaluationRecord], replay: Iterable[StrategyAEvaluationRecord]) -> list[dict[str, Any]]:
    def identity(row: StrategyAEvaluationRecord) -> tuple[str, str, str]:
        return row.timestamp, row.futures_contract, row.completed_candle_timestamp
    left = {identity(row): row for row in runtime}
    right = {identity(row): row for row in replay}
    differences: list[dict[str, Any]] = []
    fields = ("strategy_state", "trigger", "structural_stop", "underlying_r", "rejection_or_invalidation_reason", "option_contract")
    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        if a is None or b is None:
            differences.append({"identity": key, "runtime_present": a is not None, "replay_present": b is not None})
            continue
        changed = {field: (getattr(a, field), getattr(b, field)) for field in fields if getattr(a, field) != getattr(b, field)}
        if changed:
            differences.append({"identity": key, "fields": changed})
    return differences
