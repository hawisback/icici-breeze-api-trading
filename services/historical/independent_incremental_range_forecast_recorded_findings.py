"""Recorded findings from the frozen incremental range forecast study."""

INCREMENTAL_RANGE_FORECAST_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_BREEZE_INCREMENTAL_RANGE_FORECAST_RECORDED_FINDINGS_V1",
    "protocol_version": "NIFTY_BREEZE_INCREMENTAL_RANGE_FORECAST_V1",
    "research_only": True,
    "source": {
        "findings_artifact_sha256": (
            "785669843e8895fecac9ce74c02c44f47371631684feb9b1f8974ff6d2a6d8cd"
        ),
        "source_market_sha256": (
            "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
        ),
        "source_complete_sessions": 709,
        "usable_sessions_after_previous_day_lag": 708,
    },
    "folds": [
        {
            "name": "train_2022_test_2023",
            "opening_only_mae_bps": 25.5888045094811,
            "opening_plus_previous_day_mae_bps": 23.267059743175768,
            "combined_minus_opening_mae_bps": -2.3217447663053328,
            "combined_mae_lower": True,
            "opening_only_spearman": 0.3611056750649398,
            "opening_plus_previous_day_spearman": 0.36615701643024046,
        },
        {
            "name": "train_2022_2023_test_2024",
            "opening_only_mae_bps": 33.305903058542384,
            "opening_plus_previous_day_mae_bps": 34.182485977523356,
            "combined_minus_opening_mae_bps": 0.8765829189809722,
            "combined_mae_lower": False,
            "opening_only_spearman": 0.4025540936232298,
            "opening_plus_previous_day_spearman": 0.3476142782579606,
        },
    ],
    "pooled_oos": {
        "sessions": 489,
        "opening_only_mae_bps": 29.47102586545058,
        "opening_plus_previous_day_mae_bps": 28.758255762908913,
        "mae_improvement_bps": 0.7127701025416684,
        "relative_mae_improvement": 0.024185452715348554,
        "opening_only_spearman": 0.2711960214036334,
        "opening_plus_previous_day_spearman": 0.3400456068980947,
        "bootstrap_incremental_error_improvement_95pct": [
            -0.2929254531582287,
            1.7233004593966301,
        ],
    },
    "incremental_gate": {
        "passed": False,
        "failures": [
            "combined_mae_not_lower_in_both_folds",
            "bootstrap_incremental_improvement_lower_bound_not_positive",
        ],
    },
    "decision": "NO_STABLE_INCREMENTAL_INFORMATION_FROM_PREVIOUS_DAY_RANGE",
    "interpretation": (
        "Previous-session realized range is correlated with current-session "
        "volatility in isolation, but it does not add stable forecast value "
        "once the current session's first-30-minute range is known. The combined "
        "model improves 2023 but worsens 2024, and the pooled bootstrap interval "
        "for incremental absolute-error improvement crosses zero."
    ),
    "guardrails": {
        "same_history_characterization": True,
        "blind_validation": False,
        "feature_search": False,
        "model_family_search": False,
        "hyperparameter_search": False,
        "threshold_optimization": False,
        "directional_entry_exit_rule": False,
        "pnl_scored": False,
        "implementation_allowed": False,
        "no_rescue_on_same_sample": True,
        "strategy_d_remains_paused": True,
    },
}
