"""Replay execution-price estimation using production paper assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.strategy.models import RiskConfig
from services.strategy.replay_contract_selection import ReplayPriceEvidence


@dataclass(frozen=True)
class ReplayFillEstimate:
    side: str
    status: str
    method: str
    evidence_basis: str
    source: str
    raw_reference_price: float | None
    executable_price: float | None
    slippage_points: float
    evidence_timestamp: str | None
    freshness_seconds: float | None
    executable_quote_equivalent: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "status": self.status,
            "method": self.method,
            "evidence_basis": self.evidence_basis,
            "source": self.source,
            "raw_reference_price": self.raw_reference_price,
            "executable_price": self.executable_price,
            "slippage_points": self.slippage_points,
            "evidence_timestamp": self.evidence_timestamp,
            "freshness_seconds": self.freshness_seconds,
            "executable_quote_equivalent": self.executable_quote_equivalent,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReplayRoundTripExecution:
    entry: ReplayFillEstimate
    exit: ReplayFillEstimate
    quantity: int
    gross_execution_pnl: float | None
    slippage_cost: float | None
    brokerage: float | None
    exchange_charges: float | None
    stt: float | None
    gst: float | None
    sebi_charges: float | None
    stamp_duty: float | None
    transaction_costs: float | None
    net_execution_pnl: float | None
    cost_assumption_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry.to_dict(),
            "exit": self.exit.to_dict(),
            "quantity": self.quantity,
            "gross_execution_pnl": self.gross_execution_pnl,
            "slippage_cost": self.slippage_cost,
            "brokerage": self.brokerage,
            "exchange_charges": self.exchange_charges,
            "stt": self.stt,
            "gst": self.gst,
            "sebi_charges": self.sebi_charges,
            "stamp_duty": self.stamp_duty,
            "transaction_costs": self.transaction_costs,
            "net_execution_pnl": self.net_execution_pnl,
            "cost_assumption_version": self.cost_assumption_version,
        }


def estimate_fill(
    evidence: ReplayPriceEvidence,
    *,
    side: str,
    risk_config: RiskConfig,
) -> ReplayFillEstimate:
    side = side.upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"unsupported replay fill side: {side}")

    slippage = float(risk_config.paper_slippage_points)
    if evidence.status != "AVAILABLE":
        return ReplayFillEstimate(
            side=side,
            status="UNAVAILABLE",
            method="NO_FILL_ESTIMATE",
            evidence_basis=evidence.basis,
            source=evidence.source,
            raw_reference_price=None,
            executable_price=None,
            slippage_points=slippage,
            evidence_timestamp=(
                evidence.evidence_timestamp.isoformat()
                if evidence.evidence_timestamp is not None
                else None
            ),
            freshness_seconds=evidence.freshness_seconds,
            executable_quote_equivalent=False,
            reason=evidence.reason,
        )

    if evidence.bid_ask_available:
        raw = float(evidence.ask if side == "BUY" else evidence.bid)
        price = (
            round(raw + slippage, 2)
            if side == "BUY"
            else max(0.0, round(raw - slippage, 2))
        )
        return ReplayFillEstimate(
            side=side,
            status="ESTIMATED",
            method="POINT_IN_TIME_BID_ASK_PLUS_CONFIGURED_SLIPPAGE",
            evidence_basis=evidence.basis,
            source=evidence.source,
            raw_reference_price=raw,
            executable_price=price,
            slippage_points=slippage,
            evidence_timestamp=(
                evidence.evidence_timestamp.isoformat()
                if evidence.evidence_timestamp is not None
                else None
            ),
            freshness_seconds=evidence.freshness_seconds,
            executable_quote_equivalent=True,
        )

    if evidence.mark_price is not None and evidence.mark_price > 0:
        raw = float(evidence.mark_price)
        price = (
            round(raw + slippage, 2)
            if side == "BUY"
            else max(0.0, round(raw - slippage, 2))
        )
        return ReplayFillEstimate(
            side=side,
            status="ESTIMATED",
            method="COMPLETED_MARK_PLUS_CONFIGURED_SLIPPAGE_ESTIMATE",
            evidence_basis=evidence.basis,
            source=evidence.source,
            raw_reference_price=raw,
            executable_price=price,
            slippage_points=slippage,
            evidence_timestamp=(
                evidence.evidence_timestamp.isoformat()
                if evidence.evidence_timestamp is not None
                else None
            ),
            freshness_seconds=evidence.freshness_seconds,
            executable_quote_equivalent=False,
            reason=(
                "Historical bid/ask unavailable; completed candle close is a "
                "mark reference, not an executable quote."
            ),
        )

    return ReplayFillEstimate(
        side=side,
        status="UNAVAILABLE",
        method="NO_FILL_ESTIMATE",
        evidence_basis=evidence.basis,
        source=evidence.source,
        raw_reference_price=None,
        executable_price=None,
        slippage_points=slippage,
        evidence_timestamp=(
            evidence.evidence_timestamp.isoformat()
            if evidence.evidence_timestamp is not None
            else None
        ),
        freshness_seconds=evidence.freshness_seconds,
        executable_quote_equivalent=False,
        reason=evidence.reason or "NO_USABLE_PRICE_REFERENCE",
    )


def estimate_round_trip_execution(
    *,
    entry_evidence: ReplayPriceEvidence,
    exit_evidence: ReplayPriceEvidence,
    quantity: int,
    risk_config: RiskConfig,
) -> ReplayRoundTripExecution:
    entry = estimate_fill(
        entry_evidence,
        side="BUY",
        risk_config=risk_config,
    )
    exit_ = estimate_fill(
        exit_evidence,
        side="SELL",
        risk_config=risk_config,
    )
    if (
        quantity <= 0
        or entry.executable_price is None
        or exit_.executable_price is None
        or entry.raw_reference_price is None
        or exit_.raw_reference_price is None
    ):
        return ReplayRoundTripExecution(
            entry=entry,
            exit=exit_,
            quantity=quantity,
            gross_execution_pnl=None,
            slippage_cost=None,
            brokerage=None,
            exchange_charges=None,
            stt=None,
            gst=None,
            sebi_charges=None,
            stamp_duty=None,
            transaction_costs=None,
            net_execution_pnl=None,
            cost_assumption_version=risk_config.paper_cost_assumption_version,
        )

    entry_fill = float(entry.executable_price)
    exit_fill = float(exit_.executable_price)
    gross = round((exit_fill - entry_fill) * quantity, 2)
    slippage_cost = round(
        (
            abs(entry_fill - float(entry.raw_reference_price))
            + abs(float(exit_.raw_reference_price) - exit_fill)
        )
        * quantity,
        2,
    )
    turnover = (entry_fill + exit_fill) * quantity
    buy_turnover = entry_fill * quantity
    sell_turnover = exit_fill * quantity
    brokerage = round(2 * risk_config.paper_brokerage_per_order, 2)
    exchange = round(
        turnover * risk_config.paper_exchange_charge_rate,
        2,
    )
    stt = round(sell_turnover * risk_config.paper_stt_sell_rate, 2)
    sebi = round(turnover * risk_config.paper_sebi_charge_rate, 2)
    stamp = round(buy_turnover * risk_config.paper_stamp_buy_rate, 2)
    gst = round(
        (brokerage + exchange + sebi) * risk_config.paper_gst_rate,
        2,
    )
    transaction_costs = round(
        brokerage + exchange + stt + gst + sebi + stamp,
        2,
    )
    net = round(gross - transaction_costs, 2)
    return ReplayRoundTripExecution(
        entry=entry,
        exit=exit_,
        quantity=quantity,
        gross_execution_pnl=gross,
        slippage_cost=slippage_cost,
        brokerage=brokerage,
        exchange_charges=exchange,
        stt=stt,
        gst=gst,
        sebi_charges=sebi,
        stamp_duty=stamp,
        transaction_costs=transaction_costs,
        net_execution_pnl=net,
        cost_assumption_version=risk_config.paper_cost_assumption_version,
    )
