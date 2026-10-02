"""Recorded Jul-Sep 2026 corrected Relative Volatility Index findings."""

STRATEGY_F4_RELATIVE_VOLATILITY_INDEX_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F4_RELATIVE_VOLATILITY_INDEX_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F4_MACD_RELATIVE_VOLATILITY_INDEX10_V1",
    "strategy_id": "F4",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "a1bdc2df219950a5e6ea2707f4b303a71f492953c81212df7f0e0c0a8641f7cf"
        ),
        "market_artifact_sha256": (
            "1fb3e038f09dbb3aa55f2bd196c9b5d7982c56908ca4dc402e19f7227ade2840"
        ),
        "window": ["2026-07-01", "2026-09-30"],
        "selected_sessions": 65,
        "raw_bullish_macd_entry_opportunities": 312,
        "insufficient_warmup_side_sessions": 0,
    },
    "entry_rvi_distribution": {
        "p10": 37.8408,
        "p25": 43.9499,
        "p50": 52.3906,
        "p75": 60.2024,
        "p90": 67.5493,
    },
    "thresholds": {
        50: {
            "trades": 155,
            "net_pnl_inr": -15557.87,
            "profit_factor": 0.8347,
            "net_at_0_5_slippage_inr": -25627.97,
            "net_at_1_0_slippage_inr": -35698.08,
        },
        55: {
            "trades": 126,
            "net_pnl_inr": -7467.97,
            "profit_factor": 0.8976,
            "net_at_0_5_slippage_inr": -15653.99,
            "net_at_1_0_slippage_inr": -23840.00,
        },
        60: {
            "trades": 78,
            "net_pnl_inr": -5346.80,
            "profit_factor": 0.8890,
            "net_at_0_5_slippage_inr": -10414.36,
            "net_at_1_0_slippage_inr": -15481.86,
        },
        65: {
            "trades": 46,
            "net_pnl_inr": 2240.59,
            "profit_factor": 1.0682,
            "net_at_0_5_slippage_inr": -747.95,
            "net_at_1_0_slippage_inr": -3736.47,
            "monthly_net_0": {
                "2026-07": 4161.34,
                "2026-08": -1102.57,
                "2026-09": -818.18,
            },
        },
        70: {
            "trades": 24,
            "net_pnl_inr": 5315.88,
            "profit_factor": 1.2899,
            "net_at_0_5_slippage_inr": 3756.62,
            "net_at_1_0_slippage_inr": 2197.40,
            "monthly_net_0": {
                "2026-07": 6554.52,
                "2026-08": 701.72,
                "2026-09": -1940.36,
            },
        },
        75: {
            "trades": 11,
            "wins": 5,
            "losses": 6,
            "win_rate_pct": 45.45,
            "net_pnl_inr": 15831.51,
            "profit_factor": 3.4670,
            "median_net_pnl_inr": -109.55,
            "net_at_0_5_slippage_inr": 15116.85,
            "profit_factor_at_0_5_slippage": 3.2208,
            "net_at_1_0_slippage_inr": 14402.22,
            "profit_factor_at_1_0_slippage": 3.0012,
            "monthly": {
                "2026-07": {"trades": 4, "net_pnl_inr": 10093.67, "profit_factor": 4.8075},
                "2026-08": {"trades": 3, "net_pnl_inr": 3244.65, "profit_factor": 5.0531},
                "2026-09": {"trades": 4, "net_pnl_inr": 2493.19, "profit_factor": 1.8407},
            },
            "ce_leg": {"trades": 5, "net_pnl_inr": -4416.22, "profit_factor": 0.0675},
            "pe_leg": {"trades": 6, "net_pnl_inr": 20247.73, "profit_factor": 13.0418},
            "sample_size_warning": True,
        },
        80: {
            "trades": 1,
            "net_pnl_inr": 3842.01,
            "sample_size_warning": True,
        },
    },
    "interpretation": (
        "Relative Volatility Index materially changes selectivity only in the "
        "high-RVI regime. Thresholds 50-60 remain negative. RVI>=65 turns "
        "pooled zero-slippage PnL slightly positive but is not slippage-robust. "
        "RVI>=70 is pooled profitable through 1-point slippage but fails "
        "September. RVI>=75 is positive in all three development months and "
        "through 1-point slippage, but only 11 trades exist, below the frozen "
        "minimum sample requirements. RVI>=80 has only one trade and is "
        "uninformative."
    ),
    "decision": "HIGH_RVI_75_PROMISING_BUT_UNDERPOWERED_NO_ROBUST_THRESHOLD_YET",
    "status": "EXPAND_SAMPLE_WITH_FIXED_HIGH_RVI_THRESHOLDS_BEFORE_VALIDATION",
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "do_not_promote_rvi75_from_11_trades": True,
        "do_not_promote_pe_only_from_6_trades": True,
        "no_new_threshold_search_on_jul_sep": True,
        "no_macd_parameter_tuning": True,
        "no_stop_target_search": True,
        "strategy_d_remains_paused": True,
    },
}
