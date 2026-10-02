"""Derive one catastrophic F5 stop boundary from successful-trade MAE.

This is NOT a stop-grid backtest and does not optimize P&L on Jul-Sep.

Population
----------
Baseline F5 PE trades on Jul-Sep 2026 whole-day BEARISH NIFTY sessions.

Measurement
-----------
For each target trade, use the traded option's 1-minute bars from entry until:
- the +10% trail activation timestamp, if activation occurs, otherwise
- the baseline exit timestamp.

Measure intrabar MAE from 1-minute LOW relative to the actual baseline entry
open. This captures the adverse excursion a real protective stop would have
experienced, rather than only completed 2-minute closes.

Candidate derivation
--------------------
Calculate the 5th percentile MAE for:
1. baseline winners
2. trail-activated trades

The single catastrophic-stop candidate is the more conservative (farther)
distance required by those two successful-trade distributions. This candidate
is outcome-derived development research and MUST be frozen before any fresh
holdout scoring.

No P&L counterfactual is scored here. No percentage grid is searched.
"""

from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_V1"
STRATEGY_ID = "F5"
ROLE = "BEARISH_PE_SUCCESSFUL_TRADE_MAE_BOUNDARY_DERIVATION"

DEVELOPMENT_WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
    "source_interval": "1minute",
}

PRESERVATION_TARGET_PCT = 95.0
QUANTILE = 0.05

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "whole_day_bearish_regime_only_selects_development_population": True,
    "target_only_bearish_regime_PE": True,
    "use_intrabar_1m_option_lows": True,
    "no_stop_grid_search": True,
    "no_pnl_optimization_on_development": True,
    "no_support_resistance_retuning": True,
    "single_candidate_is_success_preservation_derived": True,
    "candidate_requires_fresh_holdout_before_promotion": True,
    "existing_f5_entry_exit_trail_frozen": True,
    "strategy_d_remains_paused": True,
}
