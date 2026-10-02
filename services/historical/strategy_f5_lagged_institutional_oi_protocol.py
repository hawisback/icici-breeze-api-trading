"""Frozen Phase-4A lagged institutional derivatives-positioning diagnostic.

Purpose
-------
Test whether prior-session NSE participant-wise F&O open interest adds
independent context to the established dominant-NIFTY-regime finding.

Timing
------
For trading session T, use only participant OI published for the immediately
preceding F5/NIFTY trading session T-1. Changes in positioning use T-2 -> T-1.
No same-day participant report may be backfilled into trades from T.

Primary institutional features
------------------------------
For FII and DII separately:
- index_futures_net = Future Index Long - Future Index Short
- index_futures_state:
    NET_LONG  if net > 0
    NET_SHORT if net < 0
    FLAT      otherwise
- index_futures_net_change = net(T-1) - net(T-2)
- index_futures_change_state:
    MORE_LONG  if change > 0
    MORE_SHORT if change < 0
    FLAT       otherwise

Secondary descriptive option-positioning proxy
----------------------------------------------
index_options_directional_balance =
    (Option Index Call Long + Option Index Put Short)
  - (Option Index Call Short + Option Index Put Long)

This is NOT delta, not dealer gamma, and not treated as a standalone direction
signal because participant option positions can be hedges/spreads.

Primary questions
-----------------
1. On BEARISH dominant-NIFTY days, does prior FII NET_SHORT improve PE outcomes?
2. Does prior FII MORE_SHORT improve bearish PE or further weaken bearish CE?
3. Are those effects stable across Jul, Aug and Sep?
4. Does prior FII NET_LONG / MORE_LONG help bullish CE?
5. Do DII index-futures positions add anything independently?

No magnitude threshold search and no composite score is allowed.
"""

PROTOCOL_VERSION = "STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_V1"
STRATEGY_ID = "F5"
ROLE = "LAGGED_PARTICIPANT_OI_DEVELOPMENT_DIAGNOSTIC_NOT_VALIDATION"

WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
}

PARTICIPANTS = ["FII", "DII", "Client", "Pro"]

NSE_PARTICIPANT_OI_URL_PATTERN = (
    "https://nsearchives.nseindia.com/content/nsccl/"
    "fao_participant_oi_{ddmmyyyy}.csv"
)

POSITION_STATE = {
    "NET_LONG": "INDEX_FUTURES_LONG_GT_SHORT",
    "NET_SHORT": "INDEX_FUTURES_SHORT_GT_LONG",
    "FLAT": "INDEX_FUTURES_LONG_EQ_SHORT",
}

CHANGE_STATE = {
    "MORE_LONG": "NET_INDEX_FUTURES_T_MINUS_1_GT_T_MINUS_2",
    "MORE_SHORT": "NET_INDEX_FUTURES_T_MINUS_1_LT_T_MINUS_2",
    "FLAT": "NET_INDEX_FUTURES_T_MINUS_1_EQ_T_MINUS_2",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "public_nse_reports_only": True,
    "context_is_lagged_one_session": True,
    "no_same_day_participant_report_backfill": True,
    "matched_baseline_trades_only": True,
    "no_filtered_path_resimulation": True,
    "no_position_magnitude_threshold_search": True,
    "no_composite_score": True,
    "option_directional_balance_is_descriptive_proxy_only": True,
    "no_futures_state_or_breadth_combination_in_this_pass": True,
    "no_side_filter_promotion": True,
    "keep_existing_f5_entry_exit_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
