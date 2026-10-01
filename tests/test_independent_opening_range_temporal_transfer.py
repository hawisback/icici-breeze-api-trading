import numpy as np
import pandas as pd

import services.historical.independent_opening_range_temporal_transfer as transfer
from services.historical.independent_opening_range_temporal_transfer_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    TRANSFER_GATE,
)


def _synthetic_transfer() -> pd.DataFrame:
    rows = []
    for year in (2025, 2026):
        for i in range(220):
            opening = 10.0 + (i % 40) * 1.8 + (year - 2025) * 0.2
            target = 14.0 + 1.5 * opening + ((i % 5) - 2) * 0.15
            rows.append(
                {
                    "date": f"{year}-{1 + (i // 28):02d}-{1 + (i % 28):02d}",
                    "year": year,
                    "first_30m_high_low_range_bps": opening,
                    "post_09_40_remaining_session_high_low_range_bps": target,
                }
            )
    return pd.DataFrame(rows)


def test_protocol_freezes_no_refit_transfer_question():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_OPENING_RANGE_TEMPORAL_TRANSFER_V1"
    assert TRANSFER_GATE["minimum_complete_sessions"] == 400
    assert GUARDRAILS["post_discovery_transfer"] is True
    assert GUARDRAILS["blind_validation"] is False
    assert GUARDRAILS["transfer_refit"] is False
    assert GUARDRAILS["feature_search"] is False
    assert GUARDRAILS["model_family_search"] is False
    assert GUARDRAILS["threshold_optimization"] is False


def test_fixed_model_can_transfer_with_positive_utility(monkeypatch):
    frame = _synthetic_transfer()

    # Coefficients chosen from a fixed positive log-linear relation; no fitting
    # occurs inside _score_transfer.
    beta = np.array([1.3, 0.75], dtype=float)
    baseline_value = 75.0
    monkeypatch.setitem(transfer.BOOTSTRAP, "draws", 300)

    report = transfer._score_transfer(frame, beta, baseline_value)

    assert set(report["calendar_year_results"]) == {"2025", "2026"}
    assert all(
        row["model_mae_lower"]
        for row in report["calendar_year_results"].values()
    )
    assert all(
        row["model_spearman"] > 0.0
        for row in report["calendar_year_results"].values()
    )
    assert report["pooled_transfer"]["model_mae_bps"] < (
        report["pooled_transfer"]["baseline_mae_bps"]
    )
    assert report["pooled_transfer"]["bootstrap_error_improvement_95pct"][0] > 0.0
    assert report["transfer_gate"] == {"passed": True, "failures": []}


def test_transfer_guardrails_remain_nontrading():
    assert GUARDRAILS["hyperparameter_search"] is False
    assert GUARDRAILS["directional_entry_exit_rule"] is False
    assert GUARDRAILS["pnl_scored"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["no_rescue_on_same_transfer_sample"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
