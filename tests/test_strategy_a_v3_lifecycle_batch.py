from types import SimpleNamespace

from services.historical.strategy_a_v3_lifecycle_batch import (
    _aggregate,
    _expected_signals,
    _signal_parity,
    _strategy_a_manifests,
)


def test_v3_lifecycle_batch_extracts_only_signal_sessions():
    report = {
        "sessions": [
            {"date": "2026-09-18", "signals": [{"timestamp": "A"}]},
            {"date": "2026-09-17", "signals": []},
            {"date": "2026-09-16"},
        ]
    }
    assert _expected_signals(report) == {
        "2026-09-18": [{"timestamp": "A"}],
    }


def test_v3_lifecycle_batch_filters_shared_engine_to_strategy_a_only():
    result = SimpleNamespace(
        replay_manifests=[
            {"strategy_id": "TREND_PULLBACK", "replay_signal_id": "A"},
            {"strategy_id": "VOLATILITY_BREAKOUT", "replay_signal_id": "B"},
        ]
    )
    assert _strategy_a_manifests(result) == [
        {"strategy_id": "TREND_PULLBACK", "replay_signal_id": "A"},
    ]


def test_v3_lifecycle_signal_parity_compares_time_direction_entry_stop_and_r():
    expected = [{
        "timestamp": "2026-09-18T06:30:00+00:00",
        "option_type": "CALL",
        "underlying_entry_price": 23336.085623701303,
        "structural_stop": 23309.42875259739,
        "r_points": 26.656871103914455,
    }]
    manifests = [{
        "simulated_entry_timestamp": "2026-09-18T06:30:00+00:00",
        "direction": "CALL",
        "simulated_entry_price": 23336.085623701303,
        "initial_structural_stop": 23309.42875259739,
        "initial_risk_points": 26.656871103914455,
    }]

    assert _signal_parity(expected, manifests)["match"] is True

    manifests[0]["direction"] = "PUT"
    parity = _signal_parity(expected, manifests)
    assert parity["match"] is False
    assert parity["expected_count"] == parity["actual_count"] == 1


def test_v3_lifecycle_aggregate_uses_resolved_r_and_tracks_unresolved_and_option_status():
    records = [
        {
            "lifecycle_status": "RESOLVED",
            "realized_r": 1.5,
            "ambiguous": False,
            "option_data_status": "UNAVAILABLE",
            "exit_reason": "T1_PARTIAL_EXIT",
            "direction": "CALL",
            "mfe_r": 1.8,
            "mae_r": 0.2,
        },
        {
            "lifecycle_status": "RESOLVED",
            "realized_r": -1.0,
            "ambiguous": False,
            "option_data_status": "AVAILABLE",
            "exit_reason": "UNDERLYING_STRUCTURAL_STOP",
            "direction": "PUT",
            "mfe_r": 0.2,
            "mae_r": 1.0,
        },
        {
            "lifecycle_status": "UNRESOLVED",
            "realized_r": None,
            "ambiguous": True,
            "option_data_status": "UNAVAILABLE",
            "exit_reason": None,
            "direction": "CALL",
        },
    ]

    summary = _aggregate(records)

    assert summary["signals"] == 3
    assert summary["resolved"] == 2
    assert summary["unresolved"] == 1
    assert summary["ambiguous"] == 1
    assert summary["winners"] == 1
    assert summary["losers"] == 1
    assert summary["sum_realized_r"] == 0.5
    assert summary["mean_realized_r"] == 0.25
    assert summary["max_drawdown_r"] == -1.0
    assert summary["direction_counts"] == {"CALL": 2, "PUT": 1}
