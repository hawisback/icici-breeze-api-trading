"""Recorded March 2026 holdout findings for the frozen F5 catastrophic stop."""

STRATEGY_F5_CATASTROPHIC_STOP_MARCH_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "STRATEGY_F5_CATASTROPHIC_STOP_MARCH_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "STRATEGY_F5_CATASTROPHIC_STOP_MARCH_HOLDOUT_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "35a0617b65d4ea33a20f96b53bf4b9a7c3c8e54700031a0a11897b15ad4a12c2"
        ),
        "market_sha256": (
            "308089c6797ba3ec04ef8e2aa8c88a7098f30c4591b2dc15e6685919fd0f89cb"
        ),
        "selected_sessions": 19,
        "baseline_trades": 134,
        "target_bearish_PE_trades": 37,
    },
    "frozen_candidate": {
        "stop_distance_pct": 27.95,
        "stop_triggers": 1,
        "winner_preservation_pct": 100.0,
        "activation_preservation_pct": 100.0,
    },
    "target_bearish_PE": {
        "baseline_zero_slippage_net_pnl_inr": 11614.70,
        "candidate_zero_slippage_net_pnl_inr": 11736.68,
        "delta_zero_slippage_inr": 121.98,
        "baseline_half_point_net_pnl_inr": 9210.90,
        "candidate_half_point_net_pnl_inr": 9332.88,
        "delta_half_point_inr": 121.98,
        "baseline_one_point_net_pnl_inr": 6807.05,
        "candidate_one_point_net_pnl_inr": 6929.03,
        "delta_one_point_inr": 121.98,
        "baseline_zero_slippage_max_drawdown_inr": 6316.05,
        "candidate_zero_slippage_max_drawdown_inr": 6316.05,
    },
    "full_path": {
        "baseline_zero_slippage_net_pnl_inr": -40580.27,
        "candidate_zero_slippage_net_pnl_inr": -40458.29,
        "delta_zero_slippage_inr": 121.98,
        "baseline_zero_slippage_max_drawdown_inr": 53579.20,
        "candidate_zero_slippage_max_drawdown_inr": 53457.22,
    },
    "single_trigger": {
        "date": "2026-03-30",
        "entry_timestamp": "2026-03-30T09:17:00+05:30",
        "baseline_winner": False,
        "baseline_trail_activated": False,
        "baseline_zero_slippage_net_pnl_inr": -2921.36,
        "counterfactual_zero_slippage_net_pnl_inr": -2799.38,
        "saved_zero_slippage_inr": 121.98,
        "trigger_type": "INTRABAR_LOW_TOUCH",
    },
    "validation_gate": {
        "passed": False,
        "status": "INCONCLUSIVE_COVERAGE",
        "coverage_selected_sessions": True,
        "coverage_target_trades": True,
        "coverage_stop_triggers": False,
        "minimum_stop_triggers_required": 2,
        "observed_stop_triggers": 1,
    },
    "decision": "MARCH_INCONCLUSIVE_KEEP_27_95_FROZEN_EXTEND_TO_UNTOUCHED_JAN_FEB",
    "next_step": (
        "Validate the unchanged 27.95% catastrophic stop on untouched Jan-Feb "
        "2026. Do not retune the stop from March and do not merge March into "
        "development."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "march_is_inconclusive_not_pass_or_fail": True,
        "keep_stop_27_95_frozen": True,
        "no_stop_retuning": True,
        "no_30_60_candidate": True,
        "strategy_d_remains_paused": True,
    },
}
