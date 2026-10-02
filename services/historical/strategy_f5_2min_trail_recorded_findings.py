"""Recorded Jul-Sep 2026 Strategy F5 2-minute trail findings."""

STRATEGY_F5_2MIN_TRAIL_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_2MIN_TRAIL_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_2MIN_MACD_RVI10_TRAIL10_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "window": ["2026-07-01", "2026-09-30"],
        "selected_sessions": 65,
        "raw_1m_option_rows": 242903,
        "complete_2m_option_bars": 121125,
        "incomplete_2m_buckets": 653,
        "insufficient_warmup_side_sessions": 0,
    },
    "raw_macd_exit": {
        "trades": 450,
        "net_pnl_inr": -22475.15,
        "profit_factor": 0.8592,
        "max_drawdown_inr": 43449.47,
        "average_hold_minutes": 23.61,
        "monthly_net_pnl_inr": {
            "2026-07": -1805.71,
            "2026-08": -15149.43,
            "2026-09": -5520.01,
        },
        "ce_net_pnl_inr": -27775.78,
        "ce_profit_factor": 0.6148,
        "pe_net_pnl_inr": 5300.63,
        "pe_profit_factor": 1.0606,
    },
    "trail10_close_confirmed": {
        "trades": 443,
        "net_pnl_inr": -29092.87,
        "profit_factor": 0.8056,
        "max_drawdown_inr": 49133.99,
        "average_hold_minutes": 23.14,
        "net_delta_vs_raw_macd_exit_inr": -6617.72,
        "trail_activation_count": 129,
        "trail_activation_rate_pct": 29.12,
        "trail_exit_count": 127,
        "average_peak_close_return_pct": 30.17,
        "average_final_trail_floor_return_pct": 20.17,
        "average_profit_giveback_from_peak_pct": 16.25,
        "monthly_net_pnl_inr": {
            "2026-07": -1318.52,
            "2026-08": -18163.34,
            "2026-09": -9611.01,
        },
        "ce_net_pnl_inr": -32621.03,
        "ce_profit_factor": 0.5393,
        "pe_net_pnl_inr": 3528.16,
        "pe_profit_factor": 1.0448,
        "exit_reason_counts": {
            "PRE_TRAIL_BEARISH_MACD_CROSS": 295,
            "CLOSE_CONFIRMED_TRAIL10": 127,
            "FORCE_EXIT_15_20": 21,
        },
    },
    "posthoc_trade_path_diagnostics": {
        "trail_activated_subset": {
            "trades": 129,
            "wins": 97,
            "net_pnl_inr": 112228.56,
            "profit_factor": 27.2331,
            "warning": (
                "Conditioned on a future event (reaching +10%); descriptive only, "
                "not an entry rule."
            ),
        },
        "pre_trail_bearish_exit_subset": {
            "trades": 295,
            "net_pnl_inr": -135337.95,
            "profit_factor": 0.0239,
            "warning": "Descriptive only; not a frozen stop or filter rule.",
        },
        "matched_activated_entries_raw_vs_trail": {
            "matched_entries": 124,
            "raw_macd_exit_net_pnl_inr": 116325.71,
            "raw_macd_exit_profit_factor": 10.5813,
            "trail10_net_pnl_inr": 110896.09,
            "trail10_profit_factor": 27.3628,
            "trail10_net_delta_inr": -5429.62,
            "interpretation": (
                "On entries that later activate the trail and exist in both paths, "
                "the trail reduces total PnL but materially increases profit factor. "
                "It is not an obvious too-tight-exit problem."
            ),
        },
    },
    "interpretation": (
        "RVI>=50 plus bullish 2-minute option MACD produces too many failed "
        "setups. Only 29.12% of trailing-path trades reach the +10% activation "
        "level. Trades that do reach +10% are highly profitable as a descriptive "
        "subset, while pre-activation bearish-MACD exits dominate the losses. "
        "The close-confirmed 10-point trailing distance does not improve total "
        "PnL versus the raw MACD exit and is already permissive, with average "
        "realized giveback from peak of 16.25 percentage points. The next "
        "research question should therefore focus on identifying entry-state "
        "features that predict the +10% swing, while keeping the trail frozen, "
        "rather than tuning trail width on this inspected sample."
    ),
    "decision": "ENTRY_SELECTIVITY_IS_PRIMARY_F5_PROBLEM_KEEP_TRAIL_FROZEN",
    "status": "TRAIL_FUNCTIONAL_NOT_PROFIT_IMPROVING_DIAGNOSE_ENTRY_STATE",
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "do_not_tune_trail_width_on_jul_sep": True,
        "do_not_use_reaches_10pct_as_entry_filter": True,
        "no_stop_target_search_on_inspected_sample": True,
        "any_new_entry_rule_requires_predeclared_holdout": True,
        "strategy_d_remains_paused": True,
    },
}
