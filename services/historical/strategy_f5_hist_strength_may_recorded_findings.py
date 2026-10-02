"""Recorded May 2026 fresh holdout findings for F5 histogram-strength filter."""

STRATEGY_F5_HIST_STRENGTH_MAY_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_HIST_STRENGTH_MAY_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_HIST_STRENGTH_MAY_HOLDOUT_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "holdout_artifact_sha256": (
            "10fda06290b5a1f1f19cb6f37f1c349fc8b84e571bb67ca4a1a2e6e899b67ade"
        ),
        "market_artifact_sha256": (
            "2db8d43b90f66820a8dd6fc84a004c38347c11e27fc8254daec93c046f9d0557"
        ),
        "selected_sessions": 19,
        "raw_1m_option_rows": 81566,
        "complete_2m_option_bars": 40649,
        "insufficient_warmup_side_sessions": 0,
    },
    "candidate": {
        "name": "F5_HIST_PCT_GE_0_15",
        "threshold_pct_of_option_close": 0.15,
        "trades": 91,
        "win_rate_pct": 35.16,
        "net_pnl_inr": -16944.56,
        "profit_factor": 0.6552,
        "max_drawdown_inr": 24051.01,
        "trail_activation_count": 45,
        "trail_activation_rate_pct": 49.45,
        "net_at_0_5_slippage_inr": -22856.72,
        "net_at_1_0_slippage_inr": -28768.79,
        "ce": {
            "trades": 35,
            "net_pnl_inr": -14905.13,
            "profit_factor": 0.3625,
        },
        "pe": {
            "trades": 56,
            "net_pnl_inr": -2039.43,
            "profit_factor": 0.9208,
        },
    },
    "baseline": {
        "trades": 136,
        "win_rate_pct": 28.68,
        "net_pnl_inr": -30892.03,
        "profit_factor": 0.5991,
        "max_drawdown_inr": 42533.07,
        "trail_activation_count": 55,
        "trail_activation_rate_pct": 40.44,
        "net_at_0_5_slippage_inr": -39727.77,
        "net_at_1_0_slippage_inr": -48563.42,
    },
    "relative_improvement": {
        "trade_reduction": 45,
        "win_rate_improvement_percentage_points": 6.48,
        "trail_activation_rate_improvement_percentage_points": 9.01,
        "net_pnl_improvement_0_slippage_inr": 13947.47,
        "net_pnl_improvement_0_5_slippage_inr": 16871.05,
        "net_pnl_improvement_1_0_slippage_inr": 19794.63,
        "max_drawdown_reduction_inr": 18482.06,
        "baseline_activated_entry_capture_pct": 78.18,
    },
    "validation_gate": {
        "passed": False,
        "failures": [
            "candidate_zero_slippage_net_not_positive",
            "candidate_zero_slippage_profit_factor_not_above_one",
            "candidate_half_point_net_not_positive",
        ],
    },
    "interpretation": (
        "The Jul-Sep histogram-strength effect replicated directionally on fresh "
        "May data: it reduced trade count, increased the +10% trail activation "
        "rate, improved win rate, reduced drawdown, and materially improved PnL "
        "relative to the unchanged F5 baseline. However, the candidate remained "
        "loss-making at all slippage assumptions and PF remained below one. "
        "Therefore 0.15% is useful as a descriptive selectivity feature but is "
        "not sufficient as a standalone robust entry filter."
    ),
    "decision": "REJECT_STANDALONE_HISTOGRAM_STRENGTH_FILTER_NO_MAY_RETUNING",
    "status": "HOLDOUT_FAILED_FEATURE_REPLICATED_DIRECTIONALLY_BUT_NOT_ECONOMICALLY",
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "do_not_tune_histogram_threshold_on_may": True,
        "do_not_promote_pe_only_from_may_posthoc_split": True,
        "keep_existing_post_activation_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
