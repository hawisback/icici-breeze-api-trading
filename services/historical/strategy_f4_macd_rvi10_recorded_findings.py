"""Recorded Jul-Sep 2026 Strategy F4 MACD+RVI10 exploration findings."""

STRATEGY_F4_MACD_RVI10_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F4_MACD_RVI10_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F4_OPTION_NATIVE_MACD_RVI10_EXPLORATION_V1",
    "strategy_id": "F4",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "f18a0d58175ba2668a6b3c7f246cfeeddcee172ad871b7fcd7a2c7becdd68b21"
        ),
        "market_artifact_sha256": (
            "1fb3e038f09dbb3aa55f2bd196c9b5d7982c56908ca4dc402e19f7227ade2840"
        ),
        "window": ["2026-07-01", "2026-09-30"],
        "selected_sessions": 65,
        "raw_bullish_entry_opportunities": 312,
        "insufficient_warmup_side_sessions": 0,
    },
    "learned_cutpoints": {
        "rvi_spread_positive_p50": 0.06134266384902198,
        "rvi_spread_positive_p75": 0.09422782163162731,
        "rvi_level_positive_p50": 0.06164540524024134,
        "rvi_level_positive_p75": 0.12316076294277921,
    },
    "baseline": {
        "trades": 211,
        "net_pnl_inr": -11824.80,
        "profit_factor": 0.9086,
        "net_at_0_5_slippage_inr": -25468.24,
        "net_at_1_0_slippage_inr": -39053.22,
    },
    "best_spread_candidate": {
        "name": "RVI_SPREAD_P75",
        "threshold": 0.09422782163162731,
        "trades": 48,
        "net_pnl_inr": 11697.54,
        "profit_factor": 1.4072,
        "net_at_0_5_slippage_inr": 8579.03,
        "net_at_1_0_slippage_inr": 5460.57,
        "monthly": {
            "2026-07": {"net_pnl_inr": 9295.59, "profit_factor": 1.8438},
            "2026-08": {"net_pnl_inr": -1889.22, "profit_factor": 0.7929},
            "2026-09": {"net_pnl_inr": 4291.17, "profit_factor": 1.4998},
        },
        "pooled_ce_net_pnl_inr": -7158.06,
        "pooled_pe_net_pnl_inr": 18855.60,
        "robustness_screen_passed": False,
    },
    "best_level_candidate": {
        "name": "RVI_LEVEL_P50",
        "threshold": 0.06164540524024134,
        "trades": 45,
        "net_pnl_inr": 7966.11,
        "profit_factor": 1.3051,
        "net_at_0_5_slippage_inr": 5042.56,
        "net_at_1_0_slippage_inr": 2118.97,
        "monthly": {
            "2026-07": {"net_pnl_inr": 3363.71, "profit_factor": 1.2567},
            "2026-08": {"net_pnl_inr": -2772.02, "profit_factor": 0.6057},
            "2026-09": {"net_pnl_inr": 7374.42, "profit_factor": 2.2339},
        },
        "pooled_ce_net_pnl_inr": -11173.22,
        "pooled_pe_net_pnl_inr": 19139.33,
        "robustness_screen_passed": False,
    },
    "interpretation": (
        "RVI(10) contains useful selectivity information, especially stronger "
        "positive RVI-minus-signal spread, but no frozen candidate is robust "
        "month-by-month across July-August-September. The strongest tested "
        "spread threshold is about 0.09423 and the strongest tested RVI level "
        "threshold is about 0.06165. Both remain exploratory because August "
        "fails. Pooled side summaries suggest CE remains destructive while PE "
        "is positive, motivating a diagnostic monthly side breakdown before "
        "any new candidate is frozen."
    ),
    "decision": "F4_RVI10_DID_NOT_ROBUSTIFY_RAW_MACD_ACROSS_DEVELOPMENT_MONTHS",
    "status": "NO_SURVIVOR_DIAGNOSE_SIDE_BY_MONTH_WITHOUT_NEW_THRESHOLD_SEARCH",
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_new_threshold_search_on_jul_sep": True,
        "no_rvi_length_search_on_jul_sep": True,
        "no_same_sample_time_filter": True,
        "any_new_combination_requires_fresh_holdout": True,
        "strategy_d_remains_paused": True,
    },
}
