"""Score the frozen F5 27.95% catastrophic stop after Q4 completes."""
from __future__ import annotations

import argparse
import hashlib
import json
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
from services.historical.strategy_f5_catastrophic_stop_forward_holdout_protocol import (
    FROZEN_STOP_DISTANCE_PCT,
    GUARDRAILS,
    PROTOCOL_VERSION,
    ROLE,
    SOURCE_MARKET_PROTOCOL_VERSION,
    STOP_EXECUTION,
    STRATEGY_ID,
    VALIDATION_GATE,
    WINDOW,
)
from services.historical.strategy_f5_dominant_nifty_regime import _classify_day

RESEARCH_TYPE = "STRATEGY_F5_CATASTROPHIC_STOP_FORWARD_HOLDOUT_BACKTEST_V1"
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
            "net_pnl_inr": 0.0,
            "max_drawdown_inr": 0.0,
        }
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(values),
        "wins": sum(value > 0.0 for value in values),
        "losses": sum(value < 0.0 for value in values),
        "net_pnl_inr": round(sum(values), 2),
        "max_drawdown_inr": round(max_dd, 2),
    }


def _contract_rows(
    rows_1m: list[dict[str, Any]],
    trade: dict[str, Any],
) -> list[dict[str, Any]]:
    return sorted(
        [
            row for row in rows_1m
            if str(row["date"]) == str(trade["date"])
            and str(row["expiry"]) == str(trade["expiry"])
            and int(row["strike"]) == int(trade["strike"])
            and str(row["right"]) == str(trade["right"])
        ],
        key=lambda row: str(row["timestamp"]),
    )


