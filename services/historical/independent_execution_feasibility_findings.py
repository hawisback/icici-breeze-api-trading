"""Recorded result of NIFTY_EXECUTION_FEASIBILITY_AUDIT_V1."""

from services.historical.independent_execution_feasibility_protocol import (
    FROZEN_COST_FLOORS_BPS,
    FROZEN_GROSS_EVIDENCE_BPS,
    PROTOCOL_VERSION,
)

minute1 = FROZEN_GROSS_EVIDENCE_BPS["minute1_mean"]
upper = FROZEN_GROSS_EVIDENCE_BPS[
    "minute1_upper_quartile_mean_descriptive_only"
]
delayed = FROZEN_GROSS_EVIDENCE_BPS[
    "one_minute_delayed_entry_to_minute5_close_mean"
]
stt = FROZEN_COST_FLOORS_BPS["current_futures_stt_sell_leg_only"]
roundtrip = FROZEN_COST_FLOORS_BPS[
    "representative_roundtrip_explicit_cost_before_slippage"
]

EXECUTION_FEASIBILITY_FINDINGS_V1 = {
    "research_type": "NIFTY_EXECUTION_FEASIBILITY_FINDINGS_V1",
    "protocol_version": PROTOCOL_VERSION,
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "gross_evidence_bps": dict(FROZEN_GROSS_EVIDENCE_BPS),
    "cost_floors_bps": dict(FROZEN_COST_FLOORS_BPS),
    "comparisons": {
        "stt_minus_minute1_mean_bps": stt - minute1,
        "roundtrip_explicit_cost_minus_minute1_mean_bps": roundtrip - minute1,
        "stt_multiple_of_minute1_mean": stt / minute1,
        "roundtrip_explicit_cost_multiple_of_minute1_mean": roundtrip / minute1,
        "stt_multiple_of_upper_quartile_mean": stt / upper,
        "roundtrip_explicit_cost_multiple_of_upper_quartile_mean": roundtrip / upper,
        "roundtrip_explicit_cost_multiple_of_delayed_mean": roundtrip / delayed,
    },
    "primary_gate": {
        "minute1_mean_gt_stt_floor": minute1 > stt,
        "passed": minute1 > stt,
    },
    "secondary_diagnostic": {
        "upper_quartile_mean_gt_stt_floor": upper > stt,
        "passed": upper > stt,
        "candidate_implication": None,
    },
    "decision": "ECONOMICALLY_INFEASIBLE_FOR_FUTURES_EXECUTION",
    "interpretation": (
        "The recorded earliest-executable-minute gross effect is below the "
        "current futures STT sell-leg floor alone, before brokerage, exchange "
        "and SEBI charges, stamp duty, GST, bid/ask spread, market impact, and "
        "slippage. Even the descriptive upper-quartile mean remains below STT. "
        "Therefore additional L1 execution-data collection for this exact "
        "formulation is not justified."
    ),
    "next_research_decision": (
        "Do not pursue futures execution of this timing effect. If research "
        "continues, use a separate volatility/movement-magnitude forecasting "
        "project with newly frozen development data rather than modifying this "
        "signal."
    ),
    "guardrails": {
        "no_l1_collection_for_this_formulation": True,
        "no_threshold_rescue": True,
        "no_latency_rescue": True,
        "no_spread_or_slippage_tuning": True,
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_implementation": True,
        "strategy_d_remains_paused": True,
    },
}
