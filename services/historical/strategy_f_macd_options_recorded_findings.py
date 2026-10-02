"""Recorded findings from Strategy F MACD options September 2026 backtest."""

STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F_MACD_OPTIONS_V1",
    "strategy_id": "F",
    "strategy_name": "MACD_CALL_PUT_CROSSOVER",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "5392929a14f56a656708b90eb9ad1d4c8cca3da7d2982d66fedc7ae7bca90f91"
        ),
        "market_artifact_sha256": (
            "ac22701ea605dbedd023049f4355479cf1c5cc9a60494ab56b9e1f70204caae4"
        ),
        "window": ["2026-09-01", "2026-09-30"],
        "complete_sessions": 21,
        "scorable_trades": 109,
        "skipped_trades": 0,
        "trade_price_coverage_pct": 100.0,
        "fidelity": "PARTIAL_FIDELITY_OHLC_OPEN_PLUS_EXPLICIT_COSTS",
    },
    "overall": {
        "wins": 31,
        "losses": 78,
        "win_rate_pct": 28.44,
        "total_gross_pnl_inr": -3682.25,
        "total_net_pnl_inr": -10051.74,
        "average_net_pnl_inr": -92.22,
        "median_net_pnl_inr": -483.37,
        "profit_factor": 0.8429,
        "max_drawdown_inr": 31781.45,
        "largest_win_inr": 5479.52,
        "largest_loss_inr": -2360.82,
        "average_hold_minutes": 65.73,
    },
    "call_leg": {
        "trades": 52,
        "wins": 15,
        "losses": 37,
        "win_rate_pct": 28.85,
        "total_gross_pnl_inr": -15603.25,
        "total_net_pnl_inr": -18688.47,
        "profit_factor": 0.5006,
        "median_net_pnl_inr": -669.78,
    },
    "put_leg": {
        "trades": 57,
        "wins": 16,
        "losses": 41,
        "win_rate_pct": 28.07,
        "total_gross_pnl_inr": 11921.0,
        "total_net_pnl_inr": 8636.73,
        "profit_factor": 1.3253,
        "median_net_pnl_inr": -391.22,
        "posthoc_only": True,
    },
    "slippage_sensitivity_overall": {
        "0.0_points_each_side_net_pnl_inr": -10051.74,
        "0.5_points_each_side_net_pnl_inr": -17133.29,
        "1.0_points_each_side_net_pnl_inr": -24214.90,
    },
    "posthoc_diagnostics": {
        "put_leg_net_at_0_5_point_slippage_each_side_inr": 4933.51,
        "put_leg_net_at_1_0_point_slippage_each_side_inr": 1230.26,
        "put_leg_without_largest_winner_net_pnl_inr": 3157.21,
        "put_leg_without_two_largest_winners_net_pnl_inr": -2244.85,
        "trades_holding_15_minutes_or_less": 20,
        "net_pnl_holding_15_minutes_or_less_inr": -14788.44,
        "wins_holding_15_minutes_or_less": 0,
        "trades_holding_more_than_60_minutes": 49,
        "net_pnl_holding_more_than_60_minutes_inr": 30874.71,
        "interpretation": (
            "These are same-sample descriptive diagnostics only. Holding duration "
            "is known only after the trade and cannot be used as a prospective "
            "entry filter. The diagnostics identify rapid MACD recross/whipsaw as "
            "the dominant failure mode and show a post-hoc bearish-side asymmetry."
        ),
    },
    "decision": "STRATEGY_F_V1_SYMMETRIC_MACD_CROSSOVER_REJECTED_FOR_SEPTEMBER_2026",
    "interpretation": (
        "The symmetric raw MACD crossover strategy loses before and after "
        "explicit transaction costs. The PE leg is positive on this inspected "
        "month, but that asymmetry was discovered after scoring and is not a "
        "validated Strategy F variant. No same-month PE-only promotion or "
        "parameter/filter optimization is allowed."
    ),
    "guardrails": {
        "backtest_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "pe_only_is_posthoc_diagnostic": True,
        "no_same_month_parameter_optimization": True,
        "no_same_month_filter_search": True,
        "strategy_d_remains_paused": True,
    },
}
