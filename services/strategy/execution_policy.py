"""Authoritative execution-permission policy for Strategies A/B/C/D.

This module separates a strategy's requested platform mode from the maximum
execution authority currently granted to that strategy. Promotion to LIVE is
an explicit code/config change; selecting global LIVE mode cannot promote a
validation-locked strategy by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.strategy.models import AutoTradingMode, OptionType, StrategyName


POLICY_VERSION = "strategy_execution_policy_v2"


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

    Strategy A is explicitly LIVE-promoted in policy v2 after its broker-entry,
    protective-stop, partial-exit, final-exit, audit, and reconciliation paths
    were hardened. Strategies B/C/D remain validation-locked. Runtime health,
    operator authorization, account allowlisting, arming, and market-data
    readiness remain separate fail-closed gates.
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
        effective = (
            AutoTradingMode.SHADOW_ONLY
            if requested_mode == AutoTradingMode.LIVE
            else requested_mode
        )
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=effective,
            put_mode=effective,
            live_trading_allowed=False,
            promotion_state="VALIDATION_LOCKED",
            live_block_reason="STRATEGY_B_LIVE_PROMOTION_NOT_APPROVED",
            force_entry_allowed=True,
        )

    if strategy == StrategyName.DI_CONTINUATION:
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=AutoTradingMode.PAPER,
            put_mode=AutoTradingMode.PAPER,
            live_trading_allowed=False,
            promotion_state="FROZEN_PAPER_CANDIDATE",
            live_block_reason="STRATEGY_C_FROZEN_CANDIDATE_NOT_PROMOTED",
            force_entry_allowed=False,
        )

    if strategy == StrategyName.SR_MOMENTUM_BREAKOUT:
        return StrategyExecutionPolicy(
            strategy=strategy,
            call_mode=AutoTradingMode.PAPER,
            put_mode=AutoTradingMode.PAPER,
            live_trading_allowed=False,
            promotion_state="FROZEN_PAPER_CANDIDATE",
            live_block_reason="STRATEGY_D_FROZEN_CANDIDATE_NOT_PROMOTED",
            force_entry_allowed=False,
        )

    raise ValueError(f"Unsupported strategy execution policy: {strategy}")
