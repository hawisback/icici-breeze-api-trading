"""Collect raw market data for the F5 forward regime holdout."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from services.historical.independent_options_market_research import (
    _load_local_env,
    _usable_secret,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_market import (
    BreezeOneMinuteOptionsClient,
)
from services.historical.strategy_f_macd_options_market import BreezeSpotClient
from services.historical.strategy_f5_forward_regime_holdout_protocol import (
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_FORWARD_REGIME_HOLDOUT_MARKET_V1"


def _atm_strike(price: float) -> int:
    step = int(OPTION_SELECTION["strike_step"])
    return int(math.floor(price / step + 0.5) * step)


def _nearest_expiry(day: date) -> date:
    expiries = [date.fromisoformat(x) for x in OPTION_SELECTION["weekly_expiries"]]
    eligible = [expiry for expiry in expiries if expiry >= day]
    if not eligible:
        raise ValueError(f"no frozen holdout option expiry covers {day.isoformat()}")
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
        ordered = sorted(rows, key=lambda x: str(x["timestamp"]))
        opening = next(
            (
                row
                for row in ordered
                if datetime.fromisoformat(str(row["timestamp"])).strftime("%H:%M")
                == str(WINDOW["session_start"])
            ),
            None,
        )
        if opening is None:
            continue
        spot_open = float(opening["open"])
        selected.append({
            "date": day.isoformat(),
            "month": day.strftime("%Y-%m"),
            "spot_0915_open": spot_open,
            "strike": _atm_strike(spot_open),
            "expiry": _nearest_expiry(day).isoformat(),
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
        needed = [d for d in prior[-warmup_count:] + [session_day] if d <= expiry]
        for right in OPTION_SELECTION["rights"]:
            plan[
                (str(selected["expiry"]), int(selected["strike"]), str(right))
            ].update(needed)

    return {key: sorted(days) for key, days in sorted(plan.items())}


def collect(
    *,
    spot_client: BreezeSpotClient,
    options_client: BreezeOneMinuteOptionsClient,
) -> dict[str, Any]:
    warmup = date.fromisoformat(WINDOW["warmup_start"])
    end = date.fromisoformat(WINDOW["end"])
    spot_rows = spot_client.history(warmup, end)
    daily_contracts = _daily_contracts(spot_rows)
    plan = _request_plan(spot_rows, daily_contracts)

    option_rows_1m: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for (expiry, strike, right), days in plan.items():
        fetched = options_client.history(
            days,
            expiry,
            strike,
            "call" if right == "CE" else "put",
        )
        option_rows_1m.extend(fetched)
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
        for row in option_rows_1m
    }
    option_rows_1m = [dedup[key] for key in sorted(dedup)]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "window": WINDOW,
        "option_selection": OPTION_SELECTION,
        "daily_contracts": daily_contracts,
        "spot_rows": spot_rows,
        "option_rows_1m": option_rows_1m,
        "option_contract_requests": requests,
        "quality": {
            "spot_rows": len(spot_rows),
            "selected_sessions": len(daily_contracts),
            "selected_sessions_by_month": {
                month: sum(x["month"] == month for x in daily_contracts)
                for month in WINDOW["months"]
            },
            "unique_option_contracts": len(plan),
            "option_rows_1m": len(option_rows_1m),
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
        description="Collect F5 Oct-Dec 2026 forward regime holdout market"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f5_forward_regime_holdout_market_2026_q4.json"),
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
        options_client=BreezeOneMinuteOptionsClient(
            key, secret, token, calls_per_minute=args.calls_per_minute
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "quality": report["quality"],
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
