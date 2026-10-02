"""Frozen last-week NIFTY direction vs F5 CE/PE diagnostic.

Window
------
2026-09-21 through 2026-09-25 (the previous Monday-Friday relative to
2026-10-02).

Purpose
-------
Test the user's hypothesis that NIFTY spot direction determines whether CE or
PE F5 trades behave well.

Two direction labels are reported:

FULL_DAY_DIRECTION (descriptive only)
    Sign of NIFTY spot return from 09:15 OPEN to 15:20 OPEN.
    BULLISH if >0, BEARISH if <0, FLAT if exactly 0.
    This is NOT tradable at earlier entries because it uses later information.

ENTRY_TIME_DIRECTION (no look-ahead)
    Sign of NIFTY return from 09:15 OPEN to the CLOSE of the latest completed
    5-minute spot bar whose end time is <= the option entry timestamp.
    Entries before the first completed 5-minute bar are marked unavailable.

Alignment
---------
CE is direction-aligned when NIFTY direction is BULLISH.
PE is direction-aligned when NIFTY direction is BEARISH.
FLAT/UNAVAILABLE is unclassified.

The diagnostic uses the original baseline F5 trades only. It does not alter
entries, exits, score thresholds, or trailing behavior.
"""

PROTOCOL_VERSION = "STRATEGY_F5_NIFTY_DIRECTION_LAST_WEEK_V1"
STRATEGY_ID = "F5"
ROLE = "DIRECTIONAL_CONTEXT_DIAGNOSTIC_NOT_VALIDATION"

WINDOW = {
    "start": "2026-09-21",
    "end": "2026-09-25",
    "direction_start": "09:15",
    "full_day_direction_end": "15:20",
    "spot_interval_minutes": 5,
}

DIRECTION = {
    "bullish_if_return_pct_gt": 0.0,
    "bearish_if_return_pct_lt": 0.0,
    "flat_if_return_pct_eq": 0.0,
    "full_day_price_start": "09:15_OPEN",
    "full_day_price_end": "15:20_OPEN",
    "entry_time_price_start": "09:15_OPEN",
    "entry_time_price_end": "LATEST_COMPLETED_5M_CLOSE_AT_OR_BEFORE_ENTRY",
}

ALIGNMENT = {
    "CE": "BULLISH",
    "PE": "BEARISH",
}

REPORTING = {
    "daily_spot_direction": True,
    "daily_ce_pe_performance": True,
    "full_day_aligned_vs_counter": True,
    "entry_time_aligned_vs_counter": True,
    "per_trade_entry_time_direction": True,
    "trail_activation_rate": True,
    "win_rate": True,
    "net_pnl": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "full_day_direction_is_descriptive_only": True,
    "do_not_use_full_day_direction_as_same_day_entry_filter": True,
    "entry_time_direction_has_no_lookahead": True,
    "keep_original_f5_trades_unchanged": True,
    "keep_existing_post_activation_trail_frozen": True,
    "no_direction_threshold_search": True,
    "no_side_filter_promotion_from_one_week": True,
    "strategy_d_remains_paused": True,
}
