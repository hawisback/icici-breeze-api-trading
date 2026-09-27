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


OPTIONS_BLIND_09 = {
    "candidate_id": "O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION",
    "source": {
        "filename": "independent_nifty_options_research_blind_09.json",
        "sha256": "74a3d3ef1745c92de92193485074eda4bce1a3950c0fa2092962909a501c250f",
        "start_date": "2026-01-23",
        "end_date": "2026-02-06",
        "sessions": 10,
        "underlying_rows": 750,
        "raw_option_rows": 36600,
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
        "scorable_episodes": 7,
        "sessions_with_episodes": 3,
        "control_bars": 72,
        "episode_mean_excursion_bps": 27.049839159406535,
        "control_mean_excursion_bps": 22.01515362924854,
        "mean_lift_bps": 5.034685530157994,
        "episode_median_excursion_bps": 23.11284978643625,
        "control_median_excursion_bps": 20.43698400476152,
        "median_lift_bps": 2.67586578167473,
        "pre_specified_direction_matched_mean": True,
        "pre_specified_direction_matched_median": True,
    },
    "secondary_15m": {
        "scorable_episodes": 7,
        "control_bars": 79,
        "mean_lift_bps": 5.442707813823933,
        "median_lift_bps": 7.368976275515399,
    },
    "secondary_60m": {
        "scorable_episodes": 7,
        "control_bars": 64,
        "mean_lift_bps": 2.790800971529407,
        "median_lift_bps": 0.26621573131378895,
    },
    "post_blind_evaluation_diagnostics": {
        "episode_distribution": {
            "dte_4_episodes": 6,
            "dte_1_episodes": 1,
            "sessions": {
                "2026-01-23": 3,
                "2026-02-02": 1,
                "2026-02-06": 3,
            },
        },
        "common_language_effect_probability": 0.5952380952380952,
        "session_cluster_bootstrap_30m": {
            "seed": 123,
            "resamples": 20000,
            "valid_resamples": 19446,
            "mean_lift_95pct_interval_bps": [-6.459693932848486, 17.45956329902188],
            "median_lift_95pct_interval_bps": [-1.9385158934303277, 18.47550792246493],
            "mean_lift_positive_resample_fraction": 0.695361513936028,
            "median_lift_positive_resample_fraction": 0.8447495628921114,
            "interpretation": "Very wide intervals reflect only seven episodes concentrated in three sessions.",
        },
        "leave_one_session_out_30m": {
            "mean_lift_positive_for_all_10_omissions": False,
            "median_lift_positive_for_all_10_omissions": True,
            "jan_23_removed_mean_lift_bps": -3.1249076512694707,
            "jan_23_removed_median_lift_bps": 1.1041508960043878,
            "interpretation": "The positive mean result is materially helped by 2026-01-23; the median is less fragile.",
        },
        "composition_checks": {
            "mean_residual_after_matching_dte_only_bps": 2.75803992890462,
            "mean_residual_after_matching_time_bucket_only_bps": 6.7860187053701235,
            "mean_residual_after_matching_dte_and_time_bucket_bps": 5.727672201816212,
            "exact_match_scorable_episodes": 6,
            "warning": "Positive matched residuals are encouraging but based on only six exactly matched episodes.",
        },
        "large_move_diagnostic": {
            "development_control_q75_30m_excursion_bps": 17.003605789770184,
            "blind_episode_fraction_above_development_control_q75": 0.7142857142857143,
            "blind_control_fraction_above_development_control_q75": 0.5833333333333334,
        },
    },
    "interpretation": (
        "The frozen primary 30-minute score is positive for a second blind block, but Blind09 "
        "contains only seven scorable episodes across three sessions and the mean result is "
        "sensitive to removing 2026-01-23. This is support with substantial sampling uncertainty."
    ),
    "candidate_status_after_blind": "FROZEN_RESEARCH_ONLY_SECOND_BLIND_SUPPORT_SPARSE",
    "implementation_allowed": False,
    "retune_from_blind_allowed": False,
}


