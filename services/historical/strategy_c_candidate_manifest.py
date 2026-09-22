"""Frozen research manifest for Strategy C DI-continuation candidate V1.

This module converts the raw multi-timeframe discovery output into an auditable
signal manifest using one fixed entry hypothesis:

- family == di_continuation
- completed native 1m trigger delay <= 2 minutes after the completed 5m setup
- structural entry risk >= 0.90 setup ATR
- completed 15m EMA20/EMA50 separation <= 1.80 ATR

The thresholds were selected after exploratory analysis of the same historical
sample.  They are therefore frozen for the next validation stage and must not
be re-tuned on that sample.  This module is research-only and does not alter
production Strategy A, Strategy B, option selection, or execution settings.

Signal-time fields and reference outcomes are deliberately separated so future
replay/option-validation code can consume entries without accidentally reading
post-entry information.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Sequence


CANDIDATE_ID = "STRATEGY_C_DI_CONTINUATION_V1_CANDIDATE"
FAMILY = "di_continuation"
MAX_TRIGGER_DELAY_MINUTES = 2.0
MIN_STRUCTURAL_RISK_ATR = 0.90
MAX_CONTEXT_15M_EMA_SEPARATION_ATR = 1.80


def candidate_spec() -> dict[str, Any]:
    return {
        "candidate_id": CANDIDATE_ID,
        "family": FAMILY,
        "entry_contract": {
            "trigger_delay_minutes_max": MAX_TRIGGER_DELAY_MINUTES,
            "structural_risk_atr_min": MIN_STRUCTURAL_RISK_ATR,
            "context_15m_ema_separation_atr_max": MAX_CONTEXT_15M_EMA_SEPARATION_ATR,
        },
        "timeframe_contract": {
            "context": "completed 15m only",
            "setup": "completed native 5m",
            "trigger": "completed native 1m beginning only after setup close",
        },
        "threshold_status": "FROZEN_FOR_NEXT_VALIDATION_STAGE",
    }


def _spec_fingerprint() -> str:
    payload = json.dumps(candidate_spec(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _feature(row: dict[str, Any], key: str) -> float | None:
    raw = (row.get("research_features") or {}).get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def matches_candidate(row: dict[str, Any]) -> bool:
    delay = _feature(row, "trigger_delay_minutes")
    risk = _feature(row, "risk_atr")
    separation = _feature(row, "context_15m_ema_separation_atr")
    return (
        delay is not None
        and risk is not None
        and separation is not None
        and delay <= MAX_TRIGGER_DELAY_MINUTES
        and risk >= MIN_STRUCTURAL_RISK_ATR
        and separation <= MAX_CONTEXT_15M_EMA_SEPARATION_ATR
    )


def _signal_id(row: dict[str, Any]) -> str:
    entry = datetime.fromisoformat(str(row["entry_time"]).replace("Z", "+00:00"))
    stamp = entry.isoformat().replace("+00:00", "Z")
    return f"{CANDIDATE_ID}:{stamp}:{row['direction']}"


def _signal_row(row: dict[str, Any]) -> dict[str, Any]:
    entry = float(row["entry_price"])
    stop = float(row["initial_stop"])
    risk_points = abs(entry - stop)
    risk_atr = _feature(row, "risk_atr")
    setup_atr = risk_points / risk_atr if risk_atr and risk_atr > 0 else None
    return {
        "signal_id": _signal_id(row),
        "candidate_id": CANDIDATE_ID,
        "family": FAMILY,
        "date": row["date"],
        "direction": row["direction"],
        "option_type": row["direction"],
        "setup_end": row["setup_end"],
        "entry_time": row["entry_time"],
        "entry_time_ist": row["entry_time_ist"],
        "underlying_entry_price": entry,
        "structural_stop": stop,
        "initial_risk_points": risk_points,
        "setup_atr": setup_atr,
        "entry_features": dict(row.get("research_features") or {}),
    }


def _outcome_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": _signal_id(row),
        "exit_time": row.get("exit_time"),
        "exit_price": row.get("exit_price"),
        "exit_reason": row.get("exit_reason"),
        "realized_r": row.get("realized_r"),
        "mfe_r": row.get("mfe_r"),
        "mae_r": row.get("mae_r"),
    }


def _metrics(rows: Sequence[dict[str, Any]], usable_sessions: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["entry_time"])
    values = [float(row["realized_r"]) for row in ordered]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]

    equity = peak = drawdown = 0.0
    losing_streak = current_streak = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
        if value < 0:
            current_streak += 1
            losing_streak = max(losing_streak, current_streak)
        else:
            current_streak = 0

    return {
        "trades": len(values),
        "trades_per_session": round(len(values) / usable_sessions, 6) if usable_sessions else 0.0,
        "trades_per_10_sessions": round(10 * len(values) / usable_sessions, 3) if usable_sessions else 0.0,
        "win_rate_pct": round(100 * len(wins) / len(values), 2) if values else 0.0,
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 6) if losses and sum(losses) != 0 else None,
        "max_drawdown_r": round(drawdown, 6),
        "max_losing_streak": losing_streak,
        "direction_counts": {
            "CALL": sum(row["direction"] == "CALL" for row in ordered),
            "PUT": sum(row["direction"] == "PUT" for row in ordered),
        },
    }


def _guard_parity(
    metrics: dict[str, Any],
    guard_report: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if guard_report is None:
        return None
    expected = (
        ((guard_report.get("candidates") or {}).get("di_fast_risk090_sep180") or {})
        .get("all_sessions")
    )
    if not expected:
        return {
            "match": False,
            "reason": "FOCAL_GUARD_METRICS_MISSING",
        }
    keys = (
        "trades",
        "trades_per_session",
        "mean_r",
        "total_r",
        "profit_factor",
        "max_drawdown_r",
        "max_losing_streak",
    )
    mismatches = {
        key: {"manifest": metrics.get(key), "guard_report": expected.get(key)}
        for key in keys
        if metrics.get(key) != expected.get(key)
    }
    return {
        "match": not mismatches,
        "candidate": "di_fast_risk090_sep180",
        "mismatches": mismatches,
    }


def build_manifest(
    discovery: dict[str, Any],
    *,
    guard_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    family_rows = list(
        (((discovery.get("families") or {}).get(FAMILY) or {}).get("trades") or [])
    )
    selected = [row for row in family_rows if matches_candidate(row)]
    usable_sessions = int(discovery.get("usable_sessions") or 0)
    metrics = _metrics(selected, usable_sessions)

    signal_ids = [_signal_id(row) for row in selected]
    if len(signal_ids) != len(set(signal_ids)):
        raise ValueError("Strategy C candidate signal IDs are not unique")

    return {
        "research_type": "STRATEGY_C_FROZEN_CANDIDATE_MANIFEST",
        "candidate_spec": candidate_spec(),
        "candidate_spec_fingerprint": _spec_fingerprint(),
        "source_research_type": discovery.get("research_type"),
        "source_usable_sessions": usable_sessions,
        "source_usable_dates": list(discovery.get("usable_dates") or []),
        "strategy_a_v3_signal_dates": list(discovery.get("strategy_a_v3_signal_dates") or []),
        "metrics_reference_only": metrics,
        "guard_report_parity": _guard_parity(metrics, guard_report),
        "signals": [_signal_row(row) for row in selected],
        "reference_outcomes_do_not_use_for_signal_generation": [
            _outcome_row(row) for row in selected
        ],
        "limitations": [
            "Candidate thresholds were selected after exploratory analysis of the same historical sample and are now frozen.",
            "Reference outcomes are stored separately and must never be used by signal generation or option selection.",
            "Underlying futures R is not executable historical option PnL.",
            "Option contract selection, fills, slippage, costs, and option lifecycle are not specified by this manifest.",
            "Pristine unseen validation requires future or otherwise untouched data.",
            "Strategy A V3 remains unchanged.",
        ],
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Strategy C DI-continuation candidate manifest")
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_cd_multitimeframe_discovery.json",
    )
    parser.add_argument(
        "--guard-report",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_cd_guard_validation.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_c_di_continuation_v1_candidate_manifest.json",
    )
    args = parser.parse_args()

    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    guard_report = (
        json.loads(args.guard_report.read_text(encoding="utf-8"))
        if args.guard_report.exists()
        else None
    )
    report = build_manifest(discovery, guard_report=guard_report)
    parity = report["guard_report_parity"]
    if parity is not None and not parity["match"]:
        raise SystemExit(f"guard-report parity failed: {parity}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "research_type": report["research_type"],
                "candidate_id": report["candidate_spec"]["candidate_id"],
                "candidate_spec_fingerprint": report["candidate_spec_fingerprint"],
                "metrics_reference_only": report["metrics_reference_only"],
                "guard_report_parity": report["guard_report_parity"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
