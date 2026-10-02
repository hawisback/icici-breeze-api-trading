"""Outcome-free preflight for frozen prospective NIFTY session collection.

This module performs no network request and reads no research outcome. It checks
only local execution readiness: frozen session identity, collection-time lock,
Breeze credential presence, breeze-connect availability, sealed artifact state,
and the QA-only collection ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from services.historical.independent_prospective_magnitude_collection_ledger import (
    build_collection_ledger,
)
from services.historical.independent_prospective_magnitude_market import (
    IST,
    _load_local_env,
    _secret,
    _session_completion,
    _validate_session_artifact,
)
from services.historical.independent_prospective_magnitude_protocol import (
    CONTRACT_BY_DATE,
    PROTOCOL_VERSION,
    SESSION_DATES,
)

RESEARCH_TYPE = "NIFTY_PROSPECTIVE_MAGNITUDE_COLLECTION_PREFLIGHT_V1"

GUARDRAILS = {
    "research_only": True,
    "network_request_performed": False,
    "qa_only": True,
    "outcome_scoring_performed": False,
    "predictor_computed": False,
    "target_computed": False,
    "correlation_computed": False,
    "pnl_scored": False,
    "model_fitting": False,
    "implementation_allowed": False,
    "strategy_d_remains_paused": True,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        return current.replace(tzinfo=IST)
    return current.astimezone(IST)


def preflight(
    day: str,
    *,
    session_dir: Path = Path("data/independent_prospective_magnitude_sessions"),
    now: datetime | None = None,
    load_env: bool = True,
) -> dict[str, Any]:
    if day not in SESSION_DATES:
        raise ValueError(f"{day} is not a frozen prospective session")
    if load_env:
        _load_local_env()

    current = _local_now(now)
    completion = _session_completion(day)
    credential_presence = {
        name: bool(_secret(name))
        for name in (
            "BREEZE_API_KEY",
            "BREEZE_SECRET_KEY",
            "BREEZE_SESSION_TOKEN",
        )
    }
    package_available = importlib.util.find_spec("breeze_connect") is not None

    artifact_path = session_dir / f"{day}.json"
    artifact_status = "absent"
    artifact_sha256: str | None = None
    artifact_error: str | None = None
    if artifact_path.exists():
        artifact_sha256 = _sha256(artifact_path)
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            _validate_session_artifact(payload, day)
            artifact_status = "valid_sealed"
        except Exception as exc:
            artifact_status = "invalid"
            artifact_error = str(exc)

    ledger = build_collection_ledger(session_dir)
    blockers: list[str] = []
    if not all(credential_presence.values()):
        blockers.append("missing_breeze_credentials")
    if not package_available:
        blockers.append("breeze_connect_not_installed")
    if artifact_status == "invalid":
        blockers.append("existing_session_artifact_invalid")
    if artifact_status == "valid_sealed":
        blockers.append("session_already_sealed")
    if current < completion:
        blockers.append("collection_window_not_open")

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "session_date": day,
        "futures_expiry": CONTRACT_BY_DATE[day],
        "checked_at_ist": current.isoformat(),
        "collection_opens_at_ist": completion.isoformat(),
        "collection_window_open": current >= completion,
        "credential_presence": credential_presence,
        "breeze_connect_installed": package_available,
        "session_artifact": {
            "path": str(artifact_path),
            "status": artifact_status,
            "sha256": artifact_sha256,
            "error": artifact_error,
        },
        "ledger_summary": {
            "valid_collected_sessions": ledger["valid_collected_sessions"],
            "invalid_collected_sessions": ledger["invalid_collected_sessions"],
            "missing_sessions": ledger["missing_sessions"],
            "next_missing_session_date": ledger["next_missing_session_date"],
        },
        "safe_to_attempt_collection": not blockers,
        "collection_already_complete_for_session": (
            artifact_status == "valid_sealed"
        ),
        "blockers": blockers,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run outcome-free preflight for prospective NIFTY collection"
    )
    parser.add_argument("--session", choices=SESSION_DATES, required=True)
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=Path("data/independent_prospective_magnitude_sessions"),
    )
    args = parser.parse_args()
    report = preflight(args.session, session_dir=args.session_dir)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
