"""Frozen development-only candidate for options-behavior discovery v2.

This candidate was developed exclusively from the SHA-bound 80-session development
corpus. OPTIONS_BLIND_08 and OPTIONS_BLIND_09 were already inspected during O1
validation and are explicitly ineligible for validating this candidate.
"""

OPTIONS_BEHAVIOR_VERSION = "OPTIONS_BEHAVIOR_V2"

DEVELOPMENT_CORPUS = {
    "filename": "independent_nifty_options_research_development_80_sessions.json",
    "sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
    "start_date": "2026-05-19",
    "end_date": "2026-09-09",
    "sessions": 80,
    "underlying_rows": 6000,
    "dynamic_option_rows": 108000,
    "dynamic_contracts_per_timestamp": 18,
    "strike_step_points": 50,
    "strike_radius_each_side": 4,
    "rights": ["CE", "PE"],
    "contract_stitching": False,
}

O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION = {
    "candidate_id": "O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION",
    "role": "NON_DIRECTIONAL_FUTURE_MOVEMENT_REGIME",
    "status": "FROZEN_FOR_FRESH_BLIND_VALIDATION",
    "directional_claim": False,
    "implementation_allowed": False,
    "bar_interval": "5m",
    "option_universe": {
        "expiry": "nearest explicitly supplied non-expired weekly NIFTY option expiry",
        "strikes": "dynamic futures-referenced ATM +/- 4 strikes",
        "right": ["CE", "PE"],
        "contract_returns_and_oi_changes": "exact expiry+strike+right only; never stitch contracts",
    },
    "state_features": {
        "current_futures_move": "absolute current 5m futures close-to-close return in bps",
        "option_volume": "sum current 5m volume across dynamic ATM +/-4 CE+PE",
        "option_oi_activity": "sum absolute 5m OI changes across exact dynamic ATM +/-4 CE+PE contracts",
        "option_volume_hhi": (
            "Herfindahl-Hirschman concentration of current 5m option volume shares across the "
            "18 dynamic contracts; lower values mean volume is distributed across more contracts"
        ),
        "futures_volume": "current 5m futures volume",
        "futures_oi_activity": "absolute current 5m futures OI change",
        "dte": "calendar days from session date to selected option expiry",
        "time_bucket": "30-minute bucket from the 09:15 session open",
    },
    "frozen_threshold_protocol": {
        "threshold_source": "DEVELOPMENT_CORPUS only",
        "quantile_interpolation": "linear",
        "current_futures_move": "<= Q25 within development DTE x 30-minute time bucket",
        "option_volume": ">= Q75 within development DTE x 30-minute time bucket",
        "option_oi_activity": ">= Q75 within development DTE x 30-minute time bucket",
        "option_volume_hhi": (
            "<= Q75 within development DTE x 30-minute time bucket; this excludes only the "
            "most-concentrated quartile rather than selecting the strongest backtest cutoff"
        ),
        "high_futures_volume": ">= Q75 within development 30-minute time bucket",
        "high_futures_oi_activity": ">= Q75 within development 30-minute time bucket",
        "exclusion": "exclude when high_futures_volume AND high_futures_oi_activity",
        "blind_rule": "derive all thresholds from the exact SHA-256 development corpus, then apply unchanged",
    },
    "episode_rule": "false-to-true state transition within a session",
    "primary_outcome": "next 30m maximum absolute NIFTY futures excursion in bps, excluding the signal bar",
    "primary_comparison": {
        "matching": "same validation block, same DTE, same 30-minute time bucket",
        "control_pool": (
            "bars satisfying low current futures move and not simultaneous high futures volume+high futures "
            "OI activity, excluding O2-qualified bars, with a full outcome horizon"
        ),
        "episode_score": (
            "episode excursion minus mean/median matched-control excursion; if no matched controls exist, "
            "the episode is unscorable rather than silently using a broader control"
        ),
        "reason": "make DTE/time composition part of the pre-specified validation design",
    },
    "secondary_outcomes": [
        "next 15m maximum absolute futures excursion in bps with identical matching",
        "next 60m maximum absolute futures excursion in bps with identical matching",
    ],
    "development_results": {
        "episode_starts": 75,
        "sessions_with_any_episode_start": 47,
        "primary_30m": {
            "episode_starts_with_full_horizon": 70,
            "matched_scorable_episodes": 67,
            "sessions_with_matched_episodes": 43,
            "episode_mean_excursion_bps": 17.292649137415143,
            "average_matched_control_mean_excursion_bps": 14.292384248445595,
            "mean_matched_residual_bps": 3.000264888969547,
            "median_matched_residual_bps": 1.762765094075469,
            "positive_mean_residual_blocks": "8/8",
            "positive_median_residual_blocks": "7/8",
            "blocks": {
                "1": {"episodes": 4, "mean_residual_bps": 12.791416421193507, "median_residual_bps": 12.929437344357808},
                "2": {"episodes": 11, "mean_residual_bps": 6.3676247802045935, "median_residual_bps": 6.079331634287197},
                "3": {"episodes": 4, "mean_residual_bps": 4.5889191295431235, "median_residual_bps": 0.7406385176833563},
                "4": {"episodes": 8, "mean_residual_bps": 2.7881493677956355, "median_residual_bps": 1.6344310330837484},
                "5": {"episodes": 12, "mean_residual_bps": 1.1085650345632359, "median_residual_bps": -0.2307796323480762},
                "6": {"episodes": 8, "mean_residual_bps": 0.4656846064454593, "median_residual_bps": 0.9161293279572043},
                "7": {"episodes": 13, "mean_residual_bps": 1.3978476813927756, "median_residual_bps": 0.8301891741452359},
                "8": {"episodes": 7, "mean_residual_bps": 0.5638658155669927, "median_residual_bps": 2.050985277136654},
            },
        },
        "horizon_15m": {
            "matched_scorable_episodes": 69,
            "mean_matched_residual_bps": 1.5402236955497843,
            "median_matched_residual_bps": 0.9083459037428909,
            "positive_mean_residual_blocks": "6/8",
            "positive_median_residual_blocks": "5/8",
        },
        "horizon_60m": {
            "matched_scorable_episodes": 61,
            "mean_matched_residual_bps": 1.8251338979063125,
            "median_matched_residual_bps": 1.3461665974046078,
            "positive_mean_residual_blocks": "6/8",
            "positive_median_residual_blocks": "5/8",
        },
        "dte_30m_matched_residual_bps": {
            "0": {"episodes": 14, "mean": 5.419824, "median": 2.068284},
            "1": {"episodes": 14, "mean": 3.731757, "median": 1.508690},
            "4": {"episodes": 14, "mean": 1.897119, "median": 0.842917},
            "5": {"episodes": 11, "mean": 1.481952, "median": 1.710231},
            "6": {"episodes": 14, "mean": 2.145320, "median": 2.191018},
        },
        "daypart_30m_matched_residual_bps": {
            "early": {"episodes": 26, "mean": 1.846019, "median": 1.716171},
            "middle": {"episodes": 26, "mean": 3.258417, "median": 2.088612},
            "late": {"episodes": 15, "mean": 4.553495, "median": 2.678356},
        },
        "concentration_diagnostic_within_o1_like_bars": {
            "description": (
                "Development-only bar-level diagnostic after matching DTE/time composition. "
                "Future excursion residual declined as volume concentration increased."
            ),
            "hhi_q1_most_diffuse": {"bars": 18, "mean_residual_bps": 3.810798273719561, "median_residual_bps": 2.6866168732751774},
            "hhi_q2": {"bars": 25, "mean_residual_bps": 1.668214082147311, "median_residual_bps": 1.2815266270996268},
            "hhi_q3": {"bars": 28, "mean_residual_bps": 0.6934662511218909, "median_residual_bps": 0.8970546373239516},
            "hhi_q4_most_concentrated": {"bars": 26, "mean_residual_bps": 0.6153510149981779, "median_residual_bps": -0.2917921089533788},
        },
        "hhi_cutoff_sensitivity": {
            "Q25_diffuse_only": {
                "matched_episodes": 17,
                "blocks_with_events": 7,
                "positive_mean_blocks": "7/7",
                "positive_median_blocks": "7/7",
                "mean_residual_bps": 5.935588951285073,
            },
            "Q50": {
                "matched_episodes": 41,
                "blocks_with_events": 8,
                "positive_mean_blocks": "8/8",
                "positive_median_blocks": "7/8",
                "mean_residual_bps": 4.252013843318137,
            },
            "Q75_frozen": {
                "matched_episodes": 67,
                "blocks_with_events": 8,
                "positive_mean_blocks": "8/8",
                "positive_median_blocks": "7/8",
                "mean_residual_bps": 3.000264888969547,
            },
            "selection_reason": (
                "Q75 is frozen because it is the broadest tested natural quartile cutoff that preserves "
                "the cross-block pattern; it was not selected for maximum development lift."
            ),
        },
        "b2_overlap_diagnostic": {
            "episode_starts_with_valid_futures_rvol20": 54,
            "episodes_also_rvol20_gte_1_50": 16,
            "overlap_pct": 29.629629629629626,
            "interpretation": "O2 is not simply the frozen B2 high-futures-volume regime.",
        },
    },
    "research_interpretation": (
        "High option volume and OI activity appear more informative when participation is distributed across "
        "the near-ATM option surface rather than concentrated in a few contracts. This remains a non-directional "
        "movement-regime hypothesis and requires a fresh blind block."
    ),
}

BLIND_VALIDATION_PROTOCOL = {
    "candidate": "O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION",
    "working_name": "OPTIONS_BLIND_10",
    "sessions": 10,
    "ineligible_for_validation": [
        "OPTIONS_BLIND_08",
        "OPTIONS_BLIND_09",
        "all development sessions 2026-05-19 through 2026-09-09",
        "any previously inspected block used to design or diagnose O2",
    ],
    "exact_window_must_be_frozen_before_collection": True,
    "parameter_changes_before_scoring": False,
    "mine_blind_for_new_rules": False,
    "thresholds_from_blind": False,
    "primary_score": "30m matched residual versus same-DTE same-30m-bucket frozen-protocol controls",
    "directional_score": None,
    "production_implementation_from_freeze": False,
}
