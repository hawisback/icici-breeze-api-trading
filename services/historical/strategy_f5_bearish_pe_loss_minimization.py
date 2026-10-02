"""Backtest lack-of-progress loss control inside F5 bearish-regime PE."""
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
    _observations,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_bearish_pe_loss_minimization_protocol import (
    ANATOMY_CHECKPOINTS_MINUTES,
    CANDIDATES,
    GUARDRAILS,
    PRESERVATION_SCREEN,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day
from services.historical.strategy_f5_nifty50_breadth import _breadth_day
from services.historical.strategy_f5_nifty50_breadth_protocol import (
    PROTOCOL_VERSION as BREADTH_PROTOCOL_VERSION,
    WINDOW as BREADTH_WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"
SLIPPAGES = {"0.00": 0.0, "0.50": 0.5, "1.00": 1.0}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "net_pnl_inr": 0.0,
            "average_net_pnl_inr": None,
            "largest_loss_inr": None,
        }
    return {
        "trades": len(values),
        "wins": sum(v > 0 for v in values),
        "losses": sum(v < 0 for v in values),
        "win_rate_pct": round(sum(v > 0 for v in values) / len(values) * 100.0, 2),
        "net_pnl_inr": round(sum(values), 2),
        "average_net_pnl_inr": round(sum(values) / len(values), 2),
        "largest_loss_inr": round(min(values), 2),
    }


def _breadth_status(regime: str | None, breadth: dict[str, Any]) -> str:
    available = bool(breadth.get("available"))
    if not available:
        return "UNAVAILABLE"
    if (
        regime in {"BULLISH", "BEARISH"}
        and breadth.get("breadth_direction") == regime
    ):
        return "CONFIRMED"
    return "AVAILABLE_NOT_CONFIRMED"


def _trade_path(
    trade: dict[str, Any],
    obs_by_day: dict[str, dict[str, dict[str, Any]]],
    minutes: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    entry_dt = datetime.fromisoformat(str(trade["entry_timestamp"]))
    checkpoint = entry_dt + timedelta(minutes=int(minutes))
    same_day = obs_by_day.get(str(trade["date"]), {})
    right = str(trade["right"])
    path: list[dict[str, Any]] = []
    checkpoint_obs: dict[str, Any] | None = None
    for ts in sorted(same_day):
        decision = datetime.fromisoformat(ts)
        if decision <= entry_dt:
            continue
        if decision > checkpoint:
            break
        held = same_day[ts].get(right)
        if held is None:
            continue
        close_return = (
            float(held["close"]) / float(trade["entry_open"]) - 1.0
        ) * 100.0
        row = {
            **held,
            "close_return_pct": close_return,
        }
        path.append(row)
        if decision == checkpoint:
            checkpoint_obs = row
    return path, checkpoint_obs


def _checkpoint_state(
    trade: dict[str, Any],
    obs_by_day: dict[str, dict[str, dict[str, Any]]],
    minutes: int,
) -> dict[str, Any]:
    path, checkpoint_obs = _trade_path(trade, obs_by_day, minutes)
    returns = [float(x["close_return_pct"]) for x in path]
    entry_day_obs = obs_by_day.get(str(trade["date"]), {})
    entry_obs = (
        entry_day_obs.get(str(trade["entry_timestamp"]), {})
        .get(str(trade["right"]))
    )
    return {
        "minutes": int(minutes),
        "checkpoint_available": checkpoint_obs is not None,
        "observations": len(path),
        "max_favorable_close_return_pct": (
            None if not returns else round(max(returns), 4)
        ),
        "max_adverse_close_return_pct": (
            None if not returns else round(min(returns), 4)
        ),
        "latest_close_return_pct": (
            None
            if checkpoint_obs is None
            else round(float(checkpoint_obs["close_return_pct"]), 4)
        ),
        "any_positive_close": (
            None if not returns else any(value > 0.0 for value in returns)
        ),
        "checkpoint_macd_hist_pct": (
            None
            if checkpoint_obs is None
            else round(float(checkpoint_obs["macd_hist_pct"]), 6)
        ),
        "checkpoint_rvi": (
            None
            if checkpoint_obs is None
            else round(float(checkpoint_obs["rvi"]), 4)
        ),
        "entry_macd_hist_pct": (
            None
            if entry_obs is None
            else round(float(entry_obs["macd_hist_pct"]), 6)
        ),
        "entry_rvi": (
            None if entry_obs is None else round(float(entry_obs["rvi"]), 4)
        ),
    }


def _anatomy_report(rows: list[dict[str, Any]], checkpoint: int) -> dict[str, Any]:
    eligible = [
        row for row in rows
        if row["checkpoints"][str(checkpoint)]["checkpoint_available"]
    ]
    winners = [row for row in eligible if row["baseline_winner"]]
    losers = [row for row in eligible if not row["baseline_winner"]]

    def group(group_rows: list[dict[str, Any]]) -> dict[str, Any]:
        states = [r["checkpoints"][str(checkpoint)] for r in group_rows]
        if not states:
            return {
                "trades": 0,
                "no_positive_close_pct": None,
                "latest_close_nonpositive_pct": None,
                "mfe_lt_2pct_pct": None,
                "median_mfe_pct": None,
                "median_mae_pct": None,
            }
        mfe = sorted(float(x["max_favorable_close_return_pct"]) for x in states)
        mae = sorted(float(x["max_adverse_close_return_pct"]) for x in states)
        n = len(states)
        def median(vals: list[float]) -> float:
            mid = len(vals) // 2
            return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
        return {
            "trades": n,
            "no_positive_close_pct": round(
                sum(not bool(x["any_positive_close"]) for x in states) / n * 100.0,
                2,
            ),
            "latest_close_nonpositive_pct": round(
                sum(float(x["latest_close_return_pct"]) <= 0.0 for x in states)
                / n * 100.0,
                2,
            ),
            "mfe_lt_2pct_pct": round(
                sum(float(x["max_favorable_close_return_pct"]) < 2.0 for x in states)
                / n * 100.0,
                2,
            ),
            "median_mfe_pct": round(median(mfe), 4),
            "median_mae_pct": round(median(mae), 4),
        }

    return {
        "checkpoint_minutes": checkpoint,
        "eligible_trades": len(eligible),
        "winners": group(winners),
        "losers": group(losers),
    }


def _candidate_condition(
    candidate_name: str,
    state: dict[str, Any],
) -> bool:
    if not state.get("checkpoint_available"):
        return False
    mfe = state.get("max_favorable_close_return_pct")
    if mfe is None:
        return False
    if candidate_name in {
        "NO_POSITIVE_CLOSE_BY_6M",
        "NO_POSITIVE_CLOSE_BY_10M",
    }:
        return float(mfe) <= 0.0
    if candidate_name == "MFE_LT_2PCT_BY_10M":
        return float(mfe) < 2.0
    raise ValueError(f"unknown candidate {candidate_name}")


def _candidate_report(
    candidate_name: str,
    target_rows: list[dict[str, Any]],
    baseline_trades_by_entry: dict[str, dict[str, Any]],
    open_2m: dict[tuple[str, str, int, str], float],
) -> dict[str, Any]:
    spec = CANDIDATES[candidate_name]
    checkpoint_minutes = int(spec["checkpoint_minutes"])
    results: list[dict[str, Any]] = []
    missing_exit_open = 0

    for row in target_rows:
        trade = baseline_trades_by_entry[str(row["entry_timestamp"])]
        entry_dt = datetime.fromisoformat(str(row["entry_timestamp"]))
        checkpoint_dt = entry_dt + timedelta(minutes=checkpoint_minutes)
        baseline_exit_dt = datetime.fromisoformat(str(trade["exit_timestamp"]))
        activation_text = trade.get("trail_activation_timestamp")
        activation_dt = (
            None
            if not activation_text
            else datetime.fromisoformat(str(activation_text))
        )

        state = row["checkpoints"][str(checkpoint_minutes)]
        trigger = (
            baseline_exit_dt > checkpoint_dt
            and (activation_dt is None or activation_dt > checkpoint_dt)
            and _candidate_condition(candidate_name, state)
        )

        exit_open = float(trade["exit_open"])
        exit_timestamp = str(trade["exit_timestamp"])
        if trigger:
            key = (
                checkpoint_dt.isoformat(),
                str(trade["expiry"]),
                int(trade["strike"]),
                str(trade["right"]),
            )
            cf_open = open_2m.get(key)
            if cf_open is None:
                missing_exit_open += 1
                trigger = False
            else:
                exit_open = float(cf_open)
                exit_timestamp = checkpoint_dt.isoformat()

        costs = {
            key: _costs(
                float(trade["entry_open"]),
                exit_open,
                slip,
            )
            for key, slip in SLIPPAGES.items()
        }
        results.append({
            "date": str(row["date"]),
            "month": str(row["month"]),
            "entry_timestamp": str(row["entry_timestamp"]),
            "breadth_status": str(row["breadth_status"]),
            "baseline_winner": bool(row["baseline_winner"]),
            "baseline_trail_activated": bool(row["trail_activated"]),
            "triggered": bool(trigger),
            "counterfactual_exit_timestamp": exit_timestamp,
            "costs": costs,
        })

    triggered = [r for r in results if r["triggered"]]
    winners_total = sum(r["baseline_winner"] for r in results)
    activated_total = sum(r["baseline_trail_activated"] for r in results)
    winners_untouched = sum(
        r["baseline_winner"] and not r["triggered"] for r in results
    )
    activated_untouched = sum(
        r["baseline_trail_activated"] and not r["triggered"] for r in results
    )
    winner_preservation = (
        winners_untouched / winners_total * 100.0 if winners_total else 100.0
    )
    activation_preservation = (
        activated_untouched / activated_total * 100.0 if activated_total else 100.0
    )

    baseline_net = {
        key: round(sum(
            float(baseline_trades_by_entry[str(r["entry_timestamp"])]
                  ["slippage_sensitivity"][key]["net_pnl_inr"])
            for r in results
        ), 2)
        for key in SLIPPAGES
    }
    candidate_net = {
        key: round(sum(float(r["costs"][key]["net_pnl_inr"]) for r in results), 2)
        for key in SLIPPAGES
    }
    net_delta = {
        key: round(candidate_net[key] - baseline_net[key], 2)
        for key in SLIPPAGES
    }

    months = ("2026-07", "2026-08", "2026-09")
    monthly_delta_zero = {}
    for month in months:
        group = [r for r in results if r["month"] == month]
        cf = sum(float(r["costs"]["0.00"]["net_pnl_inr"]) for r in group)
        base = sum(
            float(baseline_trades_by_entry[str(r["entry_timestamp"])]
                  ["primary_cost_model"]["net_pnl_inr"])
            for r in group
        )
        monthly_delta_zero[month] = round(cf - base, 2)

    failures: list[str] = []
    if winner_preservation < float(
        PRESERVATION_SCREEN["minimum_target_winner_untouched_pct"]
    ):
        failures.append("winner_preservation_below_95pct")
    if activation_preservation < float(
        PRESERVATION_SCREEN["minimum_target_activated_untouched_pct"]
    ):
        failures.append("activated_preservation_below_95pct")
    if net_delta["0.00"] <= 0.0:
        failures.append("pooled_zero_slippage_not_improved")
    if net_delta["0.50"] <= 0.0:
        failures.append("pooled_half_point_not_improved")
    if net_delta["1.00"] <= 0.0:
        failures.append("pooled_one_point_not_improved")
    for month, delta in monthly_delta_zero.items():
        if delta <= 0.0:
            failures.append(f"{month}_not_improved")

    breadth_confirmed = [
        r for r in results if r["breadth_status"] == "CONFIRMED"
    ]
    return {
        "candidate": candidate_name,
        "checkpoint_minutes": checkpoint_minutes,
        "target_trades": len(results),
        "triggered": len(triggered),
        "triggered_baseline_winners": sum(r["baseline_winner"] for r in triggered),
        "triggered_baseline_activated": sum(
            r["baseline_trail_activated"] for r in triggered
        ),
        "winner_untouched_pct": round(winner_preservation, 2),
        "activated_untouched_pct": round(activation_preservation, 2),
        "missing_counterfactual_exit_opens": missing_exit_open,
        "baseline_net_pnl_inr": baseline_net,
        "candidate_net_pnl_inr": candidate_net,
        "net_delta_vs_baseline_inr": net_delta,
        "monthly_net_delta_vs_baseline_zero_slippage_inr": monthly_delta_zero,
        "breadth_confirmed_secondary": {
            "trades": len(breadth_confirmed),
            "triggered": sum(r["triggered"] for r in breadth_confirmed),
            "candidate_zero_slippage": _summary([
                float(r["costs"]["0.00"]["net_pnl_inr"])
                for r in breadth_confirmed
            ]),
        },
        "screen": {
            "passed": not failures,
            "failures": failures,
            "contract": PRESERVATION_SCREEN,
        },
    }


def _loss_concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    losses = sorted(
        [-float(r["baseline_net_pnl_inr"]) for r in rows if r["baseline_net_pnl_inr"] < 0],
        reverse=True,
    )
    total = sum(losses)
    if total <= 0.0:
        return {"losing_trades": 0, "gross_loss_inr": 0.0}
    result: dict[str, Any] = {
        "losing_trades": len(losses),
        "gross_loss_inr": round(total, 2),
    }
    for pct in (25, 50, 75):
        threshold = total * pct / 100.0
        running = 0.0
        count = 0
        for value in losses:
            running += value
            count += 1
            if running >= threshold:
                break
        result[f"trades_to_reach_{pct}pct_of_gross_loss"] = count
    return result


def analyze(
    f5_market: dict[str, Any],
    backtest: dict[str, Any],
    breadth_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    backtest_sha256: str,
    breadth_market_sha256: str,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != f5_market_sha256:
        raise ValueError("F5 backtest not bound to supplied market")
    if breadth_market.get("protocol_version") != BREADTH_PROTOCOL_VERSION:
        raise ValueError("breadth market protocol mismatch")
    if breadth_market.get("source_f5_market_sha256") != f5_market_sha256:
        raise ValueError("breadth market not bound to supplied F5 market")

    start = date.fromisoformat(BREADTH_WINDOW["start"])
    end = date.fromisoformat(BREADTH_WINDOW["end"])
    spot_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list(f5_market.get("spot_rows") or []):
        day = date.fromisoformat(str(row["date"]))
        if start <= day <= end:
            spot_by_day[day.isoformat()].append(row)
    regimes = {
        day: _classify_day(day, rows)
        for day, rows in sorted(spot_by_day.items())
    }
    point_rows = list(breadth_market.get("constituent_points_5m") or [])
    breadth = {
        day: _breadth_day(day, point_rows)
        for day in sorted(spot_by_day)
    }

    bars_2m, incomplete = _aggregate_2m(
        list(f5_market.get("option_rows_1m") or [])
    )
    obs_by_day, insufficient = _observations(
        list(f5_market.get("daily_contracts") or []),
        bars_2m,
    )
    open_2m = _bar_open_lookup(bars_2m)

    baseline = list(
        backtest["candidates"][BASELINE_CANDIDATE].get("trades") or []
    )
    baseline_trades_by_entry = {
        str(t["entry_timestamp"]): t for t in baseline
    }

    target_rows: list[dict[str, Any]] = []
    for trade in baseline:
        day = str(trade["date"])
        if not (start <= date.fromisoformat(day) <= end):
            continue
        regime = regimes.get(day, {}).get("regime")
        if regime != "BEARISH" or str(trade["right"]) != "PE":
            continue
        b_status = _breadth_status(regime, breadth.get(day, {}))
        pnl = float(trade["primary_cost_model"]["net_pnl_inr"])
        target_rows.append({
            "date": day,
            "month": str(trade["month"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "baseline_net_pnl_inr": pnl,
            "baseline_winner": pnl > 0.0,
            "trail_activated": bool(trade.get("trail_activated")),
            "breadth_status": b_status,
            "checkpoints": {
                str(minutes): _checkpoint_state(trade, obs_by_day, minutes)
                for minutes in ANATOMY_CHECKPOINTS_MINUTES
            },
        })

    breadth_confirmed = [
        row for row in target_rows if row["breadth_status"] == "CONFIRMED"
    ]
    candidates = {
        name: _candidate_report(
            name,
            target_rows,
            baseline_trades_by_entry,
            open_2m,
        )
        for name in CANDIDATES
    }

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "source_breadth_market_sha256": breadth_market_sha256,
        "quality": {
            "target_bearish_PE_trades": len(target_rows),
            "breadth_confirmed_target_trades": len(breadth_confirmed),
            "breadth_available_not_confirmed_target_trades": sum(
                r["breadth_status"] == "AVAILABLE_NOT_CONFIRMED"
                for r in target_rows
            ),
            "breadth_unavailable_target_trades": sum(
                r["breadth_status"] == "UNAVAILABLE"
                for r in target_rows
            ),
            "complete_2m_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
        },
        "baseline_target": {
            "all_bearish_PE": _summary([
                float(r["baseline_net_pnl_inr"]) for r in target_rows
            ]),
            "breadth_confirmed_bearish_PE": _summary([
                float(r["baseline_net_pnl_inr"]) for r in breadth_confirmed
            ]),
            "loss_concentration": _loss_concentration(target_rows),
        },
        "loss_anatomy": {
            str(minutes): _anatomy_report(target_rows, minutes)
            for minutes in ANATOMY_CHECKPOINTS_MINUTES
        },
        "breadth_confirmed_loss_anatomy": {
            str(minutes): _anatomy_report(breadth_confirmed, minutes)
            for minutes in ANATOMY_CHECKPOINTS_MINUTES
        },
        "candidates": candidates,
        "target_trades": target_rows,
        "decision": "DEVELOPMENT_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze F5 bearish-PE loss-minimization candidates"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument("--breadth-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_bearish_pe_loss_minimization_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    f5_sha = _sha256(args.f5_market)
    backtest_sha = _sha256(args.backtest)
    breadth_sha = _sha256(args.breadth_market)
    report = analyze(
        json.loads(args.f5_market.read_text(encoding="utf-8")),
        json.loads(args.backtest.read_text(encoding="utf-8")),
        json.loads(args.breadth_market.read_text(encoding="utf-8")),
        f5_market_sha256=f5_sha,
        backtest_sha256=backtest_sha,
        breadth_market_sha256=breadth_sha,
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
        "loss_anatomy": report["loss_anatomy"],
        "breadth_confirmed_loss_anatomy": report[
            "breadth_confirmed_loss_anatomy"
        ],
        "candidates": report["candidates"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
