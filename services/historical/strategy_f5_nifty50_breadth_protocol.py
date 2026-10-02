"""Frozen Phase-3 NIFTY 50 breadth diagnostic for F5.

Purpose
-------
Test constituent participation as an independent market-context layer after
the dominant-NIFTY-regime result. Do NOT combine the futures-state layer into
this study.

Historical NIFTY 50 membership
------------------------------
Use the June 2026 NIFTY 50 membership for 2026-07-01 through 2026-09-29.
Effective 2026-09-30, BSE replaces WIPRO.

Breadth measurement
-------------------
For each constituent on each session:
- UP if 15:20 OPEN > 09:15 OPEN
- DOWN if 15:20 OPEN < 09:15 OPEN
- FLAT otherwise

Equal-weight breadth direction:
- BULLISH if UP count > DOWN count
- BEARISH if DOWN count > UP count
- MIXED if equal

A dominant NIFTY BULLISH/BEARISH whole-day regime is BREADTH_CONFIRMED when
the equal-weight breadth direction agrees with it.

This is a whole-day DEVELOPMENT diagnostic and therefore uses future
information. It must not be used directly as a same-day trading filter.

Primary questions
-----------------
1. On BEARISH NIFTY days, are PE trades cleaner when breadth is also BEARISH?
2. Are CE countertrend trades especially weak when bearish breadth confirms?
3. Does any incremental breadth effect hold separately in Jul, Aug and Sep?
4. On BULLISH days, does bullish breadth improve CE?
5. What fraction of dominant-regime sessions have broad constituent support?

No breadth-strength threshold search is allowed.
"""

PROTOCOL_VERSION = "STRATEGY_F5_NIFTY50_BREADTH_V1"
STRATEGY_ID = "F5"
ROLE = "NIFTY50_BREADTH_DEVELOPMENT_DIAGNOSTIC_NOT_VALIDATION"

WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
    "session_start": "09:15",
    "context_end": "15:20",
    "interval": "5minute",
}

# Frozen June-2026 NIFTY 50 membership.
NIFTY50_JUNE_2026 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "ITC", "INFY", "INDIGO",
    "JSWSTEEL", "JIOFIN", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NTPC", "NESTLEIND", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TMPV", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

SEPTEMBER_30_CHANGE = {
    "effective_date": "2026-09-30",
    "exclude": "WIPRO",
    "include": "BSE",
}

BREADTH_DEFINITION = {
    "constituent_up": "1520_OPEN_GT_0915_OPEN",
    "constituent_down": "1520_OPEN_LT_0915_OPEN",
    "constituent_flat": "1520_OPEN_EQ_0915_OPEN",
    "bullish": "UP_COUNT_GT_DOWN_COUNT",
    "bearish": "DOWN_COUNT_GT_UP_COUNT",
    "mixed": "UP_COUNT_EQ_DOWN_COUNT",
    "magnitude_threshold": None,
}

DATA_QUALITY = {
    "minimum_constituent_coverage_pct": 90.0,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "breeze_only_broker_api": True,
    "whole_day_context_uses_future_information": True,
    "do_not_use_as_same_day_entry_filter": True,
    "matched_baseline_trades_only": True,
    "no_filtered_path_resimulation": True,
    "no_breadth_strength_threshold_search": True,
    "no_futures_state_combination_in_this_pass": True,
    "no_side_filter_promotion": True,
    "keep_existing_f5_entry_exit_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
