"""Frozen protocol for signed-volume / price-divergence catch-up.

Hypothesis
----------
Actual futures trading volume is signed by each completed five-minute futures
return. Over the latest three return-bearing bars, a strong signed-volume
imbalance that points opposite the three-bar net price response may indicate
order-flow pressure that has not yet been incorporated into price. The frozen
test asks whether price subsequently moves in the signed-volume direction.

This is distinct from:
- low-volume price-shock reversal,
- high-volume/OI impulse reversal,
- latent OI build-up,
- directional-efficiency continuation,
- trend-pullback continuation,
- futures/spot dislocation,
- session-anchor deviation,
- opening displacement,
- compression-release,
- wick/failed-breakout reversal.

The rule is frozen before its outcomes are inspected on the corrected V2 corpus.
"""
from __future__ import annotations

PROTOCOL_VERSION = "SHORT_SWING_SIGNED_VOLUME_PRICE_DIVERGENCE_V1"
SOURCE_EVENT_SHA256 = (
    "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
)
SOURCE_PROTOCOL_VERSION = "SHORT_SWING_DEVELOPMENT_V2"
SOURCE_SESSIONS = 232
SOURCE_EVENTS = 17400
SOURCE_COHORTS = ("cohort1", "cohort2", "cohort3")
SOURCE_BLOCKS = 22

HYPOTHESIS = {
    "name": "three_bar_signed_volume_price_divergence_catch_up",
    "direction": "signed futures-volume imbalance direction",
    "economic_rationale": (
        "persistent return-signed futures volume can proxy directional order-flow "
        "pressure. When that pressure is strong but the three-bar net price move "
        "has the opposite sign, subsequent price may catch up to the pressure."
    ),
}

FEATURE = {
    "window_bars": 3,
    "same_session_only": True,
    "full_window_required": True,
    "bar_sign": "sign(futures_return_bps); zero return contributes zero signed volume",
    "signed_volume": "bar_sign * actual futures_volume",
    "signed_volume_imbalance_3": (
        "sum(signed_volume over current and prior two bars) / "
        "sum(actual futures_volume over the same three bars)"
    ),
    "price_response": "net_return_3_bps from corrected V2 corpus",
    "divergence": (
        "signed_volume_imbalance_3 and net_return_3_bps have opposite nonzero signs"
    ),
}

PRIMARY_RULE = {
    "abs_signed_volume_imbalance_3_min": 1.0 / 3.0,
    "interpretation": (
        "At least two-thirds of the three-bar volume is associated with bars "
        "moving in the signal direction when return signs are binary; threshold "
        "is fixed a priori and is not percentile-tuned."
    ),
    "net_return_3_nonzero": True,
    "divergence_required": True,
    "signal_direction": "sign(signed_volume_imbalance_3)",
    "options_fast_lead_filter": "off",
    "OI_filter": "off",
    "formulations": 1,
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute bar labelled t",
    "entry": "one-minute futures open at t+5 minutes",
    "fixed_exit_minutes": [5, 10, 15, 30],
    "non_overlapping_trades_within_each_horizon": True,
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
    "bootstrap_seed": 20260929,
    "bootstrap_samples": 10000,
    "cost_gate": (
        "structural gate is gross; any structural pass requires a separately "
        "frozen exact-option implementation protocol before candidate status"
    ),
}

DECISION_RULE = {
    "structural_pass_action": (
        "freeze a separate exact directional option implementation protocol "
        "before inspecting option P&L"
    ),
    "no_structural_pass_action": "record rejection; no rescue or alternate thresholds",
    "candidate_freeze_at_this_stage": False,
    "blind_validation_at_this_stage": False,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "single_primary_rule_only": True,
    "no_percentile_search": True,
    "no_alternate_imbalance_thresholds": True,
    "no_return_magnitude_threshold": True,
    "no_volume_percentile_threshold": True,
    "no_options_fast_lead_filter": True,
    "no_OI_filter": True,
    "no_VIX_filter": True,
    "no_time_filter": True,
    "no_DTE_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_direction_flip": True,
    "no_prior_hypothesis_retest_or_rescue": True,
    "strategy_d_remains_paused": True,
}
