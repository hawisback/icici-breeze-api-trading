"""Time-correct forward option execution validation for Strategy A."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict

from services.strategy.contract_selector import ContractSelector
from services.strategy.models import OptionSelectionConfig, RiskConfig, StrategySignal
from services.strategy.position_manager import UnderlyingRiskSizer


OptionValidationState = Literal[
    "UNDERLYING_VALID",
    "OPTION_CONTRACT_FOUND",
    "OPTION_EXECUTABLE",
    "OPTION_OUTCOME_OBSERVABLE",
]


class OptionValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_id: str
    state: OptionValidationState
    state_history: tuple[OptionValidationState, ...] = ()
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
    def __init__(self, config: OptionSelectionConfig | None = None, risk_config: RiskConfig | None = None) -> None:
        self.selector = ContractSelector(config)
        self.risk_sizer = UnderlyingRiskSizer(risk_config)

    def validate(
        self,
        signals: Iterable[StrategySignal],
        chains_by_timestamp: dict[str, dict[str, Any]],
        *,
        option_outcomes_by_signal: dict[str, bool] | None = None,
    ) -> OptionExecutionValidationReport:
        rows: list[OptionValidationResult] = []
        counts = Counter()
        option_outcomes_by_signal = option_outcomes_by_signal or {}
        for signal in signals:
            counts["total"] += 1
            snapshot = chains_by_timestamp.get(signal.timestamp.isoformat())
            if snapshot is None:
                rows.append(OptionValidationResult(signal_id=signal.signal_id, state="UNDERLYING_VALID", state_history=("UNDERLYING_VALID",), reason="NO_CHAIN_COVERAGE")); continue
            counts["coverage"] += 1
            selected, inspected, reason = self.selector.select_contract(signal.direction, underlying_price=signal.spot_reference_price, option_chain=snapshot, as_of=signal.timestamp, strategy_a=True)
            if selected is None:
                if reason and "EXPIRY" in reason: counts["expiry"] += 1
                elif reason and "DELTA" in reason: counts["delta"] += 1
                elif reason and "STALE" in reason: counts["stale"] += 1
                else: counts["spread"] += 1
                rows.append(OptionValidationResult(signal_id=signal.signal_id, state="UNDERLYING_VALID", state_history=("UNDERLYING_VALID",), reason=reason)); continue
            counts["eligible"] += 1
            history: list[OptionValidationState] = ["UNDERLYING_VALID", "OPTION_CONTRACT_FOUND"]
            try:
                sizing = self.risk_sizer.size(
                    underlying_entry=signal.spot_reference_price,
                    underlying_stop=signal.structural_stop,
                    option_delta=selected.delta,
                    lot_size=selected.lot_size,
                    option_entry=selected.ask_price,
                )
            except ValueError as exc:
                counts["sizing"] += 1
                rows.append(OptionValidationResult(signal_id=signal.signal_id, state="OPTION_CONTRACT_FOUND", state_history=tuple(history), selected_instrument_id=selected.instrument_id, reason=str(exc)))
                continue
            if sizing.lots < 1:
                counts["sizing"] += 1
                rows.append(OptionValidationResult(signal_id=signal.signal_id, state="OPTION_CONTRACT_FOUND", state_history=tuple(history), selected_instrument_id=selected.instrument_id, reason=sizing.rejection_reason or "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"))
                continue
            history.append("OPTION_EXECUTABLE")
            observed = bool(option_outcomes_by_signal.get(signal.signal_id, False))
            if observed:
                history.append("OPTION_OUTCOME_OBSERVABLE")
            rows.append(OptionValidationResult(signal_id=signal.signal_id, state=history[-1], state_history=tuple(history), selected_instrument_id=selected.instrument_id, observed_outcome=observed))
        executable = sum(row.state in ("OPTION_EXECUTABLE", "OPTION_OUTCOME_OBSERVABLE") for row in rows)
        return OptionExecutionValidationReport(
            total_underlying_signals=counts["total"], signals_inside_usable_chain_coverage=counts["coverage"], eligible_contract_count=counts["eligible"], expiry_rejection_count=counts["expiry"], delta_rejection_count=counts["delta"], stale_quote_count=counts["stale"], spread_liquidity_rejection_count=counts["spread"], sizing_rejection_count=counts["sizing"], executable_coverage=executable / counts["total"] if counts["total"] else 0.0, observable_option_outcomes=sum(row.observed_outcome for row in rows), results=rows, limitations=["Forward validation never backfills a future chain snapshot into signal time.", "EOD-only option data is not treated as an executable intraday quote.", "This report does not tune Strategy A parameters."],
        )
