from types import SimpleNamespace

from services.historical.strategy_a_swing_discovery import (
    _path_label,
    _score_row,
    _summarize,
)


def _bar(open_: float, high: float, low: float, close: float):
    return SimpleNamespace(open=open_, high=high, low=low, close=close)


def test_swing_path_label_uses_conservative_same_bar_ordering():
    current = SimpleNamespace(
        close=100.0,
        atr14=10.0,
    )
    # Same bar touches +0.6 ATR target and -0.4 ATR stop.
    future = [_bar(100.0, 106.5, 95.5, 101.0)]

    label = _path_label(
        current=current,
        future=future,
        direction="CALL",
        horizon_bars=2,
    )

    assert label["target_060_before_stop_040"] is False
    assert label["mfe_atr"] == 0.65
    assert label["mae_atr"] == 0.45


def test_swing_score_normalizes_to_stop_r_and_marks_target_success():
    row = {
        "labels": {
            "30m": {
                "mfe_atr": 0.7,
                "close_return_atr": 0.2,
                "target_060_before_stop_040": True,
            }
        }
    }

    assert _score_row(
        row,
        horizon="30m",
        target_key="target_060_before_stop_040",
    ) == 1.5


def test_swing_score_uses_horizon_close_when_no_target_success():
    row = {
        "labels": {
            "30m": {
                "mfe_atr": 0.3,
                "close_return_atr": 0.2,
                "target_060_before_stop_040": False,
            }
        }
    }

    assert _score_row(
        row,
        horizon="30m",
        target_key="target_060_before_stop_040",
    ) == 0.5


def test_swing_summary_excludes_incomplete_horizons():
    complete = {
        "date": "2026-01-01",
        "timestamp": "2026-01-01T04:30:00+00:00",
        "direction": "CALL",
        "labels": {
            "30m": {
                "mfe_atr": 0.7,
                "mae_atr": 0.2,
                "close_return_atr": 0.3,
                "target_060_before_stop_040": True,
            }
        },
    }
    incomplete = {
        "date": "2026-01-02",
        "timestamp": "2026-01-02T09:15:00+00:00",
        "direction": "PUT",
        "labels": {
            "30m": {
                "mfe_atr": None,
                "mae_atr": None,
                "close_return_atr": None,
                "target_060_before_stop_040": False,
            }
        },
    }

    result = _summarize(
        [complete, incomplete],
        usable_sessions=2,
        horizon="30m",
        target_key="target_060_before_stop_040",
    )

    assert result["trades"] == 1
    assert result["trades_per_10_sessions"] == 5.0
    assert result["mean_r_proxy"] == 1.5
