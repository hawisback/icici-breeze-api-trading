"""Backtest NIFTY resistance-based stops inside F5 bearish-regime PE."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _costs,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day
from services.historical.strategy_f5_nifty_structure_stop_protocol import (
    BREACH_RULE,
    DEVELOPMENT_SCREEN,
    DEVELOPMENT_WINDOW,
    GUARDRAILS,
    PROTOCOL_VERSION,
    RESISTANCE_CANDIDATES,
    ROLE,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_NIFTY_STRUCTURE_STOP_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"
SLIPPAGES = {"0.00": 0.0, "0.50": 0.5, "1.00": 1.0}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_pre_entry(
    rows: list[dict[str, Any]],
    entry_dt: datetime,
) -> list[dict[str, Any]]:
    interval = timedelta(
        minutes=int(DEVELOPMENT_WINDOW["spot_interval_minutes"])
    )
    return sorted(
        [
            row
            for row in rows
            if datetime.fromisoformat(str(row["timestamp"])) + interval
            <= entry_dt
        ],
        key=lambda row: str(row["timestamp"]),
    )


def _resistance_levels(
    rows: list[dict[str, Any]],
    entry_timestamp: str,
) -> dict[str, float | None]:
    entry_dt = datetime.fromisoformat(entry_timestamp)
    completed = _completed_pre_entry(rows, entry_dt)
    if not completed:
        return {name: None for name in RESISTANCE_CANDIDATES}

    session_high = max(float(row["high"]) for row in completed)

    lookback = timedelta(
        minutes=int(
            RESISTANCE_CANDIDATES["RECENT_30M_HIGH"]["lookback_minutes"]
        )
    )
    recent = [
        row
        for row in completed
        if datetime.fromisoformat(str(row["timestamp"])) >= entry_dt - lookback
    ]
    recent_high = (
        max(float(row["high"]) for row in recent) if recent else None
    )

    left = int(
        RESISTANCE_CANDIDATES["CONFIRMED_SWING_HIGH_2X2"]["left_bars"]
    )
    right = int(
        RESISTANCE_CANDIDATES["CONFIRMED_SWING_HIGH_2X2"]["right_bars"]
    )
    pivots: list[dict[str, Any]] = []
    highs = [float(row["high"]) for row in completed]
    for idx in range(left, len(completed) - right):
        current = highs[idx]
        before = highs[idx - left:idx]
        after = highs[idx + 1:idx + 1 + right]
        if all(current > value for value in before + after):
            pivots.append(completed[idx])
    swing_high = float(pivots[-1]["high"]) if pivots else None

    return {
        "CONFIRMED_SWING_HIGH_2X2": swing_high,
        "RECENT_30M_HIGH": recent_high,
        "SESSION_HIGH_TO_ENTRY": session_high,
    }


def _first_breach_confirmation(
    rows: list[dict[str, Any]],
    *,
    entry_timestamp: str,
    stop_before_timestamp: str,
    level: float,
) -> str | None:
    entry_dt = datetime.fromisoformat(entry_timestamp)
    stop_before = datetime.fromisoformat(stop_before_timestamp)
    interval = timedelta(
        minutes=int(DEVELOPMENT_WINDOW["spot_interval_minutes"])
    )
    for row in sorted(rows, key=lambda x: str(x["timestamp"])):
        bar_start = datetime.fromisoformat(str(row["timestamp"]))
        confirmation = bar_start + interval
        if confirmation <= entry_dt:
            continue
        if confirmation >= stop_before:
            break
        if float(row["close"]) > float(level):
            return confirmation.isoformat()
    return None


def _first_option_open_at_or_after(
    bars_2m: list[dict[str, Any]],
    *,
    timestamp: str,
    expiry: str,
    strike: int,
    right: str,
    before_timestamp: str,
) -> tuple[str, float] | None:
    lower = datetime.fromisoformat(timestamp)
    upper = datetime.fromisoformat(before_timestamp)
    eligible: list[tuple[datetime, float]] = []
    for row in bars_2m:
        if (
            str(row["expiry"]) != expiry
            or int(row["strike"]) != strike
            or str(row["right"]) != right
        ):
            continue
        ts = datetime.fromisoformat(str(row["timestamp"]))
        if lower <= ts < upper:
            eligible.append((ts, float(row["open"])))
    if not eligible:
        return None
    ts, value = min(eligible, key=lambda item: item[0])
    return ts.isoformat(), value


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "net_pnl_inr": 0.0,
            "average_net_pnl_inr": None,
        }
    wins = sum(value > 0.0 for value in values)
    return {
        "trades": len(values),
        "wins": wins,
        "losses": sum(value < 0.0 for value in values),
        "win_rate_pct": round(wins / len(values) * 100.0, 2),
        "net_pnl_inr": round(sum(values), 2),
        "average_net_pnl_inr": round(sum(values) / len(values), 2),
    }


def _candidate_report(
    candidate_name: str,
    target: list[dict[str, Any]],
    spot_by_day: dict[str, list[dict[str, Any]]],
    bars_2m: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing_execution_open = 0

    for item in target:
        trade = item["trade"]
        day = str(trade["date"])
        level = item["levels"].get(candidate_name)
        baseline_exit = str(trade["exit_timestamp"])
        activation_text = trade.get("trail_activation_timestamp")
        stop_before = (
            min(
                datetime.fromisoformat(baseline_exit),
                datetime.fromisoformat(str(activation_text)),
            ).isoformat()
            if activation_text
            else baseline_exit
        )

        confirmation = None
        execution = None
        triggered = False
        exit_open = float(trade["exit_open"])
        exit_timestamp = baseline_exit

        if level is not None:
            confirmation = _first_breach_confirmation(
                spot_by_day.get(day, []),
                entry_timestamp=str(trade["entry_timestamp"]),
                stop_before_timestamp=stop_before,
                level=float(level),
            )
            if confirmation is not None:
                execution = _first_option_open_at_or_after(
                    bars_2m,
                    timestamp=confirmation,
                    expiry=str(trade["expiry"]),
                    strike=int(trade["strike"]),
                    right=str(trade["right"]),
                    before_timestamp=stop_before,
                )
                if execution is None:
                    missing_execution_open += 1
                else:
                    exit_timestamp, exit_open = execution
                    triggered = True

        costs = {
            key: _costs(
                float(trade["entry_open"]),
                exit_open,
                slip,
            )
            for key, slip in SLIPPAGES.items()
        }
        baseline_pnl = float(
            trade["primary_cost_model"]["net_pnl_inr"]
        )
        rows.append({
            "date": day,
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "level": None if level is None else round(float(level), 4),
            "level_available": level is not None,
            "breach_confirmation_timestamp": confirmation,
            "triggered": triggered,
            "counterfactual_exit_timestamp": exit_timestamp,
            "baseline_winner": baseline_pnl > 0.0,
            "baseline_trail_activated": bool(trade.get("trail_activated")),
            "baseline_net_pnl_inr": baseline_pnl,
            "costs": costs,
        })

    available = [row for row in rows if row["level_available"]]
    breached = [row for row in rows if row["triggered"]]
    winners = [row for row in rows if row["baseline_winner"]]
    activated = [row for row in rows if row["baseline_trail_activated"]]

    winner_untouched = sum(not row["triggered"] for row in winners)
    activation_untouched = sum(not row["triggered"] for row in activated)
    winner_preservation = (
        winner_untouched / len(winners) * 100.0 if winners else 100.0
    )
    activation_preservation = (
        activation_untouched / len(activated) * 100.0
        if activated else 100.0
    )

    baseline_net = {
        key: round(sum(
            float(item["trade"]["slippage_sensitivity"][key]["net_pnl_inr"])
            for item in target
        ), 2)
        for key in SLIPPAGES
    }
    candidate_net = {
        key: round(sum(float(row["costs"][key]["net_pnl_inr"]) for row in rows), 2)
        for key in SLIPPAGES
    }
    delta = {
        key: round(candidate_net[key] - baseline_net[key], 2)
        for key in SLIPPAGES
    }

    monthly_delta: dict[str, float] = {}
    for month in DEVELOPMENT_WINDOW["months"]:
        month_rows = [row for row in rows if row["month"] == month]
        candidate_month = sum(
            float(row["costs"]["0.00"]["net_pnl_inr"])
            for row in month_rows
        )
        baseline_month = sum(
            float(row["baseline_net_pnl_inr"]) for row in month_rows
        )
        monthly_delta[month] = round(candidate_month - baseline_month, 2)

    failures: list[str] = []
    if len(available) < int(DEVELOPMENT_SCREEN["minimum_level_available_trades"]):
        failures.append("level_coverage_below_minimum")
    if len(breached) < int(DEVELOPMENT_SCREEN["minimum_breaches"]):
        failures.append("breach_count_below_minimum")
    if winner_preservation < float(
        DEVELOPMENT_SCREEN["minimum_target_winner_untouched_pct"]
    ):
        failures.append("winner_preservation_below_95pct")
    if activation_preservation < float(
        DEVELOPMENT_SCREEN["minimum_target_activated_untouched_pct"]
    ):
        failures.append("activated_preservation_below_95pct")
    for key, label in (
        ("0.00", "zero"),
        ("0.50", "half_point"),
        ("1.00", "one_point"),
    ):
        if delta[key] <= 0.0:
            failures.append(f"pooled_{label}_slippage_not_improved")
    for month, value in monthly_delta.items():
        if value <= 0.0:
            failures.append(f"{month}_not_improved")

    return {
        "candidate": candidate_name,
        "target_trades": len(rows),
        "level_available_trades": len(available),
        "breaches": len(breached),
        "breached_baseline_winners": sum(
            row["baseline_winner"] for row in breached
        ),
        "breached_baseline_activated": sum(
            row["baseline_trail_activated"] for row in breached
        ),
        "winner_untouched_pct": round(winner_preservation, 2),
        "activated_untouched_pct": round(activation_preservation, 2),
        "missing_counterfactual_exit_opens": missing_execution_open,
        "breached_subset_baseline": _summary([
            float(row["baseline_net_pnl_inr"]) for row in breached
        ]),
        "breached_subset_counterfactual_zero_slippage": _summary([
            float(row["costs"]["0.00"]["net_pnl_inr"]) for row in breached
        ]),
        "no_breach_subset_baseline": _summary([
            float(row["baseline_net_pnl_inr"])
            for row in rows if not row["triggered"]
        ]),
        "baseline_net_pnl_inr": baseline_net,
        "candidate_net_pnl_inr": candidate_net,
        "net_delta_vs_baseline_inr": delta,
        "monthly_net_delta_vs_baseline_zero_slippage_inr": monthly_delta,
        "screen": {
            "passed": not failures,
            "failures": failures,
            "contract": DEVELOPMENT_SCREEN,
        },
        "trade_rows": rows,
    }


def analyze(
    f5_market: dict[str, Any],
    backtest: dict[str, Any],
    *,
    f5_market_sha256: str,
    backtest_sha256: str,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != f5_market_sha256:
        raise ValueError("F5 backtest not bound to supplied market")

    start = date.fromisoformat(DEVELOPMENT_WINDOW["start"])
    end = date.fromisoformat(DEVELOPMENT_WINDOW["end"])

    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list(f5_market.get("spot_rows") or []):
        day = date.fromisoformat(str(row["date"]))
        if start <= day <= end:
            spot_by_day[day.isoformat()].append(row)

    regimes = {
        day: _classify_day(day, rows)
        for day, rows in sorted(spot_by_day.items())
    }

    bars_2m, incomplete = _aggregate_2m(
        list(f5_market.get("option_rows_1m") or [])
    )
    baseline = list(
        backtest["candidates"][BASELINE_CANDIDATE].get("trades") or []
    )

    target: list[dict[str, Any]] = []
    for trade in baseline:
        day = str(trade["date"])
        if not (start <= date.fromisoformat(day) <= end):
            continue
        if str(trade["right"]) != "PE":
            continue
        if regimes.get(day, {}).get("regime") != "BEARISH":
            continue
        target.append({
            "trade": trade,
            "levels": _resistance_levels(
                spot_by_day.get(day, []),
                str(trade["entry_timestamp"]),
            ),
        })

    candidates = {
        name: _candidate_report(name, target, spot_by_day, bars_2m)
        for name in RESISTANCE_CANDIDATES
    }

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "quality": {
            "target_bearish_PE_trades": len(target),
            "spot_sessions": len(spot_by_day),
            "regime_available_sessions": sum(
                bool(item.get("available")) for item in regimes.values()
            ),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
        },
        "baseline_target": _summary([
            float(item["trade"]["primary_cost_model"]["net_pnl_inr"])
            for item in target
        ]),
        "resistance_definition": RESISTANCE_CANDIDATES,
        "breach_rule": BREACH_RULE,
        "candidates": candidates,
        "decision": "STRUCTURE_STOP_DEVELOPMENT_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze NIFTY resistance stops for F5 bearish PE"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_nifty_structure_stop_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    market_sha = _sha256(args.f5_market)
    backtest_sha = _sha256(args.backtest)
    report = analyze(
        json.loads(args.f5_market.read_text(encoding="utf-8")),
        json.loads(args.backtest.read_text(encoding="utf-8")),
        f5_market_sha256=market_sha,
        backtest_sha256=backtest_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "sha256": _sha256(args.output),
        "quality": report["quality"],
        "baseline_target": report["baseline_target"],
        "candidates": {
            name: {
                key: value
                for key, value in block.items()
                if key != "trade_rows"
            }
            for name, block in report["candidates"].items()
        },
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
