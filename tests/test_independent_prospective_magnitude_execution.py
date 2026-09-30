from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.historical.independent_prospective_magnitude_protocol import (
    CONTRACT_BY_DATE,
    EXPECTED_ROWS,
    EXPECTED_SCORABLE_EVENTS,
    SESSION_DATES,
    TERMINAL_OUTCOMES,
)

IST = ZoneInfo("Asia/Kolkata")


def _synthetic_market_payload():
    rows = []
    for session_index, day in enumerate(SESSION_DATES):
        start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
        expiry = CONTRACT_BY_DATE[day]
        for i in range(77):
            base = 25000.0 + session_index * 20.0 + i * 0.5
            width = 1.0 + (i % 11) * 0.25
            rows.append(
                {
                    "timestamp": (start + timedelta(minutes=5 * i)).isoformat(),
                    "open": base,
                    "high": base + width,
                    "low": base - width,
                    "close": base + 0.1,
                    "volume": 1000.0 + i,
                    "open_interest": 100000.0 + session_index * 100 + i,
                    "instrument": f"NIFTY FUT {expiry}",
                    "futures_expiry": expiry,
                    "source": "BREEZE",
                }
            )
    return {
        "protocol_version": "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1",
        "provider": "BREEZE",
        "session_dates": list(SESSION_DATES),
        "contract_by_date": dict(CONTRACT_BY_DATE),
        "bars_per_session": 77,
        "rows": EXPECTED_ROWS,
        "quality": {
            "sessions": 30,
            "rows": EXPECTED_ROWS,
            "complete_77_bar_sessions": 30,
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "missing_volume_rows": 0,
            "missing_open_interest_rows": 0,
            "nonpositive_volume_rows": 0,
            "nonpositive_open_interest_rows": 0,
            "wrong_contract_rows": 0,
        },
        "futures_rows": rows,
    }


def test_collector_is_locked_until_entire_forward_window_completes():
    from services.historical.independent_prospective_magnitude_market import (
        assert_collection_window_open,
        collect,
    )

    before = datetime(2026, 11, 16, 15, 39, tzinfo=IST)
    boundary = datetime(2026, 11, 16, 15, 40, tzinfo=IST)

    with pytest.raises(RuntimeError, match="collection is locked"):
        assert_collection_window_open(before)
    with pytest.raises(RuntimeError, match="collection is locked"):
        collect(now=before)
    assert_collection_window_open(boundary)


def test_analyzer_is_locked_until_entire_forward_window_completes():
    from services.historical.independent_prospective_magnitude_findings import (
        assert_scoring_window_open,
    )

    before = datetime(2026, 11, 16, 15, 39, tzinfo=IST)
    boundary = datetime(2026, 11, 16, 15, 40, tzinfo=IST)

    with pytest.raises(RuntimeError, match="scoring is locked"):
        assert_scoring_window_open(before)
    assert_scoring_window_open(boundary)


def test_market_validation_enforces_exact_roll_and_shape():
    from services.historical.independent_prospective_magnitude_findings import (
        _validate,
    )

    payload = _synthetic_market_payload()
    frame = _validate(payload)
    assert len(frame) == EXPECTED_ROWS
    assert frame.loc[frame["date"] == "2026-10-27", "futures_expiry"].eq(
        "2026-10-27"
    ).all()
    assert frame.loc[frame["date"] == "2026-10-28", "futures_expiry"].eq(
        "2026-11-23"
    ).all()

    bad = _synthetic_market_payload()
    bad["futures_rows"][17 * 77]["futures_expiry"] = "2026-10-27"
    with pytest.raises(ValueError, match="wrong futures expiry"):
        _validate(bad)


def test_exact_30_session_sample_yields_1980_frozen_events():
    from services.historical.independent_prospective_magnitude_findings import (
        _build_events,
        _validate,
    )

    frame = _validate(_synthetic_market_payload())
    events = _build_events(frame)
    assert len(events) == EXPECTED_SCORABLE_EVENTS == 1980
    assert events.groupby("date").size().eq(66).all()
    assert events.groupby("block")["date"].nunique().to_dict() == {
        "block1": 10,
        "block2": 10,
        "block3": 10,
    }


def test_replication_gate_is_exactly_predeclared():
    from services.historical.independent_prospective_magnitude_findings import (
        _evaluate_gate,
    )

    assert _evaluate_gate(
        scorable_events=1980,
        pooled_spearman=0.01,
        block_spearman={"block1": 0.01, "block2": 0.02, "block3": 0.03},
        bootstrap_low=0.001,
    ) == []

    assert _evaluate_gate(
        scorable_events=1979,
        pooled_spearman=0.0,
        block_spearman={"block1": 0.01, "block2": -0.01, "block3": 0.03},
        bootstrap_low=0.0,
    ) == [
        "minimum_scorable_events",
        "pooled_spearman_not_positive",
        "block_spearman_not_positive_in_all_three_blocks",
        "bootstrap_lower_bound_not_positive",
    ]


def test_analyzer_produces_no_quartile_threshold_or_trading_promotion(monkeypatch):
    import services.historical.independent_prospective_magnitude_findings as findings

    monkeypatch.setattr(findings, "_spearman", lambda x, y: 0.2)
    monkeypatch.setattr(findings, "_bootstrap", lambda events: (0.1, 0.3))

    report = findings.analyze(
        _synthetic_market_payload(),
        source_sha256="abc123",
        now=datetime(2026, 11, 16, 15, 40, tzinfo=IST),
    )

    assert report["replication_gate"] == {"passed": True, "failures": []}
    assert report["results"] == {
        "pooled_spearman": 0.2,
        "block_spearman": {
            "block1": 0.2,
            "block2": 0.2,
            "block3": 0.2,
        },
        "session_cluster_bootstrap_95pct": [0.1, 0.3],
    }
    assert "quartile" not in str(report["results"]).lower()
    assert report["terminal_outcome"] == TERMINAL_OUTCOMES["if_pass"]
    assert report["guardrails"]["quartile_analysis_produced"] is False
    assert report["guardrails"]["candidate_frozen"] is False
    assert report["guardrails"]["blind_validation_allowed"] is False
    assert report["guardrails"]["implementation_allowed"] is False
