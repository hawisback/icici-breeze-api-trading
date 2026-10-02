"""Backtest option-premium support stops inside F5 bearish-regime PE."""
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
    _bar_open_lookup,
    _costs,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day
from services.historical.strategy_f5_option_support_stop_protocol import (
    BREAK_RULE,
    DEVELOPMENT_SCREEN,
    DEVELOPMENT_WINDOW,
    GUARDRAILS,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
    SUPPORT_CANDIDATES,
)

RESEARCH_TYPE = "STRATEGY_F5_OPTION_SUPPORT_STOP_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"
SLIPPAGES = {"0.00": 0.0, "0.50": 0.5, "1.00": 1.0}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract_bars(
    bars_2m: list[dict[str, Any]],
    trade: dict[str, Any],
) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in bars_2m
            if str(row["date"]) == str(trade["date"])
            and str(row["expiry"]) == str(trade["expiry"])
            and int(row["strike"]) == int(trade["strike"])
            and str(row["right"]) == str(trade["right"])
        ],
        key=lambda row: str(row["timestamp"]),
    )


def _completed_pre_entry(
    rows: list[dict[str, Any]],
    entry_dt: datetime,
) -> list[dict[str, Any]]:
    interval = timedelta(
        minutes=int(DEVELOPMENT_WINDOW["option_interval_minutes"])
    )
    return [
        row
        for row in rows
        if datetime.fromisoformat(str(row["timestamp"])) + interval <= entry_dt
    ]


def _support_levels(
    rows: list[dict[str, Any]],
    entry_timestamp: str,
) -> dict[str, float | None]:
    entry_dt = datetime.fromisoformat(entry_timestamp)
    completed = _completed_pre_entry(rows, entry_dt)
    if not completed:
        return {name: None for name in SUPPORT_CANDIDATES}

    interval = timedelta(
        minutes=int(DEVELOPMENT_WINDOW["option_interval_minutes"])
    )
    signal = next(
        (
            row
            for row in reversed(completed)
            if datetime.fromisoformat(str(row["timestamp"])) + interval
            == entry_dt
        ),
        None,
    )
    signal_low = None if signal is None else float(signal["low"])

    lookback = timedelta(
        minutes=int(SUPPORT_CANDIDATES["RECENT_10M_LOW"]["lookback_minutes"])
    )
    recent = [
        row
        for row in completed
        if datetime.fromisoformat(str(row["timestamp"])) >= entry_dt - lookback
    ]
    recent_low = min(float(row["low"]) for row in recent) if recent else None

    left = int(
        SUPPORT_CANDIDATES["CONFIRMED_SWING_LOW_2X2"]["left_bars"]
    )
    right = int(
        SUPPORT_CANDIDATES["CONFIRMED_SWING_LOW_2X2"]["right_bars"]
    )
    lows = [float(row["low"]) for row in completed]
    pivots: list[dict[str, Any]] = []
    for idx in range(left, len(completed) - right):
        current = lows[idx]
        before = lows[idx - left:idx]
        after = lows[idx + 1:idx + 1 + right]
        if all(current < value for value in before + after):
            pivots.append(completed[idx])
    swing_low = float(pivots[-1]["low"]) if pivots else None

    return {
        "SIGNAL_BAR_LOW": signal_low,
        "RECENT_10M_LOW": recent_low,
        "CONFIRMED_SWING_LOW_2X2": swing_low,
    }


