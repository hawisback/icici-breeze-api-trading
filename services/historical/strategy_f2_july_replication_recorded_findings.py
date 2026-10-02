"""Recorded July 2026 replication findings for Strategy F2."""

STRATEGY_F2_JULY_REPLICATION_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F2_JULY_REPLICATION_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F2_JULY_REPLICATION_V1",
    "strategy_id": "F2",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "7db0262543e8f42b1bb2bd284c6a5cca056f0ea0b4113f36414257c209bbfaa4"
        ),
        "market_artifact_sha256": (
            "2677e9aca7e7d366f838e64962c9a555b9eff3b37244acfc3f788cc84211bc97"
        ),
        "window": ["2026-07-01", "2026-07-31"],
        "complete_sessions": 23,
        "raw_macd_crossovers": 128,
    },
    "F2_1BAR": {
        "trades": 105,
        "wins": 24,
        "losses": 81,
        "win_rate_pct": 22.86,
        "total_gross_pnl_inr": -22509.5,
        "total_net_pnl_inr": -28739.46,
        "profit_factor": 0.6068,
        "max_drawdown_inr": 30893.24,
        "largest_win_inr": 7173.39,
        "median_net_pnl_inr": -595.73,
        "call_net_pnl_inr": -19027.46,
        "put_net_pnl_inr": -9712.0,
        "slippage_net_pnl_inr": {
            "0.0": -28739.46,
            "0.5": -35512.46,
            "1.0": -42265.99,
        },
    },
    "F2_2BAR": {
        "trades": 82,
        "wins": 18,
        "losses": 64,
        "win_rate_pct": 21.95,
        "total_gross_pnl_inr": -22808.5,
        "total_net_pnl_inr": -27665.82,
        "profit_factor": 0.5526,
        "max_drawdown_inr": 29455.44,
        "largest_win_inr": 5256.39,
        "median_net_pnl_inr": -681.7,
        "call_leg": {
            "trades": 43,
            "net_pnl_inr": -15842.15,
            "profit_factor": 0.46,
        },
        "put_leg": {
            "trades": 39,
            "net_pnl_inr": -11823.67,
            "profit_factor": 0.6362,
        },
        "slippage_net_pnl_inr": {
            "0.0": -27665.82,
            "0.5": -32944.55,
            "1.0": -38207.06,
        },
    },
    "cross_month_interpretation": {
        "august_result": (
            "Both variants were positive in August 2026, with F2_2BAR the "
            "stronger development result."
        ),
        "july_replication": (
            "Both frozen variants are materially negative before and after "
            "costs in July 2026. The August histogram-persistence result does "
            "not replicate."
        ),
        "decision": (
            "F2_HISTOGRAM_PERSISTENCE_NOT_ROBUST_ACROSS_JULY_AUGUST_2026"
        ),
    },
    "status": "REPLICATION_FAILED_CLOSE_CONFIRMATION_DEPTH_BRANCH",
    "guardrails": {
        "backtest_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_more_confirmation_depth_tuning_on_july_august": True,
        "no_option_side_selection_from_inspected_months": True,
        "no_macd_parameter_tuning_on_inspected_months": True,
        "strategy_d_remains_paused": True,
    },
}
