"""Recorded same-corpus characterization of two Breeze atlas discoveries."""

MARKET_STRUCTURE_ATLAS_CHARACTERIZATION_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_BREEZE_ATLAS_PATTERN_CHARACTERIZATION_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "NIFTY_BREEZE_ATLAS_PATTERN_CHARACTERIZATION_V1",
    "corpus_role": "SAME_CORPUS_POST_DISCOVERY_CHARACTERIZATION_NOT_VALIDATION",
    "research_only": True,
    "source": {
        "findings_artifact_sha256": (
            "3095df65d788403e01ac6760be63749de55f14f81a82560af9dae3e9a7511293"
        ),
        "source_database_sha256": (
            "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
        ),
        "accepted_complete_sessions": 412,
        "rejected_sessions": 1,
        "rejected_dates": ["2025-10-21"],
    },
    "opening_range_vs_remaining_range": {
        "pooled_spearman": 0.5086691594128394,
        "observations": 412,
        "predictor_quartile_edges_bps": [
            11.13315652820466,
            29.088095867776865,
            37.475318552359404,
            51.0345740429852,
            258.55055620122926,
        ],
        "quartiles": [
            {
                "quartile": "Q1",
                "sessions": 103,
                "predictor_mean_bps": 23.29273545159487,
                "outcome_mean_bps": 53.91420769517662,
                "outcome_median_bps": 50.7374969774726,
            },
            {
                "quartile": "Q2",
                "sessions": 103,
                "predictor_mean_bps": 33.30875854227493,
                "outcome_mean_bps": 73.17912941276963,
                "outcome_median_bps": 60.50179211469564,
            },
            {
                "quartile": "Q3",
                "sessions": 103,
                "predictor_mean_bps": 43.70900579613318,
                "outcome_mean_bps": 83.08502827340374,
                "outcome_median_bps": 75.81287589063211,
            },
            {
                "quartile": "Q4",
                "sessions": 103,
                "predictor_mean_bps": 69.77791025779501,
                "outcome_mean_bps": 105.35924024377826,
                "outcome_median_bps": 96.40262042787683,
            },
        ],
        "means_strictly_increasing": True,
        "medians_strictly_increasing": True,
        "q4_to_q1_outcome_mean_ratio": 1.9542017725543634,
        "q4_minus_q1_outcome_mean_bps": 51.44503254860164,
        "era_spearman": {
            "pre_tuesday_expiry_era": 0.39468937466842596,
            "tuesday_expiry_era": 0.5627467424877779,
        },
    },
    "daily_range_persistence": {
        "pooled_spearman": 0.39743756384484685,
        "observations": 411,
        "predictor_quartile_edges_bps": [
            20.518478536957364,
            58.10909233915862,
            80.60771129825439,
            110.70727133882502,
            405.90237423211084,
        ],
        "quartiles": [
            {
                "quartile": "Q1",
                "sessions": 103,
                "predictor_mean_bps": 45.715405355849434,
                "outcome_mean_bps": 70.79622160653459,
                "outcome_median_bps": 65.64219616043033,
            },
            {
                "quartile": "Q2",
                "sessions": 103,
                "predictor_mean_bps": 69.30759095599326,
                "outcome_mean_bps": 81.78210965069718,
                "outcome_median_bps": 76.66931139599889,
            },
            {
                "quartile": "Q3",
                "sessions": 102,
                "predictor_mean_bps": 94.01097051789942,
                "outcome_mean_bps": 94.99805259212691,
                "outcome_median_bps": 81.92176291083672,
            },
            {
                "quartile": "Q4",
                "sessions": 103,
                "predictor_mean_bps": 150.64195044012877,
                "outcome_mean_bps": 111.62224644982734,
                "outcome_median_bps": 105.1918190602304,
            },
        ],
        "means_strictly_increasing": True,
        "medians_strictly_increasing": True,
        "q4_to_q1_outcome_mean_ratio": 1.57666954417698,
        "q4_minus_q1_outcome_mean_bps": 40.82602484329276,
        "era_spearman": {
            "pre_tuesday_expiry_era": 0.14680421855343298,
            "tuesday_expiry_era": 0.5305004791269032,
        },
    },
    "interpretation": {
        "opening_range_vs_remaining_range": (
            "Shows a clean monotone same-corpus gradient and remains positive "
            "in both frozen expiry eras; prioritize for older-history replication."
        ),
        "daily_range_persistence": (
            "Shows a clean monotone same-corpus gradient but materially stronger "
            "rank association in the newer Tuesday-expiry era; treat as more "
            "regime-sensitive in older-history replication."
        ),
    },
    "guardrails": {
        "same_corpus_characterization": True,
        "independent_validation": False,
        "blind_validation": False,
        "candidate_freeze": False,
        "implementation_allowed": False,
        "pnl_scored": False,
        "directional_entry_exit_rule": False,
        "threshold_optimization": False,
        "quartiles_are_descriptive_not_trading_thresholds": True,
        "strategy_d_remains_paused": True,
    },
}
