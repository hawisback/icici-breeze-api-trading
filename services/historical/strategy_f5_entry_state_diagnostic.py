"""Diagnose entry-time features for Strategy F5 without changing trade management."""
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
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    MACD,
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
    RELATIVE_VOLATILITY_INDEX,
)
from services.historical.strategy_f5_entry_state_diagnostic_protocol import (
    CONTINUOUS_FEATURES,
    GUARDRAILS,
    NATURAL_STATES,
    PRIMARY_LABEL,
    PROTOCOL_VERSION,
    REPORTING,
    ROLE,
    SECONDARY_LABEL,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_ENTRY_STATE_DIAGNOSTIC_BACKTEST_V1"
BASELINE_CANDIDATE = "TRAIL10_CLOSE_CONFIRMED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _feature_frames(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, int, str], pd.DataFrame]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in bars_2m:
        grouped[
            (str(row["expiry"]), int(row["strike"]), str(row["right"]))
        ].append(row)

    result: dict[tuple[str, int, str], pd.DataFrame] = {}
    std_len = int(RELATIVE_VOLATILITY_INDEX["stddev_length"])
    ema_len = int(RELATIVE_VOLATILITY_INDEX["directional_ema_length"])
    ddof = int(RELATIVE_VOLATILITY_INDEX["stddev_ddof"])

    for key, rows in grouped.items():
        frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        close = frame["close"].astype(float)

        fast = close.ewm(span=int(MACD["fast_length"]), adjust=False).mean()
        slow = close.ewm(span=int(MACD["slow_length"]), adjust=False).mean()
        macd = fast - slow
        signal = macd.ewm(span=int(MACD["signal_length"]), adjust=False).mean()
        hist = macd - signal
        hist_delta = hist - hist.shift(1)
        hist_accel = hist_delta - hist_delta.shift(1)

        stddev = close.rolling(std_len, min_periods=std_len).std(ddof=ddof)
        change = close.diff()
        upper_input = stddev.where(change > 0.0, 0.0)
        lower_input = stddev.where(change <= 0.0, 0.0)
        upper = upper_input.ewm(span=ema_len, adjust=False).mean()
        lower = lower_input.ewm(span=ema_len, adjust=False).mean()
        denom = upper + lower
        neutral = float(RELATIVE_VOLATILITY_INDEX["zero_denominator_value"])
        rvi = (100.0 * upper / denom).where(denom != 0.0, neutral)

        frame["rvi_level"] = rvi
        frame["rvi_delta_1"] = rvi - rvi.shift(1)
        frame["macd_hist_pct"] = 100.0 * hist / close
        frame["macd_hist_delta_pct"] = 100.0 * hist_delta / close
        frame["macd_hist_acceleration_pct"] = 100.0 * hist_accel / close
        frame["macd_pct"] = 100.0 * macd / close
        frame["return_1bar_pct"] = 100.0 * (close / close.shift(1) - 1.0)
        frame["return_2bar_pct"] = 100.0 * (close / close.shift(2) - 1.0)
        frame["return_3bar_pct"] = 100.0 * (close / close.shift(3) - 1.0)

        volume = pd.to_numeric(frame.get("volume"), errors="coerce")
        prior5_med = volume.shift(1).rolling(5, min_periods=5).median()
        ratio = volume / prior5_med
        ratio = ratio.where(prior5_med > 0.0, np.nan)
        frame["volume_ratio_5"] = ratio

        result[key] = frame

    return result


def _feature_lookup(
    bars_2m: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    frames = _feature_frames(bars_2m)
    lookup: dict[tuple[str, str, int, str], dict[str, Any]] = {}

    for (expiry, strike, right), frame in frames.items():
        for _, row in frame.iterrows():
            start = pd.Timestamp(row["timestamp"]).to_pydatetime()
            decision = start + timedelta(minutes=2)
            close = float(row["close"])
            payload = {
                feature: (
                    None
                    if pd.isna(row[feature])
                    else float(row[feature])
                )
                for feature in CONTINUOUS_FEATURES
            }
            payload.update({
                "option_close": close,
                "RVI_RISING": (
                    payload["rvi_delta_1"] is not None
                    and payload["rvi_delta_1"] > 0.0
                ),
                "MACD_HIST_ACCELERATING": (
                    payload["macd_hist_acceleration_pct"] is not None
                    and payload["macd_hist_acceleration_pct"] > 0.0
                ),
                "MACD_ABOVE_ZERO": (
                    payload["macd_pct"] is not None
                    and payload["macd_pct"] > 0.0
                ),
                "MOMENTUM_3BAR_POSITIVE": (
                    payload["return_3bar_pct"] is not None
                    and payload["return_3bar_pct"] > 0.0
                ),
                "VOLUME_EXPANDING": (
                    None
                    if payload["volume_ratio_5"] is None
                    else payload["volume_ratio_5"] > 1.0
                ),
            })
            payload["RVI_RISING_AND_HIST_ACCELERATING"] = (
                bool(payload["RVI_RISING"])
                and bool(payload["MACD_HIST_ACCELERATING"])
            )
            payload["RVI_RISING_AND_MOMENTUM_3BAR_POSITIVE"] = (
                bool(payload["RVI_RISING"])
                and bool(payload["MOMENTUM_3BAR_POSITIVE"])
            )
            payload["HIST_ACCELERATING_AND_MOMENTUM_3BAR_POSITIVE"] = (
                bool(payload["MACD_HIST_ACCELERATING"])
                and bool(payload["MOMENTUM_3BAR_POSITIVE"])
            )

            lookup[
                (decision.isoformat(), expiry, strike, right)
            ] = payload

    return lookup


def _matched_rows(
    trades: list[dict[str, Any]],
    features: dict[tuple[str, str, int, str], dict[str, Any]],
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
        feat = features.get(key)
        if feat is None:
            missing.append({
                "trade_number": i,
                "date": str(trade["date"]),
                "entry_timestamp": str(trade["entry_timestamp"]),
                "expiry": str(trade["expiry"]),
                "strike": int(trade["strike"]),
                "right": str(trade["right"]),
            })
            continue

        primary_net = float(trade["primary_cost_model"]["net_pnl_inr"])
        rows.append({
            "trade_number": i,
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "right": str(trade["right"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "baseline_net_pnl_inr": primary_net,
            "trail_activated": bool(trade.get("trail_activated")),
            "baseline_winner": primary_net > 0.0,
            **feat,
        })

    return rows, missing


def _finite(values: list[Any]) -> np.ndarray:
    clean = []
    for value in values:
        if value is None:
            continue
        x = float(value)
        if math.isfinite(x):
            clean.append(x)
    return np.asarray(clean, dtype=float)


def _dist(values: list[Any]) -> dict[str, Any]:
    arr = _finite(values)
    if len(arr) == 0:
        return {
            "n": 0,
            "mean": None,
            "std": None,
            "p25": None,
            "median": None,
            "p75": None,
        }
    return {
        "n": int(len(arr)),
        "mean": round(float(np.mean(arr)), 6),
        "std": round(float(np.std(arr, ddof=1)), 6) if len(arr) > 1 else 0.0,
        "p25": round(float(np.quantile(arr, 0.25)), 6),
        "median": round(float(np.quantile(arr, 0.50)), 6),
        "p75": round(float(np.quantile(arr, 0.75)), 6),
    }


def _smd(pos: np.ndarray, neg: np.ndarray) -> float | None:
    if len(pos) < 2 or len(neg) < 2:
        return None
    var_pos = float(np.var(pos, ddof=1))
    var_neg = float(np.var(neg, ddof=1))
    pooled = math.sqrt((var_pos + var_neg) / 2.0)
    if pooled == 0.0:
        return 0.0
    return (float(np.mean(pos)) - float(np.mean(neg))) / pooled


def _rank_auc(pos: np.ndarray, neg: np.ndarray) -> float | None:
    if len(pos) == 0 or len(neg) == 0:
        return None
    values = np.concatenate([pos, neg])
    labels = np.concatenate([
        np.ones(len(pos), dtype=int),
        np.zeros(len(neg), dtype=int),
    ])
    ranks = pd.Series(values).rank(method="average").to_numpy(dtype=float)
    rank_sum_pos = float(ranks[labels == 1].sum())
    n_pos = len(pos)
    n_neg = len(neg)
    u = rank_sum_pos - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def _continuous_separation(
    rows: list[dict[str, Any]],
    feature: str,
) -> dict[str, Any]:
    pos = _finite([
        r[feature] for r in rows if bool(r["trail_activated"])
    ])
    neg = _finite([
        r[feature] for r in rows if not bool(r["trail_activated"])
    ])
    smd = _smd(pos, neg)
    auc = _rank_auc(pos, neg)
    return {
        "feature": feature,
        "activated": _dist(pos.tolist()),
        "not_activated": _dist(neg.tolist()),
        "median_difference_activated_minus_not": (
            round(float(np.median(pos) - np.median(neg)), 6)
            if len(pos) and len(neg)
            else None
        ),
        "standardized_mean_difference": (
            None if smd is None else round(float(smd), 4)
        ),
        "rank_auc_higher_value_predicts_activation": (
            None if auc is None else round(float(auc), 4)
        ),
        "missing_count": sum(r[feature] is None for r in rows),
    }


def _natural_state_report(
    rows: list[dict[str, Any]],
    state: str,
) -> dict[str, Any]:
    eligible = [r for r in rows if r.get(state) is not None]
    selected = [r for r in eligible if bool(r[state])]
    complement = [r for r in eligible if not bool(r[state])]

    def stats(group: list[dict[str, Any]]) -> dict[str, Any]:
        if not group:
            return {
                "trades": 0,
                "trail_activation_rate_pct": None,
                "baseline_win_rate_pct": None,
                "baseline_net_pnl_inr": 0.0,
            }
        return {
            "trades": len(group),
            "trail_activations": sum(
                bool(r["trail_activated"]) for r in group
            ),
            "trail_activation_rate_pct": round(
                sum(bool(r["trail_activated"]) for r in group)
                / len(group)
                * 100.0,
                2,
            ),
            "baseline_winners": sum(
                bool(r["baseline_winner"]) for r in group
            ),
            "baseline_win_rate_pct": round(
                sum(bool(r["baseline_winner"]) for r in group)
                / len(group)
                * 100.0,
                2,
            ),
            "baseline_net_pnl_inr": round(
                sum(float(r["baseline_net_pnl_inr"]) for r in group),
                2,
            ),
            "average_baseline_net_pnl_inr": round(
                sum(float(r["baseline_net_pnl_inr"]) for r in group)
                / len(group),
                2,
            ),
        }

    selected_stats = stats(selected)
    complement_stats = stats(complement)
    all_stats = stats(eligible)

    sel_rate = selected_stats["trail_activation_rate_pct"]
    all_rate = all_stats["trail_activation_rate_pct"]
    return {
        "state": state,
        "available_trades": len(eligible),
        "missing_trades": len(rows) - len(eligible),
        "selected": selected_stats,
        "not_selected": complement_stats,
        "all_available": all_stats,
        "activation_rate_lift_vs_all": (
            round(float(sel_rate) / float(all_rate), 4)
            if sel_rate is not None and all_rate not in (None, 0.0)
            else None
        ),
    }


def _slice_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "trades": len(rows),
        "trail_activations": sum(bool(r["trail_activated"]) for r in rows),
        "trail_activation_rate_pct": (
            round(
                sum(bool(r["trail_activated"]) for r in rows)
                / len(rows)
                * 100.0,
                2,
            )
            if rows
            else None
        ),
        "baseline_winners": sum(bool(r["baseline_winner"]) for r in rows),
        "baseline_win_rate_pct": (
            round(
                sum(bool(r["baseline_winner"]) for r in rows)
                / len(rows)
                * 100.0,
                2,
            )
            if rows
            else None
        ),
        "baseline_net_pnl_inr": round(
            sum(float(r["baseline_net_pnl_inr"]) for r in rows),
            2,
        ),
        "continuous": {
            feature: _continuous_separation(rows, feature)
            for feature in CONTINUOUS_FEATURES
        },
        "natural_states": {
            state: _natural_state_report(rows, state)
            for state in NATURAL_STATES
        },
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
        raise ValueError("F5 market artifact must be outcome-unscored")
    if backtest.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 backtest protocol mismatch")
    if backtest.get("source_market_sha256") != market_sha256:
        raise ValueError("F5 backtest is not bound to supplied market artifact")

    trades = list(
        backtest.get("candidates", {})
        .get(BASELINE_CANDIDATE, {})
        .get("trades")
        or []
    )
    bars_2m, incomplete = _aggregate_2m(
        list(market.get("option_rows_1m") or [])
    )
    lookup = _feature_lookup(bars_2m)
    rows, missing = _matched_rows(trades, lookup)

    month_reports = {
        month: _slice_report([r for r in rows if r["month"] == month])
        for month in ("2026-07", "2026-08", "2026-09")
    }
    side_reports = {
        side: _slice_report([r for r in rows if r["right"] == side])
        for side in ("CE", "PE")
    }

    ranking = []
    for feature in CONTINUOUS_FEATURES:
        item = _continuous_separation(rows, feature)
        auc = item["rank_auc_higher_value_predicts_activation"]
        if auc is None:
            separation = None
        else:
            separation = abs(float(auc) - 0.5)
        ranking.append({
            "feature": feature,
            "rank_auc": auc,
            "absolute_auc_distance_from_random": (
                None if separation is None else round(separation, 4)
            ),
            "standardized_mean_difference": item[
                "standardized_mean_difference"
            ],
        })
    ranking.sort(
        key=lambda x: (
            -1.0
            if x["absolute_auc_distance_from_random"] is None
            else float(x["absolute_auc_distance_from_random"])
        ),
        reverse=True,
    )

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
        "labels": {
            "primary": PRIMARY_LABEL,
            "secondary": SECONDARY_LABEL,
        },
        "quality": {
            "baseline_trades": len(trades),
            "matched_feature_rows": len(rows),
            "missing_feature_rows": len(missing),
            "feature_match_coverage_pct": (
                round(len(rows) / len(trades) * 100.0, 2)
                if trades
                else None
            ),
            "complete_2m_bars_rebuilt": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
        },
        "overall": _slice_report(rows),
        "by_month": month_reports,
        "by_side": side_reports,
        "continuous_feature_ranking_by_activation_auc_distance": ranking,
        "matched_rows": rows,
        "missing_feature_matches": missing,
        "reporting_contract": REPORTING,
        "guardrails": GUARDRAILS,
        "decision": (
            "ENTRY_STATE_DIAGNOSTIC_COMPLETE_NO_RULE_PROMOTED"
            if rows
            else "ENTRY_STATE_DIAGNOSTIC_NO_MATCHED_ROWS"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose F5 entry-time features associated with +10% activation"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f5_entry_state_diagnostic_2026_07_09.json"),
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

    compact_states = {
        state: report["overall"]["natural_states"][state]
        for state in NATURAL_STATES
    }
    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": market_sha,
        "source_backtest_sha256": backtest_sha,
        "quality": report["quality"],
        "overall_activation_rate_pct": report["overall"][
            "trail_activation_rate_pct"
        ],
        "continuous_feature_ranking": report[
            "continuous_feature_ranking_by_activation_auc_distance"
        ],
        "natural_states": compact_states,
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
