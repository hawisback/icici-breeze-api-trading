"""Strict QA audit for frozen Development Cohort 3 collection artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.historical.independent_cohort3_protocol import (
    COHORT_ROLE,
    EXPECTED,
    GUARDRAILS,
    PROTOCOL_VERSION,
    validate_auxiliary,
    validate_intrabar,
    validate_market,
    validate_options,
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(
    market: dict[str, Any],
    vix: dict[str, Any],
    options: dict[str, Any],
    spot: dict[str, Any],
    intrabar: dict[str, Any],
) -> dict[str, Any]:
    validate_market(market)
    validate_auxiliary(vix, "vix")
    validate_options(options)
    validate_auxiliary(spot, "spot")
    validate_intrabar(intrabar)
    return {
        "research_type": "NIFTY_DEVELOPMENT_COHORT_3_COLLECTION_AUDIT_V1",
        "protocol_version": PROTOCOL_VERSION,
        "cohort_role": COHORT_ROLE,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "expected": EXPECTED,
        "validation": "PASSED",
        "artifacts": {
            "market": "PASSED",
            "vix": "PASSED",
            "options": "PASSED",
            "spot": "PASSED",
            "intrabar": "PASSED",
        },
        "guardrails": GUARDRAILS,
        "next_step": (
            "Only after this audit passes may Cohort 3 be incorporated into a "
            "new inspected development event corpus. It must not be called blind "
            "validation or used to promote a candidate by itself."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit frozen Development Cohort 3 collection"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--vix", type=Path, required=True)
    parser.add_argument("--options", type=Path, required=True)
    parser.add_argument("--spot", type=Path, required=True)
    parser.add_argument("--intrabar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(
        _load(args.market),
        _load(args.vix),
        _load(args.options),
        _load(args.spot),
        _load(args.intrabar),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "validation": report["validation"],
        "cohort_role": report["cohort_role"],
    }, indent=2))


if __name__ == "__main__":
    main()
