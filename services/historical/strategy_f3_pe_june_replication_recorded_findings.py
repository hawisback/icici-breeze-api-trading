"""Recorded June 2026 failure for the frozen Strategy F3 PE-only candidate."""

STRATEGY_F3_PE_JUNE_REPLICATION_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F3_PE_JUNE_REPLICATION_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F3_PE_JUNE_REPLICATION_V1",
    "strategy_id": "F3_PE",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "7f71026952119473f8473ff8f4935bf40200382fa7c01388ea88fb1049b044fc"
        ),
        "market_artifact_sha256": (
            "0f746a3c41a46d7d3e4a5490963944c5e0540604fa18800ffd326b728f973ecf"
        ),
        "window": ["2026-06-01", "2026-06-30"],
        "selected_sessions": 21,
        "scorable_trades": 62,
        "skipped_trades": 0,
        "trade_price_coverage_pct": 100.0,
    },
    "summary": {
        "wins": 18,
        "losses": 44,
        "win_rate_pct": 29.03,
        "total_gross_pnl_inr": -12411.75,
        "total_net_pnl_inr": -16194.81,
        "profit_factor": 0.6478,
        "max_drawdown_inr": 26183.63,
        "median_net_pnl_inr": -663.35,
        "largest_win_inr": 7354.04,
        "largest_loss_inr": -3328.11,
    },
    "slippage_net_pnl_inr": {
        "0.0": -16194.81,
        "0.5": -20167.72,
        "1.0": -24130.88,
    },
    "replication_gate": {
        "passed": False,
        "failures": [
            "net_pnl_not_positive",
            "profit_factor_not_above_one",
            "half_point_slippage_net_not_positive",
            "one_point_slippage_net_not_positive",
        ],
    },
    "cross_month_interpretation": {
        "august_pe_net_pnl_inr": 11746.93,
        "september_pe_net_pnl_inr": 10221.71,
        "june_pe_net_pnl_inr": -16194.81,
        "decision": "PE_ONLY_OPTION_NATIVE_MACD_NOT_ROBUST_ACROSS_MONTHS",
    },
    "diagnostics_only": {
        "profitable_days": 6,
        "losing_days": 15,
        "early_0920_1025_net_pnl_inr": -11805.60,
        "note": (
            "Time-of-day results are same-sample diagnostics only and must not "
            "be promoted into a June-derived filter."
        ),
    },
    "status": "PE_ONLY_REPLICATION_FAILED_CLOSE_SIDE_SELECTION_BRANCH",
    "guardrails": {
        "backtest_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_pe_only_rescue_on_june": True,
        "no_time_of_day_filter_from_june": True,
        "no_macd_parameter_tuning_on_inspected_months": True,
        "no_stop_target_search_on_inspected_months": True,
        "strategy_d_remains_paused": True,
    },
}
