from datetime import date, timedelta

import numpy as np
import pytest

import services.historical.independent_magnitude_forecasting_model as model
from services.historical.independent_magnitude_forecasting_design_protocol import (
    BOOTSTRAP,
    PROTOCOL_VERSION,
)


def _prospective_pass() -> dict:
    return {
        "protocol_version": "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1",
        "replication_gate": {"passed": True, "failures": []},
        "decision": "PROSPECTIVE_DESCRIPTIVE_MAGNITUDE_RELATIONSHIP_REPLICATED",
    }


def _prospective_fail() -> dict:
    return {
        "protocol_version": "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1",
        "replication_gate": {"passed": False, "failures": ["bootstrap"]},
        "decision": "PROSPECTIVE_DESCRIPTIVE_MAGNITUDE_RELATIONSHIP_NOT_REPLICATED",
    }


def _synthetic_payload(sessions: int = 80) -> dict:
    start = date(2024, 1, 1)
    events = []
    for session_index in range(sessions):
        day = (start + timedelta(days=session_index)).isoformat()
        for event_index in range(8):
            predictor = 5.0 + event_index * 4.0 + (session_index % 7) * 0.5
            target = 3.0 + 1.8 * predictor + ((event_index % 3) - 1) * 0.3
            events.append(
                {
                    "date": day,
                    "trailing_30m_range_bps": predictor,
                    "next_30m_max_absolute_excursion_bps": target,
                }
            )
    return {
        "research_type": model.EVENT_RESEARCH_TYPE,
        "design_protocol_version": PROTOCOL_VERSION,
        "provider": "BREEZE",
        "genuinely_new_development_data": True,
        "source_protocol_frozen_before_collection": True,
        "source_protocol_version": "SYNTHETIC_SOURCE_PROTOCOL_V1",
        "source_market_artifact_sha256": "a" * 64,
        "source_labels": ["SYNTHETIC_NEW_DEVELOPMENT"],
        "sessions": sessions,
        "scorable_events": len(events),
        "events": events,
    }


def test_activation_hard_lock_rejects_nonpassing_prospective_result():
    with pytest.raises(RuntimeError, match="prospective gate has not passed"):
        model.assert_activation(_prospective_fail())


def test_activation_accepts_only_exact_frozen_pass():
    model.assert_activation(_prospective_pass())

    wrong = _prospective_pass()
    wrong["decision"] = "SOMETHING_ELSE"
    with pytest.raises(RuntimeError, match="required prospective pass"):
        model.assert_activation(wrong)


def test_closed_form_model_is_nonnegative_and_monotone_on_positive_slope():
    x = np.array([1.0, 2.0, 4.0, 8.0, 16.0])
    y = np.array([2.0, 3.0, 5.0, 9.0, 17.0])
    intercept, slope = model._fit_log_linear_ols(x, y)
    forecast = model._predict_log_linear(x, intercept, slope)
    assert slope > 0.0
    assert np.all(forecast >= 0.0)
    assert np.all(np.diff(forecast) > 0.0)


def test_development_payload_rejects_forbidden_existing_research_source():
    payload = _synthetic_payload()
    payload["source_labels"] = ["NIFTY_RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_V1"]
    with pytest.raises(ValueError, match="forbidden inspected samples"):
        model._validate_development_payload(payload)


def test_development_payload_requires_genuinely_new_frozen_source():
    payload = _synthetic_payload()
    payload["genuinely_new_development_data"] = False
    with pytest.raises(ValueError, match="not marked genuinely new"):
        model._validate_development_payload(payload)

    payload = _synthetic_payload()
    payload["source_protocol_frozen_before_collection"] = False
    with pytest.raises(ValueError, match="not frozen before collection"):
        model._validate_development_payload(payload)


def test_synthetic_expanding_window_forecast_can_pass_fixed_gate(monkeypatch):
    # Keep the unit test quick; production protocol remains 10,000 draws.
    monkeypatch.setitem(BOOTSTRAP, "draws", 200)

    payload = _synthetic_payload(80)
    report = model.evaluate_payload(payload, _prospective_pass())

    assert report["research_type"] == (
        "NIFTY_MAGNITUDE_FORECASTING_DEVELOPMENT_FINDINGS_V1"
    )
    assert report["sessions"] == 80
    assert report["scorable_events"] == 640
    assert [len(report["chronological_blocks"][f"block{i}"]) for i in range(1, 5)] == [
        20,
        20,
        20,
        20,
    ]
    assert len(report["fold_results"]) == 3
    assert all(
        fold["model_mae_bps"] < fold["baseline_mae_bps"]
        for fold in report["fold_results"]
    )
    assert report["pooled_oos"]["model_mae_bps"] < (
        report["pooled_oos"]["baseline_mae_bps"]
    )
    assert report["pooled_oos"]["spearman_forecast_vs_target"] > 0.0
    assert report["pooled_oos"][
        "session_cluster_bootstrap_error_improvement_95pct"
    ][0] > 0.0
    assert report["development_gate"] == {"passed": True, "failures": []}
    assert report["decision"] == (
        "FORECASTING_MODEL_DEVELOPMENT_PASSED_BUT_NO_TRADING_PROMOTION"
    )
    assert report["guardrails"]["pass_does_not_create_trading_candidate"] is True
    assert report["guardrails"]["pass_does_not_authorize_implementation"] is True
