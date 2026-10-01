"""Matched-entry F5 pre-trail RVI failure exit diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _bar_open_lookup,
    _costs,
    _observations,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_pretrail_rvi_failure_protocol import (
    GUARDRAILS,
    PRESERVATION_SCREEN,
    PROTOCOL_VERSION,
    ROLE,
    RVI_FAILURE,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_PRETRAIL_RVI_FAILURE_DIAGNOSTIC_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pf(values: list[float]) -> float | None:
    pos = sum(v for v in values if v > 0)
    neg = -sum(v for v in values if v < 0)
    if neg == 0:
        return None if pos == 0 else float("inf")
    return pos / neg


def _max_dd(values: list[float]) -> float:
    equity = peak = dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "trades": 0,
            "net_pnl_inr": 0.0,
            "profit_factor": None,
            "max_drawdown_inr": 0.0,
        }
    pf = _pf(values)
    return {
        "trades": len(values),
        "wins": sum(v > 0 for v in values),
        "losses": sum(v < 0 for v in values),
        "win_rate_pct": round(sum(v > 0 for v in values) / len(values) * 100.0, 2),
        "net_pnl_inr": round(sum(values), 2),
        "average_net_pnl_inr": round(sum(values) / len(values), 2),
        "median_net_pnl_inr": round(float(pd.Series(values).median()), 2),
        "profit_factor": (
            None
            if pf is None
            else ("INF" if math.isinf(pf) else round(float(pf), 4))
        ),
        "max_drawdown_inr": round(_max_dd(values), 2),
        "largest_loss_inr": round(min(values), 2),
        "largest_win_inr": round(max(values), 2),
    }


def _candidate_exit(
    trade: dict[str, Any],
    day_observations: dict[str, dict[str, Any]],
    open_2m: dict[tuple[str, str, int, str], float],
) -> dict[str, Any]:
    entry_dt = datetime.fromisoformat(str(trade["entry_timestamp"]))
    baseline_exit_dt = datetime.fromisoformat(str(trade["exit_timestamp"]))
    activation_text = trade.get("trail_activation_timestamp")
    activation_dt = (
        datetime.fromisoformat(str(activation_text))
        if activation_text
        else None
    )
    right = str(trade["right"])
    threshold = float(RVI_FAILURE["centerline"])
    need = int(RVI_FAILURE["consecutive_completed_2m_bars_below_centerline"])

    consecutive = 0
    first_below_ts: str | None = None
    for ts in sorted(day_observations):
        dt = datetime.fromisoformat(ts)
        if dt <= entry_dt:
            continue
        if dt > baseline_exit_dt:
            break
        if activation_dt is not None and dt >= activation_dt:
            break

        obs = day_observations[ts].get(right)
        if obs is None:
            consecutive = 0
            first_below_ts = None
            continue

        if float(obs["rvi"]) < threshold:
            consecutive += 1
            if consecutive == 1:
                first_below_ts = ts
        else:
            consecutive = 0
            first_below_ts = None

        if consecutive >= need:
            key = (
                ts,
                str(trade["expiry"]),
                int(trade["strike"]),
                right,
            )
            exit_open = open_2m.get(key)
            if exit_open is None:
                raise ValueError(f"missing 2m open for RVI failure exit {key}")
            return {
                "exited_early": True,
                "exit_timestamp": ts,
                "exit_open": float(exit_open),
                "exit_reason": "PRETRAIL_RVI_LT50_TWO_CONSECUTIVE_2M_BARS",
                "first_below_50_timestamp": first_below_ts,
                "second_below_50_timestamp": ts,
                "second_below_50_rvi": round(float(obs["rvi"]), 4),
            }

    return {
        "exited_early": False,
        "exit_timestamp": str(trade["exit_timestamp"]),
        "exit_open": float(trade["exit_open"]),
        "exit_reason": str(trade["exit_reason"]),
        "first_below_50_timestamp": None,
        "second_below_50_timestamp": None,
        "second_below_50_rvi": None,
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

    baseline = backtest["candidates"][BASELINE_CANDIDATE]
    trades = list(baseline.get("trades") or [])
    bars_2m, incomplete = _aggregate_2m(list(market.get("option_rows_1m") or []))
    obs_by_day, insufficient = _observations(
        list(market.get("daily_contracts") or []),
        bars_2m,
    )
    open_2m = _bar_open_lookup(bars_2m)

    rows: list[dict[str, Any]] = []
    for trade in trades:
        cf = _candidate_exit(
            trade,
            obs_by_day.get(str(trade["date"]), {}),
            open_2m,
        )
        costs = {
            key: _costs(
                float(trade["entry_open"]),
                float(cf["exit_open"]),
                slip,
            )
            for key, slip in (("0.00", 0.0), ("0.50", 0.5), ("1.00", 1.0))
        }
        rows.append({
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "baseline_winner": float(
                trade["primary_cost_model"]["net_pnl_inr"]
            ) > 0.0,
            "baseline_trail_activated": bool(trade.get("trail_activated")),
            **cf,
            "costs": costs,
        })

    early = [r for r in rows if r["exited_early"]]
    activated_total = sum(r["baseline_trail_activated"] for r in rows)
    winner_total = sum(r["baseline_winner"] for r in rows)
    activated_untouched = sum(
        r["baseline_trail_activated"] and not r["exited_early"] for r in rows
    )
    winner_untouched = sum(
        r["baseline_winner"] and not r["exited_early"] for r in rows
    )
    activation_pres = (
        activated_untouched / activated_total * 100.0
        if activated_total
        else 100.0
    )
    winner_pres = (
        winner_untouched / winner_total * 100.0 if winner_total else 100.0
    )

    pooled: dict[str, Any] = {}
    by_month: dict[str, Any] = {}
    baseline_net: dict[str, float] = {}
    baseline_month_net: dict[str, dict[str, float]] = {}
    for key in ("0.00", "0.50", "1.00"):
        values = [float(r["costs"][key]["net_pnl_inr"]) for r in rows]
        pooled[key] = _summary(values)
        by_month[key] = {
            month: _summary([
                float(r["costs"][key]["net_pnl_inr"])
                for r in rows
                if r["month"] == month
            ])
            for month in ("2026-07", "2026-08", "2026-09")
        }
        baseline_net[key] = round(sum(
            float(t["slippage_sensitivity"][key]["net_pnl_inr"])
            for t in trades
        ), 2)
        baseline_month_net[key] = {
            month: round(sum(
                float(t["slippage_sensitivity"][key]["net_pnl_inr"])
                for t in trades
                if str(t["month"]) == month
            ), 2)
            for month in ("2026-07", "2026-08", "2026-09")
        }

    deltas = {
        key: round(float(pooled[key]["net_pnl_inr"]) - baseline_net[key], 2)
        for key in ("0.00", "0.50", "1.00")
    }
    monthly_delta_zero = {
        month: round(
            float(by_month["0.00"][month]["net_pnl_inr"])
            - baseline_month_net["0.00"][month],
            2,
        )
        for month in ("2026-07", "2026-08", "2026-09")
    }

    failures: list[str] = []
    if activation_pres < float(
        PRESERVATION_SCREEN["minimum_activated_trade_untouched_pct"]
    ):
        failures.append("activated_trade_preservation_below_minimum")
    if winner_pres < float(
        PRESERVATION_SCREEN["minimum_baseline_winner_untouched_pct"]
    ):
        failures.append("baseline_winner_preservation_below_minimum")
    for month, delta in monthly_delta_zero.items():
        if delta <= 0.0:
            failures.append(f"{month}_zero_slippage_net_not_improved")
    if deltas["0.50"] <= 0.0:
        failures.append("pooled_half_point_net_not_improved")
    if deltas["1.00"] <= 0.0:
        failures.append("pooled_one_point_net_not_improved")

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "baseline_protocol_version": F5_PROTOCOL_VERSION,
        "baseline_candidate": BASELINE_CANDIDATE,
        "quality": {
            "matched_trades": len(trades),
            "complete_2m_bars_rebuilt": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
        },
        "rvi_failure_rule": RVI_FAILURE,
        "result": {
            "early_exits": len(early),
            "early_exited_baseline_winners": sum(
                r["baseline_winner"] for r in early
            ),
            "early_exited_activated_trades": sum(
                r["baseline_trail_activated"] for r in early
            ),
            "activated_trade_untouched_pct": round(activation_pres, 2),
            "baseline_winner_untouched_pct": round(winner_pres, 2),
            "pooled": pooled,
            "by_month": by_month,
            "net_delta_vs_baseline": deltas,
            "monthly_net_delta_vs_baseline_zero_slippage": monthly_delta_zero,
            "screen": {
                "passed": not failures,
                "failures": failures,
                "contract": PRESERVATION_SCREEN,
            },
            "early_exit_sample": early[:100],
        },
        "decision": (
            "RVI_FAILURE_EXIT_PRESERVES_WINNERS_AND_IMPROVES_LOSS_CONTROL_REQUIRES_HOLDOUT"
            if not failures
            else "RVI_FAILURE_EXIT_DID_NOT_PASS_PRESERVATION_AND_ECONOMIC_SCREEN"
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test F5 pre-trail two-bar RVI<50 failure exit"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f5_pretrail_rvi_failure_2026_07_09.json"),
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
        "result": report["result"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
