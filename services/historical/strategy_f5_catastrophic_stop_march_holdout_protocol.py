"""Fresh March 2026 holdout for the frozen F5 catastrophic stop.

The only stop candidate is 27.95% option-premium drawdown from the baseline
entry open, active only before the existing +10% close-confirmed trail
activates. The candidate was derived from the empirical 95% successful-trade
MAE preservation frontier on Jul-Sep 2026 and was frozen before March scoring.

March is used only as a fresh stop-specific holdout. The dominant whole-day
NIFTY regime remains a descriptive/hindsight selector for matching the same
development population (BEARISH NIFTY + PE); it is not a same-day live signal.

Execution model
---------------
stop_price = entry_open * (1 - 0.2795)

Scan traded-option 1-minute bars from entry until the earlier of baseline exit
or +10% trail activation. If a bar opens at/below the stop, fill at that open
(gap-through). Otherwise, if its low touches/breaches the stop, fill at the
stop price. Standard 0 / 0.5 / 1.0 point slippage sensitivity is then applied.

No re-entry is introduced by the counterfactual. All non-target trades and all
post-activation management remain identical to baseline F5.
"""

from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_CATASTROPHIC_STOP_MARCH_HOLDOUT_V1"
STRATEGY_ID = "F5"
CANDIDATE_NAME = "BEARISH_PE_PRETRAIL_CATASTROPHIC_STOP_27_95PCT"
ROLE = "FRESH_HISTORICAL_HOLDOUT_VALIDATION"
FROZEN_STOP_DISTANCE_PCT = 27.95

WINDOW = {
    "start": "2026-03-02",
    "end": "2026-03-30",
    "warmup_start": "2026-02-20",
    "provider": "BREEZE",
    "spot_interval": "5minute",
    "option_source_interval": "1minute",
    "signal_interval_minutes": 2,
    "session_start": "09:15",
    "session_end_exclusive": "15:30",
    "warmup_previous_sessions_per_contract": 5,
    "expected_trading_sessions": 19,
}

# NIFTY weekly expiry is Tuesday; when Tuesday is an NSE F&O holiday the
# contract expires on the previous trading day. 2026-03-03 and 2026-03-31
# are NSE F&O holidays, hence 2026-03-02 and 2026-03-30.
EXPIRIES = [
    "2026-03-02",
    "2026-03-10",
    "2026-03-17",
    "2026-03-24",
    "2026-03-30",
]

EXCHANGE_PROVENANCE = {
    "expiry_rule": (
        "NIFTY weekly contracts expire Tuesday; previous trading day when "
        "Tuesday is a trading holiday"
    ),
    "expiry_rule_reference": "NSE/FAOP/68747",
    "march_2026_fo_holidays": ["2026-03-03", "2026-03-26", "2026-03-31"],
    "holiday_reference": "NSE/FAOP/71777",
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
    "minimum_selected_sessions": 15,
    "minimum_target_bearish_PE_trades": 8,
    "minimum_stop_triggers": 2,
    "minimum_target_winner_preservation_pct": 95.0,
    "minimum_target_activation_preservation_pct": 95.0,
    "require_target_net_improvement_0": True,
    "require_target_net_improvement_0_5": True,
    "require_target_net_improvement_1": True,
    "require_target_max_drawdown_improvement_0": True,
    "require_full_path_net_improvement_0": True,
    "require_full_path_net_improvement_0_5": True,
    "require_full_path_net_improvement_1": True,
    "require_full_path_max_drawdown_improvement_0": True,
    "purpose": "single_fresh_stop_holdout_not_final_live_validation",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "single_frozen_stop_distance_pct": FROZEN_STOP_DISTANCE_PCT,
    "no_march_stop_retuning": True,
    "no_nearby_stop_comparison": True,
    "no_30_60_candidate": True,
    "whole_day_regime_is_descriptive_hindsight_selector": True,
    "target_only_bearish_regime_PE": True,
    "existing_f5_entry_and_post_activation_trail_frozen": True,
    "counterfactual_stop_does_not_create_reentries": True,
    "oct_dec_forward_regime_holdout_remains_blind": True,
    "strategy_d_remains_paused": True,
}
