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
The interpolated 5th-percentile MAE is reported descriptively, but the actual
candidate uses an EMPIRICAL preservation boundary because small samples can
make a linear-interpolated p05 violate the stated 95% preservation target.

For each successful-trade group:
1. allow at most floor(5% * N) historical breaches,
2. find the next-worst MAE that must remain untouched,
3. place the stop strictly beyond that MAE,
4. round outward to the next 0.01 percentage point.

The single candidate is the farther of the winner and trail-activation
empirical boundaries. A separate zero-observed-success-breach reference is
also reported, but is descriptive and is not a second candidate.

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
OPERATIONAL_ROUNDING_STEP_PCT = 0.01

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
    "interpolated_quantile_not_used_as_operational_boundary": True,
    "operational_boundary_rounded_outward_not_inward": True,
    "candidate_requires_fresh_holdout_before_promotion": True,
    "existing_f5_entry_exit_trail_frozen": True,
    "strategy_d_remains_paused": True,
}
