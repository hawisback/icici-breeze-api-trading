"""Fresh Jan-Feb 2026 holdout for the frozen F5 catastrophic stop.

This is a second independent validation attempt for exactly the same 27.95%
pre-activation option-premium stop. March 2026 produced only one stop trigger
and was therefore INCONCLUSIVE_COVERAGE. March is not used to change the stop.

The dominant whole-day NIFTY regime remains a descriptive/hindsight selector
for matching the original development population (BEARISH NIFTY + PE); it is
not a live same-day signal.

Execution is unchanged from the March protocol:
- stop = entry_open * (1 - 0.2795)
- active only before existing +10% trail activation
- 1m open through stop -> fill at open
- otherwise 1m low touch -> fill at stop
- standard 0 / 0.5 / 1.0 point slippage sensitivity
- no counterfactual re-entry
"""

from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_HOLDOUT_V1"
STRATEGY_ID = "F5"
CANDIDATE_NAME = "BEARISH_PE_PRETRAIL_CATASTROPHIC_STOP_27_95PCT"
ROLE = "FRESH_HISTORICAL_HOLDOUT_VALIDATION"
FROZEN_STOP_DISTANCE_PCT = 27.95

WINDOW = {
    "start": "2026-01-01",
    "end": "2026-02-27",
    "warmup_start": "2025-12-19",
    "provider": "BREEZE",
    "spot_interval": "5minute",
    "option_source_interval": "1minute",
    "signal_interval_minutes": 2,
    "session_start": "09:15",
    "session_end_exclusive": "15:30",
    "warmup_previous_sessions_per_contract": 5,
    "expected_trading_sessions": 40,
}

EXPIRIES = [
    "2026-01-06",
    "2026-01-13",
    "2026-01-20",
    "2026-01-27",
    "2026-02-03",
    "2026-02-10",
    "2026-02-17",
    "2026-02-24",
    "2026-03-02",
]

EXCHANGE_PROVENANCE = {
    "expiry_rule": (
        "NIFTY weekly contracts expire Tuesday; previous trading day when "
        "Tuesday is a trading holiday"
    ),
    "expiry_rule_reference": "NSE/FAOP/68747",
    "jan_feb_fo_holidays": ["2026-01-15", "2026-01-26"],
    "holiday_references": ["NSE/FAOP/71777", "NSE/FAOP/72262"],
    "march_03_holiday_causes_next_expiry": "2026-03-02",
    "nifty_lot_size": 65,
    "lot_size_reference": "NSE/FAOP/70616",
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
    "minimum_selected_sessions": 30,
    "minimum_target_bearish_PE_trades": 15,
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
    "purpose": "second_fresh_stop_holdout_not_final_live_validation",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "single_frozen_stop_distance_pct": FROZEN_STOP_DISTANCE_PCT,
    "march_result_did_not_change_stop": True,
    "no_jan_feb_stop_retuning": True,
    "no_nearby_stop_comparison": True,
    "no_30_60_candidate": True,
    "whole_day_regime_is_descriptive_hindsight_selector": True,
    "target_only_bearish_regime_PE": True,
    "existing_f5_entry_and_post_activation_trail_frozen": True,
    "counterfactual_stop_does_not_create_reentries": True,
    "oct_dec_forward_regime_holdout_remains_blind": True,
    "strategy_d_remains_paused": True,
}
