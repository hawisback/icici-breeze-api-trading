"""Collect Breeze data for Strategy F2 MACD histogram-persistence backtests."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from services.historical.independent_options_market_research import (
    BreezeOptionsClient,
    _load_local_env,
    _usable_secret,
)
from services.historical.strategy_f_macd_options_market import BreezeSpotClient
from services.historical.strategy_f2_macd_histogram_protocol import (
    BACKTEST_WINDOW,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    STRATEGY_NAME,
    VARIANTS,
)

RESEARCH_TYPE = "STRATEGY_F2_MACD_HISTOGRAM_MARKET_V1"


def _atm_strike(price: float) -> int:
    step = int(OPTION_SELECTION["strike_step"])
    return int(math.floor(price / step + 0.5) * step)


def _nearest_expiry(day: date) -> date:
    expiries = [date.fromisoformat(x) for x in OPTION_SELECTION["weekly_expiries"]]
    eligible = [expiry for expiry in expiries if expiry >= day]
    if not eligible:
        raise ValueError(f"no frozen F2 expiry covers {day.isoformat()}")
    return min(eligible)


def _macd_frame(spot_rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(spot_rows).sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        raise ValueError("no NIFTY spot rows")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    close = frame["close"].astype(float)
    fast = close.ewm(span=int(MACD["fast_length"]), adjust=False).mean()
    slow = close.ewm(span=int(MACD["slow_length"]), adjust=False).mean()
    frame["macd"] = fast - slow
    frame["signal"] = frame["macd"].ewm(
        span=int(MACD["signal_length"]), adjust=False
    ).mean()
    frame["hist"] = frame["macd"] - frame["signal"]
    frame["macd_prev"] = frame["macd"].shift(1)
    frame["signal_prev"] = frame["signal"].shift(1)
    return frame


def _is_consecutive_same_day(
    earlier: pd.Timestamp,
    later: pd.Timestamp,
) -> bool:
    return (
        earlier.date() == later.date()
        and later - earlier == pd.Timedelta(minutes=5)
    )


def _qualified_entries(
    spot_rows: list[dict[str, Any]],
    confirmation_bars: int,
) -> list[dict[str, Any]]:
    frame = _macd_frame(spot_rows)
    start = date.fromisoformat(BACKTEST_WINDOW["start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    entries: list[dict[str, Any]] = []

    for i in range(1, len(frame)):
        row = frame.iloc[i]
        prev = frame.iloc[i - 1]
        bullish = prev["macd"] <= prev["signal"] and row["macd"] > row["signal"]
        bearish = prev["macd"] >= prev["signal"] and row["macd"] < row["signal"]
        if not (bullish or bearish):
            continue

        cross_ts = pd.Timestamp(row["timestamp"])
        if not (start <= cross_ts.date() <= end):
            continue
        cross_hist = float(row["hist"])
        if bullish and cross_hist <= 0:
            continue
        if bearish and cross_hist >= 0:
            continue

        last_ts = cross_ts
        last_abs = abs(cross_hist)
        final_index = i
        valid = True

        for step in range(1, confirmation_bars + 1):
            j = i + step
            if j >= len(frame):
                valid = False
                break
            confirm = frame.iloc[j]
            confirm_ts = pd.Timestamp(confirm["timestamp"])
            if not _is_consecutive_same_day(last_ts, confirm_ts):
                valid = False
                break
            hist = float(confirm["hist"])
            same_side = hist > 0 if bullish else hist < 0
            if not same_side or abs(hist) <= last_abs:
                valid = False
                break
            last_ts = confirm_ts
            last_abs = abs(hist)
            final_index = j

        if not valid:
            continue

        final_row = frame.iloc[final_index]
        final_ts = pd.Timestamp(final_row["timestamp"])
        entry_time = final_ts.to_pydatetime() + timedelta(minutes=5)
        if entry_time.time() >= force_exit:
            continue

        close = float(final_row["close"])
        day = entry_time.date()
        entries.append(
            {
                "date": day.isoformat(),
                "cross_bar_start": cross_ts.to_pydatetime().isoformat(),
                "confirmation_bars": confirmation_bars,
                "final_confirmation_bar_start": final_ts.to_pydatetime().isoformat(),
                "entry_timestamp": entry_time.isoformat(),
                "direction": "BULLISH" if bullish else "BEARISH",
                "right": "CE" if bullish else "PE",
                "signal_close": close,
                "strike": _atm_strike(close),
                "expiry": _nearest_expiry(day).isoformat(),
                "cross_histogram": cross_hist,
                "final_histogram": float(final_row["hist"]),
            }
        )
    return entries


def _contract_plan(
    by_variant: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, int, str], set[date]]:
    plan: dict[tuple[str, int, str], set[date]] = defaultdict(set)
    for entries in by_variant.values():
        for event in entries:
            key = (
                str(event["expiry"]),
                int(event["strike"]),
                str(event["right"]),
            )
            plan[key].add(date.fromisoformat(str(event["date"])))
    return plan


def collect(
    *,
    spot_client: BreezeSpotClient,
    options_client: BreezeOptionsClient,
) -> dict[str, Any]:
    warmup = date.fromisoformat(BACKTEST_WINDOW["warmup_start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    spot_rows = spot_client.history(warmup, end)

    by_variant = {
        name: _qualified_entries(spot_rows, int(spec["confirmation_bars"]))
        for name, spec in VARIANTS.items()
    }
    plan = _contract_plan(by_variant)

    option_rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for (expiry, strike, right), days in sorted(plan.items()):
        fetched = options_client.history(
            sorted(days),
            expiry,
            strike,
            "call" if right == "CE" else "put",
        )
        option_rows.extend(fetched)
        requests.append(
            {
                "expiry": expiry,
                "strike": strike,
                "right": right,
                "session_dates": [d.isoformat() for d in sorted(days)],
                "rows_fetched": len(fetched),
            }
        )

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
        "backtest_window": BACKTEST_WINDOW,
        "variant_entry_events_for_collection_only": by_variant,
        "spot_rows": spot_rows,
        "option_rows": option_rows,
        "option_contract_requests": requests,
        "quality": {
            "spot_rows": len(spot_rows),
            "qualified_entries_by_variant": {
                name: len(rows) for name, rows in by_variant.items()
            },
            "option_contract_request_count": len(requests),
            "option_rows": len(option_rows),
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
        description="Collect August 2026 Breeze data for Strategy F2"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f2_macd_histogram_market_2026_08.json"),
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
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "quality": report["quality"],
                "strategy_outcomes_scored": False,
                "broker_called": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
