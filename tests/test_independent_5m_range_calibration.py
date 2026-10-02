import numpy as np
import pandas as pd
import pytest

import services.historical.independent_5m_range_calibration as calibration
from services.historical.independent_5m_range_calibration_protocol import (
    BINS,
    CHECKPOINT_MINUTES,
    GUARDRAILS,
    PROTOCOL_VERSION,
)


def _training_frame():
    return pd.DataFrame(
        {
            "date": [f"2022-01-{i:02d}" for i in range(1, 17)],
            "year": [2022] * 16,
            "predictor_bps": list(range(1, 17)),
            "target_bps": [10.0 + 2.0 * i for i in range(1, 17)],
        }
    )


def test_protocol_freezes_5m_and_training_quartile_rebinning():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_5M_RANGE_CALIBRATION_V1"
    assert CHECKPOINT_MINUTES == 5
    assert BINS == ["Q1", "Q2", "Q3", "Q4"]
    assert GUARDRAILS["blind_validation"] is False
    assert GUARDRAILS["transfer_refit"] is False
    assert GUARDRAILS["transfer_rebinning"] is False
    assert GUARDRAILS["feature_search"] is False
    assert GUARDRAILS["no_rescue_on_same_samples"] is True


def test_training_cutpoints_are_strict_quartiles():
    frame = _training_frame()
    cuts = calibration._training_quartile_cutpoints(frame)
    expected = list(
        np.quantile(frame["predictor_bps"].to_numpy(dtype=float), [0.25, 0.5, 0.75])
    )
    assert cuts == pytest.approx(expected)
    assert cuts[0] < cuts[1] < cuts[2]


def test_transfer_values_outside_training_range_still_assign_to_edge_quartiles():
    frame = pd.DataFrame(
        {
            "predictor_bps": [-5.0, 2.0, 6.0, 10.0, 100.0],
            "target_bps": [1, 2, 3, 4, 5],
        }
    )
    assigned = calibration._assign_frozen_quartiles(frame, [3.0, 7.0, 11.0])
    assert list(assigned["quartile"].astype(str)) == [
        "Q1", "Q1", "Q2", "Q3", "Q4"
    ]


def test_profile_reports_skill_and_signed_error():
    frame = pd.DataFrame(
        {
            "quartile": pd.Categorical(
                ["Q1", "Q1", "Q2", "Q2", "Q3", "Q3", "Q4", "Q4"],
                categories=BINS,
                ordered=True,
            ),
            "predictor_bps": [1, 2, 3, 4, 5, 6, 7, 8],
            "target_bps": [10, 12, 20, 22, 30, 32, 40, 42],
        }
    )
    forecast = np.array([11, 11, 21, 21, 31, 31, 41, 41], dtype=float)
    result = calibration._profile(
        frame,
        model_forecast=forecast,
        baseline_value=25.0,
    )
    assert all(result[label]["sessions"] == 2 for label in BINS)
    assert all(np.isfinite(result[label]["skill"]) for label in BINS)
    assert result["Q1"]["target_mean_bps"] < result["Q4"]["target_mean_bps"]


def test_strictly_increasing_helper():
    assert calibration._strictly_increasing([1.0, 2.0, 3.0, 4.0]) is True
    assert calibration._strictly_increasing([1.0, 2.0, 2.0, 4.0]) is False
