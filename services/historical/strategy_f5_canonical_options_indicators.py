"""Canonical options-indicator diagnostic for Strategy F5."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_broad_entry_indicator_atlas import (
    _feature_report,
    _indicator_frame,
    _rank,
)
from services.historical.strategy_f5_canonical_options_indicators_protocol import (
    CONTINUOUS_FEATURES,
    GUARDRAILS,
    OI_STATES,
    PROTOCOL_VERSION,
    ROLE,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    out = num / den
    return out.where(den.abs() > 1e-12, np.nan)


def _canonical_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = _indicator_frame(rows)
    oi = pd.to_numeric(frame["open_interest"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    close = frame["close"].astype(float)

    prior_oi_med20 = oi.shift(1).rolling(20, min_periods=20).median()
    prior_oi_mean20 = oi.shift(1).rolling(20, min_periods=20).mean()
    prior_oi_sd20 = oi.shift(1).rolling(20, min_periods=20).std(ddof=0)

    frame["oi_to_prior20_median"] = _safe_div(oi, prior_oi_med20)
    frame["oi_change1_pct"] = 100.0 * (oi / oi.shift(1) - 1.0)
    frame["oi_change3_pct"] = 100.0 * (oi / oi.shift(3) - 1.0)
    frame["oi_change5_pct"] = 100.0 * (oi / oi.shift(5) - 1.0)
    frame["oi_z20"] = _safe_div(oi - prior_oi_mean20, prior_oi_sd20)
    frame["volume_to_oi"] = _safe_div(volume, oi)

    price_change = close.diff()
    oi_change = oi.diff()
    states = np.full(len(frame), "FLAT_OR_MISSING", dtype=object)
    valid = price_change.notna() & oi_change.notna()
    states[valid & (price_change > 0.0) & (oi_change > 0.0)] = "LONG_BUILDUP"
    states[valid & (price_change > 0.0) & (oi_change < 0.0)] = "SHORT_COVERING"
    states[valid & (price_change < 0.0) & (oi_change > 0.0)] = "SHORT_BUILDUP"
    states[valid & (price_change < 0.0) & (oi_change < 0.0)] = "LONG_UNWINDING"
    frame["oi_state"] = states
    return frame


def _feature_lookup(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in bars_2m:
        grouped[
            (str(row["expiry"]), int(row["strike"]), str(row["right"]))
        ].append(row)

    lookup: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for (expiry, strike, right), rows in grouped.items():
        frame = _canonical_frame(rows)
        for _, row in frame.iterrows():
            start = pd.Timestamp(row["timestamp"]).to_pydatetime()
            decision = start + pd.Timedelta(minutes=2)
            payload: dict[str, Any] = {}
            for feature in CONTINUOUS_FEATURES:
                if feature == "entry_premium":
                    payload[feature] = None
                    continue
                value = row.get(feature)
                payload[feature] = (
                    None
                    if value is None or pd.isna(value)
                    else float(value)
                )
            payload["oi_state"] = str(row["oi_state"])
            lookup[
                (decision.isoformat(), expiry, strike, right)
            ] = payload
    return lookup


def _matched_rows(
    trades: list[dict[str, Any]],
    lookup: dict[tuple[str, str, int, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for i, trade in enumerate(trades, start=1):
        key = (
            str(trade["entry_timestamp"]),
            str(trade["expiry"]),
            int(trade["strike"]),
            str(trade["right"]),
        )
        features = lookup.get(key)
        if features is None:
            missing.append({
                "trade_number": i,
                "entry_timestamp": key[0],
                "expiry": key[1],
                "strike": key[2],
                "right": key[3],
            })
            continue
        features = dict(features)
        features["entry_premium"] = float(trade["entry_open"])
        net = float(trade["primary_cost_model"]["net_pnl_inr"])
        rows.append({
            "trade_number": i,
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "right": str(trade["right"]),
            "baseline_net_pnl_inr": net,
            "bad_trade": net < 0.0 and not bool(trade.get("trail_activated")),
            "trail_activated": bool(trade.get("trail_activated")),
            "baseline_winner": net > 0.0,
            **features,
        })
    return rows, missing


def _state_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trades": 0,
            "bad_trade_rate_pct": None,
            "trail_activation_rate_pct": None,
            "baseline_win_rate_pct": None,
            "baseline_net_pnl_inr": 0.0,
        }
    n = len(rows)
    return {
        "trades": n,
        "bad_trades": sum(bool(r["bad_trade"]) for r in rows),
        "bad_trade_rate_pct": round(
            sum(bool(r["bad_trade"]) for r in rows) / n * 100.0, 2
        ),
        "trail_activations": sum(bool(r["trail_activated"]) for r in rows),
        "trail_activation_rate_pct": round(
            sum(bool(r["trail_activated"]) for r in rows) / n * 100.0, 2
        ),
        "baseline_winners": sum(bool(r["baseline_winner"]) for r in rows),
        "baseline_win_rate_pct": round(
            sum(bool(r["baseline_winner"]) for r in rows) / n * 100.0, 2
        ),
        "baseline_net_pnl_inr": round(
            sum(float(r["baseline_net_pnl_inr"]) for r in rows), 2
        ),
        "average_baseline_net_pnl_inr": round(
            sum(float(r["baseline_net_pnl_inr"]) for r in rows) / n, 2
        ),
    }


def _oi_state_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for state in OI_STATES:
        selected = [r for r in rows if r["oi_state"] == state]
        report[state] = {
            "overall": _state_stats(selected),
            "by_month": {
                month: _state_stats([
                    r for r in selected if r["month"] == month
                ])
                for month in ("2026-07", "2026-08", "2026-09")
            },
            "by_side": {
                side: _state_stats([
                    r for r in selected if r["right"] == side
                ])
                for side in ("CE", "PE")
            },
        }
    return report


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
        raise ValueError("F5 market artifact must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != market_sha256:
        raise ValueError("F5 backtest is not bound to supplied market artifact")

    trades = list(
        backtest["candidates"][BASELINE_CANDIDATE].get("trades") or []
    )
    bars_2m, incomplete = _aggregate_2m(
        list(market.get("option_rows_1m") or [])
    )
    lookup = _feature_lookup(bars_2m)
    rows, missing = _matched_rows(trades, lookup)
    reports = {
        feature: _feature_report(rows, feature)
        for feature in CONTINUOUS_FEATURES
    }
    oi_states = _oi_state_report(rows)

    oi_available = sum(
        r["oi_change1_pct"] is not None for r in rows
    )

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "role": ROLE,
        "research_only": True,
        "source_market_sha256": market_sha256,
        "source_backtest_sha256": backtest_sha256,
        "quality": {
            "baseline_trades": len(trades),
            "matched_rows": len(rows),
            "missing_rows": len(missing),
            "feature_match_coverage_pct": (
                round(len(rows) / len(trades) * 100.0, 2)
                if trades else None
            ),
            "oi_change1_available_rows": oi_available,
            "oi_change1_availability_pct": (
                round(oi_available / len(rows) * 100.0, 2)
                if rows else None
            ),
            "complete_2m_bars_rebuilt": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
        },
        "continuous_features": reports,
        "ranking_bad_trade": _rank(reports, "bad_trade"),
        "ranking_trail_activation": _rank(reports, "trail_activated"),
        "oi_price_states": oi_states,
        "notes": {
            "iv_greeks": (
                "Not evaluated: current artifact does not contain IV or Greeks. "
                "No model-imputed IV/Greeks were introduced."
            ),
        },
        "missing_feature_matches": missing,
        "guardrails": GUARDRAILS,
        "decision": "CANONICAL_OPTIONS_INDICATOR_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose canonical options indicators and OI states for F5"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_canonical_options_indicators_2026_07_09.json"
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
        "quality": report["quality"],
        "top_bad_trade_features": report["ranking_bad_trade"][:10],
        "top_activation_features": report["ranking_trail_activation"][:10],
        "oi_price_states": {
            state: block["overall"]
            for state, block in report["oi_price_states"].items()
        },
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
