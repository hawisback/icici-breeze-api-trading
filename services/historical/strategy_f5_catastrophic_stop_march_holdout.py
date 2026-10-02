"""Validate the frozen 27.95% F5 catastrophic stop on March 2026."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _bar_open_lookup,
    _costs,
    _decorate,
    _observations,
    _one_min_open_lookup,
    _trail_trade_simulation,
)
from services.historical.strategy_f5_catastrophic_stop_march_holdout_protocol import (
    CANDIDATE_NAME,
    FROZEN_STOP_DISTANCE_PCT,
    GUARDRAILS,
    PROTOCOL_VERSION,
    ROLE,
    STOP_EXECUTION,
    STRATEGY_ID,
    VALIDATION_GATE,
    WINDOW,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day

RESEARCH_TYPE = "STRATEGY_F5_CATASTROPHIC_STOP_MARCH_HOLDOUT_BACKTEST_V1"
SLIPPAGES = {"0.00": 0.0, "0.50": 0.5, "1.00": 1.0}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry_key(trade: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(trade["entry_timestamp"]),
        str(trade["expiry"]),
        int(trade["strike"]),
        str(trade["right"]),
    )


def _max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _profit_factor(values: list[float]) -> float | str | None:
    wins = sum(value for value in values if value > 0.0)
    losses = -sum(value for value in values if value < 0.0)
    if losses == 0.0:
        return None if wins == 0.0 else "INF"
    return round(wins / losses, 4)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "net_pnl_inr": 0.0,
            "average_net_pnl_inr": None,
            "profit_factor": None,
            "max_drawdown_inr": 0.0,
        }
    wins = sum(value > 0.0 for value in values)
    return {
        "trades": len(values),
        "wins": wins,
        "losses": sum(value < 0.0 for value in values),
        "win_rate_pct": round(wins / len(values) * 100.0, 2),
        "net_pnl_inr": round(sum(values), 2),
        "average_net_pnl_inr": round(sum(values) / len(values), 2),
        "profit_factor": _profit_factor(values),
        "max_drawdown_inr": round(_max_drawdown(values), 2),
    }


def _spot_by_day(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row["date"])].append(row)
    return result


def _regimes(
    contracts: list[dict[str, Any]],
    spot_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_day = _spot_by_day(spot_rows)
    days = sorted({str(row["date"]) for row in contracts})
    return {
        day: _classify_day(day, by_day.get(day, []))
        for day in days
    }


def _contract_1m_rows(
    rows_1m: list[dict[str, Any]],
    trade: dict[str, Any],
) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in rows_1m
            if str(row["date"]) == str(trade["date"])
            and str(row["expiry"]) == str(trade["expiry"])
            and int(row["strike"]) == int(trade["strike"])
            and str(row["right"]) == str(trade["right"])
        ],
        key=lambda row: str(row["timestamp"]),
    )


def _counterfactual_stop(
    trade: dict[str, Any],
    rows_1m: list[dict[str, Any]],
) -> dict[str, Any]:
    entry_dt = datetime.fromisoformat(str(trade["entry_timestamp"]))
    exit_dt = datetime.fromisoformat(str(trade["exit_timestamp"]))
    activation_text = trade.get("trail_activation_timestamp")
    activation_dt = (
        datetime.fromisoformat(str(activation_text))
        if activation_text else None
    )
    boundary = min(exit_dt, activation_dt) if activation_dt else exit_dt

    entry_open = float(trade["entry_open"])
    stop_price = entry_open * (
        1.0 - float(FROZEN_STOP_DISTANCE_PCT) / 100.0
    )
    for row in rows_1m:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        if ts < entry_dt:
            continue
        if ts >= boundary:
            break
        bar_open = float(row["open"])
        bar_low = float(row["low"])
        if bar_open <= stop_price:
            return {
                "stopped": True,
                "trigger_timestamp": ts.isoformat(),
                "trigger_type": "GAP_THROUGH_OPEN",
                "raw_exit_price": bar_open,
                "stop_price": stop_price,
            }
        if bar_low <= stop_price:
            return {
                "stopped": True,
                "trigger_timestamp": ts.isoformat(),
                "trigger_type": "INTRABAR_LOW_TOUCH",
                "raw_exit_price": stop_price,
                "stop_price": stop_price,
            }

    return {
        "stopped": False,
        "trigger_timestamp": None,
        "trigger_type": None,
        "raw_exit_price": float(trade["exit_open"]),
        "stop_price": stop_price,
    }


def _target_trade(
    trade: dict[str, Any],
    regimes: dict[str, dict[str, Any]],
) -> bool:
    return (
        str(trade["right"]) == "PE"
        and regimes.get(str(trade["date"]), {}).get("regime") == "BEARISH"
    )


def _counterfactual_rows(
    baseline_trades: list[dict[str, Any]],
    rows_1m: list[dict[str, Any]],
    regimes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in baseline_trades:
        is_target = _target_trade(trade, regimes)
        cf = (
            _counterfactual_stop(
                trade,
                _contract_1m_rows(rows_1m, trade),
            )
            if is_target
            else {
                "stopped": False,
                "trigger_timestamp": None,
                "trigger_type": None,
                "raw_exit_price": float(trade["exit_open"]),
                "stop_price": None,
            }
        )
        costs = {
            key: _costs(
                float(trade["entry_open"]),
                float(cf["raw_exit_price"]),
                slip,
            )
            for key, slip in SLIPPAGES.items()
        }
        rows.append({
            "entry_key": _entry_key(trade),
            "date": str(trade["date"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "right": str(trade["right"]),
            "expiry": str(trade["expiry"]),
            "strike": int(trade["strike"]),
            "target_bearish_PE": is_target,
            "baseline_winner": (
                float(trade["primary_cost_model"]["net_pnl_inr"]) > 0.0
            ),
            "baseline_trail_activated": bool(trade.get("trail_activated")),
            "baseline_exit_timestamp": str(trade["exit_timestamp"]),
            "baseline_net_pnl_inr": {
                key: float(
                    trade["slippage_sensitivity"][key]["net_pnl_inr"]
                )
                for key in SLIPPAGES
            },
            "stopped": bool(cf["stopped"]),
            "trigger_timestamp": cf["trigger_timestamp"],
            "trigger_type": cf["trigger_type"],
            "stop_price": (
                None
                if cf["stop_price"] is None
                else round(float(cf["stop_price"]), 4)
            ),
            "counterfactual_raw_exit_price": round(
                float(cf["raw_exit_price"]), 4
            ),
            "costs": costs,
        })
    return rows


def _reports(rows: list[dict[str, Any]]) -> dict[str, Any]:
    target = [row for row in rows if row["target_bearish_PE"]]
    full_path: dict[str, Any] = {}
    target_report: dict[str, Any] = {}
    for key in SLIPPAGES:
        full_path[key] = {
            "baseline": _summary([
                float(row["baseline_net_pnl_inr"][key]) for row in rows
            ]),
            "candidate": _summary([
                float(row["costs"][key]["net_pnl_inr"]) for row in rows
            ]),
        }
        target_report[key] = {
            "baseline": _summary([
                float(row["baseline_net_pnl_inr"][key]) for row in target
            ]),
            "candidate": _summary([
                float(row["costs"][key]["net_pnl_inr"]) for row in target
            ]),
        }
    return {
        "full_path": full_path,
        "target_bearish_PE": target_report,
    }


def _gate(
    *,
    selected_sessions: int,
    rows: list[dict[str, Any]],
    reports: dict[str, Any],
) -> dict[str, Any]:
    target = [row for row in rows if row["target_bearish_PE"]]
    stopped = [row for row in target if row["stopped"]]
    winners = [row for row in target if row["baseline_winner"]]
    activated = [
        row for row in target if row["baseline_trail_activated"]
    ]
    winner_preservation = (
        sum(not row["stopped"] for row in winners) / len(winners) * 100.0
        if winners else 100.0
    )
    activation_preservation = (
        sum(not row["stopped"] for row in activated)
        / len(activated) * 100.0
        if activated else 100.0
    )

    coverage = {
        "selected_sessions": (
            selected_sessions >= int(VALIDATION_GATE["minimum_selected_sessions"])
        ),
        "target_bearish_PE_trades": (
            len(target)
            >= int(VALIDATION_GATE["minimum_target_bearish_PE_trades"])
        ),
        "stop_triggers": (
            len(stopped) >= int(VALIDATION_GATE["minimum_stop_triggers"])
        ),
    }
    if not all(coverage.values()):
        return {
            "passed": False,
            "status": "INCONCLUSIVE_COVERAGE",
            "coverage": coverage,
            "target_trades": len(target),
            "stop_triggers": len(stopped),
            "winner_preservation_pct": round(winner_preservation, 2),
            "activation_preservation_pct": round(activation_preservation, 2),
            "effect_checks": None,
            "contract": VALIDATION_GATE,
        }

    effect: dict[str, bool] = {
        "winner_preservation_ge_95": (
            winner_preservation
            >= float(
                VALIDATION_GATE[
                    "minimum_target_winner_preservation_pct"
                ]
            )
        ),
        "activation_preservation_ge_95": (
            activation_preservation
            >= float(
                VALIDATION_GATE[
                    "minimum_target_activation_preservation_pct"
                ]
            )
        ),
    }

    for scope in ("target_bearish_PE", "full_path"):
        for key, label in (
            ("0.00", "0"),
            ("0.50", "0_5"),
            ("1.00", "1"),
        ):
            block = reports[scope][key]
            effect[f"{scope}_net_improvement_{label}"] = (
                float(block["candidate"]["net_pnl_inr"])
                > float(block["baseline"]["net_pnl_inr"])
            )
        zero = reports[scope]["0.00"]
        effect[f"{scope}_max_drawdown_improvement_0"] = (
            float(zero["candidate"]["max_drawdown_inr"])
            < float(zero["baseline"]["max_drawdown_inr"])
        )

    return {
        "passed": all(effect.values()),
        "status": (
            "PASS_FRESH_HOLDOUT"
            if all(effect.values())
            else "FAIL_FRESH_HOLDOUT"
        ),
        "coverage": coverage,
        "target_trades": len(target),
        "stop_triggers": len(stopped),
        "winner_preservation_pct": round(winner_preservation, 2),
        "activation_preservation_pct": round(activation_preservation, 2),
        "effect_checks": effect,
        "contract": VALIDATION_GATE,
    }


def backtest(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("March catastrophic-stop market protocol mismatch")
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("March market must be outcome-unscored")

    contracts = list(payload.get("daily_contracts") or [])
    spot_rows = list(payload.get("spot_rows") or [])
    rows_1m = list(payload.get("option_rows_1m") or [])

    bars_2m, incomplete = _aggregate_2m(rows_1m)
    obs, insufficient = _observations(contracts, bars_2m)
    open_2m = _bar_open_lookup(bars_2m)
    open_1m = _one_min_open_lookup(rows_1m)
    baseline_trades, baseline_skips = _trail_trade_simulation(
        contracts, obs, open_2m, open_1m
    )
    baseline_trades = _decorate(baseline_trades)

    regimes = _regimes(contracts, spot_rows)
    rows = _counterfactual_rows(
        baseline_trades,
        rows_1m,
        regimes,
    )
    reports = _reports(rows)
    gate = _gate(
        selected_sessions=len(contracts),
        rows=rows,
        reports=reports,
    )
    stopped_target = [
        row for row in rows
        if row["target_bearish_PE"] and row["stopped"]
    ]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "candidate_name": CANDIDATE_NAME,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "frozen_stop_distance_pct": FROZEN_STOP_DISTANCE_PCT,
        "stop_execution": STOP_EXECUTION,
        "quality": {
            "selected_sessions": len(contracts),
            "expected_trading_sessions": int(WINDOW["expected_trading_sessions"]),
            "raw_1m_option_rows": len(rows_1m),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
            "baseline_trades": len(baseline_trades),
            "skipped_trade_intents": len(baseline_skips),
            "regime_available_sessions": sum(
                bool(block.get("available")) for block in regimes.values()
            ),
        },
        "regimes": regimes,
        "reports": reports,
        "stopped_target_trades": stopped_target,
        "validation_gate": gate,
        "decision": (
            "F5_CATASTROPHIC_STOP_27_95_MARCH_HOLDOUT_PASSED_REQUIRES_SECOND_VALIDATION"
            if gate["passed"]
            else (
                "F5_CATASTROPHIC_STOP_27_95_MARCH_HOLDOUT_INCONCLUSIVE"
                if gate["status"] == "INCONCLUSIVE_COVERAGE"
                else "F5_CATASTROPHIC_STOP_27_95_MARCH_HOLDOUT_FAILED_DO_NOT_RETUNE"
            )
        ),
        "guardrails": GUARDRAILS,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate frozen 27.95% F5 catastrophic stop on March"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_catastrophic_stop_march_holdout_2026_03.json"
        ),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    report = backtest(
        json.loads(args.market.read_text(encoding="utf-8")),
        source_sha256=source_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "sha256": _sha256(args.output),
        "source_market_sha256": source_sha,
        "quality": report["quality"],
        "reports": report["reports"],
        "stopped_target_trades": report["stopped_target_trades"],
        "validation_gate": report["validation_gate"],
        "decision": report["decision"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
