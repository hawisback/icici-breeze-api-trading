"""Recorded August 2026 findings for Strategy F2 histogram persistence."""

STRATEGY_F2_AUGUST_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F2_AUGUST_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F2_MACD_HISTOGRAM_PERSISTENCE_V1",
    "strategy_id": "F2",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "01e2d5d3d4c82414e24dadc6fd39fb45e385cfbcf64bd243c232e936c745cf4e"
        ),
        "market_artifact_sha256": (
            "c1ee144f0a4421351f1fd403e89627af1177493470fd8e55a7ef511c7df8662d"
        ),
        "window": ["2026-08-01", "2026-08-31"],
        "complete_sessions": 21,
        "raw_macd_crossovers": 93,
    },
    "F2_1BAR": {
        "trades": 76,
        "wins": 21,
        "losses": 55,
        "win_rate_pct": 27.63,
        "total_gross_pnl_inr": 12307.75,
        "total_net_pnl_inr": 7901.11,
        "profit_factor": 1.2023,
        "max_drawdown_inr": 18110.55,
        "largest_win_inr": 5859.0,
        "median_net_pnl_inr": -371.78,
        "call_net_pnl_inr": 4089.51,
        "put_net_pnl_inr": 3811.6,
        "slippage_net_pnl_inr": {
            "0.0": 7901.11,
            "0.5": 2963.52,
            "1.0": -1974.06,
        },
    },
    "F2_2BAR": {
        "trades": 62,
        "wins": 19,
        "losses": 43,
        "win_rate_pct": 30.65,
        "total_gross_pnl_inr": 12060.75,
        "total_net_pnl_inr": 8444.15,
        "profit_factor": 1.3027,
        "max_drawdown_inr": 13284.1,
        "largest_win_inr": 5771.21,
        "median_net_pnl_inr": -294.82,
        "call_leg": {
            "trades": 31,
            "net_pnl_inr": 10019.29,
            "profit_factor": 1.785,
        },
        "put_leg": {
            "trades": 31,
            "net_pnl_inr": -1575.14,
            "profit_factor": 0.8959,
        },
        "slippage_net_pnl_inr": {
            "0.0": 8444.15,
            "0.5": 4416.17,
            "1.0": 388.12,
        },
    },
    "interpretation": {
        "development_result": (
            "Both frozen confirmation depths are profitable after explicit costs "
            "in August 2026. The 2-bar version has higher net P&L, profit factor, "
            "lower drawdown, and better slippage resilience on this inspected month."
        ),
        "concentration_warning": (
            "The largest F2_2BAR winner is a large fraction of total monthly net "
            "P&L, so another month is required before promotion."
        ),
        "direction_warning": (
            "The profitable option side is not stable across inspected months: "
            "August F2_2BAR is driven by calls while September F1 had positive puts."
        ),
    },
    "status": "PROMISING_DEVELOPMENT_RESULT_REQUIRES_SEPARATE_MONTH_REPLICATION",
    "guardrails": {
        "backtest_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "do_not_select_confirmation_depth_from_august_alone": True,
        "do_not_select_option_side_from_august_alone": True,
        "no_august_parameter_optimization": True,
        "strategy_d_remains_paused": True,
    },
}
