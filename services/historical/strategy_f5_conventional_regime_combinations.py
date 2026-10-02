"""Screen a small frozen set of conventional F5 indicator regimes."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _bar_open_lookup,
    _decorate,
    _observations,
    _one_min_open_lookup,
    _report,
    _trail_trade_simulation,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_broad_entry_indicator_atlas import (
    _indicator_frame,
)
from services.historical.strategy_f5_conventional_regime_combinations_protocol import (
    CANDIDATE_PRIORITY,
    CANDIDATES,
    DEVELOPMENT_SCREEN,
    GUARDRAILS,
    PROTOCOL_VERSION,
    REGIME,
    ROLE,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_BACKTEST_V1"
BASELINE_NAME = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regime_lookup(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in bars_2m:
        grouped[
            (str(row["expiry"]), int(row["strike"]), str(row["right"]))
        ].append(row)

    lookup: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for (expiry, strike, right), rows in grouped.items():
        frame = _indicator_frame(rows)
        frame["atr14_prior20_median"] = (
            frame["atr14_pct"].shift(1).rolling(20, min_periods=20).median()
        )
        frame["bb_prior20_median"] = (
            frame["bb_bandwidth20_pct"]
            .shift(1)
            .rolling(20, min_periods=20)
            .median()
        )

        for _, row in frame.iterrows():
            decision = (
                pd.Timestamp(row["timestamp"]).to_pydatetime()
                + pd.Timedelta(minutes=2)
            )
            atr = row.get("atr14_pct")
            atr_ref = row.get("atr14_prior20_median")
            bb = row.get("bb_bandwidth20_pct")
            bb_ref = row.get("bb_prior20_median")
            stoch = row.get("stoch_d3")
            hist = row.get("macd_hist_pct")

            payload = {
                "atr14_pct": None if pd.isna(atr) else float(atr),
                "atr14_prior20_median": (
                    None if pd.isna(atr_ref) else float(atr_ref)
                ),
                "bb_bandwidth20_pct": None if pd.isna(bb) else float(bb),
                "bb_prior20_median": (
                    None if pd.isna(bb_ref) else float(bb_ref)
                ),
                "stoch_d3": None if pd.isna(stoch) else float(stoch),
                "macd_hist_pct": None if pd.isna(hist) else float(hist),
            }
            payload["ATR_ACTIVE"] = (
                payload["atr14_pct"] is not None
                and payload["atr14_prior20_median"] is not None
                and payload["atr14_pct"] >= payload["atr14_prior20_median"]
            )
            payload["BB_ACTIVE"] = (
                payload["bb_bandwidth20_pct"] is not None
                and payload["bb_prior20_median"] is not None
                and payload["bb_bandwidth20_pct"] >= payload["bb_prior20_median"]
            )
            payload["STOCH_NOT_OVERBOUGHT"] = (
                payload["stoch_d3"] is not None
                and payload["stoch_d3"]
                < float(REGIME["stochastic_not_overbought_max_exclusive"])
            )
            payload["HIST_STRONG"] = (
                payload["macd_hist_pct"] is not None
                and payload["macd_hist_pct"]
                >= float(REGIME["histogram_strength_pct_min"])
            )
            lookup[
                (decision.isoformat(), expiry, strike, right)
            ] = payload
    return lookup


def _passes(
    features: dict[str, Any] | None,
    candidate: str,
) -> bool:
    if features is None:
        return False
    return all(bool(features.get(state)) for state in CANDIDATES[candidate])


def _filtered_observations(
    obs_by_day: dict[str, dict[str, dict[str, Any]]],
    lookup: dict[tuple[str, str, int, str], dict[str, Any]],
    candidate: str,
) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    result = copy.deepcopy(obs_by_day)
    missing_bullish = 0
    for day in result.values():
        for ts, same in day.items():
            for right, obs in same.items():
                if not bool(obs.get("bullish_cross")):
                    continue
                key = (
                    ts,
                    str(obs["expiry"]),
                    int(obs["strike"]),
                    str(right),
                )
                features = lookup.get(key)
                if features is None:
                    missing_bullish += 1
                    obs["bullish_cross"] = False
                    continue
                obs["bullish_cross"] = _passes(features, candidate)
    return result, missing_bullish


def _entry_key(trade: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(trade["entry_timestamp"]),
        str(trade["expiry"]),
        int(trade["strike"]),
        str(trade["right"]),
    )


def _signal_capture(
    baseline_trades: list[dict[str, Any]],
    lookup: dict[tuple[str, str, int, str], dict[str, Any]],
    candidate: str,
) -> dict[str, Any]:
    activated = [t for t in baseline_trades if bool(t.get("trail_activated"))]
    winners = [
        t
        for t in baseline_trades
        if float(t["primary_cost_model"]["net_pnl_inr"]) > 0.0
    ]

    def captured(group: list[dict[str, Any]]) -> tuple[int, float]:
        if not group:
            return 0, 100.0
        n = sum(_passes(lookup.get(_entry_key(t)), candidate) for t in group)
        return n, n / len(group) * 100.0

    act_n, act_pct = captured(activated)
    win_n, win_pct = captured(winners)
    return {
        "baseline_activated_signals": len(activated),
        "captured_baseline_activated_signals": act_n,
        "baseline_activated_signal_capture_pct": round(act_pct, 2),
        "baseline_winner_signals": len(winners),
        "captured_baseline_winner_signals": win_n,
        "baseline_winner_signal_capture_pct": round(win_pct, 2),
    }


def _screen(
    baseline: dict[str, Any],
    candidate_report: dict[str, Any],
    capture: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    c0 = candidate_report["pooled"]["0.00"]
    b0 = baseline["pooled"]["0.00"]

    if int(c0["trades"]) < int(DEVELOPMENT_SCREEN["minimum_trades"]):
        failures.append("trade_count_below_minimum")

    for month in ("2026-07", "2026-08", "2026-09"):
        trades = int(candidate_report["by_month"][month]["0.00"]["trades"])
        if trades < int(DEVELOPMENT_SCREEN["minimum_trades_each_month"]):
            failures.append(f"{month}_trade_count_below_minimum")

    for side, block in (
        ("CE", candidate_report["ce_0_00"]),
        ("PE", candidate_report["pe_0_00"]),
    ):
        if int(block["trades"]) < int(
            DEVELOPMENT_SCREEN["minimum_trades_each_side"]
        ):
            failures.append(f"{side}_trade_count_below_minimum")

    if float(capture["baseline_activated_signal_capture_pct"]) < float(
        DEVELOPMENT_SCREEN["minimum_baseline_activated_signal_capture_pct"]
    ):
        failures.append("activated_signal_capture_below_minimum")

    if float(capture["baseline_winner_signal_capture_pct"]) < float(
        DEVELOPMENT_SCREEN["minimum_baseline_winner_signal_capture_pct"]
    ):
        failures.append("winner_signal_capture_below_minimum")

    if float(c0["trail_activation_rate_pct"]) <= float(
        b0["trail_activation_rate_pct"]
    ):
        failures.append("pooled_activation_rate_not_above_baseline")

    month_activation_wins = 0
    month_net_wins = 0
    month_detail: dict[str, Any] = {}
    for month in ("2026-07", "2026-08", "2026-09"):
        c = candidate_report["by_month"][month]["0.00"]
        b = baseline["by_month"][month]["0.00"]
        act_better = float(c["trail_activation_rate_pct"]) > float(
            b["trail_activation_rate_pct"]
        )
        net_better = float(c["net_pnl_inr"]) > float(b["net_pnl_inr"])
        month_activation_wins += int(act_better)
        month_net_wins += int(net_better)
        month_detail[month] = {
            "candidate_activation_rate_pct": c["trail_activation_rate_pct"],
            "baseline_activation_rate_pct": b["trail_activation_rate_pct"],
            "activation_rate_improved": act_better,
            "candidate_net_pnl_inr": c["net_pnl_inr"],
            "baseline_net_pnl_inr": b["net_pnl_inr"],
            "net_improved": net_better,
        }

    if month_activation_wins < int(
        DEVELOPMENT_SCREEN["minimum_months_activation_rate_above_baseline"]
    ):
        failures.append("too_few_months_with_activation_rate_improvement")

    if month_net_wins < int(
        DEVELOPMENT_SCREEN["minimum_months_zero_slippage_net_above_baseline"]
    ):
        failures.append("too_few_months_with_net_improvement")

    slip_delta: dict[str, float] = {}
    for key in ("0.00", "0.50", "1.00"):
        delta = (
            float(candidate_report["pooled"][key]["net_pnl_inr"])
            - float(baseline["pooled"][key]["net_pnl_inr"])
        )
        slip_delta[key] = round(delta, 2)
        if delta <= 0.0:
            failures.append(f"pooled_net_not_above_baseline_{key}")

    if float(c0["max_drawdown_inr"]) >= float(b0["max_drawdown_inr"]):
        failures.append("max_drawdown_not_below_baseline")

    return {
        "passed": not failures,
        "failures": failures,
        "signal_capture": capture,
        "pooled_net_delta_vs_baseline_inr": slip_delta,
        "months_activation_rate_improved": month_activation_wins,
        "months_zero_slippage_net_improved": month_net_wins,
        "month_detail": month_detail,
        "contract": DEVELOPMENT_SCREEN,
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

    contracts = list(market.get("daily_contracts") or [])
    rows_1m = list(market.get("option_rows_1m") or [])
    bars_2m, incomplete = _aggregate_2m(rows_1m)
    obs, insufficient = _observations(contracts, bars_2m)
    lookup = _regime_lookup(bars_2m)
    open_2m = _bar_open_lookup(bars_2m)
    open_1m = _one_min_open_lookup(rows_1m)

    baseline_trades, baseline_skips = _trail_trade_simulation(
        contracts, obs, open_2m, open_1m
    )
    baseline_trades = _decorate(baseline_trades)
    baseline_report = _report(baseline_trades, baseline_skips)

    supplied_baseline = backtest["candidates"][BASELINE_NAME]
    if int(baseline_report["pooled"]["0.00"]["trades"]) != int(
        supplied_baseline["pooled"]["0.00"]["trades"]
    ):
        raise ValueError("recomputed F5 baseline trade count mismatch")
    if abs(
        float(baseline_report["pooled"]["0.00"]["net_pnl_inr"])
        - float(supplied_baseline["pooled"]["0.00"]["net_pnl_inr"])
    ) > 0.01:
        raise ValueError("recomputed F5 baseline PnL mismatch")

    candidates: dict[str, Any] = {}
    for name in CANDIDATE_PRIORITY:
        filtered, missing_bullish = _filtered_observations(
            obs, lookup, name
        )
        trades, skips = _trail_trade_simulation(
            contracts, filtered, open_2m, open_1m
        )
        trades = _decorate(trades)
        report = _report(trades, skips)
        capture = _signal_capture(
            baseline_trades, lookup, name
        )
        screen = _screen(baseline_report, report, capture)
        candidates[name] = {
            "states": CANDIDATES[name],
            "missing_bullish_feature_rows": missing_bullish,
            "report": report,
            "screen": screen,
        }

    survivors = [
        name
        for name in CANDIDATE_PRIORITY
        if candidates[name]["screen"]["passed"]
    ]
    selected = survivors[0] if survivors else None

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "baseline_protocol_version": F5_PROTOCOL_VERSION,
        "regime_definitions": REGIME,
        "candidate_priority": CANDIDATE_PRIORITY,
        "quality": {
            "selected_sessions": len(contracts),
            "raw_1m_option_rows": len(rows_1m),
            "complete_2m_option_bars": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
            "insufficient_warmup_side_sessions": len(insufficient),
            "regime_feature_rows": len(lookup),
        },
        "baseline": baseline_report,
        "candidates": candidates,
        "survivors": survivors,
        "selected_candidate": selected,
        "decision": (
            "FREEZE_SELECTED_CONVENTIONAL_REGIME_FOR_FRESH_HOLDOUT"
            if selected
            else "NO_CONVENTIONAL_REGIME_COMBINATION_PASSED_DEVELOPMENT_SCREEN"
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Screen frozen conventional indicator combinations for F5"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_conventional_regime_combinations_2026_07_09.json"
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

    compact = {}
    for name in CANDIDATE_PRIORITY:
        block = report["candidates"][name]
        compact[name] = {
            "states": block["states"],
            "pooled": block["report"]["pooled"],
            "by_month": block["report"]["by_month"],
            "ce_0_00": block["report"]["ce_0_00"],
            "pe_0_00": block["report"]["pe_0_00"],
            "screen": block["screen"],
        }

    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": market_sha,
        "source_backtest_sha256": backtest_sha,
        "quality": report["quality"],
        "baseline": report["baseline"]["pooled"],
        "candidates": compact,
        "survivors": report["survivors"],
        "selected_candidate": report["selected_candidate"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
