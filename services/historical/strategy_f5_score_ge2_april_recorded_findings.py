"""Recorded April 2026 fresh holdout findings for F5 quality score >=2."""

STRATEGY_F5_SCORE_GE2_APRIL_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_SCORE_GE2_APRIL_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_SCORE_GE2_APRIL_HOLDOUT_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "holdout_artifact_sha256": (
            "9862b569e8db9015f53a0c1e0f8c9ff96c878e40fa149313f561eb0e22b752ba"
        ),
        "market_artifact_sha256": (
            "e18dd112d55627a504a73c851f1beb022ff138f478f9944d72ef8ce07eb9cf54"
        ),
        "selected_sessions": 20,
        "raw_1m_option_rows": 79291,
        "complete_2m_option_bars": 39504,
        "insufficient_warmup_side_sessions": 0,
        "missing_candidate_regime_rows": 0,
    },
    "baseline": {
        "trades": 127,
        "win_rate_pct": 32.28,
        "net_pnl_inr": -31975.61,
        "profit_factor": 0.6536,
        "max_drawdown_inr": 41043.22,
        "trail_activation_count": 34,
        "trail_activation_rate_pct": 26.77,
        "net_at_0_5_slippage_inr": -40226.57,
        "net_at_1_0_slippage_inr": -48477.65,
    },
    "candidate": {
        "name": "F5_QUALITY_SCORE_GE_2",
        "trades": 90,
        "win_rate_pct": 35.56,
        "net_pnl_inr": -22167.57,
        "profit_factor": 0.6797,
        "max_drawdown_inr": 33554.37,
        "trail_activation_count": 28,
        "trail_activation_rate_pct": 31.11,
        "net_at_0_5_slippage_inr": -28014.70,
        "net_at_1_0_slippage_inr": -33861.88,
        "ce": {
            "trades": 51,
            "net_pnl_inr": 11603.07,
            "profit_factor": 1.4533,
            "trail_activation_rate_pct": 37.25,
        },
        "pe": {
            "trades": 39,
            "net_pnl_inr": -33770.64,
            "profit_factor": 0.2256,
            "trail_activation_rate_pct": 23.08,
        },
    },
    "relative_improvement": {
        "trade_reduction": 37,
        "win_rate_improvement_percentage_points": 3.28,
        "trail_activation_rate_improvement_percentage_points": 4.34,
        "net_pnl_improvement_0_slippage_inr": 9808.04,
        "net_pnl_improvement_0_5_slippage_inr": 12211.87,
        "net_pnl_improvement_1_0_slippage_inr": 14615.77,
        "max_drawdown_reduction_inr": 7488.85,
        "baseline_activated_signal_capture_pct": 82.35,
        "baseline_winner_signal_capture_pct": 78.05,
    },
    "validation_gate": {
        "passed": False,
        "failures": [
            "candidate_zero_slippage_net_not_positive",
            "candidate_zero_slippage_profit_factor_not_above_one",
            "candidate_half_point_net_not_positive",
        ],
        "all_preservation_and_relative_improvement_conditions_passed": True,
    },
    "interpretation": (
        "The score>=2 entry-quality effect replicated on untouched April data: "
        "it preserved most baseline activated signals and winners, raised trail "
        "activation and win rates, reduced drawdown, and improved PnL relative "
        "to baseline at every slippage assumption. However, the candidate remained "
        "materially loss-making and PF stayed below one, so it failed economic "
        "validation. April also showed extreme side asymmetry, with CE profitable "
        "and PE deeply loss-making; this is descriptive only and must not be "
        "converted into a post-hoc side filter."
    ),
    "decision": "REJECT_SCORE_GE2_AS_ECONOMICALLY_VALIDATED_F5_ENTRY_FILTER",
    "status": "FRESH_HOLDOUT_FAILED_SELECTIVITY_REPLICATED_BUT_ABSOLUTE_EDGE_ABSENT",
    "next_step": (
        "Do not tune score cutoff, score weights, component thresholds, side, "
        "time, stops, or trail on April. Treat the four-factor score as a useful "
        "descriptive quality measure, not a validated trading filter. Further F5 "
        "work should require a newly predeclared research question rather than "
        "another cutoff search."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_score_cutoff_search_on_april": True,
        "no_score_weight_search_on_april": True,
        "no_component_threshold_search_on_april": True,
        "no_side_filter_from_april": True,
        "keep_existing_post_activation_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
