"""Evaluate F5 CE/PE performance against NIFTY direction for last week."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_nifty_direction_last_week_protocol import (
    ALIGNMENT,
    DIRECTION,
    GUARDRAILS,
    PROTOCOL_VERSION,
    REPORTING,
    ROLE,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_NIFTY_DIRECTION_LAST_WEEK_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _direction(return_pct: float) -> str:
    if return_pct > 0.0:
        return "BULLISH"
    if return_pct < 0.0:
        return "BEARISH"
    return "FLAT"


def _spot_by_day(
    spot_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spot_rows:
        day = date.fromisoformat(str(row["date"]))
        if start <= day <= end:
            grouped[day.isoformat()].append(row)
    for day in grouped:
        grouped[day].sort(key=lambda r: str(r["timestamp"]))
    return dict(grouped)


def _row_at(
    rows: list[dict[str, Any]],
    hhmm: str,
) -> dict[str, Any] | None:
    for row in rows:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        if ts.strftime("%H:%M") == hhmm:
            return row
    return None


def _daily_direction(
    day: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    opening = _row_at(rows, str(WINDOW["direction_start"]))
    ending = _row_at(rows, str(WINDOW["full_day_direction_end"]))
    if opening is None or ending is None:
        return {
            "date": day,
            "available": False,
            "missing_0915_open": opening is None,
            "missing_1520_open": ending is None,
            "direction": None,
            "return_pct": None,
        }

    start_price = float(opening["open"])
    end_price = float(ending["open"])
    ret = 100.0 * (end_price / start_price - 1.0)
    return {
        "date": day,
        "available": True,
        "spot_0915_open": start_price,
        "spot_1520_open": end_price,
        "return_pct": round(ret, 4),
        "direction": _direction(ret),
        "session_high_to_1520": max(
            float(r["high"])
            for r in rows
            if datetime.fromisoformat(str(r["timestamp"])).strftime("%H:%M")
            <= str(WINDOW["full_day_direction_end"])
        ),
        "session_low_to_1520": min(
            float(r["low"])
            for r in rows
            if datetime.fromisoformat(str(r["timestamp"])).strftime("%H:%M")
            <= str(WINDOW["full_day_direction_end"])
        ),
    }


def _entry_time_direction(
    entry_timestamp: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    entry = datetime.fromisoformat(entry_timestamp)
    opening = _row_at(rows, str(WINDOW["direction_start"]))
    if opening is None:
        return {
            "available": False,
            "reason": "MISSING_0915_SPOT_BAR",
            "direction": None,
            "return_pct": None,
        }

    interval = timedelta(minutes=int(WINDOW["spot_interval_minutes"]))
    completed = []
    for row in rows:
        start = datetime.fromisoformat(str(row["timestamp"]))
        if start + interval <= entry:
            completed.append(row)

    if not completed:
        return {
            "available": False,
            "reason": "NO_COMPLETED_5M_SPOT_BAR_BEFORE_ENTRY",
            "direction": None,
            "return_pct": None,
        }

    latest = completed[-1]
    start_price = float(opening["open"])
    end_price = float(latest["close"])
    ret = 100.0 * (end_price / start_price - 1.0)
    latest_start = datetime.fromisoformat(str(latest["timestamp"]))
    return {
        "available": True,
        "reason": None,
        "spot_0915_open": start_price,
        "latest_completed_bar_start": latest_start.isoformat(),
        "latest_completed_bar_end": (latest_start + interval).isoformat(),
        "spot_completed_bar_close": end_price,
        "return_pct": round(ret, 4),
        "direction": _direction(ret),
    }


def _alignment(right: str, direction: str | None) -> str:
    if direction not in {"BULLISH", "BEARISH"}:
        return "UNCLASSIFIED"
    return (
        "ALIGNED"
        if ALIGNMENT.get(right) == direction
        else "COUNTER_DIRECTION"
    )


def _summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "trail_activations": 0,
            "trail_activation_rate_pct": None,
            "net_pnl_inr": 0.0,
            "average_net_pnl_inr": None,
            "median_net_pnl_inr": None,
        }

    pnl = [float(t["net_pnl_inr"]) for t in trades]
    ordered = sorted(pnl)
    n = len(ordered)
    if n % 2:
        median = ordered[n // 2]
    else:
        median = (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0

    wins = sum(v > 0.0 for v in pnl)
    activations = sum(bool(t["trail_activated"]) for t in trades)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": sum(v < 0.0 for v in pnl),
        "win_rate_pct": round(wins / len(trades) * 100.0, 2),
        "trail_activations": activations,
        "trail_activation_rate_pct": round(
            activations / len(trades) * 100.0, 2
        ),
        "net_pnl_inr": round(sum(pnl), 2),
        "average_net_pnl_inr": round(sum(pnl) / len(trades), 2),
        "median_net_pnl_inr": round(median, 2),
    }


def analyze(
    market: dict[str, Any],
    backtest: dict[str, Any],
    *,
    market_sha256: str,
    backtest_sha256: str,
) -> dict[str, Any]:
    if market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != market_sha256:
        raise ValueError("F5 backtest is not bound to supplied market artifact")

    spot = _spot_by_day(list(market.get("spot_rows") or []))
    daily_direction = {
        day: _daily_direction(day, rows)
        for day, rows in sorted(spot.items())
    }

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    baseline_trades = list(
        backtest["candidates"][BASELINE_CANDIDATE].get("trades") or []
    )
    selected = [
        trade
        for trade in baseline_trades
        if start <= date.fromisoformat(str(trade["date"])) <= end
    ]

    enriched: list[dict[str, Any]] = []
    for trade in selected:
        day = str(trade["date"])
        rows = spot.get(day, [])
        full = daily_direction.get(day)
        entry_dir = _entry_time_direction(
            str(trade["entry_timestamp"]), rows
        )
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        enriched.append({
            "date": day,
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "expiry": str(trade["expiry"]),
            "strike": int(trade["strike"]),
            "entry_open": float(trade["entry_open"]),
            "exit_timestamp": str(trade["exit_timestamp"]),
            "exit_reason": str(trade["exit_reason"]),
            "trail_activated": bool(trade.get("trail_activated")),
            "net_pnl_inr": pnl,
            "winner": pnl > 0.0,
            "full_day_direction": (
                None if full is None else full.get("direction")
            ),
            "full_day_return_pct": (
                None if full is None else full.get("return_pct")
            ),
            "full_day_alignment": _alignment(
                str(trade["right"]),
                None if full is None else full.get("direction"),
            ),
            "entry_time_direction": entry_dir["direction"],
            "entry_time_return_pct": entry_dir["return_pct"],
            "entry_time_alignment": _alignment(
                str(trade["right"]), entry_dir["direction"]
            ),
            "entry_time_direction_detail": entry_dir,
        })

    daily: dict[str, Any] = {}
    cursor = start
    while cursor <= end:
        day = cursor.isoformat()
        day_trades = [t for t in enriched if t["date"] == day]
        daily[day] = {
            "spot": daily_direction.get(
                day,
                {
                    "date": day,
                    "available": False,
                    "direction": None,
                    "return_pct": None,
                    "reason": "NO_SPOT_ROWS",
                },
            ),
            "all_trades": _summary(day_trades),
            "CE": _summary([t for t in day_trades if t["right"] == "CE"]),
            "PE": _summary([t for t in day_trades if t["right"] == "PE"]),
        }
        cursor += timedelta(days=1)

    full_aligned = [
        t for t in enriched if t["full_day_alignment"] == "ALIGNED"
    ]
    full_counter = [
        t
        for t in enriched
        if t["full_day_alignment"] == "COUNTER_DIRECTION"
    ]
    entry_aligned = [
        t for t in enriched if t["entry_time_alignment"] == "ALIGNED"
    ]
    entry_counter = [
        t
        for t in enriched
        if t["entry_time_alignment"] == "COUNTER_DIRECTION"
    ]
    entry_unclassified = [
        t for t in enriched if t["entry_time_alignment"] == "UNCLASSIFIED"
    ]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "baseline_candidate": BASELINE_CANDIDATE,
        "window": WINDOW,
        "direction_definition": DIRECTION,
        "quality": {
            "spot_days_found": len(spot),
            "baseline_trades_in_window": len(selected),
            "entry_time_direction_available_trades": (
                len(entry_aligned) + len(entry_counter)
            ),
            "entry_time_direction_unclassified_trades": len(entry_unclassified),
        },
        "daily": daily,
        "full_day_alignment": {
            "aligned": _summary(full_aligned),
            "counter_direction": _summary(full_counter),
            "unclassified": _summary([
                t
                for t in enriched
                if t["full_day_alignment"] == "UNCLASSIFIED"
            ]),
        },
        "entry_time_alignment": {
            "aligned": _summary(entry_aligned),
            "counter_direction": _summary(entry_counter),
            "unclassified": _summary(entry_unclassified),
        },
        "by_full_day_direction_and_side": {
            direction: {
                side: _summary([
                    t
                    for t in enriched
                    if t["full_day_direction"] == direction
                    and t["right"] == side
                ])
                for side in ("CE", "PE")
            }
            for direction in ("BULLISH", "BEARISH")
        },
        "by_entry_time_direction_and_side": {
            direction: {
                side: _summary([
                    t
                    for t in enriched
                    if t["entry_time_direction"] == direction
                    and t["right"] == side
                ])
                for side in ("CE", "PE")
            }
            for direction in ("BULLISH", "BEARISH")
        },
        "trades": enriched,
        "decision": "LAST_WEEK_NIFTY_DIRECTION_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "reporting": REPORTING,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare last-week F5 CE/PE trades with NIFTY direction"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_nifty_direction_last_week_2026_09_21_25.json"
        ),
    )
    args = parser.parse_args()

    market_sha = _sha256(args.market)
    backtest_sha = _sha256(args.backtest)
    market = json.loads(args.market.read_text(encoding="utf-8"))
    backtest = json.loads(args.backtest.read_text(encoding="utf-8"))
    report = analyze(
        market,
        backtest,
        market_sha256=market_sha,
        backtest_sha256=backtest_sha,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = _sha256(args.output)

    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": market_sha,
        "source_backtest_sha256": backtest_sha,
        "quality": report["quality"],
        "daily": report["daily"],
        "full_day_alignment": report["full_day_alignment"],
        "entry_time_alignment": report["entry_time_alignment"],
        "by_full_day_direction_and_side": report[
            "by_full_day_direction_and_side"
        ],
        "by_entry_time_direction_and_side": report[
            "by_entry_time_direction_and_side"
        ],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
