"""Protocol-driven market collection for Development Cohort 3."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from services.historical.independent_cohort3_protocol import (
    EXPECTED,
    FUTURES_MONTHLY_EXPIRIES,
    SESSION_DATES,
    validate_market,
)
from services.historical.independent_market_research import build_research_dataset


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect frozen Development Cohort 3 NIFTY market data"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lookback-days", type=int, default=150)
    args = parser.parse_args()

    report = build_research_dataset(
        sessions=int(EXPECTED["sessions"]),
        lookback_days=args.lookback_days,
        breeze_near_month_expiries=list(FUTURES_MONTHLY_EXPIRIES),
        end_date=date.fromisoformat(SESSION_DATES[-1]),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    try:
        validate_market(report)
    except ValueError:
        print(json.dumps({
            "output": str(args.output),
            "validation": "FAILED",
            "session_dates": report.get("session_dates"),
            "coverage": report.get("coverage"),
            "provenance": report.get("provenance"),
        }, indent=2))
        raise
    print(json.dumps({
        "output": str(args.output),
        "validation": "PASSED",
        "session_dates": report["session_dates"],
        "coverage": report["coverage"],
    }, indent=2))


if __name__ == "__main__":
    main()