OPTIONS_BLIND_08_09_COMBINED_EVALUATION = {
    "candidate_id": "O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION",
    "blind_blocks": ["OPTIONS_BLIND_08", "OPTIONS_BLIND_09"],
    "sessions": 20,
    "primary_30m": {
        "scorable_episodes": 22,
        "sessions_with_episodes": 11,
        "control_bars": 213,
        "episode_mean_excursion_bps": 18.296115814373163,
        "control_mean_excursion_bps": 15.966669382156311,
        "mean_lift_bps": 2.3294464322168515,
        "episode_median_excursion_bps": 13.209277216286257,
        "control_median_excursion_bps": 12.44363937072931,
        "median_lift_bps": 0.765637845556947,
        "positive_frozen_primary_blocks_mean": "2/2",
        "positive_frozen_primary_blocks_median": "2/2",
        "common_language_effect_probability": 0.5729833546734955,
    },
    "secondary_15m": {
        "scorable_episodes": 22,
        "control_bars": 223,
        "mean_lift_bps": 3.112893642737509,
        "median_lift_bps": 1.125641911431659,
    },
    "secondary_60m": {
        "scorable_episodes": 21,
        "control_bars": 192,
        "mean_lift_bps": 0.9307693313257843,
        "median_lift_bps": -3.8008168144632606,
    },
    "session_cluster_bootstrap_30m": {
        "seed": 123,
        "resamples": 20000,
        "mean_lift_95pct_interval_bps": [-2.9053551946341436, 8.72899343185486],
        "median_lift_95pct_interval_bps": [-2.0460439902486955, 8.012666560040039],
        "mean_lift_positive_resample_fraction": 0.7484,
        "median_lift_positive_resample_fraction": 0.75905,
    },
    "composition_checks": {
        "mean_residual_after_matching_dte_only_bps": 1.1534500008221078,
        "mean_residual_after_matching_time_bucket_only_bps": 2.3109092307282935,
        "mean_residual_after_matching_dte_and_time_bucket_bps": 0.23080188285661893,
        "median_residual_after_matching_dte_and_time_bucket_bps": -3.7624505393238703,
        "interpretation": (
            "The pooled frozen headline score remains positive, but exact DTE x time-bucket "
            "matching removes most of the mean lift and leaves a negative median residual. This "
            "weakens any claim that O1 adds much incremental information beyond regime composition."
        ),
    },
    "large_move_diagnostic": {
        "development_control_q75_30m_excursion_bps": 17.003605789770184,
        "episode_fraction_above_threshold": 0.4090909090909091,
        "control_fraction_above_threshold": 0.3474178403755869,
    },
    "research_conclusion": (
        "Two blind blocks support the frozen aggregate near-term expansion association, but the "
        "combined sample is small, bootstrap intervals cross zero, and composition-adjusted "
        "incremental lift is weak. Preserve O1 as a descriptive research regime; do not promote "
        "it to strategy logic or retune it from blind data."
    ),
    "implementation_allowed": False,
    "retune_from_blind_allowed": False,
}


