"""Recorded Jul-Sep 2026 broad F5 indicator-atlas findings."""

STRATEGY_F5_BROAD_INDICATOR_ATLAS_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_BROAD_INDICATOR_ATLAS_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_BROAD_ENTRY_INDICATOR_ATLAS_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "atlas_artifact_sha256": (
            "1b7d3952ef4429250f3a2b8e4edad621f438a52426dec5227e325214c4ae9dfa"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "trades": 443,
        "bad_trades": 292,
        "trail_activations": 129,
        "feature_coverage_pct": 100.0,
    },
    "main_pattern": (
        "Bad F5 entries are disproportionately weak bullish MACD crossovers "
        "occurring in lower option-volatility/range regimes. Successful +10% "
        "activations occur with higher ATR%, Bollinger bandwidth, realized "
        "volatility, option-bar range, and stronger normalized MACD histogram. "
        "They also tend to cross from a more-negative MACD/EMA regime rather "
        "than after the option is already extended."
    ),
    "top_independent_families": {
        "crossover_strength": {
            "feature": "macd_hist_pct",
            "bad_trade_auc": 0.3896,
            "activation_auc": 0.6337,
            "bad_trade_median": 0.145766,
            "not_bad_median": 0.224957,
            "activated_median": 0.235248,
            "not_activated_median": 0.144487,
            "bad_month_direction_consistency": "3/3",
            "bad_side_direction_consistency": "2/2",
        },
        "volatility_regime": {
            "feature": "atr14_pct",
            "bad_trade_auc": 0.4000,
            "activation_auc": 0.6515,
            "bad_trade_median": 4.461826,
            "not_bad_median": 5.103296,
            "activated_median": 5.543956,
            "not_activated_median": 4.396546,
            "activation_auc_by_month": {
                "2026-07": 0.6664,
                "2026-08": 0.6680,
                "2026-09": 0.6123,
            },
            "activation_auc_by_side": {
                "CE": 0.6840,
                "PE": 0.6209,
            },
        },
        "trend_position": {
            "feature": "macd_pct",
            "bad_trade_auc": 0.5829,
            "activation_auc": 0.3918,
            "bad_trade_median": -0.404690,
            "not_bad_median": -1.118864,
            "activated_median": -1.619778,
            "not_activated_median": -0.322365,
            "interpretation": (
                "More-negative MACD at bullish crossover is favorable; "
                "crossovers closer to/above zero are more failure-prone."
            ),
        },
        "extension_state": {
            "feature": "stoch_d3",
            "bad_trade_auc": 0.5738,
            "activation_auc": 0.3905,
            "bad_trade_median": 55.761590,
            "not_bad_median": 50.609859,
            "activated_median": 49.329083,
            "not_activated_median": 56.098554,
            "interpretation": (
                "Lower Stochastic %D at the bullish MACD crossover is favorable; "
                "already-extended oscillator states are more failure-prone."
            ),
        },
    },
    "structural_context": {
        "days_to_expiry": {
            "bad_trade_auc": 0.5704,
            "activation_auc": 0.3881,
            "interpretation": "Nearer-expiry contracts activate more often.",
        },
        "entry_premium": {
            "bad_trade_auc": 0.5687,
            "activation_auc": 0.4101,
            "interpretation": "Lower-premium entries activate more often.",
        },
        "minutes_from_open": {
            "bad_trade_auc": 0.5636,
            "activation_auc": 0.4275,
            "interpretation": "Earlier entries activate more often.",
        },
    },
    "weak_or_low_priority_features": {
        "rvi10": {
            "bad_trade_auc": 0.5438,
            "activation_auc": 0.4682,
            "interpretation": "RVI>=50 is base setup; higher RVI adds little.",
        },
        "volume_ratio5": {
            "bad_trade_auc": 0.5182,
            "activation_auc": 0.4897,
            "interpretation": "Simple volume confirmation is near random.",
        },
        "cci10": {
            "bad_trade_auc": 0.5065,
            "activation_auc": 0.4894,
        },
        "ema9_slope3_pct": {
            "bad_trade_auc": 0.5108,
            "activation_auc": 0.4923,
        },
    },
    "ce_pe_observation": {
        "histogram_activation_auc": {"CE": 0.6962, "PE": 0.5931},
        "atr14_activation_auc": {"CE": 0.6840, "PE": 0.6209},
        "stoch_d_bad_trade_auc": {"CE": 0.5605, "PE": 0.5835},
        "interpretation": (
            "The same regime direction exists on both rights. Crossover strength "
            "and volatility are more discriminating on CE, while Stochastic "
            "extension is slightly more discriminating on PE. Do not promote a "
            "side filter from this development sample."
        ),
    },
    "decision": "TEST_SMALL_NATURAL_REGIME_COMBINATIONS_BEFORE_FRESH_HOLDOUT",
    "next_step": (
        "Use existing Jul-Sep data to test a very small predeclared set of "
        "natural, non-optimized regime states combining one volatility feature, "
        "one crossover/trend feature, and one extension feature. Select at most "
        "one simple rule, freeze it, then validate on a fresh month."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "do_not_search_indicator_threshold_grids_on_jul_sep": True,
        "do_not_add_ce_pe_side_filter": True,
        "keep_existing_post_activation_trail_frozen": True,
        "fresh_holdout_required_after_combination_selection": True,
        "strategy_d_remains_paused": True,
    },
}
