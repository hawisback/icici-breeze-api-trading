"""Recorded Jul-Sep 2026 F5 four-factor quality-score findings."""

STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "fea4565b13321d720080f5036e9d0ec6c65b65e5ad58781013a26001a94a0abf"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "matched_trades": 443,
        "feature_coverage_pct": 100.0,
    },
    "overall_score_buckets": {
        "0": {
            "trades": 8,
            "bad_trade_rate_pct": 87.50,
            "trail_activation_rate_pct": 0.00,
            "win_rate_pct": 12.50,
            "net_pnl_inr": -2043.05,
            "profit_factor": 0.1674,
        },
        "1": {
            "trades": 109,
            "bad_trade_rate_pct": 77.06,
            "trail_activation_rate_pct": 17.43,
            "win_rate_pct": 21.10,
            "net_pnl_inr": -17109.95,
            "profit_factor": 0.5066,
        },
        "2": {
            "trades": 135,
            "bad_trade_rate_pct": 65.93,
            "trail_activation_rate_pct": 27.41,
            "win_rate_pct": 28.89,
            "net_pnl_inr": -3875.51,
            "profit_factor": 0.9069,
        },
        "3": {
            "trades": 116,
            "bad_trade_rate_pct": 61.21,
            "trail_activation_rate_pct": 34.48,
            "win_rate_pct": 28.45,
            "net_pnl_inr": -1959.82,
            "profit_factor": 0.9509,
        },
        "4": {
            "trades": 75,
            "bad_trade_rate_pct": 54.67,
            "trail_activation_rate_pct": 44.00,
            "win_rate_pct": 30.67,
            "net_pnl_inr": -4104.54,
            "profit_factor": 0.8674,
        },
    },
    "score_auc": {
        "bad_trade_higher_score_predicts_bad_auc": 0.4015,
        "trail_activation_higher_score_auc": 0.6289,
        "baseline_winner_higher_score_auc": 0.5476,
        "trail_activation_by_month": {
            "2026-07": 0.6224,
            "2026-08": 0.6250,
            "2026-09": 0.6379,
        },
        "trail_activation_by_side": {
            "CE": 0.6934,
            "PE": 0.5794,
        },
    },
    "cumulative_cutoffs_descriptive_only": {
        "score_ge_1": {
            "trades": 435,
            "bad_trade_rate_pct": 65.52,
            "trail_activation_rate_pct": 29.66,
            "winner_rate_pct": 27.13,
            "net_pnl_inr": -27049.82,
            "profit_factor": 0.8162,
            "activated_signal_capture_pct": 100.00,
            "winner_signal_capture_pct": 99.16,
        },
        "score_ge_2": {
            "trades": 326,
            "bad_trade_rate_pct": 61.66,
            "trail_activation_rate_pct": 33.74,
            "winner_rate_pct": 29.14,
            "net_pnl_inr": -9939.87,
            "profit_factor": 0.9116,
            "activated_signal_capture_pct": 85.27,
            "winner_signal_capture_pct": 79.83,
        },
        "score_ge_3": {
            "trades": 191,
            "bad_trade_rate_pct": 58.64,
            "trail_activation_rate_pct": 38.22,
            "winner_rate_pct": 29.32,
            "net_pnl_inr": -6064.36,
            "profit_factor": 0.9144,
            "activated_signal_capture_pct": 56.59,
            "winner_signal_capture_pct": 47.06,
        },
        "score_ge_4": {
            "trades": 75,
            "bad_trade_rate_pct": 54.67,
            "trail_activation_rate_pct": 44.00,
            "winner_rate_pct": 30.67,
            "net_pnl_inr": -4104.54,
            "profit_factor": 0.8674,
            "activated_signal_capture_pct": 25.58,
            "winner_signal_capture_pct": 19.33,
        },
    },
    "component_pass_rates": {
        "ATR_ACTIVE_pct": 32.28,
        "BB_ACTIVE_pct": 51.24,
        "STOCH_NOT_OVERBOUGHT_pct": 93.68,
        "HIST_STRONG_pct": 54.63,
    },
    "interpretation": (
        "The equal-weight four-factor score is a genuine setup-quality ranking for "
        "the probability of reaching the frozen +10% activation: activation rises "
        "monotonically overall, and bad-trade rate falls monotonically overall. "
        "The activation-score relationship is directionally consistent in all "
        "three development months and both CE and PE. Final PnL is not monotonic, "
        "so the score should not be treated as an expected-PnL ranking. Stochastic "
        "<80 is present in 93.68% of trades and therefore contributes relatively "
        "little discrimination at its standard boundary."
    ),
    "selection_rule_for_fresh_holdout": {
        "rule": "QUALITY_SCORE_GE_2",
        "basis": (
            "Use the highest integer score cutoff that still satisfies the "
            "previously frozen 65% preservation floor for BOTH baseline activated "
            "signals and baseline winners. Score>=2 retains 85.27% and 79.83%; "
            "score>=3 falls to 56.59% and 47.06%."
        ),
        "not_selected_by_maximum_pnl": True,
        "development_derived": True,
        "requires_fresh_holdout": True,
    },
    "decision": "FREEZE_SCORE_GE_2_FOR_FRESH_HOLDOUT",
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_score_weight_tuning_on_jul_sep": True,
        "no_component_threshold_retuning_on_jul_sep": True,
        "do_not_promote_score_4_from_activation_rate": True,
        "keep_existing_post_activation_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
