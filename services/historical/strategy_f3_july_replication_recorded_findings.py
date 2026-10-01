"""Recorded July 2026 replication findings for full Strategy F3."""

STRATEGY_F3_JULY_REPLICATION_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F3_JULY_REPLICATION_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F3_JULY_REPLICATION_V1",
    "strategy_id": "F3",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "ddaf215936afc43247d7d62ffde9f44f932cf9875603afffbadabaaaa5b571ec"
        ),
        "market_artifact_sha256": (
            "1ab7ac11f01be97336f67cc114c9aecaef01e6e955eb00a7b8d05f14b2a8abe7"
        ),
        "window": ["2026-07-01", "2026-07-31"],
        "selected_sessions": 23,
        "scorable_trades": 80,
        "skipped_trades": 0,
        "trade_price_coverage_pct": 100.0,
        "insufficient_warmup_side_sessions": 0,
    },
    "overall": {
        "wins": 21,
        "losses": 59,
        "win_rate_pct": 26.25,
        "total_gross_pnl_inr": -14794.0,
        "total_net_pnl_inr": -19533.55,
        "profit_factor": 0.6661,
        "max_drawdown_inr": 19847.87,
        "largest_win_inr": 12425.13,
        "median_net_pnl_inr": -601.41,
    },
    "ce_leg": {
        "trades": 35,
        "wins": 10,
        "losses": 25,
        "total_gross_pnl_inr": -13130.0,
        "total_net_pnl_inr": -15213.44,
        "profit_factor": 0.3868,
    },
    "pe_leg": {
        "trades": 45,
        "wins": 11,
        "losses": 34,
        "total_gross_pnl_inr": -1664.0,
        "total_net_pnl_inr": -4320.11,
        "profit_factor": 0.8718,
        "largest_win_inr": 12425.13,
    },
    "slippage_net_pnl_inr": {
        "0.0": -19533.55,
        "0.5": -24666.14,
        "1.0": -29766.26,
    },
    "posthoc_diagnostics": {
        "late_session_1330_1515": {
            "trades": 29,
            "net_pnl_inr": 4967.79,
            "profit_factor": 1.3701,
            "largest_win_inr": 12425.13,
            "posthoc_only": True,
        },
        "note": (
            "The late-session bucket is same-month post-hoc and is dominated by "
            "a single very large winner; it must not be promoted into a filter."
        ),
    },
    "cross_month_full_f3": {
        "months": ["2026-07", "2026-08", "2026-09"],
        "net_pnl_inr": {
            "0.0": -11824.80,
            "0.5": -25468.24,
            "1.0": -39053.22,
        },
        "interpretation": (
            "The full option-native raw MACD crossover is not robust across "
            "July-August-September. July more than offsets the positive August "
            "and near-flat September results."
        ),
    },
    "decision": "FULL_OPTION_NATIVE_MACD_RAW_CROSSOVER_NOT_ROBUST",
    "status": "CLOSE_RAW_CROSSOVER_BRANCH_NO_SAME_SAMPLE_FILTER_RESCUE",
    "guardrails": {
        "backtest_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_time_of_day_filter_from_july": True,
        "no_side_filter_from_inspected_months": True,
        "no_macd_parameter_tuning_on_inspected_months": True,
        "no_stop_target_search_on_inspected_months": True,
        "strategy_d_remains_paused": True,
    },
}
