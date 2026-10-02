"""Frozen manifest for the first options-behavior state discovered independently.

Research-only. This is a non-directional market-state hypothesis, not a trading
strategy. The development thresholds are derived only from the exact development
corpus identified by SHA-256 below; a blind scorer must never derive thresholds
from the blind corpus.
"""

OPTIONS_BEHAVIOR_VERSION = "OPTIONS_BEHAVIOR_V1"

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

O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION = {
    "candidate_id": "O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION",
    "role": "NON_DIRECTIONAL_FUTURE_MOVEMENT_REGIME",
    "status": "FROZEN_FOR_BLIND_VALIDATION",
    "directional_claim": False,
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
        "high_futures_volume": ">= Q75 within development 30-minute time bucket",
        "high_futures_oi_activity": ">= Q75 within development 30-minute time bucket",
        "exclusion": "exclude when high_futures_volume AND high_futures_oi_activity",
        "blind_rule": "derive these thresholds from the exact SHA-256 development corpus, then apply unchanged to blind data",
    },
    "episode_rule": "false-to-true state transition within a session",
    "primary_outcome": "next 30m maximum absolute NIFTY futures excursion in bps, excluding the signal bar",
    "control_pool": (
        "same blind/development block bars satisfying low current futures move and not simultaneous "
        "high futures volume+high futures OI activity, excluding O1-qualified bars"
    ),
    "primary_comparison": "candidate episode forward excursion minus control forward excursion, reported by chronological block",
    "secondary_outcomes": [
        "next 15m maximum absolute futures excursion in bps",
        "next 60m maximum absolute futures excursion in bps",
    ],
    "development_results": {
        "primary_30m": {
            "scorable_episodes": 96,
            "sessions_with_episodes": 55,
            "positive_mean_lift_blocks": "8/8",
            "positive_median_lift_blocks": "8/8",
            "average_block_mean_lift_bps": 3.362494,
            "average_block_median_lift_bps": 3.700017,
        },
        "horizon_15m": {
            "scorable_episodes": 98,
            "positive_mean_lift_blocks": "7/8",
            "positive_median_lift_blocks": "8/8",
            "average_block_mean_lift_bps": 1.225998,
            "average_block_median_lift_bps": 1.259205,
        },
        "horizon_60m": {
            "scorable_episodes": 86,
            "positive_mean_lift_blocks": "6/8",
            "positive_median_lift_blocks": "7/8",
            "average_block_mean_lift_bps": 5.083528,
            "average_block_median_lift_bps": 6.499904,
        },
        "dte_30m_lift_bps": {
            "0": {"episodes": 22, "mean": 3.108, "median": 2.566},
            "1": {"episodes": 18, "mean": 5.718, "median": 3.782},
            "4": {"episodes": 17, "mean": 2.147, "median": 0.178},
            "5": {"episodes": 18, "mean": 0.699, "median": 0.513},
            "6": {"episodes": 21, "mean": 1.906, "median": 5.381},
        },
        "daypart_30m_lift_bps": {
            "early": {"episodes": 40, "mean": 2.708, "median": 3.141},
            "middle": {"episodes": 33, "mean": 1.983, "median": 2.441},
            "late": {"episodes": 23, "mean": 3.976, "median": 1.647},
        },
        "threshold_sensitivity": {
            "Q67_high_participation": {
                "episodes": 148,
                "positive_mean_lift_blocks": "7/8",
                "positive_median_lift_blocks": "6/8",
            },
            "Q75_high_participation": {
                "episodes": 96,
                "positive_mean_lift_blocks": "8/8",
                "positive_median_lift_blocks": "8/8",
            },
            "Q80_high_participation": {
                "episodes": 65,
                "positive_mean_lift_blocks": "6/8",
                "positive_median_lift_blocks": "8/8",
            },
        },
        "component_diagnostic": (
            "High option OI activity alone did not show the same expansion; high option volume alone "
            "was weaker. The joint high-volume + high-OI-activity state was the relevant observation."
        ),
        "b2_overlap_diagnostic": {
            "eligible_episode_starts_with_futures_rvol20": 74,
            "episodes_also_rvol20_gte_1_50": 22,
            "overlap_pct": 29.72973,
            "interpretation": "O1 is not simply the previously frozen B2 high-futures-volume regime.",
        },
    },
    "rejected_directional_interpretations": [
        "CE-vs-PE volume imbalance as a stable forward direction signal",
        "CE-vs-PE OI-change imbalance as a stable forward direction signal",
        "CE-vs-PE volume-center migration as a stable forward direction signal",
        "simple bullish/bearish labels inferred mechanically from option OI changes",
    ],
    "implementation_allowed": False,
}

NEXT_BLIND_PROTOCOL = {
    "candidate": "O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION",
    "window": "older than 2026-05-19 development start",
    "sessions": 10,
    "working_name": "OPTIONS_BLIND_08",
    "parameter_changes_before_scoring": False,
    "mine_blind_for_new_rules": False,
    "thresholds_from_blind": False,
    "required_data": "NIFTY futures + exact near-expiry dynamic ATM +/-4 CE/PE 5m OHLCV/OI",
    "primary_score": "30m maximum absolute futures excursion versus frozen-protocol control",
    "directional_score": None,
    "production_implementation_from_freeze": False,
}
