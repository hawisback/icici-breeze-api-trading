from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import services.historical.independent_range_forecast_checkpoints as study
from services.historical.independent_range_forecast_checkpoints_protocol import (
    CHECKPOINTS_MINUTES,
    GUARDRAILS,
    PROTOCOL_VERSION,
)


def _one_session_bars(day: str = "2025-01-02") -> pd.DataFrame:
    start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
    rows = []
    close = 20000.0
    for i in range(75):
        open_px = close
        close = open_px + 1.0
        rows.append(
            {
                "date": day,
                "timestamp": start + timedelta(minutes=5 * i),
                "open": open_px,
                "high": open_px + 3.0 + i * 0.1,
                "low": open_px - 2.0 - i * 0.05,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def test_protocol_freezes_exact_checkpoint_set():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_RANGE_FORECAST_CHECKPOINTS_V1"
    assert CHECKPOINTS_MINUTES == [15, 30, 60, 90, 120]
    assert GUARDRAILS["blind_validation"] is False
    assert GUARDRAILS["transfer_refit"] is False
    assert GUARDRAILS["feature_search"] is False
    assert GUARDRAILS["model_family_search"] is False
    assert GUARDRAILS["no_rescue_on_same_samples"] is True


def test_30_minute_checkpoint_uses_first_six_bars_only():
    bars = _one_session_bars()
    result = study._checkpoint_sessions(bars, 30)

    session_open = float(bars.iloc[0]["open"])
    observed = bars.iloc[:6]
    remaining = bars.iloc[6:]
    expected_predictor = (
        float(observed["high"].max()) - float(observed["low"].min())
    ) / session_open * 10000.0
    expected_target = (
        float(remaining["high"].max()) - float(remaining["low"].min())
    ) / session_open * 10000.0

    assert len(result) == 1
    assert result.iloc[0]["predictor_bps"] == pytest.approx(expected_predictor)
    assert result.iloc[0]["target_bps"] == pytest.approx(expected_target)


def _synthetic_transfer() -> pd.DataFrame:
    rows = []
    beta = np.array([2.0, 0.55], dtype=float)
    for year in (2025, 2026):
        for i in range(220):
            x = 8.0 + (i % 55) * 1.4
            y = float(np.expm1(beta[0] + beta[1] * np.log1p(x)))
            rows.append(
                {
                    "date": f"{year}-{1 + (i // 28):02d}-{1 + (i % 28):02d}",
                    "year": year,
                    "predictor_bps": x,
                    "target_bps": y,
                }
            )
    return pd.DataFrame(rows)


def test_checkpoint_gate_can_pass_for_fixed_useful_model(
    monkeypatch: pytest.MonkeyPatch,
):
    frame = _synthetic_transfer()
    beta = np.array([2.0, 0.55], dtype=float)
    baseline = float(frame["target_bps"].median())
    monkeypatch.setitem(study.BOOTSTRAP, "draws", 300)

    result = study._score_checkpoint(frame, beta, baseline)

    assert result["calendar_year_results"]["2025"]["model_mae_lower"] is True
    assert result["calendar_year_results"]["2026"]["model_mae_lower"] is True
    assert result["calendar_year_results"]["2025"]["model_spearman"] > 0.0
    assert result["calendar_year_results"]["2026"]["model_spearman"] > 0.0
    assert result["pooled_transfer"]["model_mae_bps"] < (
        result["pooled_transfer"]["baseline_mae_bps"]
    )
    assert result["pooled_transfer"]["bootstrap_error_improvement_95pct"][0] > 0.0
    assert result["checkpoint_gate"] == {"passed": True, "failures": []}


def test_nonfrozen_checkpoint_is_rejected():
    with pytest.raises(ValueError, match="not in frozen protocol"):
        study._checkpoint_sessions(_one_session_bars(), 45)
