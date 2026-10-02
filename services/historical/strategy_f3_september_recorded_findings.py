"""Recorded September 2026 findings for corrected option-native Strategy F3."""

STRATEGY_F3_SEPTEMBER_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F3_SEPTEMBER_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F3_OPTION_NATIVE_MACD_V1",
    "strategy_id": "F3",
    "research_only": True,
    "source": {
        "backtest_artifact_sha256": (
            "ac1d6103670fa869d665b124a49c9eb2900e90cada6bff405096afae7a2406f9"
        ),
        "market_artifact_sha256": (
            "f44907ef39b070514fe175f8f7aecac84049799b6b90e7f99be8a6a9632e0921"
        ),
        "window": ["2026-09-01", "2026-09-30"],
        "selected_sessions": 21,
        "scorable_trades": 71,
        "skipped_trades": 0,
        "trade_price_coverage_pct": 100.0,
    },
    "overall": {
        "wins": 21,
        "losses": 50,
        "win_rate_pct": 29.58,
        "total_gross_pnl_inr": 4546.75,
        "total_net_pnl_inr": 170.37,
        "profit_factor": 1.0041,
        "max_drawdown_inr": 12256.03,
        "median_net_pnl_inr": -392.16,
        "largest_win_inr": 6869.3,
        "largest_loss_inr": -4340.08,
        "average_hold_minutes": 69.93,
        "profitable_days": 5,
        "losing_days": 16,
    },
    "ce_leg": {
        "trades": 34,
        "wins": 9,
        "losses": 25,
        "win_rate_pct": 26.47,
        "total_gross_pnl_inr": -7936.5,
        "total_net_pnl_inr": -10051.34,
        "profit_factor": 0.565,
        "max_drawdown_inr": 12022.66,
    },
    "pe_leg": {
        "trades": 37,
        "wins": 12,
        "losses": 25,
        "win_rate_pct": 32.43,
        "total_gross_pnl_inr": 12483.25,
        "total_net_pnl_inr": 10221.71,
        "profit_factor": 1.569,
        "max_drawdown_inr": 6256.77,
        "largest_win_inr": 6869.3,
        "largest_winner_share_of_net_pct": 67.2,
        "net_after_removing_largest_winner_inr": 3352.41,
        "profit_factor_after_removing_largest_winner": 1.1866,
        "net_after_removing_two_largest_winners_inr": -939.57,
        "posthoc_only": True,
    },
    "slippage_sensitivity": {
        "overall": {
            "0.0": 170.37,
            "0.5": -4442.37,
            "1.0": -9055.12,
        },
        "pe_leg_posthoc": {
            "0.0": 10221.71,
            "0.5": 7817.87,
            "1.0": 5414.04,
        },
        "ce_leg_posthoc": {
            "0.0": -10051.34,
            "0.5": -12260.24,
            "1.0": -14469.16,
        },
    },
    "interpretation": (
        "Correct option-native MACD is materially stronger than the earlier "
        "spot-driven Strategy F variants, but the symmetric CE/PE strategy is "
        "only marginally positive at zero slippage and fails under modest "
        "slippage. The PE leg is a strong post-hoc asymmetry that requires "
        "unchanged replication before any PE-only promotion."
    ),
    "status": "MARGINAL_OVERALL_PE_ASYMMETRY_REQUIRES_REPLICATION",
    "guardrails": {
        "backtest_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "pe_only_is_posthoc": True,
        "no_pe_only_promotion_from_september": True,
        "no_macd_parameter_tuning_on_september": True,
        "no_time_of_day_filter_selection_from_september": True,
        "no_stop_target_search_on_september": True,
        "strategy_d_remains_paused": True,
    },
}
