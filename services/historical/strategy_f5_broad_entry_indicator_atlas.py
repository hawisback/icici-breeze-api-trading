"""Broad entry-time indicator atlas for Strategy F5."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, time
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
from services.historical.strategy_f5_broad_entry_indicator_atlas_protocol import (
    CONTINUOUS_FEATURES,
    GUARDRAILS,
    PROTOCOL_VERSION,
    REPORTING,
    ROLE,
    STRATEGY_ID,
)

RESEARCH_TYPE = "STRATEGY_F5_BROAD_ENTRY_INDICATOR_ATLAS_BACKTEST_V1"
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


def _wilder(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / float(length), adjust=False).mean()


def _rolling_mean_deviation(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).apply(
        lambda x: float(np.mean(np.abs(x - np.mean(x)))),
        raw=True,
    )


def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, length)
    avg_loss = _wilder(loss, length)
    rs = _safe_div(avg_gain, avg_loss)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(avg_loss > 0.0, 100.0)
    rsi = rsi.where(avg_gain > 0.0, 0.0)
    return rsi


def _indicator_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    open_ = frame["open"].astype(float)
    volume = pd.to_numeric(frame.get("volume"), errors="coerce")

    # Existing F5 indicators.
    fast = close.ewm(span=int(MACD["fast_length"]), adjust=False).mean()
    slow = close.ewm(span=int(MACD["slow_length"]), adjust=False).mean()
    macd = fast - slow
    macd_signal = macd.ewm(
        span=int(MACD["signal_length"]), adjust=False
    ).mean()
    hist = macd - macd_signal

    std_len = int(RELATIVE_VOLATILITY_INDEX["stddev_length"])
    rvi_ema_len = int(
        RELATIVE_VOLATILITY_INDEX["directional_ema_length"]
    )
    stddev = close.rolling(std_len, min_periods=std_len).std(
        ddof=int(RELATIVE_VOLATILITY_INDEX["stddev_ddof"])
    )
    change = close.diff()
    upper_input = stddev.where(change > 0.0, 0.0)
    lower_input = stddev.where(change <= 0.0, 0.0)
    upper = upper_input.ewm(span=rvi_ema_len, adjust=False).mean()
    lower = lower_input.ewm(span=rvi_ema_len, adjust=False).mean()
    denom = upper + lower
    rvi = (100.0 * upper / denom).where(
        denom != 0.0,
        float(RELATIVE_VOLATILITY_INDEX["zero_denominator_value"]),
    )

    frame["rvi10"] = rvi
    frame["macd_pct"] = 100.0 * macd / close
    frame["macd_hist_pct"] = 100.0 * hist / close
    frame["macd_hist_delta_pct"] = 100.0 * (hist - hist.shift(1)) / close

    # Momentum / oscillators.
    frame["rsi7"] = _rsi(close, 7)
    frame["rsi14"] = _rsi(close, 14)

    for length, name in ((9, "stoch_k9"), (14, "stoch_k14")):
        hh = high.rolling(length, min_periods=length).max()
        ll = low.rolling(length, min_periods=length).min()
        frame[name] = 100.0 * _safe_div(close - ll, hh - ll)
    frame["stoch_d3"] = frame["stoch_k14"].rolling(
        3, min_periods=3
    ).mean()

    hh14 = high.rolling(14, min_periods=14).max()
    ll14 = low.rolling(14, min_periods=14).min()
    frame["williams_r14"] = -100.0 * _safe_div(hh14 - close, hh14 - ll14)

    typical = (high + low + close) / 3.0
    for length, name in ((10, "cci10"), (20, "cci20")):
        tp_ma = typical.rolling(length, min_periods=length).mean()
        md = _rolling_mean_deviation(typical, length)
        frame[name] = _safe_div(typical - tp_ma, 0.015 * md)

    for length, name in (
        (3, "roc3_pct"),
        (5, "roc5_pct"),
        (10, "roc10_pct"),
    ):
        frame[name] = 100.0 * (close / close.shift(length) - 1.0)

    # Trend.
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    frame["price_to_ema9_pct"] = 100.0 * (close / ema9 - 1.0)
    frame["price_to_ema20_pct"] = 100.0 * (close / ema20 - 1.0)
    frame["ema9_to_ema20_pct"] = 100.0 * (ema9 / ema20 - 1.0)
    frame["ema9_slope3_pct"] = 100.0 * (ema9 / ema9.shift(3) - 1.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0.0),
            up_move,
            0.0,
        ),
        index=frame.index,
        dtype=float,
    )
    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0.0),
            down_move,
            0.0,
        ),
        index=frame.index,
        dtype=float,
    )
    atr14_abs = _wilder(tr, 14)
    plus_di = 100.0 * _safe_div(_wilder(plus_dm, 14), atr14_abs)
    minus_di = 100.0 * _safe_div(_wilder(minus_dm, 14), atr14_abs)
    dx = 100.0 * _safe_div(
        (plus_di - minus_di).abs(),
        plus_di + minus_di,
    )
    frame["plus_di14"] = plus_di
    frame["minus_di14"] = minus_di
    frame["di_spread14"] = plus_di - minus_di
    frame["adx14"] = _wilder(dx, 14)

    # Volatility / range.
    atr5_abs = _wilder(tr, 5)
    frame["atr5_pct"] = 100.0 * atr5_abs / close
    frame["atr14_pct"] = 100.0 * atr14_abs / close
    prior_tr_med10 = tr.shift(1).rolling(10, min_periods=10).median()
    frame["tr_expansion10"] = _safe_div(tr, prior_tr_med10)

    ma20 = close.rolling(20, min_periods=20).mean()
    sd20 = close.rolling(20, min_periods=20).std(ddof=0)
    upper_bb = ma20 + 2.0 * sd20
    lower_bb = ma20 - 2.0 * sd20
    frame["bb_percent_b20"] = _safe_div(close - lower_bb, upper_bb - lower_bb)
    frame["bb_bandwidth20_pct"] = 100.0 * _safe_div(
        upper_bb - lower_bb, ma20
    )

    log_ret = np.log(close / close.shift(1))
    rv5 = log_ret.rolling(5, min_periods=5).std(ddof=0) * 100.0
    rv20 = log_ret.rolling(20, min_periods=20).std(ddof=0) * 100.0
    frame["realized_vol5_pct"] = rv5
    frame["realized_vol20_pct"] = rv20
    frame["realized_vol_ratio_5_20"] = _safe_div(rv5, rv20)

    # Volume / flow.
    prior_vol_med5 = volume.shift(1).rolling(5, min_periods=5).median()
    frame["volume_ratio5"] = _safe_div(volume, prior_vol_med5)
    prior_vol_mean20 = volume.shift(1).rolling(20, min_periods=20).mean()
    prior_vol_sd20 = volume.shift(1).rolling(20, min_periods=20).std(ddof=0)
    frame["volume_z20"] = _safe_div(
        volume - prior_vol_mean20,
        prior_vol_sd20,
    )

    direction = np.sign(close.diff()).fillna(0.0)
    obv = (direction * volume.fillna(0.0)).cumsum()
    vol_scale20 = volume.rolling(20, min_periods=20).mean() * 5.0
    frame["obv_change5_norm"] = _safe_div(obv - obv.shift(5), vol_scale20)

    raw_money = typical * volume
    tp_change = typical.diff()
    pos_money = raw_money.where(tp_change > 0.0, 0.0)
    neg_money = raw_money.where(tp_change < 0.0, 0.0)
    pos14 = pos_money.rolling(14, min_periods=14).sum()
    neg14 = neg_money.rolling(14, min_periods=14).sum()
    money_ratio = _safe_div(pos14, neg14)
    mfi = 100.0 - 100.0 / (1.0 + money_ratio)
    mfi = mfi.where(neg14 > 0.0, 100.0)
    mfi = mfi.where(pos14 > 0.0, 0.0)
    frame["mfi14"] = mfi

    hl_range = high - low
    mf_multiplier = _safe_div(
        (close - low) - (high - close),
        hl_range,
    )
    mf_volume = mf_multiplier * volume
    frame["cmf20"] = _safe_div(
        mf_volume.rolling(20, min_periods=20).sum(),
        volume.rolling(20, min_periods=20).sum(),
    )

    # Session VWAP: current completed signal bar volume is known.
    frame["_session_date"] = frame["timestamp"].dt.date
    pv = typical * volume
    cum_pv = pv.groupby(frame["_session_date"]).cumsum()
    cum_vol = volume.groupby(frame["_session_date"]).cumsum()
    vwap = _safe_div(cum_pv, cum_vol)
    frame["vwap_distance_pct"] = 100.0 * (close / vwap - 1.0)

    # Candle geometry and short returns.
    frame["body_pct"] = 100.0 * (close - open_) / close
    frame["range_pct"] = 100.0 * (high - low) / close
    frame["close_location_value"] = _safe_div(
        (close - low) - (high - close),
        high - low,
    )
    frame["upper_wick_pct"] = 100.0 * (
        high - pd.concat([open_, close], axis=1).max(axis=1)
    ) / close
    frame["lower_wick_pct"] = 100.0 * (
        pd.concat([open_, close], axis=1).min(axis=1) - low
    ) / close
    for length, name in (
        (1, "return1_pct"),
        (2, "return2_pct"),
        (3, "return3_pct"),
        (5, "return5_pct"),
    ):
        frame[name] = 100.0 * (close / close.shift(length) - 1.0)

    session_start = time(9, 15)
    frame["minutes_from_open"] = frame["timestamp"].map(
        lambda ts: (
            ts.hour * 60 + ts.minute
            - (session_start.hour * 60 + session_start.minute)
        )
    ).astype(float)
    expiry = datetime.fromisoformat(str(rows[0]["expiry"])).date()
    frame["days_to_expiry"] = frame["timestamp"].map(
        lambda ts: float((expiry - ts.date()).days)
    )

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
        frame = _indicator_frame(rows)
        for _, row in frame.iterrows():
            signal_start = pd.Timestamp(row["timestamp"]).to_pydatetime()
            entry_time = signal_start + pd.Timedelta(minutes=2)
            payload = {}
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
            lookup[
                (entry_time.isoformat(), expiry, strike, right)
            ] = payload
    return lookup


def _matched_rows(
    trades: list[dict[str, Any]],
    feature_lookup: dict[tuple[str, str, int, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    missing = []
    for i, trade in enumerate(trades, start=1):
        key = (
            str(trade["entry_timestamp"]),
            str(trade["expiry"]),
            int(trade["strike"]),
            str(trade["right"]),
        )
        features = feature_lookup.get(key)
        if features is None:
            missing.append({
                "trade_number": i,
                "entry_timestamp": key[0],
                "expiry": key[1],
                "strike": key[2],
                "right": key[3],
            })
            continue

        net = float(trade["primary_cost_model"]["net_pnl_inr"])
        features = dict(features)
        features["entry_premium"] = float(trade["entry_open"])
        rows.append({
            "trade_number": i,
            "date": str(trade["date"]),
            "month": str(trade["month"]),
            "right": str(trade["right"]),
            "entry_timestamp": str(trade["entry_timestamp"]),
            "baseline_net_pnl_inr": net,
            "bad_trade": (net < 0.0 and not bool(trade.get("trail_activated"))),
            "trail_activated": bool(trade.get("trail_activated")),
            "baseline_winner": net > 0.0,
            **features,
        })
    return rows, missing


def _finite(values: list[Any]) -> np.ndarray:
    out = []
    for value in values:
        if value is None:
            continue
        try:
            x = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x):
            out.append(x)
    return np.asarray(out, dtype=float)


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


def _rank_auc(pos: np.ndarray, neg: np.ndarray) -> float | None:
    if len(pos) == 0 or len(neg) == 0:
        return None
    values = np.concatenate([pos, neg])
    labels = np.concatenate(
        [np.ones(len(pos), dtype=int), np.zeros(len(neg), dtype=int)]
    )
    ranks = pd.Series(values).rank(method="average").to_numpy(dtype=float)
    n_pos = len(pos)
    n_neg = len(neg)
    rank_sum = float(ranks[labels == 1].sum())
    u = rank_sum - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def _smd(pos: np.ndarray, neg: np.ndarray) -> float | None:
    if len(pos) < 2 or len(neg) < 2:
        return None
    pooled = math.sqrt(
        (float(np.var(pos, ddof=1)) + float(np.var(neg, ddof=1))) / 2.0
    )
    if pooled == 0.0:
        return 0.0
    return (float(np.mean(pos)) - float(np.mean(neg))) / pooled


def _label_separation(
    rows: list[dict[str, Any]],
    feature: str,
    label: str,
) -> dict[str, Any]:
    pos = _finite([r[feature] for r in rows if bool(r[label])])
    neg = _finite([r[feature] for r in rows if not bool(r[label])])
    auc = _rank_auc(pos, neg)
    smd = _smd(pos, neg)
    return {
        "positive": _dist(pos.tolist()),
        "negative": _dist(neg.tolist()),
        "rank_auc_higher_value_predicts_positive": (
            None if auc is None else round(float(auc), 4)
        ),
        "standardized_mean_difference_positive_minus_negative": (
            None if smd is None else round(float(smd), 4)
        ),
    }


def _auc_only(
    rows: list[dict[str, Any]],
    feature: str,
    label: str,
) -> float | None:
    return _label_separation(rows, feature, label)[
        "rank_auc_higher_value_predicts_positive"
    ]


def _sign(auc: float | None) -> int:
    if auc is None:
        return 0
    if auc > 0.5:
        return 1
    if auc < 0.5:
        return -1
    return 0


def _feature_report(
    rows: list[dict[str, Any]],
    feature: str,
) -> dict[str, Any]:
    bad = _label_separation(rows, feature, "bad_trade")
    activation = _label_separation(rows, feature, "trail_activated")
    winner = _label_separation(rows, feature, "baseline_winner")

    month_bad = {
        month: _auc_only(
            [r for r in rows if r["month"] == month],
            feature,
            "bad_trade",
        )
        for month in ("2026-07", "2026-08", "2026-09")
    }
    month_activation = {
        month: _auc_only(
            [r for r in rows if r["month"] == month],
            feature,
            "trail_activated",
        )
        for month in ("2026-07", "2026-08", "2026-09")
    }
    side_bad = {
        side: _auc_only(
            [r for r in rows if r["right"] == side],
            feature,
            "bad_trade",
        )
        for side in ("CE", "PE")
    }
    side_activation = {
        side: _auc_only(
            [r for r in rows if r["right"] == side],
            feature,
            "trail_activated",
        )
        for side in ("CE", "PE")
    }

    overall_bad_auc = bad["rank_auc_higher_value_predicts_positive"]
    overall_activation_auc = activation[
        "rank_auc_higher_value_predicts_positive"
    ]
    bad_sign = _sign(overall_bad_auc)
    activation_sign = _sign(overall_activation_auc)

    month_bad_consistency = sum(
        _sign(v) == bad_sign and bad_sign != 0 for v in month_bad.values()
    )
    side_bad_consistency = sum(
        _sign(v) == bad_sign and bad_sign != 0 for v in side_bad.values()
    )
    month_activation_consistency = sum(
        _sign(v) == activation_sign and activation_sign != 0
        for v in month_activation.values()
    )
    side_activation_consistency = sum(
        _sign(v) == activation_sign and activation_sign != 0
        for v in side_activation.values()
    )

    return {
        "feature": feature,
        "missing_count": sum(r[feature] is None for r in rows),
        "bad_trade": bad,
        "trail_activated": activation,
        "baseline_winner": winner,
        "by_month_bad_trade_auc": month_bad,
        "by_month_activation_auc": month_activation,
        "by_side_bad_trade_auc": side_bad,
        "by_side_activation_auc": side_activation,
        "bad_trade_direction_consistency": {
            "months_same_direction_as_overall": month_bad_consistency,
            "months_total": 3,
            "sides_same_direction_as_overall": side_bad_consistency,
            "sides_total": 2,
        },
        "activation_direction_consistency": {
            "months_same_direction_as_overall": month_activation_consistency,
            "months_total": 3,
            "sides_same_direction_as_overall": side_activation_consistency,
            "sides_total": 2,
        },
    }


def _rank(
    reports: dict[str, dict[str, Any]],
    label_key: str,
) -> list[dict[str, Any]]:
    output = []
    for feature, report in reports.items():
        auc = report[label_key]["rank_auc_higher_value_predicts_positive"]
        distance = None if auc is None else abs(float(auc) - 0.5)
        direction = (
            None
            if auc is None
            else ("HIGHER" if float(auc) > 0.5 else "LOWER")
        )
        consistency_key = (
            "bad_trade_direction_consistency"
            if label_key == "bad_trade"
            else "activation_direction_consistency"
        )
        output.append({
            "feature": feature,
            "auc": auc,
            "absolute_auc_distance_from_random": (
                None if distance is None else round(distance, 4)
            ),
            "predictive_direction": direction,
            "month_direction_consistency": report[consistency_key][
                "months_same_direction_as_overall"
            ],
            "side_direction_consistency": report[consistency_key][
                "sides_same_direction_as_overall"
            ],
            "missing_count": report["missing_count"],
        })
    output.sort(
        key=lambda x: (
            -1.0
            if x["absolute_auc_distance_from_random"] is None
            else float(x["absolute_auc_distance_from_random"])
        ),
        reverse=True,
    )
    return output


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
    feature_lookup = _feature_lookup(bars_2m)
    rows, missing = _matched_rows(trades, feature_lookup)
    reports = {
        feature: _feature_report(rows, feature)
        for feature in CONTINUOUS_FEATURES
    }

    bad_count = sum(bool(r["bad_trade"]) for r in rows)
    activation_count = sum(bool(r["trail_activated"]) for r in rows)
    winner_count = sum(bool(r["baseline_winner"]) for r in rows)

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
            "baseline_trades": len(trades),
            "matched_rows": len(rows),
            "missing_rows": len(missing),
            "feature_match_coverage_pct": (
                round(len(rows) / len(trades) * 100.0, 2)
                if trades
                else None
            ),
            "complete_2m_bars_rebuilt": len(bars_2m),
            "incomplete_2m_buckets": len(incomplete),
        },
        "labels": {
            "bad_trade_definition": (
                "zero_slippage_net_pnl_lt_0_AND_trail_not_activated"
            ),
            "bad_trade_count": bad_count,
            "bad_trade_rate_pct": round(
                bad_count / len(rows) * 100.0, 2
            ) if rows else None,
            "trail_activation_count": activation_count,
            "trail_activation_rate_pct": round(
                activation_count / len(rows) * 100.0, 2
            ) if rows else None,
            "baseline_winner_count": winner_count,
            "baseline_win_rate_pct": round(
                winner_count / len(rows) * 100.0, 2
            ) if rows else None,
        },
        "features": reports,
        "ranking_bad_trade": _rank(reports, "bad_trade"),
        "ranking_trail_activation": _rank(reports, "trail_activated"),
        "missing_feature_matches": missing,
        "reporting_contract": REPORTING,
        "guardrails": GUARDRAILS,
        "decision": "BROAD_INDICATOR_ATLAS_COMPLETE_NO_RULE_PROMOTED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Screen broad entry-time indicators against bad F5 trades"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f5_broad_entry_indicator_atlas_2026_07_09.json"),
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
        "labels": report["labels"],
        "top_bad_trade_features": report["ranking_bad_trade"][:15],
        "top_activation_features": report["ranking_trail_activation"][:15],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
