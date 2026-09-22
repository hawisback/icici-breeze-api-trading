from services.historical.strategy_a_v3_v31_comparison import (
    _records_for_dates,
    _signal_dates,
    _variant_summary,
)


def test_v31_comparison_extracts_signal_dates_and_incremental_records():
    state = {"aggregate": {"signal_dates": ["2026-01-01", "2026-01-03"]}}
    lifecycle = {
        "records": [
            {"trading_date": "2026-01-01"},
            {"trading_date": "2026-01-02"},
            {"trading_date": "2026-01-03"},
        ]
    }

    assert _signal_dates(state) == {"2026-01-01", "2026-01-03"}
    assert _records_for_dates(lifecycle, {"2026-01-03"}) == [
        {"trading_date": "2026-01-03"}
    ]


def test_v31_variant_summary_uses_usable_sessions_and_lifecycle_metrics():
    state = {
        "sessions": [
            {"date": "2026-01-01", "signal_count": 1},
            {"date": "2026-01-02", "signal_count": 0},
            {
                "date": "2026-01-03",
                "signal_count": 0,
                "skip_reason": "NO_ACTIVE_FUTURES_CONTRACT",
            },
        ],
        "aggregate": {
            "setup_count": 2,
            "signal_count": 1,
            "signal_dates": ["2026-01-01"],
        },
    }
    lifecycle = {
        "strategy_a": {"signals": 1, "sum_realized_r": 1.25},
        "signal_parity": {"all_match": True, "failure_dates": []},
    }

    summary = _variant_summary(state, lifecycle)

    assert summary["usable_sessions"] == 2
    assert summary["setup_count"] == 2
    assert summary["signal_count"] == 1
    assert summary["signal_days"] == 1
    assert summary["usable_sessions_per_signal_day"] == 2.0
    assert summary["lifecycle"]["sum_realized_r"] == 1.25
    assert summary["signal_parity"]["all_match"] is True
