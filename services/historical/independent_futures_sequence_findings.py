"""Recorded development-only findings from the futures sequence atlas.

No candidate is frozen here. Strong movement-state observations are explicitly
kept separate from the paused target-sizing work.
"""

FUTURES_SEQUENCE_ATLAS_FINDING_V1 = {
    "finding_id": "NIFTY_FUTURES_SEQUENCE_ATLAS_V1",
    "research_only": True,
    "development_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "target_sizing_restarted": False,
    "source": {
        "futures_sha256": "3724809a5ddc1c0dce05990b6b405442bb299406f13af585b589a7b522cdc21e",
        "india_vix_sha256": "c2167725d89e2bf2b30843a44dcec2a1fdab7aeb48b60773ed12b9245f4523ac",
        "options_sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "sessions": 80,
        "futures_rows": 6000,
        "chronological_blocks": 8,
    },
    "fixed_discovery_design": {
        "sequence_windows_bars": [3, 6],
        "future_horizons_minutes": [10, 15, 30, 60],
        "outcome_entry": "next 5-minute bar open",
        "features": [
            "net return",
            "path length",
            "path efficiency",
            "range",
            "close location in sequence range",
            "volume expansion versus preceding equal window",
            "OI change",
            "price x OI interaction",
            "absolute-return acceleration",
        ],
        "threshold_optimization": False,
        "pnl_optimization": False,
    },
    "movement_state": {
        "three_bar_range_vs_future_max_excursion": {
            "10m": {"spearman": 0.42753334354942674, "positive_blocks": "8/8"},
            "15m": {"spearman": 0.42053069247136204, "positive_blocks": "8/8"},
            "30m": {"spearman": 0.396422395437978, "positive_blocks": "8/8"},
            "60m": {"spearman": 0.3489566977337475, "positive_blocks": "8/8"},
        },
        "six_bar_range_vs_future_max_excursion": {
            "10m": {"spearman": 0.40538524249333735, "positive_blocks": "8/8"},
            "15m": {"spearman": 0.4002146507478463, "positive_blocks": "8/8"},
            "30m": {"spearman": 0.382956711009978, "positive_blocks": "8/8"},
            "60m": {"spearman": 0.34440584463480484, "positive_blocks": "8/8"},
        },
        "other_30m_max_excursion_spearman": {
            "three_bar_path_length": 0.3066600074917443,
            "six_bar_path_length": 0.3533942108768912,
            "three_bar_oi_change": 0.08708036121137643,
            "six_bar_oi_change": 0.10264694057076443,
            "three_bar_volume_expansion": -0.014086122293857244,
            "six_bar_volume_expansion": 0.017068782678747497,
        },
        "cross_fitted_three_bar_range_after_context": {
            "controls": [
                "INDIA VIX level",
                "INDIA VIX 5-minute change",
                "INDIA VIX 15-minute change",
                "time bucket",
                "futures-contract DTE",
                "current futures return",
                "futures volume",
                "futures OI change",
            ],
            "future_30m_max_excursion_residual_spearman": 0.21613479051250264,
            "positive_blocks": "8/8",
        },
        "vix_incremental_over_three_bar_range_and_futures_state": {
            "future_30m_max_excursion_residual_spearman": 0.35156514603422623,
            "positive_blocks": "8/8",
        },
        "atm_straddle_incremental_over_range_vix_and_futures_state": {
            "future_30m_max_excursion_residual_spearman": 0.11081182875143882,
            "positive_blocks": "5/8",
            "interpretation": (
                "The strongest previously observed longer-horizon options state "
                "does not add chronologically stable movement information once "
                "recent futures range and VIX are known."
            ),
        },
        "classification": "ROBUST_FUTURES_VOLATILITY_CLUSTERING_WITH_VIX_CONTEXT",
        "candidate_status": "DESCRIPTIVE_ONLY_TARGET_SIZING_REMAINS_PAUSED",
    },
    "directional_state": {
        "strongest_fixed_descriptor": "three_bar_close_location",
        "future_10m_return_spearman": -0.06795995397150808,
        "negative_blocks": "8/8",
        "controlled_standardized_coefficient_bps": -0.47681874816893244,
        "controlled_negative_blocks": "8/8",
        "controls": [
            "three-bar net return",
            "three-bar range",
            "three-bar path length",
            "three-bar volume expansion",
            "three-bar OI change",
            "current bar return",
            "time bucket",
            "futures-contract DTE",
        ],
        "absolute_close_location_quartiles_descriptive_only": {
            "warning": "no threshold is frozen",
            "top_quartile_mean_reversal_aligned_10m_bps": 0.5973341206897782,
            "top_quartile_median_reversal_aligned_10m_bps": 0.833673783488531,
            "top_quartile_hit_rate": 0.5438356164383562,
            "top_quartile_positive_mean_blocks": "6/8",
        },
        "interpretation": (
            "Short-horizon mean reversion is chronologically consistent as a "
            "continuous relationship but too small and not strong enough in the "
            "descriptive extreme quartile to justify a candidate freeze."
        ),
        "candidate_status": "DESCRIPTIVE_ONLY_NOT_O3",
    },
    "decision": {
        "status": "NO_NEW_CANDIDATE_FREEZE",
        "reason": (
            "The fixed futures atlas finds robust volatility clustering and VIX "
            "conditioning, but that belongs to movement-state/target-sizing work "
            "which remains paused. The strongest directional sequence is too small. "
            "Options do not add stable longer-horizon movement information on top "
            "of recent futures range plus VIX."
        ),
        "consume_fresh_blind_block": False,
        "mine_more_filters_from_same_80_sessions": False,
    },
}