OPTIONS_BLIND_10_O2 = {
    "candidate_id": "O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION",
    "source": {
        "filename": "independent_nifty_options_research_blind_10.json",
        "sha256": "e15b86efac489802eb5a8fca20f30d05f5fb88cd77dcc75e893332a812da136c",
        "start_date": "2026-01-08",
        "end_date": "2026-01-22",
        "sessions": 10,
        "underlying_rows": 750,
        "raw_option_rows": 31800,
        "dynamic_option_rows": 13500,
        "dynamic_contracts_per_timestamp": 18,
        "failed_requests": 0,
    },
    "scoring": {
        "threshold_source": "frozen OPTIONS_BEHAVIOR_V2 DEVELOPMENT_CORPUS only",
        "development_sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "development_reproduction": {
            "episode_starts": 75,
            "primary_matched_episodes": 67,
            "primary_mean_matched_residual_bps": 3.000264888969547,
            "primary_median_matched_residual_bps": 1.762765094075469,
        },
        "threshold_changes": False,
        "candidate_definition_changes": False,
        "control_matching_changes": False,
        "directional_claim": False,
        "matching": "same blind block, same DTE, same 30-minute time bucket",
        "unmatched_episode_policy": "unscorable; no broader-control fallback",
    },
    "episode_counts": {
        "state_bars": 15,
        "episode_starts": 15,
        "sessions_with_episode_starts": 6,
    },
    "primary_30m": {
        "episode_starts_with_full_horizon": 15,
        "matched_scorable_episodes": 10,
        "sessions_with_matched_episodes": 5,
        "matching_coverage_pct": 66.66666666666667,
        "episode_mean_excursion_bps": 20.41751673211212,
        "average_matched_control_mean_excursion_bps": 20.279016735853038,
        "mean_matched_residual_bps": 0.1384999962590804,
        "median_matched_residual_bps": -0.7385012198385388,
        "episodes_positive_vs_matched_control_mean": 5,
        "episodes_positive_vs_matched_control_median": 5,
    },
    "secondary_15m": {
        "matched_scorable_episodes": 10,
        "mean_matched_residual_bps": 2.620883874676701,
        "median_matched_residual_bps": 2.8221201863740113,
    },
    "secondary_60m": {
        "matched_scorable_episodes": 10,
        "mean_matched_residual_bps": -7.449686747669782,
        "median_matched_residual_bps": -1.359341765959087,
    },
    "post_blind_evaluation_diagnostics": {
        "purpose": "Diagnostics after frozen scoring only; not eligible for retuning O2.",
        "primary_by_dte": {
            "0": {"episodes": 2, "mean_residual_bps": -0.29767073864111393, "median_residual_bps": 3.641630713270491},
            "1": {"episodes": 2, "mean_residual_bps": 3.4394293226602513, "median_residual_bps": 3.494120774726221},
            "4": {"episodes": 2, "mean_residual_bps": 1.1682766538382844, "median_residual_bps": 0.4982784797932267},
            "6": {"episodes": 4, "mean_residual_bps": -1.8087676727925216, "median_residual_bps": -1.280431326954118},
        },
        "unmatched_primary_episodes": 5,
        "development_primary_matching_coverage_pct": 95.71428571428572,
        "session_bootstrap_30m": {
            "seed": 123,
            "resamples": 20000,
            "valid_resamples": 19903,
            "mean_residual_95pct_interval_bps": [-6.41879236, 3.1654348],
            "median_residual_95pct_interval_bps": [-10.42205492, 5.38166512],
            "mean_residual_positive_resample_fraction": 0.4155654926393006,
            "median_residual_positive_resample_fraction": 0.27935487112495605,
        },
        "leave_one_session_out_30m": {
            "positive_mean_omissions": 6,
            "negative_mean_omissions": 4,
            "positive_median_omissions": 1,
            "negative_median_omissions": 9,
            "interpretation": "The near-zero aggregate mean is unstable to single-session removal; the median remains negative in most omissions.",
        },
    },
    "interpretation": (
        "Blind10 does not reproduce the development-sized O2 primary effect. The matched 30-minute "
        "mean residual is approximately zero and the median residual is negative. The 15-minute "
        "secondary result is positive while the 60-minute result is negative, and exact matching "
        "coverage is materially lower than in development. Preserve the frozen result without "
        "retuning or broadening controls."
    ),
    "candidate_status_after_blind": "FROZEN_RESEARCH_ONLY_FIRST_FRESH_BLIND_WEAK_OR_CONFLICTING",
    "implementation_allowed": False,
    "retune_from_blind_allowed": False,
}