def _first_support_break(
    rows: list[dict[str, Any]],
    *,
    entry_timestamp: str,
    stop_before_timestamp: str,
    support: float,
) -> str | None:
    entry_dt = datetime.fromisoformat(entry_timestamp)
    stop_before = datetime.fromisoformat(stop_before_timestamp)
    interval = timedelta(
        minutes=int(DEVELOPMENT_WINDOW["option_interval_minutes"])
    )
    for row in rows:
        bar_start = datetime.fromisoformat(str(row["timestamp"]))
        confirmation = bar_start + interval
        if confirmation <= entry_dt:
            continue
        if confirmation >= stop_before:
            break
        if float(row["close"]) < float(support):
            return confirmation.isoformat()
    return None


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
    bars_2m: list[dict[str, Any]],
    open_2m: dict[tuple[str, str, int, str], float],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing_execution_open = 0

    for item in target:
        trade = item["trade"]
        support = item["levels"].get(candidate_name)
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
        contract_rows = item["contract_rows"]

        confirmation = None
        triggered = False
        exit_timestamp = baseline_exit
        exit_open = float(trade["exit_open"])

        if support is not None:
            confirmation = _first_support_break(
                contract_rows,
                entry_timestamp=str(trade["entry_timestamp"]),
                stop_before_timestamp=stop_before,
                support=float(support),
            )
            if confirmation is not None:
                key = (
                    confirmation,
                    str(trade["expiry"]),
                    int(trade["strike"]),
                    str(trade["right"]),
                )
                cf_open = open_2m.get(key)
                if cf_open is None:
                    missing_execution_open += 1
                else:
                    exit_timestamp = confirmation
                    exit_open = float(cf_open)
                    triggered = True

        costs = {
            key: _costs(
                float(trade["entry_open"]),
                exit_open,
                slip,
            )
            for key, slip in SLIPPAGES.items()
        }
        baseline_pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        distance_pct = (
            None
            if support is None
            else round(
                (float(support) / float(trade["entry_open"]) - 1.0) * 100.0,
                4,
            )
        )
        rows.append({
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "support": None if support is None else round(float(support), 4),
            "support_distance_pct_vs_entry": distance_pct,
            "level_available": support is not None,
            "break_confirmation_timestamp": confirmation,
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

    winner_preservation = (
        sum(not row["triggered"] for row in winners) / len(winners) * 100.0
        if winners else 100.0
    )
    activation_preservation = (
        sum(not row["triggered"] for row in activated) / len(activated) * 100.0
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
        group = [row for row in rows if row["month"] == month]
        candidate_month = sum(
            float(row["costs"]["0.00"]["net_pnl_inr"]) for row in group
        )
        baseline_month = sum(
            float(row["baseline_net_pnl_inr"]) for row in group
        )
        monthly_delta[month] = round(candidate_month - baseline_month, 2)

    failures: list[str] = []
    if len(available) < int(DEVELOPMENT_SCREEN["minimum_level_available_trades"]):
        failures.append("level_coverage_below_minimum")
    if len(breached) < int(DEVELOPMENT_SCREEN["minimum_breaks"]):
        failures.append("break_count_below_minimum")
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

    support_distances = [
        float(row["support_distance_pct_vs_entry"])
        for row in available
        if row["support_distance_pct_vs_entry"] is not None
    ]
    return {
        "candidate": candidate_name,
        "target_trades": len(rows),
        "level_available_trades": len(available),
        "breaks": len(breached),
        "broken_baseline_winners": sum(
            row["baseline_winner"] for row in breached
        ),
        "broken_baseline_activated": sum(
            row["baseline_trail_activated"] for row in breached
        ),
        "winner_untouched_pct": round(winner_preservation, 2),
        "activated_untouched_pct": round(activation_preservation, 2),
        "average_support_distance_pct_vs_entry": (
            None
            if not support_distances
            else round(sum(support_distances) / len(support_distances), 4)
        ),
        "missing_counterfactual_exit_opens": missing_execution_open,
        "broken_subset_baseline": _summary([
            float(row["baseline_net_pnl_inr"]) for row in breached
        ]),
        "broken_subset_counterfactual_zero_slippage": _summary([
            float(row["costs"]["0.00"]["net_pnl_inr"]) for row in breached
        ]),
        "no_break_subset_baseline": _summary([
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
    open_2m = _bar_open_lookup(bars_2m)
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
        contract_rows = _contract_bars(bars_2m, trade)
        target.append({
            "trade": trade,
            "contract_rows": contract_rows,
            "levels": _support_levels(
                contract_rows,
                str(trade["entry_timestamp"]),
            ),
        })

    candidates = {
        name: _candidate_report(name, target, bars_2m, open_2m)
        for name in SUPPORT_CANDIDATES
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
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
        },
        "baseline_target": _summary([
            float(item["trade"]["primary_cost_model"]["net_pnl_inr"])
            for item in target
        ]),
        "support_definition": SUPPORT_CANDIDATES,
        "break_rule": BREAK_RULE,
        "candidates": candidates,
        "decision": "OPTION_SUPPORT_STOP_DEVELOPMENT_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze option-support stops for F5 bearish PE"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_option_support_stop_2026_07_09.json"
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
