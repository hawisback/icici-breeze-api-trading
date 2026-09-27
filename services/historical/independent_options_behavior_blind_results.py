"""Recorded blind results for frozen independent options-behavior candidates.

These are immutable observations from pre-specified blind scoring. They do not
change the frozen candidate definition, thresholds, or implementation status.
"""

OPTIONS_BLIND_08 = {
    "candidate_id": "O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION",
    "source": {
        "filename": "independent_nifty_options_research_blind_08.json",
        "sha256": "cfb9fb78a85e6171ae4c93c3257e4ec8e356f7a5096982540208ecdc67e943ab",
        "start_date": "2026-02-09",
        "end_date": "2026-02-20",
        "sessions": 10,
        "underlying_rows": 750,
        "raw_option_rows": 27000,
        "dynamic_option_rows": 13500,
        "dynamic_contracts_per_timestamp": 18,
        "failed_requests": 0,
    },
    "scoring": {
        "threshold_source": "frozen DEVELOPMENT_CORPUS only",
        "threshold_changes": False,
        "candidate_definition_changes": False,
        "directional_claim": False,
        "episode_rule": "false-to-true state transition within a session",
        "excursion_semantics": (
            "maximum absolute excursion from signal-bar futures close using future futures "
            "high/low over the full horizon; signal bar excluded"
        ),
    },
    "primary_30m": {
        "scorable_episodes": 15,
        "sessions_with_episodes": 8,
        "control_bars": 141,
        "episode_mean_excursion_bps": 14.211044920024117,
        "control_mean_excursion_bps": 12.878081681513489,
        "mean_lift_bps": 1.3329632385106276,
        "episode_median_excursion_bps": 11.703591952905157,
        "control_median_excursion_bps": 10.047159340937917,
        "median_lift_bps": 1.6564326119672401,
        "pre_specified_direction_matched_mean": True,
        "pre_specified_direction_matched_median": True,
    },
    "secondary_15m": {
        "scorable_episodes": 15,
        "control_bars": 144,
        "mean_lift_bps": 2.2723549151835876,
        "median_lift_bps": 1.9439406862686859,
    },
    "secondary_60m": {
        "scorable_episodes": 14,
        "control_bars": 128,
        "mean_lift_bps": 0.0007535112240795172,
        "median_lift_bps": -1.9117785671851628,
    },
    "post_blind_evaluation_diagnostics": {
        "purpose": (
            "Stress tests performed after the frozen Blind08 score. These diagnostics are not "
            "candidate-selection rules and must not be used to retune O1."
        ),
        "development_overall_30m_lift_bps": {
            "mean": 2.7763035802363945,
            "median": 2.0263012949790813,
        },
        "blind_to_development_lift_ratio": {
            "mean": 0.4801215717184962,
            "median": 0.8174660975010618,
        },
        "blind_relative_lift_vs_control": {
            "mean_pct": 10.350635067210812,
            "median_pct": 16.486576511420026,
        },
        "common_language_effect_probability": {
            "development_episode_excursion_gt_control": 0.6044376987568661,
            "blind_episode_excursion_gt_control": 0.6094562647754137,
        },
        "session_cluster_bootstrap_30m": {
            "seed": 123,
            "resamples": 20000,
            "mean_lift_95pct_interval_bps": [-1.28340747, 4.55839973],
            "median_lift_95pct_interval_bps": [-0.74647667, 7.14518052],
            "mean_lift_positive_resample_fraction": 0.84005,
            "median_lift_positive_resample_fraction": 0.92085,
            "interpretation": "Intervals cross zero; the first blind block is not statistically conclusive.",
        },
        "leave_one_session_out_30m": {
            "mean_lift_positive_for_all_10_omissions": True,
            "median_lift_positive_for_all_10_omissions": True,
            "interpretation": "The aggregate positive result is not dependent on one single session.",
        },
        "composition_checks": {
            "development_mean_residual_after_matching_dte_only_bps": 2.7127301543610933,
            "blind_mean_residual_after_matching_dte_only_bps": 0.320452518451602,
            "development_mean_residual_after_matching_time_bucket_only_bps": 3.2216538259192755,
            "blind_mean_residual_after_matching_time_bucket_only_bps": -0.02455738211778898,
            "development_mean_residual_after_matching_dte_and_time_bucket_bps": 3.1188150568181743,
            "blind_mean_residual_after_matching_dte_and_time_bucket_bps": -2.8852446159382574,
            "blind_exact_match_scorable_episodes": 14,
            "warning": (
                "Exact DTE x time-bucket matching is sparse in a 10-session blind block. These "
                "post-hoc diagnostics suggest some aggregate lift may be compositional and should "
                "be challenged in additional untouched blocks, not repaired by retuning."
            ),
        },
        "large_move_diagnostic": {
            "development_control_q75_30m_excursion_bps": 17.003605789770184,
            "blind_episode_fraction_above_development_control_q75": 0.26666666666666666,
            "blind_control_fraction_above_development_control_q75": 0.22695035460992907,
            "interpretation": "Only a modest increase in the rate of very large moves in Blind08.",
        },
    },
    "interpretation": (
        "The frozen primary 30-minute movement-regime effect was positive on both mean and "
        "median in this first blind block, but smaller than development. The 60-minute secondary "
        "result was mixed. Post-blind matching diagnostics show that DTE/time composition may "
        "explain part of the aggregate lift. One blind block is support, not confirmation."
    ),
    "candidate_status_after_blind": "FROZEN_RESEARCH_ONLY_FIRST_BLIND_SUPPORT",
    "implementation_allowed": False,
    "retune_from_blind_allowed": False,
}
