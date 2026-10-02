"""Recorded Jul-Sep 2026 F5 entry-state diagnostic findings."""

STRATEGY_F5_ENTRY_STATE_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_ENTRY_STATE_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_ENTRY_STATE_DIAGNOSTIC_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "diagnostic_artifact_sha256": (
            "605db4ed38b05819a292a509ec023009424cec5c7ad9d0fb3347bfe8a744ff0e"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "matched_trades": 443,
        "feature_match_coverage_pct": 100.0,
    },
    "baseline": {
        "trail_activation_rate_pct": 29.12,
        "baseline_win_rate_pct": 26.86,
        "baseline_net_pnl_inr": -29092.87,
    },
    "continuous_feature_ranking": {
        "macd_hist_pct": {
            "auc": 0.6337,
            "smd": 0.3487,
            "activated_median": 0.235248,
            "not_activated_median": 0.144487,
            "month_auc": {
                "2026-07": 0.6483,
                "2026-08": 0.5881,
                "2026-09": 0.6675,
            },
            "side_auc": {"CE": 0.6962, "PE": 0.5931},
        },
        "macd_hist_delta_pct": {
            "auc": 0.6325,
            "smd": 0.3571,
            "activated_median": 0.458350,
            "not_activated_median": 0.319628,
            "month_auc": {
                "2026-07": 0.6460,
                "2026-08": 0.5922,
                "2026-09": 0.6514,
            },
            "side_auc": {"CE": 0.6817, "PE": 0.5924},
        },
        "macd_pct": {
            "auc": 0.3918,
            "smd": -0.3841,
            "interpretation": (
                "More-negative MACD level at bullish crossover is associated "
                "with later +10% activation; above-zero MACD is not favored."
            ),
        },
        "return_2bar_pct": {"auc": 0.5762, "smd": 0.1980},
        "return_3bar_pct": {"auc": 0.5663, "smd": 0.1828},
        "rvi_level": {"auc": 0.4682, "smd": -0.0929},
        "rvi_delta_1": {"auc": 0.4742, "smd": 0.0118},
        "volume_ratio_5": {"auc": 0.4897, "smd": 0.0381},
        "macd_hist_acceleration_pct": {"auc": 0.5039, "smd": 0.1386},
    },
    "interpretation": (
        "The most stable entry-time discriminator is normalized MACD histogram "
        "strength at the bullish crossover. Histogram size and one-bar histogram "
        "change separate later +10% activations in all three inspected months "
        "and both option rights. RVI>=50 remains useful as the base setup but "
        "RVI level/slope above 50 does not further distinguish winners. Volume "
        "and histogram acceleration add little. No diagnostic state is promoted "
        "as validated on Jul-Sep."
    ),
    "next_candidate": {
        "name": "F5_HIST_PCT_GE_0_15",
        "rule": (
            "Keep the complete F5 setup and require normalized MACD histogram "
            "on the completed signal bar >= 0.15% of option close."
        ),
        "threshold_pct": 0.15,
        "derivation": (
            "Development-derived round threshold just above the pooled "
            "not-activated median 0.144487 and below the activated median "
            "0.235248. It must not be judged on Jul-Sep; validate on fresh data."
        ),
        "fresh_holdout": "2026-05",
    },
    "decision": "FREEZE_SINGLE_HISTOGRAM_STRENGTH_CANDIDATE_FOR_FRESH_HOLDOUT",
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "keep_existing_post_activation_trail_frozen": True,
        "keep_rvi10_ge_50_base_entry": True,
        "no_more_histogram_threshold_search_on_jul_sep": True,
        "no_side_filter": True,
        "no_time_filter": True,
        "no_stop_or_target_search": True,
        "strategy_d_remains_paused": True,
    },
}
