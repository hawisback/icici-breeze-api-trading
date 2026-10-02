import numpy as np
import pandas as pd
import pytest

import services.historical.independent_incremental_range_forecast as study
from services.historical.independent_incremental_range_forecast_protocol import (
    FOLDS,
    GUARDRAILS,
    INCREMENTAL_GATE,
    MODELS,
    PROTOCOL_VERSION,
)


def _synthetic_sessions() -> pd.DataFrame:
    rows = []
    index = 0
    for year in (2022, 2023, 2024):
        for i in range(120):
            opening = 15.0 + (i % 30) * 2.0 + (year - 2022) * 0.3
            previous = 35.0 + ((i * 7) % 50) * 2.1 + (year - 2022) * 0.7
            # Fixed positive contribution from both predictors plus tiny deterministic noise.
            target = (
                8.0
                + 1.15 * opening
                + 0.42 * previous
                + ((i % 5) - 2) * 0.15
            )
            rows.append(
                {
                    "date": f"{year}-{1 + (i // 28):02d}-{1 + (i % 28):02d}",
                    "year": year,
                    "first_30m_high_low_range_bps": opening,
                    "previous_session_high_low_range_bps": previous,
                    "post_09_40_remaining_session_high_low_range_bps": target,
                }
            )
            index += 1
    return pd.DataFrame(rows)


def test_protocol_freezes_exact_incremental_question():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_INCREMENTAL_RANGE_FORECAST_V1"
    assert list(MODELS) == ["opening_only", "opening_plus_previous_day"]
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
    assert INCREMENTAL_GATE["minimum_complete_sessions"] == 600


def test_fixed_combined_model_can_show_incremental_information(monkeypatch):
    monkeypatch.setitem(study.BOOTSTRAP, "draws", 300)
    report = study._evaluate_sessions(_synthetic_sessions())

    assert len(report["fold_results"]) == 2
    assert all(row["combined_mae_lower"] for row in report["fold_results"])
    assert report["pooled_oos"]["opening_plus_previous_day_mae_bps"] < (
        report["pooled_oos"]["opening_only_mae_bps"]
    )
    assert report["pooled_oos"]["mae_improvement_bps"] > 0.0
    assert report["pooled_oos"]["relative_mae_improvement"] > 0.0
    assert report["pooled_oos"][
        "bootstrap_incremental_error_improvement_95pct"
    ][0] > 0.0
    assert report["incremental_gate"] == {"passed": True, "failures": []}


def test_fixed_ols_shapes_are_pinned():
    frame = _synthetic_sessions().loc[lambda x: x["year"] == 2022]
    beta_open = study._fit_log_ols(
        frame,
        list(MODELS["opening_only"]["features"]),
    )
    beta_combined = study._fit_log_ols(
        frame,
        list(MODELS["opening_plus_previous_day"]["features"]),
    )
    assert beta_open.shape == (2,)
    assert beta_combined.shape == (3,)
    assert np.isfinite(beta_open).all()
    assert np.isfinite(beta_combined).all()


def test_guardrails_keep_incremental_study_nontrading():
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
