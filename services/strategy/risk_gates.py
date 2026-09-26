"""Shared entry risk gates used by production and historical execution replay.

These helpers are intentionally pure.  Callers own data retrieval and decide
when each gate runs; the comparison logic and status/detail semantics live in
one place so replay cannot drift from production thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.strategy.models import RiskConfig


@dataclass(frozen=True)
class RiskGateDecision:
    allowed: bool
    status: str = "READY"
    details: dict[str, Any] | None = None

    def as_result(self) -> dict[str, Any]:
        payload = {"status": self.status}
        if self.details:
            payload.update(self.details)
        return payload


def check_position_capacity(
    risk: RiskConfig,
    *,
    active_count: int,
) -> RiskGateDecision:
    if active_count >= risk.max_concurrent_positions:
        return RiskGateDecision(
            False,
            "MAX_CONCURRENT_POSITIONS_REACHED",
            {
                "active_count": active_count,
                "max_concurrent_positions": risk.max_concurrent_positions,
            },
        )
    return RiskGateDecision(True)


def check_loss_cooldown(
    risk: RiskConfig,
    *,
    at: datetime,
    last_loss_exit_time: datetime | None,
) -> RiskGateDecision:
    if last_loss_exit_time is None:
        return RiskGateDecision(True)
    elapsed_minutes = (at - last_loss_exit_time).total_seconds() / 60.0
    if elapsed_minutes < risk.cooldown_after_loss_min:
        return RiskGateDecision(
            False,
            "IN_LOSS_COOLDOWN",
            {
                "cooldown_remaining_min": round(
                    risk.cooldown_after_loss_min - elapsed_minutes,
                    1,
                ),
            },
        )
    return RiskGateDecision(True)


def check_daily_loss_limits(
    risk: RiskConfig,
    *,
    realized_r_total: float,
    net_pnl_total: float | None,
    account_equity: float | None = None,
) -> RiskGateDecision:
    r_blocked = realized_r_total <= -risk.max_daily_loss_r
    equity = account_equity if account_equity is not None else risk.account_equity
    pnl_limit = equity * risk.max_daily_loss_pct / 100.0
    pnl_blocked = (
        net_pnl_total is not None
        and net_pnl_total <= -pnl_limit
    )
    if r_blocked or pnl_blocked:
        reasons: list[str] = []
        if r_blocked:
            reasons.append("MAX_DAILY_LOSS_R")
        if pnl_blocked:
            reasons.append("MAX_DAILY_LOSS_PCT")
        return RiskGateDecision(
            False,
            "DAILY_LOSS_LIMIT_REACHED",
            {
                "reasons": reasons,
                "realized_r_total": round(realized_r_total, 4),
                "max_daily_loss_r": risk.max_daily_loss_r,
                "net_pnl_total": (
                    round(net_pnl_total, 2)
                    if net_pnl_total is not None
                    else None
                ),
                "max_daily_loss_amount": round(pnl_limit, 2),
            },
        )
    return RiskGateDecision(True)


def check_daily_trade_limit(
    risk: RiskConfig,
    *,
    daily_count: int,
    max_trades_per_day: int | None = None,
) -> RiskGateDecision:
    limit = (
        max_trades_per_day
        if max_trades_per_day is not None
        else risk.max_trades_per_day
    )
    if daily_count >= limit:
        return RiskGateDecision(
            False,
            "DAILY_TRADE_LIMIT_REACHED",
            {
                "today_trades": daily_count,
                "max_trades_per_day": limit,
            },
        )
    return RiskGateDecision(True)


def check_strategy_trade_limits(
    risk: RiskConfig,
    *,
    strategy_trade_count: int,
    strategy_failure_count: int,
) -> RiskGateDecision:
    if strategy_trade_count >= risk.max_trades_per_strategy_per_day:
        return RiskGateDecision(
            False,
            "STRATEGY_DAILY_TRADE_LIMIT_REACHED",
            {
                "strategy_trades": strategy_trade_count,
                "max_trades_per_strategy_per_day": (
                    risk.max_trades_per_strategy_per_day
                ),
            },
        )
    if strategy_failure_count >= risk.max_failed_trades_per_strategy:
        return RiskGateDecision(
            False,
            "STRATEGY_FAILURE_LIMIT_REACHED",
            {
                "strategy_failures": strategy_failure_count,
                "max_failed_trades_per_strategy": (
                    risk.max_failed_trades_per_strategy
                ),
            },
        )
    return RiskGateDecision(True)
