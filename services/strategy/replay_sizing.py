"""Historical replay sizing that reuses production sizing algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.strategy.models import (
    OptionSelectionConfig,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
)
from services.strategy.position_manager import PositionManager, UnderlyingRiskSizer


@dataclass(frozen=True)
class ReplaySizingDecision:
    status: str
    method: str | None
    price_basis: str
    account_equity: float
    risk_per_trade_pct: float
    risk_budget: float | None
    option_loss_per_lot: float | None
    delta_proxy: float | None
    delta_source: str | None
    lots: int | None
    quantity: int | None
    rejection_reason: str | None
    contract_instrument_id: str
    contract_symbol: str
    contract_expiry: str
    contract_strike: float
    lot_size: int
    entry_mark: float

    def to_manifest_kwargs(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "method": self.method,
            "price_basis": self.price_basis,
            "account_equity": self.account_equity,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "risk_budget": self.risk_budget,
            "option_loss_per_lot": self.option_loss_per_lot,
            "delta_proxy": self.delta_proxy,
            "delta_source": self.delta_source,
            "contract_instrument_id": self.contract_instrument_id,
            "contract_symbol": self.contract_symbol,
            "contract_expiry": self.contract_expiry,
            "contract_strike": self.contract_strike,
            "contract_lot_size": self.lot_size,
            "entry_mark": self.entry_mark,
            "lots": self.lots,
            "quantity": self.quantity,
            "rejection_reason": self.rejection_reason,
        }


def calculate_replay_sizing(
    *,
    signal: StrategySignal,
    contract: Any,
    entry_mark: float,
    risk_config: RiskConfig,
    option_selection: OptionSelectionConfig,
    session_config: SessionTimersConfig,
    strategy_config: StrategyTunablesConfig,
    account_equity: float,
) -> ReplaySizingDecision:
    """Calculate replay lots using the same production sizing implementations.

    Historical option history has completed OHLC marks rather than point-in-time
    bid/ask/Greeks. For structural-risk sizing, the configured preferred delta
    midpoint is therefore an explicit proxy until contract-selection parity is
    implemented.
    """
    lot_size = int(getattr(contract, "lot_size", 0) or 0)
    instrument_id = str(getattr(contract, "instrument_id", "") or "")
    symbol = str(
        getattr(contract, "stock_code", None)
        or getattr(contract, "symbol", None)
        or instrument_id
    )
    expiry = str(getattr(contract, "expiry", "") or "")
    strike = float(getattr(contract, "strike", 0.0) or 0.0)
    price_basis = "HISTORICAL_OPTION_COMPLETED_CANDLE_CLOSE_MARK"
    budget = account_equity * risk_config.risk_per_trade_pct_of_account / 100.0

    if lot_size <= 0 or entry_mark <= 0:
        return ReplaySizingDecision(
            status="UNAVAILABLE",
            method=None,
            price_basis=price_basis,
            account_equity=account_equity,
            risk_per_trade_pct=risk_config.risk_per_trade_pct_of_account,
            risk_budget=budget,
            option_loss_per_lot=None,
            delta_proxy=None,
            delta_source=None,
            lots=None,
            quantity=None,
            rejection_reason="INVALID_HISTORICAL_SIZING_INPUT",
            contract_instrument_id=instrument_id,
            contract_symbol=symbol,
            contract_expiry=expiry,
            contract_strike=strike,
            lot_size=lot_size,
            entry_mark=entry_mark,
        )

    if signal.strategy == StrategyName.TREND_PULLBACK:
        underlying_entry = signal.underlying_entry_price
        if underlying_entry is None:
            return ReplaySizingDecision(
                status="REJECTED",
                method="UNDERLYING_STRUCTURAL_RISK",
                price_basis=price_basis,
                account_equity=account_equity,
                risk_per_trade_pct=risk_config.risk_per_trade_pct_of_account,
                risk_budget=budget,
                option_loss_per_lot=None,
                delta_proxy=None,
                delta_source=None,
                lots=0,
                quantity=0,
                rejection_reason="INVALID_UNDERLYING_ENTRY_REFERENCE",
                contract_instrument_id=instrument_id,
                contract_symbol=symbol,
                contract_expiry=expiry,
                contract_strike=strike,
                lot_size=lot_size,
                entry_mark=entry_mark,
            )

        delta_proxy = round(
            (
                option_selection.preferred_delta_min
                + option_selection.preferred_delta_max
            )
            / 2.0,
            4,
        )
        sizing = UnderlyingRiskSizer(risk_config).size(
            underlying_entry=float(underlying_entry),
            underlying_stop=float(signal.structural_stop),
            option_delta=delta_proxy,
            lot_size=lot_size,
            option_entry=entry_mark,
            account_equity=account_equity,
        )
        status = "APPLIED" if sizing.lots >= 1 else "REJECTED"
        return ReplaySizingDecision(
            status=status,
            method="UNDERLYING_R_WITH_CONFIGURED_DELTA_PROXY",
            price_basis=price_basis,
            account_equity=account_equity,
            risk_per_trade_pct=risk_config.risk_per_trade_pct_of_account,
            risk_budget=sizing.risk_budget,
            option_loss_per_lot=sizing.option_loss_per_lot,
            delta_proxy=delta_proxy,
            delta_source="CONFIGURED_PREFERRED_DELTA_MIDPOINT",
            lots=sizing.lots,
            quantity=sizing.quantity,
            rejection_reason=sizing.rejection_reason,
            contract_instrument_id=instrument_id,
            contract_symbol=symbol,
            contract_expiry=expiry,
            contract_strike=strike,
            lot_size=lot_size,
            entry_mark=entry_mark,
        )

    manager = PositionManager(
        risk_config,
        session_config,
        strategy_config=strategy_config,
    )
    lots, quantity = manager.calculate_position_size(
        entry_premium=entry_mark,
        account_equity=account_equity,
        lot_size=lot_size,
    )
    option_loss_per_lot = (
        entry_mark
        * (risk_config.option_hard_stop_pct / 100.0)
        * lot_size
    )
    return ReplaySizingDecision(
        status="APPLIED" if lots >= 1 else "REJECTED",
        method="OPTION_HARD_STOP_PREMIUM_RISK",
        price_basis=price_basis,
        account_equity=account_equity,
        risk_per_trade_pct=risk_config.risk_per_trade_pct_of_account,
        risk_budget=budget,
        option_loss_per_lot=round(option_loss_per_lot, 4),
        delta_proxy=None,
        delta_source=None,
        lots=lots,
        quantity=quantity,
        rejection_reason=(
            None if lots >= 1 else "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"
        ),
        contract_instrument_id=instrument_id,
        contract_symbol=symbol,
        contract_expiry=expiry,
        contract_strike=strike,
        lot_size=lot_size,
        entry_mark=entry_mark,
    )
