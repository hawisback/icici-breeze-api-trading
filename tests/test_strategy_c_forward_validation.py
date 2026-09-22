from datetime import date

import pytest

from services.historical.strategy_c_candidate_manifest import _spec_fingerprint
from services.historical.strategy_c_forward_validation import (
    CANDIDATE_ID,
    build_forward_scorecard,
)


def _row(day: str, realized_r: float = 2.0) -> dict:
    return {
        "date": day,
        "direction": "CALL",
        "entry_time": f"{day}T04:31:00+00:00",
        "entry_time_ist": f"{day}T10:01:00+05:30",
        "entry_price": 25000.0,
        "initial_stop": 24900.0,
        "exit_time": f"{day}T05:00:00+00:00",
        "exit_reason": "HARD_TARGET",
        "realized_r": realized_r,
        "research_features": {
            "trigger_delay_minutes": 2.0,
            "risk_atr": 0.90,
            "context_15m_ema_separation_atr": 1.80,
        },
    }


def _manifest() -> dict:
    return {
        "candidate_spec": {"candidate_id": CANDIDATE_ID},
        "candidate_spec_fingerprint": _spec_fingerprint(),
    }


def test_forward_scorecard_excludes_freeze_date_and_prior_history():
    discovery = {
        "usable_dates": ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"],
        "families": {
            "di_continuation": {
                "trades": [
                    _row("2026-09-21"),
                    _row("2026-09-22"),
                    _row("2026-09-23"),
                    _row("2026-09-24", -1.0),
                ]
            }
        },
    }
    report = build_forward_scorecard(
        discovery,
        _manifest(),
        freeze_date=date(2026, 9, 22),
        min_forward_sessions=2,
        min_forward_trades=2,
    )
    assert report["forward_window"]["usable_sessions"] == 2
    assert report["metrics"]["trades"] == 2
    assert report["metrics"]["total_r"] == 1.0
    assert report["status"] == "READY_FOR_FORWARD_REVIEW"


def test_forward_scorecard_stays_collecting_below_evidence_floor():
    discovery = {
        "usable_dates": ["2026-09-23"],
        "families": {"di_continuation": {"trades": [_row("2026-09-23")]}}
    }
    report = build_forward_scorecard(
        discovery,
        _manifest(),
        min_forward_sessions=2,
        min_forward_trades=2,
    )
    assert report["status"] == "COLLECTING_FORWARD_EVIDENCE"
    assert report["evidence_floor"] == {
        "sessions_met": False,
        "trades_met": False,
        "note": (
            "These are minimum evidence-count gates only, not profitability "
            "targets and not production approval criteria."
        ),
    }


def test_forward_scorecard_rejects_candidate_fingerprint_drift():
    discovery = {
        "usable_dates": [],
        "families": {"di_continuation": {"trades": []}},
    }
    manifest = _manifest()
    manifest["candidate_spec_fingerprint"] = "not-the-frozen-spec"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        build_forward_scorecard(discovery, manifest)
