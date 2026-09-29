"""Freeze exact raw source hashes for Development Cohort 3.

This step runs only after the frozen collection audit has passed. It validates
the five collected artifacts again, verifies the passed audit shape, computes
SHA256 for every source file, and writes a manifest. It does not construct
features, outcomes, strategy signals, thresholds, or P&L.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from services.historical.independent_cohort3_protocol import (
    COHORT_ROLE,
    EXPECTED,
    PROTOCOL_VERSION,
    validate_auxiliary,
    validate_intrabar,
    validate_market,
    validate_options,
)

RESEARCH_TYPE = "NIFTY_DEVELOPMENT_COHORT_3_SOURCE_MANIFEST_V1"
EXPECTED_AUDIT_SHA256 = "ee6e0bf50fb6e839e0f7b0fed98447bf03b73323e351d2e0dd7f43a6d62291ac"
EXPECTED_MARKET_SHA256 = "f320af82ed96cf80b740afb3fe150b56d9a97213b180e5472169ba82a9353049"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_manifest(
    *,
    audit_path: Path,
    market_path: Path,
    vix_path: Path,
    options_path: Path,
    spot_path: Path,
    intrabar_path: Path,
) -> dict[str, Any]:
    audit_sha256 = _sha256(audit_path)
    market_sha256 = _sha256(market_path)
    if audit_sha256 != EXPECTED_AUDIT_SHA256:
        raise ValueError(
            f"collection audit SHA changed: {audit_sha256} != {EXPECTED_AUDIT_SHA256}"
        )
    if market_sha256 != EXPECTED_MARKET_SHA256:
        raise ValueError(
            f"market artifact SHA changed: {market_sha256} != {EXPECTED_MARKET_SHA256}"
        )

    audit = _load(audit_path)
    if audit.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("audit protocol version mismatch")
    if audit.get("validation") != "PASSED":
        raise ValueError("Cohort-3 collection audit must be PASSED")
    if audit.get("cohort_role") != COHORT_ROLE:
        raise ValueError("audit cohort role mismatch")
    if audit.get("blind_data_used") is not False:
        raise ValueError("audit must remain development-only")
    if audit.get("implementation_allowed") is not False:
        raise ValueError("audit must not authorize implementation")
    if audit.get("expected") != EXPECTED:
        raise ValueError("audit expected shape differs from frozen protocol")
    expected_artifacts = {
        "market": "PASSED",
        "vix": "PASSED",
        "options": "PASSED",
        "spot": "PASSED",
        "intrabar": "PASSED",
    }
    if audit.get("artifacts") != expected_artifacts:
        raise ValueError("not every Cohort-3 source artifact passed the audit")

    market = _load(market_path)
    vix = _load(vix_path)
    options = _load(options_path)
    spot = _load(spot_path)
    intrabar = _load(intrabar_path)

    validate_market(market)
    validate_auxiliary(vix, "vix")
    validate_options(options)
    validate_auxiliary(spot, "spot")
    validate_intrabar(intrabar)

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "cohort_role": COHORT_ROLE,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "expected": EXPECTED,
        "audit": {
            "sha256": audit_sha256,
            "validation": "PASSED",
        },
        "sources": {
            "market": {
                "sha256": market_sha256,
                "sessions": len(market["session_dates"]),
                "rows": len(market["canonical_market_rows"]),
            },
            "vix": {
                "sha256": _sha256(vix_path),
                "sessions": len(vix["session_dates"]),
                "rows": len(vix["vix_rows"]),
            },
            "options": {
                "sha256": _sha256(options_path),
                "sessions": len(options["session_dates"]),
                "rows": len(options["option_candles"]),
            },
            "spot": {
                "sha256": _sha256(spot_path),
                "sessions": len(spot["session_dates"]),
                "rows": len(spot["spot_rows"]),
            },
            "intrabar": {
                "sha256": _sha256(intrabar_path),
                "sessions": len(intrabar["session_dates"]),
                "rows": len(intrabar["rows"]),
            },
        },
        "manifest_policy": {
            "hashes_frozen_before_event_feature_construction": True,
            "hashes_frozen_before_event_outcome_construction": True,
            "strategy_scoring_performed": False,
            "threshold_search_performed": False,
            "blind_validation_performed": False,
        },
        "next_step": (
            "Commit these exact source hashes into the expanded development-corpus "
            "protocol before constructing Cohort-3 events or combining them with "
            "the frozen 152-session event corpus."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze exact Development Cohort 3 raw source hashes"
    )
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--vix", type=Path, required=True)
    parser.add_argument("--options", type=Path, required=True)
    parser.add_argument("--spot", type=Path, required=True)
    parser.add_argument("--intrabar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = build_manifest(
        audit_path=args.audit,
        market_path=args.market,
        vix_path=args.vix,
        options_path=args.options,
        spot_path=args.spot,
        intrabar_path=args.intrabar,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "audit_sha256": report["audit"]["sha256"],
        "source_sha256": {
            key: value["sha256"]
            for key, value in report["sources"].items()
        },
        "next_step": report["next_step"],
    }, indent=2))


if __name__ == "__main__":
    main()
