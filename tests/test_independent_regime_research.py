from datetime import datetime, timedelta, timezone

from services.historical.independent_regime_research import (
    analyze_dataset,
    build_feature_sessions,
    extract_events,
    metrics,
)


IST = timezone(timedelta(hours=5, minutes=30))


def _session_rows():
    start = datetime(2026, 8, 12, 9, 15, tzinfo=IST)
    rows = []
    for index in range(75):
        # Monotone decline makes MOM3 and OI3 negative before ATR eligibility.
        close = 100.0 - index
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=5 * index)).isoformat(),
                "futures_open": close + 0.2,
                "futures_high": close + 0.3,
                "futures_low": close - 0.3,
                "futures_close": close,
                "futures_volume": 1000.0 + index,
                "futures_open_interest": 100000.0 - 100.0 * index,
                "vix_close": 12.0,
            }
        )
    return rows


def test_h1_resets_condition_state_at_first_executable_bar():
    sessions = build_feature_sessions(_session_rows())

    events, ambiguous = extract_events(sessions, "H1")

    assert ambiguous == 0
    assert len(events) == 1
    # ATR min_periods=8 makes index 7 / 09:50 the first executable bar.
    assert events[0]["timestamp"].startswith("2026-08-12T09:50:00")
    assert events[0]["or_state"] == "BELOW"


def test_metrics_reports_positive_short_continuation():
    sessions = build_feature_sessions(_session_rows())
    events, _ = extract_events(sessions, "H1")

    result = metrics(events)

    assert result["episodes"] == 1
    assert result["active_days"] == 1
    assert result["total_r"] > 0
    assert result["mean_directional_30m_atr"] > 0
