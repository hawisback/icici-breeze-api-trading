"""Prospective Oct-Dec 2026 validation of the frozen F5 catastrophic stop.

Frozen on 2026-10-02 before the forward window opens. This study consumes the
same raw market artifact as the existing F5 forward-regime holdout but is a
separate stop-specific analyzer and does not alter that holdout's primary gate.

Candidate
---------
Exactly one pre-activation catastrophic stop:
27.95% option-premium drawdown from the baseline F5 entry open.

Target population
-----------------
For comparability with the development program, primary scoring is restricted
to baseline F5 PE trades on whole-day BEARISH NIFTY sessions. That regime label
is descriptive hindsight and is not a live same-day signal. Entry-time bearish
PE is reported secondarily for translational context only.

No partial-window outcomes may be scored before the final 2026-12-29 session.
No nearby stop distance, 30.60% reference, buffer, or alternate execution rule
may be compared.
"""

from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_CATASTROPHIC_STOP_FORWARD_HOLDOUT_V1"
SOURCE_MARKET_PROTOCOL_VERSION = "STRATEGY_F5_FORWARD_REGIME_HOLDOUT_V1"
STRATEGY_ID = "F5"
ROLE = "FRESH_FORWARD_CATASTROPHIC_STOP_VALIDATION"
FROZEN_ON = "2026-10-02"
FROZEN_STOP_DISTANCE_PCT = 27.95

WINDOW = {
    "start": "2026-10-05",
    "end": "2026-12-29",
    "final_required_session": "2026-12-29",
    "expected_target_sessions": 58,
}

STOP_EXECUTION = {
    "distance_pct": FROZEN_STOP_DISTANCE_PCT,
    "active_until": "EARLIER_OF_BASELINE_EXIT_OR_TRAIL_ACTIVATION",
    "trigger_source": "1M_OPTION_LOW",
    "gap_rule": "IF_1M_OPEN_LE_STOP_PRICE_FILL_AT_1M_OPEN",
    "touch_rule": "ELSE_IF_1M_LOW_LE_STOP_PRICE_FILL_AT_STOP_PRICE",
    "post_activation_change": False,
    "reentry_after_counterfactual_stop": False,
}

VALIDATION_GATE = {
    "minimum_target_bearish_PE_trades": 20,
    "minimum_stop_triggers": 2,
    "minimum_target_winner_preservation_pct": 95.0,
    "minimum_target_activation_preservation_pct": 95.0,
    "require_target_net_improvement_0": True,
    "require_target_net_improvement_0_5": True,
    "require_target_net_improvement_1": True,
    "require_target_max_drawdown_not_worse_0": True,
    "require_full_path_net_improvement_0": True,
    "require_full_path_net_improvement_0_5": True,
    "require_full_path_net_improvement_1": True,
    "require_full_path_max_drawdown_not_worse_0": True,
    "if_trigger_coverage_fails": "INCONCLUSIVE_NOT_FAIL",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "fresh_forward_holdout": True,
    "no_partial_window_outcome_reporting": True,
    "single_frozen_stop_distance_pct": FROZEN_STOP_DISTANCE_PCT,
    "no_stop_retuning": True,
    "no_nearby_stop_comparison": True,
    "no_30_60_candidate": True,
    "primary_whole_day_regime_is_descriptive_hindsight_selector": True,
    "existing_forward_regime_holdout_gate_unchanged": True,
    "existing_f5_entry_exit_postactivation_trail_frozen": True,
    "strategy_d_remains_paused": True,
}
