"""F5 market-context research roadmap.

Research architecture
---------------------
1. Dominant NIFTY regime
2. NIFTY futures confirmation
3. Breadth / heavyweight / sector confirmation
4. Prior-session FII/DII and participant positioning
5. Volatility / option-chain context
6. Combined regime model
7. F5 timing only after directional permission
8. Existing F5 post-entry management remains frozen unless separately studied

Principle
---------
F5 is treated as the entry-timing engine, not the market-direction engine.

Each layer must first demonstrate standalone descriptive/predictive value before
it is combined with another layer. No broad parameter grid, no post-hoc side
promotion, and no use of future information in any eventually tradable rule.

Current sequence
----------------
PHASE_1:
    Expand the frozen whole-day dominant-NIFTY-regime diagnostic over the full
    Jul-Sep 2026 F5 development sample. This is descriptive because the regime
    uses the completed session.

PHASE_2:
    Add NIFTY futures price, basis, volume and open-interest context using
    Breeze-supported data only.

PHASE_3:
    Add breadth and heavyweight/sector confirmation.

PHASE_4:
    Add lagged FII/DII cash and participant derivatives positioning. Same-day
    end-of-day institutional reports must not be backfilled into earlier trades.

PHASE_5:
    Add volatility and option-chain context. If historical chain state is not
    available from existing artifacts, collect prospectively rather than
    reconstruct or impute it.

PHASE_6:
    Only after standalone evidence exists, combine the minimal useful layers
    into a no-lookahead intraday regime detector.

Global guardrails
-----------------
- research/backtest only
- Breeze is the only broker/trading API
- no live or paper execution
- Strategy D remains paused
- preserve the existing F5 post-activation trail
- fresh holdout required before any new trading rule is promoted
"""

RESEARCH_PROGRAM = "F5_MARKET_CONTEXT_RESEARCH_V1"

PHASES = [
    "DOMINANT_NIFTY_REGIME",
    "NIFTY_FUTURES_CONFIRMATION",
    "BREADTH_AND_LEADERSHIP",
    "LAGGED_INSTITUTIONAL_POSITIONING",
    "VOLATILITY_AND_OPTION_CHAIN",
    "COMBINED_NO_LOOKAHEAD_REGIME",
]

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "breeze_only_broker_api": True,
    "no_large_parameter_grid": True,
    "no_posthoc_side_filter": True,
    "no_future_information_in_tradable_rule": True,
    "fresh_holdout_required_before_rule_promotion": True,
    "keep_existing_f5_post_activation_trail_frozen": True,
    "strategy_d_remains_paused": True,
}
