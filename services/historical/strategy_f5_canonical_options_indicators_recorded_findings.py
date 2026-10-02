"""Recorded Jul-Sep 2026 canonical options-indicator findings."""

STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "diagnostic_artifact_sha256": (
            "08251543629a572b5681eab8bb20058d27ecf64a743474853109a962e49dddc3"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "trades": 443,
        "feature_coverage_pct": 100.0,
        "oi_coverage_pct": 100.0,
    },
    "strongest_canonical_indicators": {
        "atr14_pct": {
            "bad_trade_auc": 0.4000,
            "activation_auc": 0.6515,
            "direction": "HIGHER_ATR_FAVORABLE",
            "month_consistency": "3/3",
            "side_consistency": "2/2",
        },
        "bb_bandwidth20_pct": {
            "bad_trade_auc": 0.4001,
            "activation_auc": 0.6371,
            "direction": "HIGHER_BANDWIDTH_FAVORABLE",
            "month_consistency": "3/3",
            "side_consistency": "2/2",
        },
        "macd_hist_pct": {
            "bad_trade_auc": 0.3896,
            "activation_auc": 0.6337,
            "direction": "STRONGER_HISTOGRAM_FAVORABLE",
            "month_consistency": "3/3",
            "side_consistency": "2/2",
        },
        "stoch_d3": {
            "bad_trade_auc": 0.5738,
            "activation_auc": 0.3905,
            "direction": "LOWER_STOCHASTIC_FAVORABLE",
            "month_consistency": "3/3",
            "side_consistency": "2/2",
        },
    },
    "moderate_options_specific_metrics": {
        "volume_to_oi": {
            "bad_trade_auc": 0.4351,
            "activation_auc": 0.5841,
            "direction": "HIGHER_VOLUME_TO_OI_FAVORABLE",
            "month_consistency": "3/3",
            "side_consistency": "2/2",
        },
        "oi_to_prior20_median": {
            "bad_trade_auc": 0.4372,
            "activation_auc": 0.5693,
            "direction": "HIGHER_RELATIVE_OI_FAVORABLE",
            "month_consistency": "3/3",
            "side_consistency": "2/2",
        },
        "oi_z20": {
            "bad_trade_auc": 0.4604,
            "activation_auc": 0.5507,
            "direction": "HIGHER_OI_REGIME_MODESTLY_FAVORABLE",
            "month_consistency": "3/3",
            "side_consistency": "2/2",
        },
    },
    "weak_oi_change_metrics": {
        "oi_change1_pct": {
            "bad_trade_auc": 0.4871,
            "activation_auc": 0.4998,
        },
        "oi_change3_pct": {
            "bad_trade_auc": 0.4983,
            "activation_auc": 0.5088,
        },
        "oi_change5_pct": {
            "bad_trade_auc": 0.4950,
            "activation_auc": 0.5140,
        },
        "interpretation": (
            "Short-horizon OI direction/change at the entry candle is near-random "
            "for this exact F5 setup and should not be used as a filter."
        ),
    },
    "oi_price_states": {
        "LONG_BUILDUP": {
            "trades": 169,
            "bad_trade_rate_pct": 66.27,
            "activation_rate_pct": 27.81,
            "net_pnl_inr": -29510.15,
        },
        "SHORT_COVERING": {
            "trades": 247,
            "bad_trade_rate_pct": 66.80,
            "activation_rate_pct": 28.34,
            "net_pnl_inr": -2700.89,
        },
        "SHORT_BUILDUP": {
            "trades": 9,
            "bad_trade_rate_pct": 44.44,
            "activation_rate_pct": 55.56,
            "net_pnl_inr": -1465.24,
            "warning": "UNDERPOWERED",
        },
        "LONG_UNWINDING": {
            "trades": 18,
            "bad_trade_rate_pct": 61.11,
            "activation_rate_pct": 38.89,
            "net_pnl_inr": 4583.41,
            "warning": "UNDERPOWERED",
        },
        "interpretation": (
            "Long-buildup and short-covering states dominate the sample and have "
            "almost identical bad-trade/activation rates. The rarer states are "
            "too small and unstable to support a trading rule."
        ),
    },
    "other_known_indicators": {
        "adx14": {
            "bad_trade_auc": 0.4477,
            "activation_auc": 0.5536,
            "interpretation": "MODERATE_AND_MONTH_INCONSISTENT",
        },
        "rsi14": {
            "bad_trade_auc": 0.5559,
            "activation_auc": 0.4273,
            "interpretation": "LOWER_RSI_MODESTLY_FAVORABLE_BUT_WEAKER_THAN_VOLATILITY",
        },
        "vwap_distance_pct": {
            "bad_trade_auc": 0.5605,
            "activation_auc": 0.4515,
            "interpretation": "CLOSER_TO_VWAP_MODESTLY_FAVORABLE",
        },
    },
    "structural_context": {
        "days_to_expiry": {
            "bad_trade_auc": 0.5704,
            "activation_auc": 0.3881,
            "direction": "NEARER_EXPIRY_FAVORABLE",
        },
        "entry_premium": {
            "bad_trade_auc": 0.5687,
            "activation_auc": 0.4101,
            "direction": "LOWER_PREMIUM_FAVORABLE",
        },
    },
    "interpretation": (
        "For F5, the strongest known-indicator signal is not OI direction. "
        "Successful short swings are associated primarily with an already-active "
        "option volatility regime (ATR/Bollinger bandwidth) and a stronger MACD "
        "histogram crossover, while lower Stochastic reduces extension risk. "
        "Relative OI and volume/OI add modest confirmation; raw OI changes and "
        "classic price/OI state labels do not cleanly separate bad trades."
    ),
    "decision": "PRIORITIZE_ATR_BOLLINGER_MACD_STOCHASTIC_WITH_OI_AS_SECONDARY_CONFIRMATION",
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "do_not_use_short_horizon_oi_change_as_filter": True,
        "do_not_promote_price_oi_state_filter": True,
        "do_not_search_oi_threshold_grid_on_jul_sep": True,
        "keep_existing_post_activation_trail_frozen": True,
        "fresh_holdout_required_for_any_combined_rule": True,
        "strategy_d_remains_paused": True,
    },
}
