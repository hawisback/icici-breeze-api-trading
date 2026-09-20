"""Time-correct forward option execution validation for Strategy A."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict

from services.strategy.contract_selector import ContractSelector
from services.strategy.models import OptionSelectionConfig, StrategySignal


class OptionValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_id: str
    state: str
    reason: str | None = None
    selected_instrument_id: str | None = None
    observed_outcome: bool = False


class OptionExecutionValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_underlying_signals: int
    signals_inside_usable_chain_coverage: int
    eligible_contract_count: int
    expiry_rejection_count: int
    delta_rejection_count: int
    stale_quote_count: int
    spread_liquidity_rejection_count: int
    sizing_rejection_count: int
    executable_coverage: float
    observable_option_outcomes: int
    results: list[OptionValidationResult]
    limitations: list[str]


class ForwardOptionExecutionValidator:
    def __init__(self, config: OptionSelectionConfig | None = None) -> None:
        self.selector = ContractSelector(config)

    def validate(self, signals: Iterable[StrategySignal], chains_by_timestamp: dict[str, dict[str, Any]]) -> OptionExecutionValidationReport:
        rows: list[OptionValidationResult] = []
        counts = Counter()
        for signal in signals:
            counts["total"] += 1
            snapshot = chains_by_timestamp.get(signal.timestamp.isoformat())
            if snapshot is None:
                rows.append(OptionValidationResult(signal_id=signal.signal_id, state="UNDERLYING_VALID", reason="NO_CHAIN_COVERAGE")); continue
            counts["coverage"] += 1
            selected, inspected, reason = self.selector.select_contract(signal.direction, underlying_price=signal.spot_reference_price, option_chain=snapshot, as_of=signal.timestamp, strategy_a=True)
            if selected is None:
                if reason and "EXPIRY" in reason: counts["expiry"] += 1
                elif reason and "DELTA" in reason: counts["delta"] += 1
                elif reason and "STALE" in reason: counts["stale"] += 1
                else: counts["spread"] += 1
                rows.append(OptionValidationResult(signal_id=signal.signal_id, state="UNDERLYING_VALID", reason=reason)); continue
            counts["eligible"] += 1
            rows.append(OptionValidationResult(signal_id=signal.signal_id, state="OPTION_EXECUTABLE", selected_instrument_id=selected.instrument_id))
        executable = counts["eligible"]
        return OptionExecutionValidationReport(
            total_underlying_signals=counts["total"], signals_inside_usable_chain_coverage=counts["coverage"], eligible_contract_count=counts["eligible"], expiry_rejection_count=counts["expiry"], delta_rejection_count=counts["delta"], stale_quote_count=counts["stale"], spread_liquidity_rejection_count=counts["spread"], sizing_rejection_count=counts["sizing"], executable_coverage=executable / counts["total"] if counts["total"] else 0.0, observable_option_outcomes=sum(row.observed_outcome for row in rows), results=rows, limitations=["Forward validation never backfills a future chain snapshot into signal time.", "EOD-only option data is not treated as an executable intraday quote.", "This report does not tune Strategy A parameters."],
        )
