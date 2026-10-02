from services.historical.independent_execution_feasibility_findings import (
    EXECUTION_FEASIBILITY_FINDINGS_V1,
)
from services.historical.independent_execution_feasibility_protocol import (
    FROZEN_COST_FLOORS_BPS,
    FROZEN_GROSS_EVIDENCE_BPS,
)


def test_execution_audit_is_non_tunable_and_non_candidate():
    result = EXECUTION_FEASIBILITY_FINDINGS_V1
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["guardrails"]["no_threshold_rescue"] is True
    assert result["guardrails"]["no_spread_or_slippage_tuning"] is True


def test_minute1_effect_fails_stt_floor_before_slippage():
    gross = FROZEN_GROSS_EVIDENCE_BPS["minute1_mean"]
    upper = FROZEN_GROSS_EVIDENCE_BPS[
        "minute1_upper_quartile_mean_descriptive_only"
    ]
    stt = FROZEN_COST_FLOORS_BPS["current_futures_stt_sell_leg_only"]
    assert gross == 0.4072887276291944
    assert upper == 0.851829226580394
    assert stt == 5.0
    assert gross < stt
    assert upper < stt
    assert EXECUTION_FEASIBILITY_FINDINGS_V1["primary_gate"]["passed"] is False
    assert (
        EXECUTION_FEASIBILITY_FINDINGS_V1["decision"]
        == "ECONOMICALLY_INFEASIBLE_FOR_FUTURES_EXECUTION"
    )


def test_no_l1_collection_is_authorized_after_cost_floor_failure():
    assert (
        EXECUTION_FEASIBILITY_FINDINGS_V1["guardrails"][
            "no_l1_collection_for_this_formulation"
        ]
        is True
    )
