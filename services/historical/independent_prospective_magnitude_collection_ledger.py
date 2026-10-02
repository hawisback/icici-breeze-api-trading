"""Outcome-free ledger for frozen prospective NIFTY session collection.

The ledger scans the sealed per-session QA artifacts and reports only collection
status, file hashes, exact-shape QA validity, and missing frozen dates. It does
not compute or expose predictor values, target values, correlations, prices,
P&L, thresholds, or any research finding.

This is operational provenance for the frozen prospective replication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from services.historical.independent_prospective_magnitude_market import (
    _validate_session_artifact,
)
from services.historical.independent_prospective_magnitude_protocol import (
    CONTRACT_BY_DATE,
    EXPECTED_BARS_PER_SESSION,
    PROTOCOL_VERSION,
    SESSION_DATES,
)

RESEARCH_TYPE = "NIFTY_PROSPECTIVE_MAGNITUDE_COLLECTION_LEDGER_V1"

GUARDRAILS = {
    "research_only": True,
    "qa_only": True,
    "outcome_scoring_performed": False,
    "predictor_computed": False,
    "target_computed": False,
    "correlation_computed": False,
    "pnl_scored": False,
    "threshold_selection": False,
    "model_fitting": False,
    "candidate_frozen": False,
    "blind_validation_opened": False,
    "implementation_allowed": False,
    "strategy_d_remains_paused": True,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_collection_ledger(session_dir: Path) -> dict[str, Any]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    missing: list[str] = []

    for day in SESSION_DATES:
        path = session_dir / f"{day}.json"
        if not path.exists():
            missing.append(day)
            continue

        digest = _sha256(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _validate_session_artifact(payload, day)
        except Exception as exc:
            invalid.append(
                {
                    "date": day,
                    "path": str(path),
                    "sha256": digest,
                    "error": str(exc),
                }
            )
            continue

        valid.append(
            {
                "date": day,
                "path": str(path),
                "sha256": digest,
                "provider": "BREEZE",
                "futures_expiry": CONTRACT_BY_DATE[day],
                "rows": EXPECTED_BARS_PER_SESSION,
                "qa_only": True,
                "outcome_scoring_performed": False,
            }
        )

    extra_json_files = sorted(
        str(path.name)
        for path in session_dir.glob("*.json")
        if path.stem not in SESSION_DATES
    ) if session_dir.exists() else []

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "session_directory": str(session_dir),
        "expected_sessions": len(SESSION_DATES),
        "valid_collected_sessions": len(valid),
        "invalid_collected_sessions": len(invalid),
        "missing_sessions": len(missing),
        "collection_complete": (
            len(valid) == len(SESSION_DATES)
            and not invalid
            and not missing
        ),
        "valid_artifacts": valid,
        "invalid_artifacts": invalid,
        "missing_session_dates": missing,
        "extra_json_files_ignored": extra_json_files,
        "next_missing_session_date": missing[0] if missing else None,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build outcome-free prospective NIFTY collection ledger"
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=Path("data/independent_prospective_magnitude_sessions"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/independent_prospective_magnitude_collection_ledger.json"
        ),
    )
    args = parser.parse_args()
    report = build_collection_ledger(args.session_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "expected_sessions": report["expected_sessions"],
                "valid_collected_sessions": report[
                    "valid_collected_sessions"
                ],
                "invalid_collected_sessions": report[
                    "invalid_collected_sessions"
                ],
                "missing_sessions": report["missing_sessions"],
                "collection_complete": report["collection_complete"],
                "next_missing_session_date": report[
                    "next_missing_session_date"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
