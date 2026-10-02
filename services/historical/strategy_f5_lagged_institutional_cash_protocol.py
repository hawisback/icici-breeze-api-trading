"""Frozen Phase-4B lagged institutional cash-flow diagnostic.

Purpose
-------
Test whether prior-session provisional FII/FPI and DII cash-market activity
adds independent context to the established dominant-NIFTY-regime finding.

Timing
------
For trading session T, use only cash activity published for the immediately
preceding F5/NIFTY trading session T-1. Missing prior-session cash data remains
UNAVAILABLE and is never folded into a sign bucket.

Primary questions
-----------------
1. On BEARISH dominant-NIFTY days, does prior FII/FPI net selling concentrate
   PE outcomes?
2. Does prior FII/FPI net selling further weaken bearish-regime CE?
3. On BULLISH dominant-NIFTY days, does prior FII/FPI net buying improve CE?
4. Does the offset state FII_SELL_DII_BUY add descriptive information?
5. Are any effects stable across Jul, Aug and Sep?

No magnitude threshold search and no composite score is allowed.
"""

PROTOCOL_VERSION = "STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_V1"
STRATEGY_ID = "F5"
ROLE = "LAGGED_INSTITUTIONAL_CASH_DEVELOPMENT_DIAGNOSTIC_NOT_VALIDATION"

WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
}

NSE_PAGE_URL = "https://www.nseindia.com/reports/fii-dii"
NSE_API_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

FLOW_STATE = {
    "BUYING": "NET_VALUE_GT_ZERO",
    "SELLING": "NET_VALUE_LT_ZERO",
    "FLAT": "NET_VALUE_EQ_ZERO",
}

OFFSET_STATE = {
    "FII_SELL_DII_BUY": "FII_SELLING_AND_DII_BUYING",
    "FII_BUY_DII_SELL": "FII_BUYING_AND_DII_SELLING",
    "BOTH_BUY": "FII_BUYING_AND_DII_BUYING",
    "BOTH_SELL": "FII_SELLING_AND_DII_SELLING",
    "OTHER": "AT_LEAST_ONE_SIDE_FLAT",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "context_is_lagged_one_session": True,
    "no_same_day_cash_report_backfill": True,
    "missing_cash_data_separate_from_sign": True,
    "matched_baseline_trades_only": True,
    "no_filtered_path_resimulation": True,
    "no_cash_magnitude_threshold_search": True,
    "no_composite_score": True,
    "no_oi_breadth_or_futures_state_combination_in_this_pass": True,
    "no_side_filter_promotion": True,
    "keep_existing_f5_entry_exit_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
