"""Authoritative execution-permission policy for Strategies A/B/C/D/E.

This module separates a strategy's requested platform mode from the maximum
execution authority currently granted to that strategy. Promotion to LIVE is
an explicit code/config change; selecting global LIVE mode cannot promote a
validation-locked strategy by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.strategy.models import AutoTradingMode, OptionType, StrategyName


POLICY_VERSION = "strategy_execution_policy_v5"


@dataclass(frozen=True)
class StrategyExecutionPolicy:
    strategy: StrategyName
    call_mode: AutoTradingMode
    put_mode: AutoTradingMode
    live_trading_allowed: bool
    promotion_state: str
    live_block_reason: str
    force_entry_allowed: bool

    def mode_for(self, option_type: OptionType) -> AutoTradingMode:
        return self.call_mode if option_type == OptionType.CALL else self.put_mode

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "strategy": self.strategy.value,
            "call_mode": self.call_mode.value,
            "put_mode": self.put_mode.value,
            "live_trading_allowed": self.live_trading_allowed,
            "promotion_state": self.promotion_state,
            "live_block_reason": self.live_block_reason,
            "force_entry_allowed": self.force_entry_allowed,
        }


def resolve_strategy_execution_policy(
    strategy: StrategyName,
    requested_mode: AutoTradingMode,
    *,
    strategy_a_option_execution_ready: bool = False,
    strategy_a_option_execution_reason: str | None = None,
) -> StrategyExecutionPolicy:
    """Return the maximum currently-approved execution authority.

    Strategies A/B/C/D/E are LIVE-promoted through this single authority boundary.
    Runtime health, account allowlisting, arming, market-data readiness, and each
    strategy's frozen signal contract remain separate fail-closed gates.
    """

    if strategy == StrategyName.TREND_PULLBACK:
        effective = requested_mode
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=effective,
            put_mode=effective,
            live_trading_allowed=True,
            promotion_state="LIVE_PROMOTED",
            live_block_reason="",
            force_entry_allowed=False,
        )

    if strategy == StrategyName.VOLATILITY_BREAKOUT:
        effective = requested_mode
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=effective,
            put_mode=effective,
            live_trading_allowed=True,
            promotion_state="LIVE_PROMOTED",
            live_block_reason="",
            force_entry_allowed=True,
        )

    if strategy == StrategyName.DI_CONTINUATION:
        effective = requested_mode
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=effective,
            put_mode=effective,
            live_trading_allowed=True,
            promotion_state="LIVE_PROMOTED",
            live_block_reason="",
            force_entry_allowed=False,
        )

    if strategy == StrategyName.SR_MOMENTUM_BREAKOUT:
        effective = requested_mode
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=effective,
            put_mode=effective,
            live_trading_allowed=True,
            promotion_state="LIVE_PROMOTED",
            live_block_reason="",
            force_entry_allowed=False,
        )

    if strategy == StrategyName.PIVOT_VWAP_SCALP:
        effective = requested_mode
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=effective,
            put_mode=effective,
            live_trading_allowed=True,
            promotion_state="LIVE_PROMOTED",
            live_block_reason="",
            force_entry_allowed=False,
        )

    raise ValueError(f"Unsupported strategy execution policy: {strategy}")
