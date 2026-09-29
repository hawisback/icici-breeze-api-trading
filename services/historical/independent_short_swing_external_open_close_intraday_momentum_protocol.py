"""Externally motivated intraday momentum protocol.

This study is allowed under SHORT_SWING_V2_HYPOTHESIS_MINING_PAUSE_V1 because
the mechanism and exact rule are taken from published literature and frozen
before any additional V2 outcome inspection.

External literature
-------------------
1. Gao, Han, Li & Zhou (2018), Journal of Financial Economics:
   "Market intraday momentum", DOI 10.1016/j.jfineco.2018.05.009.
   Reports that first-half-hour market returns predict last-half-hour returns.
2. Jin, Kearney, Li & Yang (2020), Journal of Futures Markets:
   "Intraday time-series momentum: Evidence from China",
   DOI 10.1002/fut.22084.
   Reports first-half-hour return predicts last-half-hour return across four
   Chinese commodity futures contracts.
3. Zhang, Wang & Li (2020), Research in International Business and Finance:
   "Intraday momentum in Chinese commodity futures markets",
   DOI 10.1016/j.ribaf.2020.101278.
   Reports first-half-hour return predicts last-half-hour return and that the
   predictive content mainly comes from the opening half-hour component.
4. Rosa (2022), Journal of Futures Markets:
   "Understanding intraday momentum strategies", DOI 10.1002/fut.22375.
   Provides an important out-of-sample caution: a related overnight-return
   predictor loses predictability out of sample.

Frozen NIFTY-futures translation
--------------------------------
Use the corrected V2 five-minute futures data only:
- opening-half-hour signal = corrected net_return_6_bps on the 09:40 event row,
  which spans the first six five-minute bars from the 09:15 futures open through
  the close of the 09:40-09:44 bar;
- direction = sign(opening-half-hour return);
- closing-half-hour execution = the 14:55 event row's already-frozen entry_price,
  which is the 15:00 one-minute futures open;
- exit = that row's h30 terminal price at the 15:29 one-minute close.

There is one formulation only. No magnitude threshold, volatility/volume
conditioning, overnight component, options/OI/VIX filter, time variant, or
reversal alternative is tested.
"""
from __future__ import annotations

PROTOCOL_VERSION = "SHORT_SWING_EXTERNAL_OPEN_CLOSE_INTRADAY_MOMENTUM_V1"
PAUSE_VERSION = "SHORT_SWING_V2_HYPOTHESIS_MINING_PAUSE_V1"
SOURCE_EVENT_SHA256 = (
    "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
)
SOURCE_PROTOCOL_VERSION = "SHORT_SWING_DEVELOPMENT_V2"
SOURCE_SESSIONS = 232
SOURCE_EVENTS = 17400
SOURCE_COHORTS = ("cohort1", "cohort2", "cohort3")
SOURCE_BLOCKS = 22

EXTERNAL_SOURCES = [
    {
        "authors": "Gao, Han, Li, Zhou",
        "year": 2018,
        "title": "Market intraday momentum",
        "journal": "Journal of Financial Economics",
        "doi": "10.1016/j.jfineco.2018.05.009",
        "role": "first-half-hour predicts last-half-hour market return",
    },
    {
        "authors": "Jin, Kearney, Li, Yang",
        "year": 2020,
        "title": "Intraday time-series momentum: Evidence from China",
        "journal": "Journal of Futures Markets",
        "doi": "10.1002/fut.22084",
        "role": "first-half-hour predicts last-half-hour commodity-futures return",
    },
    {
        "authors": "Zhang, Wang, Li",
        "year": 2020,
        "title": "Intraday momentum in Chinese commodity futures markets",
        "journal": "Research in International Business and Finance",
        "doi": "10.1016/j.ribaf.2020.101278",
        "role": (
            "commodity-futures replication; predictive content mainly opening "
            "half-hour rather than close-to-open component"
        ),
    },
    {
        "authors": "Rosa",
        "year": 2022,
        "title": "Understanding intraday momentum strategies",
        "journal": "Journal of Futures Markets",
        "doi": "10.1002/fut.22375",
        "role": "out-of-sample caution for related overnight-to-close predictor",
    },
]

HYPOTHESIS = {
    "name": "external_opening_half_hour_to_closing_half_hour_momentum",
    "direction": "sign of same-session opening-half-hour futures return",
    "economic_rationale": (
        "published intraday-momentum evidence links early-session directional "
        "returns to same-direction last-half-hour returns, potentially through "
        "infrequent portfolio rebalancing and late-informed trading."
    ),
    "external_before_v2_outcome_inspection": True,
}

FEATURE = {
    "signal_timestamp": "09:40 five-minute event row",
    "opening_window": "09:15 futures open through 09:44:59 futures close",
    "source_feature": "corrected V2 net_return_6_bps",
    "signal_direction": "sign(net_return_6_bps at 09:40)",
    "nonzero_signal_required": True,
    "overnight_return_component": "excluded",
}

PRIMARY_RULE = {
    "signal_row_time": "09:40",
    "trade_row_time": "14:55",
    "entry_time": "15:00 one-minute futures open",
    "exit_time": "15:29 one-minute futures close",
    "holding_minutes": 30,
    "signal_magnitude_threshold": None,
    "volume_filter": "off",
    "volatility_filter": "off",
    "OI_filter": "off",
    "options_fast_lead_filter": "off",
    "VIX_filter": "off",
    "overnight_filter": "off",
    "formulations": 1,
}

EXECUTION = {
    "signal_known_by": "09:45",
    "entry": "14:55 event row entry_price = 15:00 one-minute futures open",
    "outcome": "14:55 event row h30_terminal_bps ending at 15:29 close",
    "one_trade_per_session": True,
    "stops": False,
    "targets": False,
    "same_bar_path_assumptions": False,
}

STRUCTURAL_GATE = {
    "minimum_pooled_trades": 150,
    "minimum_trades_each_cohort": 40,
    "mean_gross_bps_positive_each_cohort": True,
    "minimum_positive_chronological_blocks": 15,
    "total_chronological_blocks": 22,
    "pooled_session_cluster_bootstrap_95pct_lower_gt_zero": True,
    "bootstrap_seed": 20261002,
    "bootstrap_samples": 10000,
    "cost_gate": (
        "structural gate is gross; a pass requires a separately frozen exact-option "
        "implementation protocol before candidate status"
    ),
}

DECISION_RULE = {
    "structural_pass_action": (
        "freeze a separate exact directional option implementation protocol "
        "before inspecting option P&L"
    ),
    "no_structural_pass_action": (
        "record rejection; do not add magnitude, volatility, volume, overnight, "
        "or other conditioning"
    ),
    "candidate_freeze_at_this_stage": False,
    "blind_validation_at_this_stage": False,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "external_hypothesis_path": True,
    "single_primary_rule_only": True,
    "one_trade_per_session": True,
    "no_signal_magnitude_search": True,
    "no_volume_conditioning": True,
    "no_volatility_conditioning": True,
    "no_overnight_component": True,
    "no_OI_filter": True,
    "no_options_fast_lead_filter": True,
    "no_VIX_filter": True,
    "no_alternate_entry_time": True,
    "no_alternate_exit_time": True,
    "no_post_hoc_reversal_test": True,
    "no_prior_hypothesis_retest_or_rescue": True,
    "fresh_blind_data_used": False,
    "strategy_d_remains_paused": True,
}
