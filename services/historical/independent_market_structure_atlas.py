"""Analyze the frozen Breeze NIFTY futures market-structure atlas."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_market_structure_atlas_protocol import (
    BOOTSTRAP,
    CHRONOLOGICAL_ROBUSTNESS,
    CORPUS_ROLE,
    DISCOVERY_LABELS,
    EXCLUDED_ALREADY_STUDIED_HYPOTHESES,
    GUARDRAILS,
    PATTERN_FAMILIES,
    PROTOCOL_VERSION,
    WINDOW,
)

RESEARCH_TYPE = "NIFTY_BREEZE_MARKET_STRUCTURE_ATLAS_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) != len(y) or len(x) < 3:
        raise ValueError("Spearman inputs must have equal length >= 3")
    rx = pd.Series(x).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(y).rank(method="average").to_numpy(dtype=float)
    if float(np.std(rx)) == 0.0 or float(np.std(ry)) == 0.0:
        raise ValueError("Spearman input has zero rank variance")
    return float(np.corrcoef(rx, ry)[0, 1])


def _chronological_blocks(days: list[str], blocks: int = 6) -> dict[str, list[str]]:
    ordered = sorted(days)
    base, remainder = divmod(len(ordered), blocks)
    sizes = [base + (1 if i < remainder else 0) for i in range(blocks)]
    result: dict[str, list[str]] = {}
    cursor = 0
    for i, size in enumerate(sizes, start=1):
        result[f"block{i}"] = ordered[cursor : cursor + size]
        cursor += size
    return result


def _block_map(blocks: dict[str, list[str]]) -> dict[str, str]:
    return {day: block for block, days in blocks.items() for day in days}


def _read_breeze_rows(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        raise FileNotFoundError(f"historical database not found: {db_path}")
    with sqlite3.connect(str(db_path)) as conn:
        frame = pd.read_sql_query(
            """
            SELECT instrument_id, interval, start_time,
                   open, high, low, close, volume, open_interest, source
            FROM historical_candles
            WHERE interval = '5m'
              AND source = 'BREEZE'
              AND instrument_id LIKE 'INST-NIFTY-FUT-%'
            ORDER BY start_time ASC
            """,
            conn,
        )
    if frame.empty:
        raise ValueError("no Breeze NIFTY futures 5m rows found")
    frame["timestamp"] = pd.to_datetime(
        frame["start_time"], utc=True, errors="raise"
    ).dt.tz_convert("Asia/Kolkata")
    frame["date"] = frame["timestamp"].dt.date.astype(str)
    frame = frame.loc[
        (frame["date"] >= WINDOW["start"])
        & (frame["date"] <= WINDOW["end"])
        & frame["timestamp"].dt.time.between(
            time.fromisoformat(WINDOW["session_start"]),
            time.fromisoformat(WINDOW["session_last_bar"]),
            inclusive="both",
        )
    ].copy()
    return frame


def _expiry_from_instrument(instrument_id: str) -> str:
    prefix = "INST-NIFTY-FUT-"
    if not instrument_id.startswith(prefix):
        raise ValueError(f"unexpected futures instrument id {instrument_id}")
    return instrument_id[len(prefix) :]


def _validate_and_prepare_sessions(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    accepted_bars: list[pd.DataFrame] = []
    session_rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for day, group in frame.groupby("date", sort=True):
        group = group.sort_values("timestamp").copy()
        reasons: list[str] = []
        if len(group) != int(WINDOW["bars_per_session"]):
            reasons.append(f"bar_count_{len(group)}")
        if group["timestamp"].duplicated().any():
            reasons.append("duplicate_timestamp")
        instrument_ids = sorted(set(group["instrument_id"].astype(str)))
        if len(instrument_ids) != 1:
            reasons.append("multiple_contracts")

        for col in ("open", "high", "low", "close"):
            group[col] = pd.to_numeric(group[col], errors="coerce")
        values = group[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if len(group) and not np.isfinite(values).all():
            reasons.append("nonfinite_ohlc")
        elif len(group) and not (values > 0.0).all():
            reasons.append("nonpositive_ohlc")
        elif len(group) and not (
            (group["low"] <= group["open"])
            & (group["open"] <= group["high"])
            & (group["low"] <= group["close"])
            & (group["close"] <= group["high"])
        ).all():
            reasons.append("invalid_ohlc")

        expected = pd.date_range(
            f"{day} {WINDOW['session_start']}",
            periods=int(WINDOW["bars_per_session"]),
            freq="5min",
            tz="Asia/Kolkata",
        )
        if len(group) == len(expected):
            actual = pd.DatetimeIndex(group["timestamp"])
            if not actual.equals(expected):
                reasons.append("timestamp_shape")

        if reasons:
            rejected.append({"date": str(day), "reasons": sorted(set(reasons))})
            continue

        instrument_id = instrument_ids[0]
        expiry = _expiry_from_instrument(instrument_id)
        expiry_date = pd.Timestamp(expiry).date()
        session_date = pd.Timestamp(day).date()
        dte = (expiry_date - session_date).days
        if dte < 0:
            rejected.append({"date": str(day), "reasons": ["negative_dte"]})
            continue

        session_open = float(group.iloc[0]["open"])
        session_close = float(group.iloc[-1]["close"])
        session_high = float(group["high"].max())
        session_low = float(group["low"].min())
        session_range_bps = (session_high - session_low) / session_open * 10000.0

        first6 = group.iloc[:6]
        first30_range = (
            float(first6["high"].max()) - float(first6["low"].min())
        ) / session_open * 10000.0

        remaining = group.iloc[6:]
        remaining_range = (
            float(remaining["high"].max()) - float(remaining["low"].min())
        ) / session_open * 10000.0

        group["bar_time"] = group["timestamp"].dt.strftime("%H:%M")
        group["close_to_close_return_bps"] = (
            group["close"].pct_change() * 10000.0
        )
        group["absolute_close_to_close_return_bps"] = group[
            "close_to_close_return_bps"
        ].abs()
        group["expiry"] = expiry
        group["dte_calendar_days"] = dte
        accepted_bars.append(group)

        session_rows.append(
            {
                "date": str(day),
                "weekday": pd.Timestamp(day).day_name(),
                "instrument_id": instrument_id,
                "expiry": expiry,
                "dte_calendar_days": dte,
                "session_open": session_open,
                "session_close": session_close,
                "session_high_low_range_bps": session_range_bps,
                "first_30m_high_low_range_bps": first30_range,
                "post_09_40_remaining_session_high_low_range_bps": remaining_range,
            }
        )

    if not session_rows:
        raise ValueError("no QA-complete sessions in atlas window")

    bars = pd.concat(accepted_bars, ignore_index=True)
    sessions = pd.DataFrame(session_rows).sort_values("date").reset_index(drop=True)
    sessions["previous_session_close"] = sessions["session_close"].shift(1)
    sessions["absolute_open_vs_previous_close_gap_bps"] = (
        (sessions["session_open"] - sessions["previous_session_close"]).abs()
        / sessions["previous_session_close"]
        * 10000.0
    )
    sessions["previous_session_high_low_range_bps"] = sessions[
        "session_high_low_range_bps"
    ].shift(1)
    return bars, sessions, rejected


def _bootstrap_spearman(x: np.ndarray, y: np.ndarray) -> list[float]:
    rng = np.random.default_rng(int(BOOTSTRAP["seed"]))
    n = len(x)
    draws = np.empty(int(BOOTSTRAP["draws"]), dtype=float)
    for i in range(len(draws)):
        idx = rng.integers(0, n, size=n)
        try:
            draws[i] = _spearman(x[idx], y[idx])
        except ValueError:
            draws[i] = np.nan
    draws = draws[np.isfinite(draws)]
    if not len(draws):
        raise ValueError("all bootstrap Spearman draws were invalid")
    return [
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    ]


def _scalar_relationship(
    sessions: pd.DataFrame,
    blocks: dict[str, list[str]],
    x_col: str,
    y_col: str,
) -> dict[str, Any]:
    usable = sessions[["date", x_col, y_col]].dropna().copy()
    pooled = _spearman(
        usable[x_col].to_numpy(dtype=float),
        usable[y_col].to_numpy(dtype=float),
    )
    mapping = _block_map(blocks)
    usable["block"] = usable["date"].map(mapping)
    block_rho: dict[str, float | None] = {}
    for block in blocks:
        chunk = usable.loc[usable["block"] == block]
        if len(chunk) < 3:
            block_rho[block] = None
            continue
        block_rho[block] = _spearman(
            chunk[x_col].to_numpy(dtype=float),
            chunk[y_col].to_numpy(dtype=float),
        )
    nonnull = [value for value in block_rho.values() if value is not None]
    same_sign = sum(
        1 for value in nonnull
        if (pooled > 0 and value > 0) or (pooled < 0 and value < 0)
    )
    ci = _bootstrap_spearman(
        usable[x_col].to_numpy(dtype=float),
        usable[y_col].to_numpy(dtype=float),
    )
    criteria = DISCOVERY_LABELS["scalar_relationship"]
    ci_excludes_zero = ci[0] > 0.0 or ci[1] < 0.0
    labeled = (
        abs(pooled) >= float(criteria["minimum_abs_pooled_spearman"])
        and same_sign >= int(criteria["minimum_same_sign_blocks"])
        and ci_excludes_zero
    )
    return {
        "observations": int(len(usable)),
        "pooled_spearman": pooled,
        "block_spearman": block_rho,
        "same_sign_blocks": same_sign,
        "bootstrap_95pct": ci,
        "exploratory_pattern_label": bool(labeled),
    }


def _intraday_seasonality(
    bars: pd.DataFrame,
    blocks: dict[str, list[str]],
) -> dict[str, Any]:
    valid = bars.dropna(subset=["absolute_close_to_close_return_bps"]).copy()
    slot = (
        valid.groupby("bar_time", sort=True)["absolute_close_to_close_return_bps"]
        .agg(["mean", "median", "count"])
        .reset_index()
    )
    cfg = PATTERN_FAMILIES["intraday_volatility_seasonality"]

    def window_mean(frame: pd.DataFrame, start: str, end: str) -> float:
        sub = frame.loc[(frame["bar_time"] >= start) & (frame["bar_time"] <= end)]
        return float(sub["absolute_close_to_close_return_bps"].mean())

    opening = window_mean(valid, *cfg["opening_window"])
    midday = window_mean(valid, *cfg["midday_window"])
    late = window_mean(valid, *cfg["late_window"])
    opening_ratio = opening / midday if midday > 0 else float("nan")
    late_ratio = late / midday if midday > 0 else float("nan")

    mapping = _block_map(blocks)
    valid["block"] = valid["date"].map(mapping)
    block_ratios: dict[str, dict[str, float]] = {}
    for block in blocks:
        chunk = valid.loc[valid["block"] == block]
        op = window_mean(chunk, *cfg["opening_window"])
        mid = window_mean(chunk, *cfg["midday_window"])
        lat = window_mean(chunk, *cfg["late_window"])
        block_ratios[block] = {
            "opening_to_midday": op / mid if mid > 0 else float("nan"),
            "late_to_midday": lat / mid if mid > 0 else float("nan"),
        }

    criteria = DISCOVERY_LABELS["intraday_seasonality"]
    labeled = (
        opening_ratio >= float(criteria["minimum_opening_to_midday_abs_return_ratio"])
        and late_ratio >= float(criteria["minimum_late_to_midday_abs_return_ratio"])
    )
    return {
        "slot_profile": [
            {
                "time": str(row["bar_time"]),
                "mean_abs_return_bps": float(row["mean"]),
                "median_abs_return_bps": float(row["median"]),
                "observations": int(row["count"]),
            }
            for _, row in slot.iterrows()
        ],
        "opening_mean_abs_return_bps": opening,
        "midday_mean_abs_return_bps": midday,
        "late_mean_abs_return_bps": late,
        "opening_to_midday_ratio": opening_ratio,
        "late_to_midday_ratio": late_ratio,
        "block_ratios": block_ratios,
        "exploratory_pattern_label": bool(labeled),
    }


def _categorical_structure(
    sessions: pd.DataFrame,
    blocks: dict[str, list[str]],
    category_col: str,
    metric_col: str,
    ordered_categories: list[str],
) -> dict[str, Any]:
    usable = sessions[["date", category_col, metric_col]].dropna().copy()
    summary: list[dict[str, Any]] = []
    means: dict[str, float] = {}
    for category in ordered_categories:
        chunk = usable.loc[usable[category_col] == category, metric_col]
        if chunk.empty:
            continue
        means[category] = float(chunk.mean())
        summary.append(
            {
                "category": category,
                "sessions": int(len(chunk)),
                "mean_range_bps": float(chunk.mean()),
                "median_range_bps": float(chunk.median()),
            }
        )
    if len(means) < 2:
        raise ValueError("categorical structure requires at least two categories")
    max_category = max(means, key=means.get)
    min_category = min(means, key=means.get)
    ratio = means[max_category] / means[min_category]

    mapping = _block_map(blocks)
    usable["block"] = usable["date"].map(mapping)
    block_max: dict[str, str | None] = {}
    for block in blocks:
        chunk = usable.loc[usable["block"] == block]
        category_means = (
            chunk.groupby(category_col)[metric_col].mean().dropna().to_dict()
        )
        block_max[block] = (
            max(category_means, key=category_means.get)
            if category_means
            else None
        )
    same_extreme = sum(1 for value in block_max.values() if value == max_category)
    criteria = DISCOVERY_LABELS["categorical_structure"]
    labeled = (
        ratio >= float(criteria["minimum_max_to_min_mean_ratio"])
        and same_extreme >= int(criteria["minimum_blockwise_same_extreme_category"])
    )
    return {
        "categories": summary,
        "max_mean_category": max_category,
        "min_mean_category": min_category,
        "max_to_min_mean_ratio": ratio,
        "block_max_mean_category": block_max,
        "blocks_matching_pooled_max_category": same_extreme,
        "exploratory_pattern_label": bool(labeled),
    }


def _dte_bucket(value: int) -> str:
    for label, bounds in PATTERN_FAMILIES[
        "expiry_distance_range_structure"
    ]["buckets_calendar_days"].items():
        low, high = bounds
        if int(low) <= int(value) <= int(high):
            return str(label)
    raise ValueError(f"DTE {value} outside frozen buckets")


def analyze_database(db_path: Path) -> dict[str, Any]:
    raw = _read_breeze_rows(db_path)
    bars, sessions, rejected = _validate_and_prepare_sessions(raw)
    blocks = _chronological_blocks(
        sessions["date"].tolist(),
        int(CHRONOLOGICAL_ROBUSTNESS["blocks"]),
    )

    sessions = sessions.copy()
    sessions["dte_bucket"] = sessions["dte_calendar_days"].map(_dte_bucket)

    scalar = {
        "overnight_gap_vs_session_range": _scalar_relationship(
            sessions,
            blocks,
            "absolute_open_vs_previous_close_gap_bps",
            "session_high_low_range_bps",
        ),
        "opening_range_vs_remaining_range": _scalar_relationship(
            sessions,
            blocks,
            "first_30m_high_low_range_bps",
            "post_09_40_remaining_session_high_low_range_bps",
        ),
        "daily_range_persistence": _scalar_relationship(
            sessions,
            blocks,
            "previous_session_high_low_range_bps",
            "session_high_low_range_bps",
        ),
    }

    weekday = _categorical_structure(
        sessions,
        blocks,
        "weekday",
        "session_high_low_range_bps",
        PATTERN_FAMILIES["weekday_range_seasonality"]["categories"],
    )
    dte = _categorical_structure(
        sessions,
        blocks,
        "dte_bucket",
        "session_high_low_range_bps",
        list(
            PATTERN_FAMILIES[
                "expiry_distance_range_structure"
            ]["buckets_calendar_days"].keys()
        ),
    )
    intraday = _intraday_seasonality(bars, blocks)

    labeled_patterns = [
        name for name, result in scalar.items()
        if result["exploratory_pattern_label"]
    ]
    if intraday["exploratory_pattern_label"]:
        labeled_patterns.append("intraday_volatility_seasonality")
    if weekday["exploratory_pattern_label"]:
        labeled_patterns.append("weekday_range_seasonality")
    if dte["exploratory_pattern_label"]:
        labeled_patterns.append("expiry_distance_range_structure")

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "corpus_role": CORPUS_ROLE,
        "research_only": True,
        "exploratory": True,
        "source_database": str(db_path),
        "source_database_sha256": _sha256(db_path),
        "window": WINDOW,
        "qa": {
            "raw_sessions_with_rows_in_common_slice": int(raw["date"].nunique()),
            "accepted_complete_sessions": int(len(sessions)),
            "rejected_sessions": int(len(rejected)),
            "rejections": rejected,
            "accepted_first_date": str(sessions["date"].iloc[0]),
            "accepted_last_date": str(sessions["date"].iloc[-1]),
            "chronological_block_sizes": {
                block: len(days) for block, days in blocks.items()
            },
        },
        "excluded_already_studied_hypotheses": EXCLUDED_ALREADY_STUDIED_HYPOTHESES,
        "results": {
            "intraday_volatility_seasonality": intraday,
            **scalar,
            "weekday_range_seasonality": weekday,
            "expiry_distance_range_structure": dte,
        },
        "exploratory_patterns_meeting_frozen_labels": labeled_patterns,
        "pattern_count": len(labeled_patterns),
        "interpretation_policy": (
            "Patterns meeting frozen exploratory labels are discovery findings "
            "only. They are not validated, tradable, or implementation-ready."
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze frozen Breeze NIFTY market-structure atlas"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/market/historical.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_market_structure_atlas_findings.json"),
    )
    args = parser.parse_args()
    report = analyze_database(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "source_database_sha256": report["source_database_sha256"],
                "accepted_complete_sessions": report["qa"][
                    "accepted_complete_sessions"
                ],
                "rejected_sessions": report["qa"]["rejected_sessions"],
                "exploratory_patterns_meeting_frozen_labels": report[
                    "exploratory_patterns_meeting_frozen_labels"
                ],
                "pattern_count": report["pattern_count"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
