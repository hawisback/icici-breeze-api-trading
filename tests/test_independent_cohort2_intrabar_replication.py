import pandas as pd
import pytest

from services.historical.independent_cohort2_intrabar_replication import (
    INTRABAR_PROTOCOL_VERSION,
    PRIMARY_CHECKS,
    _attach_intrabar_returns,
    _directional_check,
    _validate_gate,
)
from services.historical.independent_cohort2_protocol import PROTOCOL_VERSION


def test_intrabar_primary_checks_are_frozen_before_collection():
    assert INTRABAR_PROTOCOL_VERSION == "DEVELOPMENT_COHORT_2_INTRABAR_V1"
    assert PRIMARY_CHECKS == (
        "boundary_from_signal_close_to_next_minute_open",
        "minute_1_open_to_close",
        "cumulative_minute_5",
    )


def test_intrabar_gate_requires_both_frozen_fast_lead_replications():
    payload = {
        "research_type": "NIFTY_DEVELOPMENT_COHORT_2_REPLICATION_V1",
        "protocol_version": PROTOCOL_VERSION,
        "replication_checks": [
            {
                "metric": "options_fast_lead.raw",
                "directional_replication": True,
            },
            {
                "metric": "options_fast_lead.spot_attributed_crossfit",
                "directional_replication": True,
            },
        ],
    }
    _validate_gate(payload)
    payload["replication_checks"][1]["directional_replication"] = False
    with pytest.raises(ValueError):
        _validate_gate(payload)


def test_intrabar_directional_replication_requires_five_of_six_blocks():
    cohort2 = {
        "spearman": 0.09,
        "positive_correlation_blocks": 5,
        "negative_correlation_blocks": 1,
        "total_blocks": 6,
    }
    result = _directional_check(
        metric="minute_1_open_to_close",
        cohort1_spearman=0.17,
        cohort2=cohort2,
    )
    assert result["same_pooled_sign"] is True
    assert result["cohort2_same_sign_blocks"] == 5
    assert result["directional_replication"] is True

    cohort2["positive_correlation_blocks"] = 4
    cohort2["negative_correlation_blocks"] = 2
    result = _directional_check(
        metric="minute_1_open_to_close",
        cohort1_spearman=0.17,
        cohort2=cohort2,
    )
    assert result["directional_replication"] is False


def test_attach_intrabar_returns_uses_first_executable_minute_after_signal_bar():
    timestamp = pd.Timestamp("2025-09-09T09:15:00+05:30")
    signals = pd.DataFrame(
        [{
            "timestamp": timestamp,
            "date": "2025-09-09",
            "block": 1,
            "futures_close": 100.0,
            "signal": 1.0,
        }]
    )
    rows = []
    for offset in range(5, 10):
        rows.append({
            "timestamp": (timestamp + pd.Timedelta(minutes=offset)).isoformat(),
            "open": 100.0 + offset,
            "close": 100.5 + offset,
        })
    result = _attach_intrabar_returns(signals, {"rows": rows})
    row = result.iloc[0]
    assert row["minute_1_open"] == 105.0
    assert row["minute_1_close"] == 105.5
    assert row["minute_5_open"] == 109.0
    assert row["minute_5_close"] == 109.5
    assert row["boundary_bps"] == pytest.approx(500.0)
    assert row["minute_2_open_to_minute_5_close_bps"] == pytest.approx(
        (109.5 / 106.0 - 1.0) * 10000.0
    )
