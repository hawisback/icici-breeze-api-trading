"""Matched-entry pre-trail stop diagnostic for Strategy F5."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _costs,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_pretrail_loss_control_protocol import (
    GUARDRAILS,
    PRESERVATION_SCREEN,
    PROTOCOL_VERSION,
    REPORTING,
    ROLE,
    STOP_CANDIDATES_PCT,
    STOP_EXECUTION,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_PRETRAIL_LOSS_CONTROL_DIAGNOSTIC_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract_paths(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    paths: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in bars_2m:
        start = datetime.fromisoformat(str(row["timestamp"]))
        decision = start + timedelta(minutes=2)
        paths[
            (str(row["expiry"]), int(row["strike"]), str(row["right"]))
        ].append({
            "bar_start": start.isoformat(),
            "decision_timestamp": decision.isoformat(),
            "close": float(row["close"]),
            "open": float(row["open"]),
        })
    for rows in paths.values():
        rows.sort(key=lambda x: x["decision_timestamp"])
    return paths


def _next_open_lookup(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], float]:
    return {
        (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
        ): float(row["open"])
        for row in bars_2m
    }


def _preactivation_path(
    trade: dict[str, Any],
    paths: dict[tuple[str, int, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    key = (
        str(trade["expiry"]),
        int(trade["strike"]),
        str(trade["right"]),
    )
    entry_ts = datetime.fromisoformat(str(trade["entry_timestamp"]))
    exit_ts = datetime.fromisoformat(str(trade["exit_timestamp"]))
    activation_text = trade.get("trail_activation_timestamp")
    activation_ts = (
        datetime.fromisoformat(str(activation_text))
        if activation_text
        else None
    )

    out: list[dict[str, Any]] = []
    for bar in paths.get(key, []):
        decision = datetime.fromisoformat(str(bar["decision_timestamp"]))
        if decision <= entry_ts:
            continue
        if decision > exit_ts:
            break
        # On the +10% activation bar the frozen F5 rule gives activation
        # precedence. Protective stops are therefore only allowed strictly
        # before that timestamp.
        if activation_ts is not None and decision >= activation_ts:
            break
        close_return = (
            float(bar["close"]) / float(trade["entry_open"]) - 1.0
        ) * 100.0
        out.append({
            **bar,
            "close_return_pct": close_return,
        })
    return out


def _trade_diagnostics(
    trades: list[dict[str, Any]],
    paths: dict[tuple[str, int, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for idx, trade in enumerate(trades, start=1):
        path = _preactivation_path(trade, paths)
        returns = [float(x["close_return_pct"]) for x in path]
        primary_net = float(trade["primary_cost_model"]["net_pnl_inr"])
        diagnostics.append({
            "baseline_trade_number": idx,
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "exit_timestamp": str(trade["exit_timestamp"]),
            "right": str(trade["right"]),
            "expiry": str(trade["expiry"]),
            "strike": int(trade["strike"]),
            "entry_open": float(trade["entry_open"]),
            "baseline_exit_open": float(trade["exit_open"]),
            "baseline_exit_reason": str(trade["exit_reason"]),
            "baseline_net_pnl_inr": primary_net,
            "baseline_winner": primary_net > 0.0,
            "trail_activated": bool(trade.get("trail_activated")),
            "trail_activation_timestamp": trade.get(
                "trail_activation_timestamp"
            ),
            "preactivation_observation_count": len(path),
            "preactivation_min_close_return_pct": (
                min(returns) if returns else 0.0
            ),
            "preactivation_max_close_return_pct": (
                max(returns) if returns else 0.0
            ),
            "path": path,
        })
    return diagnostics


def _pf(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return wins / losses


def _max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "trades": 0,
            "net_pnl_inr": 0.0,
            "profit_factor": None,
            "max_drawdown_inr": 0.0,
            "largest_loss_inr": None,
        }
    pf = _pf(values)
    return {
        "trades": len(values),
        "wins": sum(v > 0 for v in values),
        "losses": sum(v < 0 for v in values),
        "win_rate_pct": round(
            sum(v > 0 for v in values) / len(values) * 100.0,
            2,
        ),
        "net_pnl_inr": round(sum(values), 2),
        "average_net_pnl_inr": round(sum(values) / len(values), 2),
        "median_net_pnl_inr": round(float(pd.Series(values).median()), 2),
        "profit_factor": (
            None
            if pf is None
            else ("INF" if math.isinf(pf) else round(float(pf), 4))
        ),
        "max_drawdown_inr": round(_max_drawdown(values), 2),
        "largest_loss_inr": round(min(values), 2),
        "largest_win_inr": round(max(values), 2),
    }


def _counterfactual_trade(
    diagnostic: dict[str, Any],
    stop_pct: float,
    next_open: dict[tuple[str, str, int, str], float],
    baseline_trade: dict[str, Any],
) -> dict[str, Any]:
    trigger: dict[str, Any] | None = None
    for bar in diagnostic["path"]:
        if float(bar["close_return_pct"]) <= -float(stop_pct):
            trigger = bar
            break

    if trigger is None:
        return {
            "stopped_early": False,
            "stop_pct": stop_pct,
            "exit_timestamp": str(baseline_trade["exit_timestamp"]),
            "exit_open": float(baseline_trade["exit_open"]),
            "exit_reason": str(baseline_trade["exit_reason"]),
        }

    exit_ts = str(trigger["decision_timestamp"])
    key = (
        exit_ts,
        str(baseline_trade["expiry"]),
        int(baseline_trade["strike"]),
        str(baseline_trade["right"]),
    )
    exit_open = next_open.get(key)
    if exit_open is None:
        raise ValueError(
            f"missing next 2m open for protective stop exit {key}"
        )
    return {
        "stopped_early": True,
        "stop_pct": stop_pct,
        "trigger_decision_timestamp": exit_ts,
        "trigger_close_return_pct": round(
            float(trigger["close_return_pct"]), 4
        ),
        "exit_timestamp": exit_ts,
        "exit_open": float(exit_open),
        "exit_reason": f"PRETRAIL_CLOSE_STOP_{stop_pct:g}PCT",
    }


def _candidate_report(
    *,
    stop_pct: float,
    baseline_trades: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    next_open: dict[tuple[str, str, int, str], float],
) -> dict[str, Any]:
    slippage_keys = ("0.00", "0.50", "1.00")
    slippages = (0.0, 0.5, 1.0)
    result_rows: list[dict[str, Any]] = []

    for trade, diagnostic in zip(baseline_trades, diagnostics):
        cf = _counterfactual_trade(
            diagnostic, stop_pct, next_open, trade
        )
        costs = {
            key: _costs(
                float(trade["entry_open"]),
                float(cf["exit_open"]),
                slip,
            )
            for key, slip in zip(slippage_keys, slippages)
        }
        result_rows.append({
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "baseline_winner": bool(diagnostic["baseline_winner"]),
            "baseline_trail_activated": bool(
                diagnostic["trail_activated"]
            ),
            **cf,
            "costs": costs,
        })

    activated_total = sum(
        bool(x["trail_activated"]) for x in diagnostics
    )
    baseline_winners_total = sum(
        bool(x["baseline_winner"]) for x in diagnostics
    )
    activated_untouched = sum(
        row["baseline_trail_activated"] and not row["stopped_early"]
        for row in result_rows
    )
    winners_untouched = sum(
        row["baseline_winner"] and not row["stopped_early"]
        for row in result_rows
    )
    stopped = [row for row in result_rows if row["stopped_early"]]

    pooled: dict[str, Any] = {}
    by_month: dict[str, Any] = {}
    for slip_key in slippage_keys:
        vals = [
            float(row["costs"][slip_key]["net_pnl_inr"])
            for row in result_rows
        ]
        pooled[slip_key] = _summary(vals)
        by_month[slip_key] = {
            month: _summary([
                float(row["costs"][slip_key]["net_pnl_inr"])
                for row in result_rows
                if row["month"] == month
            ])
            for month in ("2026-07", "2026-08", "2026-09")
        }

    baseline_by_slip = {
        slip_key: [
            float(t["slippage_sensitivity"][slip_key]["net_pnl_inr"])
            for t in baseline_trades
        ]
        for slip_key in slippage_keys
    }
    baseline_month_net = {
        slip_key: {
            month: round(sum(
                float(t["slippage_sensitivity"][slip_key]["net_pnl_inr"])
                for t in baseline_trades
                if str(t["month"]) == month
            ), 2)
            for month in ("2026-07", "2026-08", "2026-09")
        }
        for slip_key in slippage_keys
    }

    deltas = {
        slip_key: round(
            float(pooled[slip_key]["net_pnl_inr"])
            - sum(baseline_by_slip[slip_key]),
            2,
        )
        for slip_key in slippage_keys
    }
    monthly_deltas_zero = {
        month: round(
            float(by_month["0.00"][month]["net_pnl_inr"])
            - float(baseline_month_net["0.00"][month]),
            2,
        )
        for month in ("2026-07", "2026-08", "2026-09")
    }

    activation_preservation = (
        activated_untouched / activated_total * 100.0
        if activated_total
        else 100.0
    )
    winner_preservation = (
        winners_untouched / baseline_winners_total * 100.0
        if baseline_winners_total
        else 100.0
    )

    failures: list[str] = []
    if activation_preservation < float(
        PRESERVATION_SCREEN["minimum_activated_trade_untouched_pct"]
    ):
        failures.append("activated_trade_preservation_below_minimum")
    if winner_preservation < float(
        PRESERVATION_SCREEN["minimum_baseline_winner_untouched_pct"]
    ):
        failures.append("baseline_winner_preservation_below_minimum")
    for month, delta in monthly_deltas_zero.items():
        if delta <= 0.0:
            failures.append(f"{month}_zero_slippage_net_not_improved")
    if deltas["0.50"] <= 0.0:
        failures.append("pooled_half_point_net_not_improved")
    if deltas["1.00"] <= 0.0:
        failures.append("pooled_one_point_net_not_improved")

    return {
        "stop_pct": stop_pct,
        "stops_triggered": len(stopped),
        "stopped_baseline_winners": sum(
            row["baseline_winner"] for row in stopped
        ),
        "stopped_baseline_activated_trades": sum(
            row["baseline_trail_activated"] for row in stopped
        ),
        "activated_trade_untouched_pct": round(
            activation_preservation, 2
        ),
        "baseline_winner_untouched_pct": round(
            winner_preservation, 2
        ),
        "pooled": pooled,
        "by_month": by_month,
        "net_delta_vs_baseline": deltas,
        "monthly_net_delta_vs_baseline_zero_slippage": monthly_deltas_zero,
        "screen": {
            "passed": not failures,
            "failures": failures,
            "contract": PRESERVATION_SCREEN,
        },
        "stopped_trade_sample": stopped[:50],
    }


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "p05": None, "p10": None, "p25": None,
            "p50": None, "p75": None, "p90": None,
        }
    arr = np.array(values, dtype=float)
    return {
        "p05": round(float(np.quantile(arr, 0.05)), 4),
        "p10": round(float(np.quantile(arr, 0.10)), 4),
        "p25": round(float(np.quantile(arr, 0.25)), 4),
        "p50": round(float(np.quantile(arr, 0.50)), 4),
        "p75": round(float(np.quantile(arr, 0.75)), 4),
        "p90": round(float(np.quantile(arr, 0.90)), 4),
    }


def analyze(
    market: dict[str, Any],
    backtest: dict[str, Any],
    *,
    market_sha256: str,
    backtest_sha256: str,
) -> dict[str, Any]:
    if market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 raw market protocol mismatch")
    if market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 raw market artifact must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != market_sha256:
        raise ValueError("F5 backtest is not bound to supplied market artifact")

    baseline_block = (
        backtest.get("candidates", {}).get(BASELINE_CANDIDATE) or {}
    )
    baseline_trades = list(baseline_block.get("trades") or [])
    bars_2m, incomplete = _aggregate_2m(
        list(market.get("option_rows_1m") or [])
    )
    paths = _contract_paths(bars_2m)
    next_open = _next_open_lookup(bars_2m)
    diagnostics = _trade_diagnostics(baseline_trades, paths)

    activated_mae = [
        float(x["preactivation_min_close_return_pct"])
        for x in diagnostics
        if x["trail_activated"]
    ]
    nonactivated_mae = [
        float(x["preactivation_min_close_return_pct"])
        for x in diagnostics
        if not x["trail_activated"]
    ]
    winner_mae = [
        float(x["preactivation_min_close_return_pct"])
        for x in diagnostics
        if x["baseline_winner"]
    ]
    loser_mae = [
        float(x["preactivation_min_close_return_pct"])
        for x in diagnostics
        if not x["baseline_winner"]
    ]

    candidates = {
        f"STOP_{stop:g}PCT": _candidate_report(
            stop_pct=float(stop),
            baseline_trades=baseline_trades,
            diagnostics=diagnostics,
            next_open=next_open,
        )
        for stop in STOP_CANDIDATES_PCT
    }
    survivors = [
        name
        for name, result in candidates.items()
        if result["screen"]["passed"]
    ]

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
            "baseline_matched_trades": len(baseline_trades),
            "complete_2m_bars_rebuilt": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "diagnostic_trade_count": len(diagnostics),
        },
        "baseline": {
            "pooled": baseline_block.get("pooled"),
            "by_month": baseline_block.get("by_month"),
            "ce_0_00": baseline_block.get("ce_0_00"),
            "pe_0_00": baseline_block.get("pe_0_00"),
        },
        "preactivation_mae_close_return_pct": {
            "trail_activated_trades": {
                "count": len(activated_mae),
                "distribution": _distribution(activated_mae),
            },
            "nonactivated_trades": {
                "count": len(nonactivated_mae),
                "distribution": _distribution(nonactivated_mae),
            },
            "baseline_winners": {
                "count": len(winner_mae),
                "distribution": _distribution(winner_mae),
            },
            "baseline_nonwinners": {
                "count": len(loser_mae),
                "distribution": _distribution(loser_mae),
            },
        },
        "stop_execution": STOP_EXECUTION,
        "candidates": candidates,
        "survivors": survivors,
        "decision": (
            "PRETRAIL_STOP_CANDIDATE_PRESERVES_WINNERS_AND_IMPROVES_LOSS_CONTROL_REQUIRES_HOLDOUT"
            if survivors
            else "NO_PRETRAIL_FIXED_STOP_PRESERVES_WINNERS_AND_IMPROVES_ALL_MONTHS"
        ),
        "reporting_contract": REPORTING,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose pre-trail protective stops for Strategy F5"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_pretrail_loss_control_2026_07_09.json"
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
    compact = {
        name: {
            "stop_pct": result["stop_pct"],
            "stops_triggered": result["stops_triggered"],
            "activated_trade_untouched_pct": result[
                "activated_trade_untouched_pct"
            ],
            "baseline_winner_untouched_pct": result[
                "baseline_winner_untouched_pct"
            ],
            "pooled": result["pooled"],
            "monthly_delta_zero": result[
                "monthly_net_delta_vs_baseline_zero_slippage"
            ],
            "screen": result["screen"],
        }
        for name, result in report["candidates"].items()
    }
    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": market_sha,
        "source_backtest_sha256": backtest_sha,
        "preactivation_mae": report[
            "preactivation_mae_close_return_pct"
        ],
        "candidates": compact,
        "survivors": report["survivors"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
