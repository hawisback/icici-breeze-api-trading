from services.historical.strategy_c_candidate_manifest import (
    CANDIDATE_ID,
    build_manifest,
    matches_candidate,
)


def _row(
    *,
    delay: float = 2.0,
    risk: float = 0.90,
    separation: float = 1.80,
    realized_r: float = 2.0,
) -> dict:
    return {
        "date": "2026-01-05",
        "direction": "CALL",
        "setup_end": "2026-01-05T04:30:00+00:00",
        "entry_time": "2026-01-05T04:31:00+00:00",
        "entry_time_ist": "2026-01-05T10:01:00+05:30",
        "entry_price": 25000.0,
        "initial_stop": 24900.0,
        "exit_time": "2026-01-05T05:00:00+00:00",
        "exit_price": 25200.0,
        "exit_reason": "HARD_TARGET",
        "realized_r": realized_r,
        "mfe_r": 2.1,
        "mae_r": 0.25,
        "research_features": {
            "trigger_delay_minutes": delay,
            "risk_atr": risk,
            "context_15m_ema_separation_atr": separation,
        },
    }


def test_candidate_boundaries_are_inclusive():
    assert matches_candidate(_row(delay=2.0, risk=0.90, separation=1.80))
    assert not matches_candidate(_row(delay=2.01))
    assert not matches_candidate(_row(risk=0.899))
    assert not matches_candidate(_row(separation=1.801))


def test_manifest_separates_entry_fields_from_reference_outcomes():
    discovery = {
        "research_type": "STRATEGY_CD_NATIVE_15M_5M_1M_DISCOVERY",
        "usable_sessions": 10,
        "usable_dates": ["2026-01-05"],
        "strategy_a_v3_signal_dates": [],
        "families": {"di_continuation": {"trades": [_row()]}},
    }
    report = build_manifest(discovery)

    assert report["candidate_spec"]["candidate_id"] == CANDIDATE_ID
    assert report["metrics_reference_only"]["trades"] == 1
    assert report["signals"][0]["initial_risk_points"] == 100.0
    assert "realized_r" not in report["signals"][0]
    assert report["reference_outcomes_do_not_use_for_signal_generation"][0]["realized_r"] == 2.0
    assert report["production_thresholds_changed"] is False


def test_manifest_guard_report_parity():
    discovery = {
        "research_type": "STRATEGY_CD_NATIVE_15M_5M_1M_DISCOVERY",
        "usable_sessions": 10,
        "usable_dates": ["2026-01-05"],
        "strategy_a_v3_signal_dates": [],
        "families": {"di_continuation": {"trades": [_row()]}},
    }
    first = build_manifest(discovery)
    expected = first["metrics_reference_only"]
    guard = {
        "candidates": {
            "di_fast_risk090_sep180": {
                "all_sessions": expected,
            }
        }
    }
    report = build_manifest(discovery, guard_report=guard)
    assert report["guard_report_parity"]["match"] is True
    assert report["guard_report_parity"]["mismatches"] == {}


def test_manifest_detects_guard_report_metric_mismatch():
    discovery = {
        "research_type": "STRATEGY_CD_NATIVE_15M_5M_1M_DISCOVERY",
        "usable_sessions": 10,
        "usable_dates": ["2026-01-05"],
        "strategy_a_v3_signal_dates": [],
        "families": {"di_continuation": {"trades": [_row()]}},
    }
    first = build_manifest(discovery)
    expected = dict(first["metrics_reference_only"])
    expected["trades"] = 99
    guard = {
        "candidates": {
            "di_fast_risk090_sep180": {
                "all_sessions": expected,
            }
        }
    }
    report = build_manifest(discovery, guard_report=guard)
    assert report["guard_report_parity"]["match"] is False
    assert report["guard_report_parity"]["mismatches"]["trades"] == {
        "manifest": 1,
        "guard_report": 99,
    }
