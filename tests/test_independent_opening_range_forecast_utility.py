import numpy as np
import pandas as pd
import pytest

import services.historical.independent_opening_range_forecast_utility as utility
from services.historical.independent_opening_range_forecast_utility_protocol import (
    FOLDS,
    GUARDRAILS,
    PROTOCOL_VERSION,
    UTILITY_GATE,
)


def _synthetic_sessions() -> pd.DataFrame:
    rows = []
    for year in (2022, 2023, 2024):
        for i in range(120):
            opening = 12.0 + (i % 30) * 2.5 + (year - 2022) * 0.2
            target = 18.0 + 1.45 * opening + ((i % 7) - 3) * 0.2
            rows.append(
                {
                    "date": f"{year}-{1 + (i // 28):02d}-{1 + (i % 28):02d}",
                    "year": year,
                    "first_30m_high_low_range_bps": opening,
                    "post_09_40_remaining_session_high_low_range_bps": target,
                }
            )
    return pd.DataFrame(rows)


def test_protocol_freezes_two_chronological_oos_folds():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_OPENING_RANGE_FORECAST_UTILITY_V1"
    assert FOLDS == [
        {
            "name": "train_2022_test_2023",
            "train_years": [2022],
            "test_year": 2023,
        },
        {
            "name": "train_2022_2023_test_2024",
            "train_years": [2022, 2023],
            "test_year": 2024,
        },
    ]
    assert UTILITY_GATE["minimum_complete_sessions"] == 600


def test_opening_range_model_can_beat_training_median_baseline(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setitem(utility.BOOTSTRAP, "draws", 300)
    report = utility._evaluate_sessions(_synthetic_sessions())

    assert len(report["fold_results"]) == 2
    assert all(row["model_mae_lower"] for row in report["fold_results"])
    assert all(row["model_spearman"] > 0.0 for row in report["fold_results"])
    assert report["pooled_oos"]["model_mae_bps"] < (
        report["pooled_oos"]["baseline_mae_bps"]
    )
    assert report["pooled_oos"]["mae_improvement_bps"] > 0.0
    assert report["pooled_oos"]["skill"] > 0.0
    assert report["pooled_oos"]["bootstrap_error_improvement_95pct"][0] > 0.0
    assert report["utility_gate"] == {"passed": True, "failures": []}


def test_baseline_is_training_sample_median_only():
    sessions = _synthetic_sessions()
    train_2022 = sessions.loc[sessions["year"] == 2022]
    expected = float(
        train_2022["post_09_40_remaining_session_high_low_range_bps"].median()
    )
    report = utility._evaluate_sessions(sessions)
    assert report["fold_results"][0]["baseline_median_bps"] == pytest.approx(
        expected
    )


def test_guardrails_keep_utility_study_nontrading():
    assert GUARDRAILS["same_history_characterization"] is True
    assert GUARDRAILS["blind_validation"] is False
    assert GUARDRAILS["feature_search"] is False
    assert GUARDRAILS["model_family_search"] is False
    assert GUARDRAILS["hyperparameter_search"] is False
    assert GUARDRAILS["threshold_optimization"] is False
    assert GUARDRAILS["directional_entry_exit_rule"] is False
    assert GUARDRAILS["pnl_scored"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["no_rescue_on_same_sample"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
