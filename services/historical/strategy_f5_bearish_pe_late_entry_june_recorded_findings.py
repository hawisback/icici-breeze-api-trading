"""Recorded June 2026 holdout findings for F5 late bearish-PE entries."""

STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_HOLDOUT_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "08f764ae82c289fc344045d01b426870008f3d95b7797e6c668a7f3811a8e93b"
        ),
        "market_sha256": (
            "446d0c03ab52fe6d42ed345c9cf58bf177b4c7edb063aef119e1908fae008d14"
        ),
        "selected_sessions": 21,
        "baseline_trades": 164,
        "candidate_trades": 161,
    },
    "full_path": {
        "baseline_zero_slippage_net_pnl_inr": -11565.50,
        "candidate_zero_slippage_net_pnl_inr": -9113.77,
        "zero_slippage_delta_vs_baseline_inr": 2451.73,
        "half_point_delta_vs_baseline_inr": 2646.64,
        "one_point_delta_vs_baseline_inr": 2841.55,
        "baseline_zero_slippage_max_drawdown_inr": 22772.03,
        "candidate_zero_slippage_max_drawdown_inr": 19057.93,
        "max_drawdown_improvement_inr": 3714.10,
        "interpretation": (
            "Suppressing the frozen late bearish-PE entries reduced losses and "
            "drawdown, but the candidate full path remained negative."
        ),
    },
    "matched_entry_time_bearish_PE": {
        "all": {
            "trades": 30,
            "wins": 11,
            "trail_activations": 14,
            "net_pnl_inr": -8210.65,
            "average_net_pnl_inr": -273.69,
        },
        "late_ge_1430": {
            "trades": 3,
            "wins": 1,
            "trail_activations": 1,
            "net_pnl_inr": -2451.73,
            "average_net_pnl_inr": -817.24,
        },
        "before_1430": {
            "trades": 27,
            "wins": 10,
            "trail_activations": 13,
            "net_pnl_inr": -5758.92,
            "average_net_pnl_inr": -213.29,
        },
        "interpretation": (
            "The late subset was materially worse, but the broader entry-time "
            "bearish-PE subset was also negative in June. Time-of-day therefore "
            "did not explain the full June weakness."
        ),
    },
    "validation_gate": {
        "passed": False,
        "status": "FAIL_FRESH_HOLDOUT",
        "winner_capture_pct": 90.91,
        "activation_capture_pct": 92.86,
        "required_capture_pct": 95.0,
        "full_path_net_improved_all_slippage_models": True,
        "max_drawdown_improved": True,
        "failure_reason": (
            "One of the three suppressed baseline target trades was both a "
            "winner and a trail activation, so the frozen preservation floor "
            "was not met."
        ),
    },
    "decision": (
        "NO_1430_HARD_BLOCK_PROMOTED_KEEP_LATE_ENTRY_AS_FORWARD_DIAGNOSTIC_ONLY"
    ),
    "next_step": (
        "Do not retune the cutoff, stop, side rule or regime threshold on June. "
        "Carry late>=14:30 bearish-PE as secondary descriptive reporting into "
        "the already-frozen Oct-Dec forward holdout while leaving the primary "
        "F5 path and primary holdout gate unchanged."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_1430_hard_block_promoted": True,
        "no_june_retuning": True,
        "no_new_historical_time_cutoff_search": True,
        "no_new_pretrail_exit_search": True,
        "forward_late_entry_reporting_descriptive_only": True,
        "keep_existing_f5_entry_exit_trail_unchanged": True,
        "strategy_d_remains_paused": True,
    },
}
