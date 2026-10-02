"""Protocol-driven options collection for Development Cohort 3."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.historical.independent_cohort3_protocol import (
    OPTION_EXPIRIES,
    SESSION_DATES,
    validate_market,
    validate_options,
)
from services.historical.independent_options_market_research import (
    BreezeOptionsClient,
    _load_local_env,
    _usable_secret,
    build_options_dataset,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect frozen Development Cohort 3 NIFTY options"
    )
    parser.add_argument("--market-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calls-per-minute", type=int, default=80)
    args = parser.parse_args()

    market = json.loads(args.market_input.read_text(encoding="utf-8"))
    validate_market(market)
    if list(market["session_dates"]) != SESSION_DATES:
        raise ValueError("market sessions differ from frozen Cohort 3 protocol")

    _load_local_env()
    key = _usable_secret("BREEZE_API_KEY")
    secret = _usable_secret("BREEZE_SECRET_KEY")
    token = _usable_secret("BREEZE_SESSION_TOKEN")
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )

    report = build_options_dataset(
        market,
        OPTION_EXPIRIES,
        client=BreezeOptionsClient(
            key, secret, token, calls_per_minute=args.calls_per_minute
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    try:
        validate_options(report)
    except ValueError:
        print(json.dumps({
            "output": str(args.output),
            "validation": "FAILED",
            "quality": report.get("quality"),
            "option_expiries": OPTION_EXPIRIES,
        }, indent=2))
        raise
    print(json.dumps({
        "output": str(args.output),
        "validation": "PASSED",
        "option_expiries": OPTION_EXPIRIES,
        "quality": report["quality"],
    }, indent=2))


if __name__ == "__main__":
    main()
