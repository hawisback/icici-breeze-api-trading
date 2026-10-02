from services.historical.strategy_f5_dominant_nifty_regime import (
    _classify_day,
    _status,
    _vote,
)
from services.historical.strategy_f5_dominant_nifty_regime_protocol import (
    EXPIRIES,
    GUARDRAILS,
    PROTOCOL_VERSION,
    TARGET_DATES,
)


def _rows(closes, *, opening=100.0, ending_open=95.0):
    rows = []
    hhmm = [
        "09:15", "09:20", "09:25", "09:30", "09:35", "09:40",
        "09:45", "09:50", "09:55", "10:00",
    ]
    for i, (t, close) in enumerate(zip(hhmm, closes)):
        rows.append({
            "timestamp": f"2026-10-01T{t}:00+05:30",
            "date": "2026-10-01",
            "open": opening if i == 0 else closes[i - 1],
            "high": max(opening if i == 0 else closes[i - 1], close) + 1,
            "low": min(opening if i == 0 else closes[i - 1], close) - 1,
            "close": close,
        })
    rows.append({
        "timestamp": "2026-10-01T15:20:00+05:30",
        "date": "2026-10-01",
        "open": ending_open,
        "high": ending_open + 1,
        "low": ending_open - 1,
        "close": ending_open,
    })
    return rows


def test_protocol_targets_last_week_and_october_1():
    assert PROTOCOL_VERSION == "STRATEGY_F5_DOMINANT_NIFTY_REGIME_V1"
    assert TARGET_DATES == [
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
        "2026-10-01",
    ]
    assert EXPIRIES == [
        "2026-09-22",
        "2026-09-29",
        "2026-10-06",
    ]
    assert GUARDRAILS["whole_day_regime_uses_future_information"] is True
    assert GUARDRAILS["do_not_use_as_same_day_entry_filter"] is True
    assert GUARDRAILS["live_execution"] is False


def test_vote_and_side_status():
    assert _vote(1.0) == "BULLISH"
    assert _vote(-1.0) == "BEARISH"
    assert _vote(0.0) == "FLAT"
    assert _status("CE", "BULLISH") == "REGIME_ALIGNED"
    assert _status("PE", "BULLISH") == "COUNTER_REGIME"
    assert _status("PE", "BEARISH") == "REGIME_ALIGNED"
    assert _status("CE", "BEARISH") == "COUNTER_REGIME"
    assert _status("CE", "MIXED") == "MIXED_REGIME"


def test_classify_day_requires_all_three_bearish_votes():
    closes = [99.5, 99.0, 98.5, 98.0, 97.5, 97.0, 96.5, 96.0, 95.5, 95.0]
    result = _classify_day("2026-10-01", _rows(closes, ending_open=94.5))
    assert result["available"] is True
    assert result["regime"] == "BEARISH"
    assert set(result["votes"].values()) == {"BEARISH"}


def test_conflicting_votes_are_mixed_not_forced():
    closes = [99.0, 98.5, 99.2, 99.5, 100.2, 100.5, 100.8, 101.0, 101.2, 101.4]
    result = _classify_day("2026-10-01", _rows(closes, ending_open=99.0))
    assert result["available"] is True
    assert result["regime"] == "MIXED"