def _stop(
    trade: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    entry = datetime.fromisoformat(str(trade["entry_timestamp"]))
    exit_dt = datetime.fromisoformat(str(trade["exit_timestamp"]))
    activation_text = trade.get("trail_activation_timestamp")
    activation = (
        datetime.fromisoformat(str(activation_text))
        if activation_text else None
    )
    boundary = min(exit_dt, activation) if activation else exit_dt
    stop_price = float(trade["entry_open"]) * (
        1.0 - FROZEN_STOP_DISTANCE_PCT / 100.0
    )
    for row in rows:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        if ts < entry:
            continue
        if ts >= boundary:
            break
        bar_open = float(row["open"])
        if bar_open <= stop_price:
            return {
                "stopped": True,
                "timestamp": ts.isoformat(),
                "raw_exit_price": bar_open,
                "trigger_type": "GAP_THROUGH_OPEN",
            }
        if float(row["low"]) <= stop_price:
            return {
                "stopped": True,
                "timestamp": ts.isoformat(),
                "raw_exit_price": stop_price,
                "trigger_type": "INTRABAR_LOW_TOUCH",
            }
    return {
        "stopped": False,
        "timestamp": None,
        "raw_exit_price": float(trade["exit_open"]),
        "trigger_type": None,
    }


def _regimes(
    contracts: list[dict[str, Any]],
    spot_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spot_rows:
        by_day[str(row["date"])].append(row)
    return {
        str(contract["date"]): _classify_day(
            str(contract["date"]),
            by_day.get(str(contract["date"]), []),
        )
        for contract in contracts
    }


def analyze(
    market: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if market.get("protocol_version") != SOURCE_MARKET_PROTOCOL_VERSION:
        raise ValueError("forward market protocol mismatch")
    if market.get("strategy_outcomes_scored") is not False:
        raise ValueError("forward market must be outcome-unscored")

    contracts = list(market.get("daily_contracts") or [])
    dates = sorted({str(row["date"]) for row in contracts})
    complete = (
        bool(dates)
        and dates[-1] == str(WINDOW["final_required_session"])
        and len(dates) >= int(WINDOW["expected_target_sessions"])
    )
    if not complete:
        return {
            "research_type": RESEARCH_TYPE,
            "protocol_version": PROTOCOL_VERSION,
            "strategy_id": STRATEGY_ID,
            "role": ROLE,
            "research_only": True,
            "source_market_sha256": source_sha256,
            "quality": {
                "selected_sessions": len(dates),
                "last_selected_session": dates[-1] if dates else None,
            },
            "decision": "HOLDOUT_NOT_COMPLETE_NO_OUTCOMES_SCORED",
            "guardrails": GUARDRAILS,
        }

    rows_1m = list(market.get("option_rows_1m") or [])
    bars_2m, incomplete = _aggregate_2m(rows_1m)
    obs, insufficient = _observations(contracts, bars_2m)
    open_2m = _bar_open_lookup(bars_2m)
    open_1m = _one_min_open_lookup(rows_1m)
    trades, skips = _trail_trade_simulation(
        contracts, obs, open_2m, open_1m
    )
    trades = _decorate(trades)
    regimes = _regimes(contracts, list(market.get("spot_rows") or []))

    rows: list[dict[str, Any]] = []
    for trade in trades:
        target = (
            str(trade["right"]) == "PE"
            and regimes.get(str(trade["date"]), {}).get("regime") == "BEARISH"
        )
        cf = (
            _stop(trade, _contract_rows(rows_1m, trade))
            if target
            else {
                "stopped": False,
                "timestamp": None,
                "raw_exit_price": float(trade["exit_open"]),
                "trigger_type": None,
            }
        )
        rows.append({
            "date": str(trade["date"]),
            "target_bearish_PE": target,
            "baseline_winner": (
                float(trade["primary_cost_model"]["net_pnl_inr"]) > 0.0
            ),
            "baseline_trail_activated": bool(trade.get("trail_activated")),
            "stopped": bool(cf["stopped"]),
            "trigger_timestamp": cf["timestamp"],
            "trigger_type": cf["trigger_type"],
            "baseline": {
                key: float(trade["slippage_sensitivity"][key]["net_pnl_inr"])
                for key in SLIPPAGES
            },
            "candidate": {
                key: float(
                    _costs(
                        float(trade["entry_open"]),
                        float(cf["raw_exit_price"]),
                        slip,
                    )["net_pnl_inr"]
                )
                for key, slip in SLIPPAGES.items()
            },
        })

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

    reports: dict[str, Any] = {"target_bearish_PE": {}, "full_path": {}}
    for scope, group in (
        ("target_bearish_PE", target),
        ("full_path", rows),
    ):
        for key in SLIPPAGES:
            reports[scope][key] = {
                "baseline": _summary([float(row["baseline"][key]) for row in group]),
                "candidate": _summary([float(row["candidate"][key]) for row in group]),
            }

    coverage = {
        "target_trades": (
            len(target)
            >= int(VALIDATION_GATE["minimum_target_bearish_PE_trades"])
        ),
        "stop_triggers": (
            len(stopped) >= int(VALIDATION_GATE["minimum_stop_triggers"])
        ),
    }
    if not all(coverage.values()):
        gate = {
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
    else:
        effect = {
            "winner_preservation_ge_95": winner_preservation >= 95.0,
            "activation_preservation_ge_95": activation_preservation >= 95.0,
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
            effect[f"{scope}_max_drawdown_not_worse_0"] = (
                float(zero["candidate"]["max_drawdown_inr"])
                <= float(zero["baseline"]["max_drawdown_inr"])
            )
        gate = {
            "passed": all(effect.values()),
            "status": (
                "PASS_FRESH_FORWARD_HOLDOUT"
                if all(effect.values())
                else "FAIL_FRESH_FORWARD_HOLDOUT"
            ),
            "coverage": coverage,
            "target_trades": len(target),
            "stop_triggers": len(stopped),
            "winner_preservation_pct": round(winner_preservation, 2),
            "activation_preservation_pct": round(activation_preservation, 2),
            "effect_checks": effect,
            "contract": VALIDATION_GATE,
        }

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "frozen_stop_distance_pct": FROZEN_STOP_DISTANCE_PCT,
        "stop_execution": STOP_EXECUTION,
        "quality": {
            "selected_sessions": len(dates),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
            "baseline_trades": len(trades),
            "skipped_trade_intents": len(skips),
        },
        "reports": reports,
        "stopped_target_trades": stopped,
        "validation_gate": gate,
        "decision": (
            "F5_CATASTROPHIC_STOP_27_95_FORWARD_HOLDOUT_PASSED"
            if gate["passed"]
            else (
                "F5_CATASTROPHIC_STOP_27_95_FORWARD_HOLDOUT_INCONCLUSIVE"
                if gate["status"] == "INCONCLUSIVE_COVERAGE"
                else "F5_CATASTROPHIC_STOP_27_95_FORWARD_HOLDOUT_FAILED_DO_NOT_RETUNE"
            )
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score frozen 27.95% F5 catastrophic stop after Q4"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_catastrophic_stop_forward_holdout_2026_q4.json"
        ),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    report = analyze(
        json.loads(args.market.read_text(encoding="utf-8")),
        source_sha256=source_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
