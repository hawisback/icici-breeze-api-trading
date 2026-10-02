"""Frozen dominant-NIFTY-regime diagnostic for F5.

Target sessions
---------------
- 2026-09-21 through 2026-09-25
- 2026-10-01

This directly tests the hypothesis that F5 should trade only the option side
aligned with the DOMINANT NIFTY behaviour of the day:
- dominant bullish day -> evaluate/retain CE setups, ignore PE setups
- dominant bearish day -> evaluate/retain PE setups, ignore CE setups
- mixed day -> do not force a side

This first pass intentionally uses the whole session and is therefore
DESCRIPTIVE ONLY. It must not be used as a same-day live/paper entry filter.

Dominant day-regime classifier
------------------------------
Use NIFTY spot 5-minute bars from 09:15 through information available by 15:20.
Three untuned directional votes are computed:

1. NET_RETURN_VOTE
   sign(15:20 OPEN / 09:15 OPEN - 1)

2. MEDIAN_LOCATION_VOTE
   sign(median of completed 5-minute CLOSES through 15:20 - 09:15 OPEN)

3. SESSION_SLOPE_VOTE
   sign(OLS slope of completed 5-minute CLOSES through 15:20)

Classification:
- BULLISH only if all 3 votes are bullish
- BEARISH only if all 3 votes are bearish
- otherwise MIXED

No magnitude threshold is searched or fitted.

Evaluation
----------
Use the original baseline F5 trades generated on each target session. Do not
resimulate after removing counter-regime trades in this diagnostic because
skipping one trade could change later one-position-at-a-time availability.

For matched baseline trades:
- BULLISH + CE = REGIME_ALIGNED
- BULLISH + PE = COUNTER_REGIME
- BEARISH + PE = REGIME_ALIGNED
- BEARISH + CE = COUNTER_REGIME
- MIXED = MIXED_REGIME

Report daily CE/PE performance, aligned/counter summaries, and Oct-1 trade
details separately.
"""

PROTOCOL_VERSION = "STRATEGY_F5_DOMINANT_NIFTY_REGIME_V1"
STRATEGY_ID = "F5"
ROLE = "WHOLE_DAY_DOMINANT_REGIME_DIAGNOSTIC_NOT_VALIDATION"

TARGET_DATES = [
    "2026-09-21",
    "2026-09-22",
    "2026-09-23",
    "2026-09-24",
    "2026-09-25",
    "2026-10-01",
]

WINDOW = {
    "warmup_start": "2026-09-15",
    "end": "2026-10-01",
    "session_start": "09:15",
    "regime_end": "15:20",
    "spot_interval_minutes": 5,
    "option_interval": "1minute",
    "signal_interval_minutes": 2,
    "warmup_previous_sessions_per_contract": 5,
}

EXPIRIES = [
    "2026-09-22",
    "2026-09-29",
    "2026-10-06",
]

REGIME = {
    "net_return_vote": "SIGN_OF_1520_OPEN_MINUS_0915_OPEN",
    "median_location_vote": (
        "SIGN_OF_MEDIAN_COMPLETED_5M_CLOSE_THROUGH_1520_MINUS_0915_OPEN"
    ),
    "session_slope_vote": "SIGN_OF_OLS_SLOPE_COMPLETED_5M_CLOSE_THROUGH_1520",
    "bullish": "ALL_THREE_VOTES_BULLISH",
    "bearish": "ALL_THREE_VOTES_BEARISH",
    "mixed": "ANY_OTHER_COMBINATION",
    "magnitude_threshold": None,
}

SIDE_ALIGNMENT = {
    "BULLISH": "CE",
    "BEARISH": "PE",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "whole_day_regime_uses_future_information": True,
    "do_not_use_as_same_day_entry_filter": True,
    "matched_baseline_trades_only": True,
    "do_not_resimulate_filtered_path_in_this_pass": True,
    "no_regime_magnitude_threshold_search": True,
    "no_side_rule_promotion_from_six_sessions": True,
    "keep_original_f5_entry_exit_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
