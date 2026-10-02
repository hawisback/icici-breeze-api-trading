"""Recorded structural pass for low-volume price-shock reversal.

The frozen 5-minute formulation passed every predeclared structural criterion.
This is not an implementation candidate: its futures gross edge is far below
the recorded reference futures round-trip cost. Exact directional long-option
P&L therefore requires a separately frozen protocol before inspection.
"""

LOW_VOLUME_PRICE_SHOCK_REVERSAL_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_LOW_VOLUME_PRICE_SHOCK_REVERSAL_RECORDED_V1",
    "protocol_version": "SHORT_SWING_LOW_VOLUME_PRICE_SHOCK_REVERSAL_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "source": {
        "event_dataset_sha256": (
            "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
        ),
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "derived_current_abs_return_bps_min": 4.65280279654,
    "passing_formulation": {
        "formulation_id": "ret70_volle1_reversal_opt_off_h5",
        "current_abs_return_percentile_min": 70,
        "volume_vs_prior3_mean_max": 1.0,
        "options_fast_lead_filter": "off",
        "OI_filter": "off",
        "exit_minutes": 5,
        "trades": 1648,
        "trades_by_cohort": {"cohort1": 979, "cohort2": 669},
        "sessions_traded": 151,
        "pooled_mean_bps": 0.35906800155151697,
        "cohort1_mean_bps": 0.29797002861818184,
        "cohort2_mean_bps": 0.448477441763378,
        "positive_chronological_blocks": 10,
        "session_cluster_bootstrap_mean_95pct_bps": [
            0.061384960182667904,
            0.6595313403438255,
        ],
        "reference_current_futures_roundtrip_cost_bps_before_slippage": (
            5.95293863340026
        ),
        "gross_minus_reference_cost_bps": -5.593870631848743,
        "structural_gate_pass": True,
    },
    "nonpassing_neighbors": {
        "h10": {
            "pooled_mean_bps": 0.5094391451947993,
            "cohort1_mean_bps": 0.3559156506220551,
            "cohort2_mean_bps": 0.7330007303572992,
            "positive_chronological_blocks": 8,
            "bootstrap_95pct_bps": [
                0.01001002864416124,
                1.0073337516274392,
            ],
            "failure": "chronological_block_stability",
        },
        "h15": {
            "pooled_mean_bps": 0.3697313262603284,
            "cohort1_mean_bps": 0.45814726136170847,
            "cohort2_mean_bps": 0.24413630547824275,
            "positive_chronological_blocks": 10,
            "bootstrap_95pct_bps": [
                -0.22688249791643142,
                0.9835613181936241,
            ],
            "failure": "bootstrap_lower_bound_not_positive",
        },
        "h30": {
            "pooled_mean_bps": -0.10405242036937792,
            "cohort1_mean_bps": -0.12026759092875003,
            "cohort2_mean_bps": -0.0821892690533709,
            "positive_chronological_blocks": 5,
            "bootstrap_95pct_bps": [
                -0.9648728975965178,
                0.7277695085861456,
            ],
            "failure": "multiple_structural_gate_failures",
        },
    },
    "decision": "STRUCTURAL_PASS_REQUIRES_SEPARATE_OPTIONS_PROTOCOL",
    "interpretation": (
        "The five-minute low-volume price-shock reversal effect is positive in both "
        "inspected cohorts, stable in ten of fourteen chronological blocks, and has "
        "a positive pooled session-cluster bootstrap lower bound. Its 0.3591 bps "
        "gross futures mean is nevertheless far below the recorded 5.9529 bps "
        "reference futures round-trip cost before slippage, so it is not a futures "
        "implementation candidate."
    ),
    "guardrails": {
        "no_futures_candidate_freeze": True,
        "no_blind_validation": True,
        "exact_option_pnl_requires_separate_frozen_protocol": True,
        "no_other_return_thresholds": True,
        "no_other_volume_thresholds": True,
        "no_options_filter_addition": True,
        "no_time_or_DTE_filters": True,
        "no_stop_target_search": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
