from services.historical.strategy_a_v3_robustness_suite import (
    _bootstrap_mean_ci,
    _cadence,
    _concentration,
    _lifecycle_analysis,
)


def _record(
    date: str,
    direction: str,
    realized_r: float,
    timestamp: str,
) -> dict:
    return {
        "trading_date": date,
        "direction": direction,
        "realized_r": realized_r,
        "lifecycle_status": "RESOLVED",
        "ambiguous": False,
        "option_data_status": "UNAVAILABLE",
        "exit_reason": "SESSION_EXIT",
        "simulated_entry_timestamp": timestamp,
    }


def test_v3_robustness_cadence_excludes_skipped_sessions():
    state = {
        "sessions": [
            {"date": "2026-01-01", "setup_count": 0, "signal_count": 0},
            {"date": "2026-01-02", "setup_count": 1, "signal_count": 1},
            {
                "date": "2026-01-03",
                "setup_count": 0,
                "signal_count": 0,
                "skip_reason": "NO_ACTIVE_FUTURES_CONTRACT",
            },
            {"date": "2026-01-04", "setup_count": 1, "signal_count": 0},
            {"date": "2026-01-05", "setup_count": 1, "signal_count": 1},
        ]
    }

    result = _cadence(state)

    assert result["sessions_total"] == 5
    assert result["usable_sessions"] == 4
    assert result["skipped_sessions"] == 1
    assert result["setup_days"] == 3
    assert result["signal_days"] == 2
    assert result["usable_sessions_per_signal_day"] == 2.0
    assert result["median_usable_session_gap_between_signal_days"] == 2.0


def test_v3_robustness_bootstrap_is_deterministic():
    records = [
        _record("2026-01-01", "CALL", 1.0, "2026-01-01T05:00:00Z"),
        _record("2026-01-02", "PUT", -1.0, "2026-01-02T05:00:00Z"),
        _record("2026-01-03", "CALL", 2.0, "2026-01-03T05:00:00Z"),
    ]

    first = _bootstrap_mean_ci(records, iterations=1000, seed=7)
    second = _bootstrap_mean_ci(records, iterations=1000, seed=7)

    assert first == second
    assert first["sample_size"] == 3
    assert first["mean_r"] == 0.666667
    assert first["ci95_low_r"] <= first["mean_r"] <= first["ci95_high_r"]


def test_v3_robustness_concentration_reports_leave_best_out():
    records = [
        _record("2026-01-01", "CALL", 3.0, "2026-01-01T05:00:00Z"),
        _record("2026-01-02", "PUT", 2.0, "2026-01-02T05:00:00Z"),
        _record("2026-01-03", "CALL", -1.0, "2026-01-03T05:00:00Z"),
    ]

    result = _concentration(records)

    assert result["total_r"] == 4.0
    assert result["best_trade_r"] == 3.0
    assert result["worst_trade_r"] == -1.0
    assert result["total_without_best_trade_r"] == 1.0
    assert result["total_without_best_two_trades_r"] == -1.0


def test_v3_robustness_lifecycle_groups_direction_year_quarter_and_time():
    lifecycle = {
        "strategy_a": {"signals": 3},
        "signal_parity": {"all_match": True, "failure_dates": []},
        "records": [
            _record("2025-12-30", "CALL", 1.0, "2025-12-30T04:30:00Z"),
            _record("2026-01-05", "PUT", -1.0, "2026-01-05T05:30:00Z"),
            _record("2026-04-01", "CALL", 2.0, "2026-04-01T08:45:00Z"),
        ],
    }

    result = _lifecycle_analysis(lifecycle)

    assert set(result["by_direction"]) == {"CALL", "PUT"}
    assert set(result["by_year"]) == {"2025", "2026"}
    assert set(result["by_quarter"]) == {"2025-Q4", "2026-Q1", "2026-Q2"}
    assert "09:45-10:29" in result["by_entry_time_bucket_ist"]
    assert "10:30-11:29" in result["by_entry_time_bucket_ist"]
    assert "13:30-14:45" in result["by_entry_time_bucket_ist"]
