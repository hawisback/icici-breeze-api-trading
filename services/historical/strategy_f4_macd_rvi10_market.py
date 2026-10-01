"""Collect Jul-Sep 2026 Breeze data for Strategy F4 MACD+RVI exploration."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from services.historical.independent_options_market_research import (
    BreezeOptionsClient,
    _load_local_env,
    _usable_secret,
)
from services.historical.strategy_f_macd_options_market import BreezeSpotClient
from services.historical.strategy_f4_macd_rvi10_protocol import (
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    STRATEGY_NAME,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F4_MACD_RVI10_MARKET_V1"


def _atm_strike(price: float) -> int:
    step = int(OPTION_SELECTION["strike_step"])
    return int(math.floor(price / step + 0.5) * step)


def _nearest_expiry(day: date) -> date:
    expiries = [date.fromisoformat(x) for x in OPTION_SELECTION["weekly_expiries"]]
    eligible = [expiry for expiry in expiries if expiry >= day]
    if not eligible:
        raise ValueError(f"no frozen F4 option expiry covers {day.isoformat()}")
    return min(eligible)


def _daily_contracts(spot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spot_rows:
        by_day[str(row["date"])].append(row)

    selected: list[dict[str, Any]] = []
    for day_text, rows in sorted(by_day.items()):
        day = date.fromisoformat(day_text)
        if not (start <= day <= end):
            continue
        rows = sorted(rows, key=lambda x: x["timestamp"])
        opening = next(
            (
                row
                for row in rows
                if datetime.fromisoformat(str(row["timestamp"])).strftime("%H:%M")
                == "09:15"
            ),
            None,
        )
        if opening is None:
            continue
        spot_open = float(opening["open"])
        strike = _atm_strike(spot_open)
        expiry = _nearest_expiry(day)
        selected.append({
            "date": day.isoformat(),
            "month": day.strftime("%Y-%m"),
            "spot_0915_open": spot_open,
            "strike": strike,
            "expiry": expiry.isoformat(),
        })
    return selected


def _request_plan(
    spot_rows: list[dict[str, Any]],
    daily_contracts: list[dict[str, Any]],
) -> dict[tuple[str, int, str], list[date]]:
    all_dates = sorted({date.fromisoformat(str(r["date"])) for r in spot_rows})
    warmup_count = int(WINDOW["warmup_previous_sessions_per_contract"])
    plan: dict[tuple[str, int, str], set[date]] = defaultdict(set)

    for selected in daily_contracts:
        session_day = date.fromisoformat(str(selected["date"]))
        expiry = date.fromisoformat(str(selected["expiry"]))
        prior = [d for d in all_dates if d < session_day]
        needed = prior[-warmup_count:] + [session_day]
        needed = [d for d in needed if d <= expiry]
        for right in OPTION_SELECTION["rights"]:
            plan[
                (str(selected["expiry"]), int(selected["strike"]), str(right))
            ].update(needed)

    return {key: sorted(days) for key, days in sorted(plan.items())}


def collect(
    *,
    spot_client: BreezeSpotClient,
    options_client: BreezeOptionsClient,
) -> dict[str, Any]:
    warmup = date.fromisoformat(WINDOW["warmup_start"])
    end = date.fromisoformat(WINDOW["end"])
    spot_rows = spot_client.history(warmup, end)
    daily_contracts = _daily_contracts(spot_rows)
    plan = _request_plan(spot_rows, daily_contracts)

    option_rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for (expiry, strike, right), days in plan.items():
        fetched = options_client.history(
            days,
            expiry,
            strike,
            "call" if right == "CE" else "put",
        )
        option_rows.extend(fetched)
        requests.append({
            "expiry": expiry,
            "strike": strike,
            "right": right,
            "session_dates": [d.isoformat() for d in days],
            "rows_fetched": len(fetched),
        })

    dedup = {
        (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
        ): row
        for row in option_rows
    }
    option_rows = [dedup[key] for key in sorted(dedup)]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "research_only": True,
        "window": WINDOW,
        "daily_contracts": daily_contracts,
        "spot_rows": spot_rows,
        "option_rows": option_rows,
        "option_contract_requests": requests,
        "quality": {
            "spot_rows": len(spot_rows),
            "selected_sessions": len(daily_contracts),
            "selected_sessions_by_month": {
                month: sum(x["month"] == month for x in daily_contracts)
                for month in WINDOW["months"]
            },
            "unique_option_contracts": len(plan),
            "option_rows": len(option_rows),
            "option_contract_request_count": len(requests),
        },
        "request_diagnostics": {
            "spot": spot_client.request_diagnostics,
            "options": options_client.request_diagnostics,
        },
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Jul-Sep 2026 data for Strategy F4 MACD+RVI10"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f4_macd_rvi10_market_2026_07_09.json"),
    )
    parser.add_argument("--calls-per-minute", type=int, default=80)
    args = parser.parse_args()

    _load_local_env()
    key = _usable_secret("BREEZE_API_KEY")
    secret = _usable_secret("BREEZE_SECRET_KEY")
    token = _usable_secret("BREEZE_SESSION_TOKEN")
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )

    report = collect(
        spot_client=BreezeSpotClient(
            key, secret, token, calls_per_minute=args.calls_per_minute
        ),
        options_client=BreezeOptionsClient(
            key, secret, token, calls_per_minute=args.calls_per_minute
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "quality": report["quality"],
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
