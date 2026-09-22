"""Post-freeze forward scorecard for Strategy C candidate V1.

The Strategy C candidate was frozen on 2026-09-22 after exploratory historical
research.  This module evaluates only sessions strictly after that freeze date
using the unchanged candidate specification and fingerprint.

It deliberately does not optimize thresholds and does not declare a production
pass/fail.  Before a minimum evidence floor is reached, status remains
COLLECTING_FORWARD_EVIDENCE.  Once the floor is reached, the report becomes
READY_FOR_FORWARD_REVIEW so performance can be assessed without moving the
goalposts mid-sample.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from services.historical.strategy_c_candidate_manifest import (
    CANDIDATE_ID,
    _spec_fingerprint,
    matches_candidate,
)


FREEZE_DATE = date(2026, 9, 22)
DEFAULT_MIN_FORWARD_SESSIONS = 80
DEFAULT_MIN_FORWARD_TRADES = 30


def _metrics(rows: Sequence[dict[str, Any]], sessions: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["entry_time"])
    values = [float(row["realized_r"]) for row in ordered]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]

    equity = peak = 0.0
    max_drawdown = 0.0
    current_streak = max_losing_streak = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value < 0:
            current_streak += 1
            max_losing_streak = max(max_losing_streak, current_streak)
        else:
            current_streak = 0

    return {
        "trades": len(values),
        "sessions": sessions,
        "trades_per_session": round(len(values) / sessions, 6) if sessions else 0.0,
        "trades_per_10_sessions": round(10 * len(values) / sessions, 3) if sessions else 0.0,
        "win_rate_pct": round(100 * len(wins) / len(values), 2) if values else 0.0,
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 6) if losses and sum(losses) != 0 else None,
        "max_drawdown_r": round(max_drawdown, 6),
        "max_losing_streak": max_losing_streak,
        "direction_counts": dict(sorted(Counter(str(row["direction"]) for row in ordered).items())),
    }


def _month_key(row: dict[str, Any]) -> str:
    return str(row["date"])[:7]


def build_forward_scorecard(
    discovery: dict[str, Any],
    manifest: dict[str, Any],
    *,
    freeze_date: date = FREEZE_DATE,
    min_forward_sessions: int = DEFAULT_MIN_FORWARD_SESSIONS,
    min_forward_trades: int = DEFAULT_MIN_FORWARD_TRADES,
) -> dict[str, Any]:
    spec = manifest.get("candidate_spec") or {}
    if spec.get("candidate_id") != CANDIDATE_ID:
        raise ValueError(f"Unexpected candidate_id: {spec.get('candidate_id')!r}")

    expected_fingerprint = _spec_fingerprint()
    actual_fingerprint = manifest.get("candidate_spec_fingerprint")
    if actual_fingerprint != expected_fingerprint:
        raise ValueError(
            "Frozen Strategy C candidate fingerprint mismatch: "
            f"{actual_fingerprint!r} != {expected_fingerprint!r}"
        )

    all_usable_dates = sorted(set(discovery.get("usable_dates") or []))
    forward_dates = [
        value
        for value in all_usable_dates
        if date.fromisoformat(str(value)) > freeze_date
    ]
    forward_date_set = set(forward_dates)

    family_rows = list(
        (((discovery.get("families") or {}).get("di_continuation") or {}).get("trades") or [])
    )
    forward_rows = [
        row
        for row in family_rows
        if str(row.get("date")) in forward_date_set and matches_candidate(row)
    ]

    metrics = _metrics(forward_rows, len(forward_dates))
    enough_sessions = len(forward_dates) >= min_forward_sessions
    enough_trades = len(forward_rows) >= min_forward_trades
    status = (
        "READY_FOR_FORWARD_REVIEW"
        if enough_sessions and enough_trades
        else "COLLECTING_FORWARD_EVIDENCE"
    )

    monthly: dict[str, Any] = {}
    for month in sorted({_month_key(row) for row in forward_rows}):
        rows = [row for row in forward_rows if _month_key(row) == month]
        month_sessions = sum(value.startswith(month) for value in forward_dates)
        monthly[month] = _metrics(rows, month_sessions)

    return {
        "research_type": "STRATEGY_C_POST_FREEZE_FORWARD_SCORECARD",
        "candidate_id": CANDIDATE_ID,
        "candidate_spec_fingerprint": actual_fingerprint,
        "freeze_date": freeze_date.isoformat(),
        "forward_window": {
            "first_usable_date": forward_dates[0] if forward_dates else None,
            "last_usable_date": forward_dates[-1] if forward_dates else None,
            "usable_sessions": len(forward_dates),
            "minimum_sessions_before_review": min_forward_sessions,
            "minimum_trades_before_review": min_forward_trades,
        },
        "status": status,
        "evidence_floor": {
            "sessions_met": enough_sessions,
            "trades_met": enough_trades,
            "note": (
                "These are minimum evidence-count gates only, not profitability "
                "targets and not production approval criteria."
            ),
        },
        "metrics": metrics,
        "monthly": monthly,
        "signals": [
            {
                "date": row["date"],
                "direction": row["direction"],
                "entry_time": row["entry_time"],
                "entry_time_ist": row.get("entry_time_ist"),
                "entry_price": row["entry_price"],
                "initial_stop": row["initial_stop"],
                "realized_r": row["realized_r"],
                "exit_time": row.get("exit_time"),
                "exit_reason": row.get("exit_reason"),
            }
            for row in sorted(forward_rows, key=lambda row: row["entry_time"])
        ],
        "limitations": [
            "Only sessions strictly after the candidate freeze date are included.",
            "The candidate specification and fingerprint are fixed; this report must not retune thresholds.",
            "Underlying futures R is not executable option PnL.",
            "Historical option coverage was insufficient for credible option replay, so option economics require forward observation.",
            "READY_FOR_FORWARD_REVIEW means the evidence-count floor is met; it is not production approval.",
            "Strategy A V3 remains unchanged.",
        ],
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build post-freeze Strategy C forward scorecard")
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_cd_multitimeframe_discovery.json",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_c_di_continuation_v1_candidate_manifest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_c_post_freeze_forward_scorecard.json",
    )
    parser.add_argument("--min-forward-sessions", type=int, default=DEFAULT_MIN_FORWARD_SESSIONS)
    parser.add_argument("--min-forward-trades", type=int, default=DEFAULT_MIN_FORWARD_TRADES)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = build_forward_scorecard(
        discovery,
        manifest,
        min_forward_sessions=max(1, args.min_forward_sessions),
        min_forward_trades=max(1, args.min_forward_trades),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "research_type": report["research_type"],
                "candidate_id": report["candidate_id"],
                "candidate_spec_fingerprint": report["candidate_spec_fingerprint"],
                "freeze_date": report["freeze_date"],
                "status": report["status"],
                "forward_window": report["forward_window"],
                "metrics": report["metrics"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
