from datetime import datetime, timedelta

import pandas as pd
import pytest

import services.historical.independent_ultra_early_range_forecast as study
from services.historical.independent_ultra_early_range_forecast_protocol import (
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
                "high": open_px + 2.0 + i * 0.1,
                "low": open_px - 1.0 - i * 0.05,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def test_protocol_freezes_only_5m_and_10m():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_ULTRA_EARLY_RANGE_FORECAST_V1"
    assert CHECKPOINTS_MINUTES == [5, 10]
    assert GUARDRAILS["blind_validation"] is False
    assert GUARDRAILS["transfer_refit"] is False
    assert GUARDRAILS["feature_search"] is False
    assert GUARDRAILS["model_family_search"] is False
    assert GUARDRAILS["no_rescue_on_same_samples"] is True


def test_5m_checkpoint_uses_exactly_first_bar():
    bars = _one_session_bars()
    result = study._early_checkpoint_sessions(bars, 5)
    session_open = float(bars.iloc[0]["open"])
    observed = bars.iloc[:1]
    remaining = bars.iloc[1:]
    expected_x = (
        float(observed["high"].max()) - float(observed["low"].min())
    ) / session_open * 10000.0
    expected_y = (
        float(remaining["high"].max()) - float(remaining["low"].min())
    ) / session_open * 10000.0
    assert result.iloc[0]["predictor_bps"] == pytest.approx(expected_x)
    assert result.iloc[0]["target_bps"] == pytest.approx(expected_y)


def test_10m_checkpoint_uses_exactly_first_two_bars():
    bars = _one_session_bars()
    result = study._early_checkpoint_sessions(bars, 10)
    session_open = float(bars.iloc[0]["open"])
    observed = bars.iloc[:2]
    remaining = bars.iloc[2:]
    expected_x = (
        float(observed["high"].max()) - float(observed["low"].min())
    ) / session_open * 10000.0
    expected_y = (
        float(remaining["high"].max()) - float(remaining["low"].min())
    ) / session_open * 10000.0
    assert result.iloc[0]["predictor_bps"] == pytest.approx(expected_x)
    assert result.iloc[0]["target_bps"] == pytest.approx(expected_y)


def test_nonfrozen_checkpoint_rejected():
    with pytest.raises(ValueError, match="not in frozen ultra-early protocol"):
        study._early_checkpoint_sessions(_one_session_bars(), 15)
