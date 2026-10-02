"""Recorded Jan-Feb 2026 holdout findings for the frozen F5 catastrophic stop."""

STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_HOLDOUT_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "8881532e0b97030d8cc92be202be6a6f6c4049f3be97fcbf18b13a0e52e43f17"
        ),
        "market_sha256": (
            "f5a8261aa4f031c61a072f43d4c6097942f8043d6a9a95820e1a2078b7f89326"
        ),
        "selected_sessions": 41,
        "baseline_trades": 276,
        "target_bearish_PE_trades": 104,
    },
    "session_count_note": {
        "protocol_expected_sessions_was": 40,
        "correct_expected_sessions": 41,
        "reason": (
            "NSE held a live F&O trading session on Sunday 2026-02-01 for the "
            "Union Budget. The market artifact correctly includes it."
        ),
        "result_impact": "NONE_ON_GATE_OR_TRADE_RESULTS",
    },
    "frozen_candidate": {
        "stop_distance_pct": 27.95,
        "stop_triggers": 0,
        "winner_preservation_pct": 100.0,
        "activation_preservation_pct": 100.0,
    },
    "target_bearish_PE": {
        "trades": 104,
        "wins": 44,
        "losses": 60,
        "baseline_zero_slippage_net_pnl_inr": 30105.87,
        "candidate_zero_slippage_net_pnl_inr": 30105.87,
        "baseline_zero_slippage_profit_factor": 1.5909,
        "candidate_zero_slippage_profit_factor": 1.5909,
        "baseline_zero_slippage_max_drawdown_inr": 9077.16,
        "candidate_zero_slippage_max_drawdown_inr": 9077.16,
    },
    "fresh_validation_evidence": {
        "jan_feb_target_trades": 104,
        "jan_feb_stop_triggers": 0,
        "march_target_trades": 37,
        "march_stop_triggers": 1,
        "combined_target_trades": 141,
        "combined_stop_triggers": 1,
        "combined_observed_trigger_rate_pct": 0.71,
        "interpretation": (
            "The 27.95% boundary behaves as a rare catastrophic backstop, not "
            "a routine loss-control exit. Fresh evidence is too sparse to "
            "validate an economic improvement claim."
        ),
    },
    "validation_gate": {
        "passed": False,
        "status": "INCONCLUSIVE_COVERAGE",
        "coverage_selected_sessions": True,
        "coverage_target_trades": True,
        "coverage_stop_triggers": False,
        "minimum_stop_triggers_required": 2,
        "observed_stop_triggers": 0,
    },
    "decision": (
        "KEEP_27_95_AS_UNVALIDATED_CATASTROPHIC_BACKSTOP_FORWARD_VALIDATE"
    ),
    "next_step": (
        "Do not tighten or retune the stop. Pre-register the unchanged 27.95% "
        "catastrophic boundary for prospective Oct-Dec reporting using the "
        "already-frozen raw forward market data, without altering the existing "
        "regime holdout or baseline F5 path."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "jan_feb_is_inconclusive_not_pass_or_fail": True,
        "keep_stop_27_95_frozen": True,
        "no_stop_retuning": True,
        "no_30_60_candidate": True,
        "do_not_call_27_95_validated": True,
        "strategy_d_remains_paused": True,
    },
}
