"""Recorded August 2026 replication findings for corrected Strategy F3."""

STRATEGY_F3_AUGUST_REPLICATION_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F3_AUGUST_REPLICATION_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F3_AUGUST_REPLICATION_V1",
    "strategy_id": "F3",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "e6d25f2cdd895e5d540b85a38d88ef4957c9255e8f6564864c63add264699b5a"
        ),
        "market_artifact_sha256": (
            "97cbc53c85d1e6c6da905f896e988f5bcbbe1816eaed0aa4e8b4a5a1ce97f271"
        ),
        "window": ["2026-08-01", "2026-08-31"],
        "selected_sessions": 21,
        "scorable_trades": 60,
        "skipped_trades": 0,
        "trade_price_coverage_pct": 100.0,
    },
    "overall": {
        "wins": 16,
        "losses": 44,
        "win_rate_pct": 26.67,
        "total_gross_pnl_inr": 11066.25,
        "total_net_pnl_inr": 7538.38,
        "profit_factor": 1.2529,
        "max_drawdown_inr": 11046.48,
        "largest_win_inr": 5811.72,
        "median_net_pnl_inr": -332.68,
        "profitable_days": 9,
        "losing_days": 12,
    },
    "ce_leg": {
        "trades": 31,
        "wins": 6,
        "losses": 25,
        "win_rate_pct": 19.35,
        "total_gross_pnl_inr": -2424.5,
        "total_net_pnl_inr": -4208.55,
        "profit_factor": 0.7131,
        "max_drawdown_inr": 8230.29,
        "slippage_net_pnl_inr": {
            "0.0": -4208.55,
            "0.5": -6222.59,
            "1.0": -8210.63,
        },
    },
    "pe_leg": {
        "trades": 29,
        "wins": 10,
        "losses": 19,
        "win_rate_pct": 34.48,
        "total_gross_pnl_inr": 13490.75,
        "total_net_pnl_inr": 11746.93,
        "profit_factor": 1.7761,
        "max_drawdown_inr": 5460.45,
        "largest_win_inr": 5811.72,
        "largest_winner_share_of_net_pct": 49.47,
        "net_after_removing_largest_winner_inr": 5935.21,
        "profit_factor_after_removing_largest_winner": 1.3921,
        "net_after_removing_two_largest_winners_inr": 679.36,
        "profit_factor_after_removing_two_largest_winners": 1.0449,
        "slippage_net_pnl_inr": {
            "0.0": 11746.93,
            "0.5": 9862.86,
            "1.0": 7978.79,
        },
    },
    "full_strategy_slippage_net_pnl_inr": {
        "0.0": 7538.38,
        "0.5": 3640.27,
        "1.0": -231.84,
    },
    "cross_month_august_september": {
        "pe_leg": {
            "trades": 66,
            "net_pnl_inr": {
                "0.0": 21968.64,
                "0.5": 17680.73,
                "1.0": 13392.83,
            },
            "profit_factor": {
                "0.0": 1.6637,
                "0.5": 1.4910,
                "1.0": 1.3435,
            },
            "months_positive_after_costs": ["2026-08", "2026-09"],
            "months_positive_at_1_point_slippage_each_side": [
                "2026-08",
                "2026-09",
            ],
        },
        "ce_leg": {
            "trades": 65,
            "net_pnl_inr": {
                "0.0": -14259.89,
                "0.5": -18482.83,
                "1.0": -22679.79,
            },
            "months_negative_after_costs": ["2026-08", "2026-09"],
        },
        "full_strategy": {
            "trades": 131,
            "net_pnl_inr": {
                "0.0": 7708.75,
                "0.5": -802.10,
                "1.0": -9286.96,
            },
        },
    },
    "interpretation": (
        "The predeclared PE-side asymmetry from September replicated in August. "
        "PE option-native MACD is positive in both months after explicit costs "
        "and remains positive in both months under 1.0 premium-point slippage "
        "per side. CE is negative in both months. This supports advancing a "
        "separate PE-only candidate to a new frozen historical month, without "
        "changing MACD parameters, strike selection, or exits."
    ),
    "status": "PE_ASYMMETRY_REPLICATED_ADVANCE_PE_ONLY_CANDIDATE",
    "guardrails": {
        "backtest_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_macd_parameter_tuning": True,
        "no_time_of_day_filter_selection": True,
        "no_stop_target_search": True,
        "pe_only_candidate_requires_new_month": True,
        "strategy_d_remains_paused": True,
    },
}
