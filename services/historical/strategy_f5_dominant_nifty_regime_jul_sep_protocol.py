"""Frozen Jul-Sep expansion of the dominant-NIFTY-regime diagnostic.

Purpose
-------
Test whether the six-session finding generalizes across the full Jul-Sep 2026
F5 development window without changing any F5 trades.

Use exactly the already-frozen whole-day regime classifier:
- NET_RETURN_VOTE
- MEDIAN_LOCATION_VOTE
- SESSION_SLOPE_VOTE

BULLISH only if all three are bullish.
BEARISH only if all three are bearish.
Otherwise MIXED.

This study is descriptive/development-only because whole-day classification
uses information through 15:20.

Primary questions
-----------------
1. On BEARISH days, do PE trades outperform CE trades?
2. On BEARISH days, are CE trail activations/wins materially suppressed?
3. Does the bearish PE-vs-CE separation hold in Jul, Aug and Sep independently?
4. On BULLISH days, does CE similarly outperform PE, or is the effect asymmetric?
5. How do regime-aligned, counter-regime and mixed-regime trades compare overall?

No thresholds, side rules, time rules, stops, targets or trail parameters may
be changed in this pass.
"""

PROTOCOL_VERSION = "STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_V1"
STRATEGY_ID = "F5"
ROLE = "WHOLE_DAY_DOMINANT_REGIME_DEVELOPMENT_DIAGNOSTIC_NOT_VALIDATION"

WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
}

REGIME_DEFINITION = {
    "source_protocol": "STRATEGY_F5_DOMINANT_NIFTY_REGIME_V1",
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
    "no_filtered_path_resimulation": True,
    "no_regime_threshold_search": True,
    "no_side_filter_promotion": True,
    "no_time_filter": True,
    "no_stop_target_search": True,
    "keep_existing_f5_entry_exit_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
