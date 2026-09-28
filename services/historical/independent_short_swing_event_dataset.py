"""Build the combined inspected short-swing development event dataset.

The output combines Development Cohort 1 and Development Cohort 2 only. It
contains reproducible state features plus executable one-minute fixed-horizon
outcomes. It is a development artifact, not a blind-validation result.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_cohort2_replication import (
    _crossfit_residual,
    build_frame,
)
from services.historical.independent_short_swing_development_protocol import (
    COHORTS,
    CORPUS_ROLE,
    DEVELOPMENT_RULES,
    ENTRY,
    FEATURE_POLICY,
    FIXED_EXIT_HORIZONS_MINUTES,
    OUTCOMES,
    PROTOCOL_VERSION,
    STRATEGY_FAMILIES,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_DEVELOPMENT_EVENTS_V1"
FUTURES_RE = re.compile(r"^NIFTY FUT (\d{4}-\d{2}-\d{2})$")


def _market_contract_by_date(market: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, set[str]] = {
        day: set() for day in market["session_dates"]
    }
    for row in market["canonical_market_rows"]:
        day = str(row["timestamp"])[:10]
        match = FUTURES_RE.match(str(row["futures_instrument"]))
        if day not in mapping or not match:
            raise ValueError(f"invalid canonical futures row for {day}")
        mapping[day].add(match.group(1))
    result: dict[str, str] = {}
    for day, expiries in mapping.items():
        if len(expiries) != 1:
            raise ValueError(f"{day} has {len(expiries)} canonical futures expiries")
        result[day] = next(iter(expiries))
    return result


def validate_intrabar(
    payload: dict[str, Any],
    market: dict[str, Any],
    *,
    expected_sessions: int,
    expected_rows: int,
) -> dict[str, Any]:
    sessions = list(market["session_dates"])
    if list(payload.get("session_dates") or []) != sessions:
        raise ValueError("intrabar sessions do not exactly match market sessions")
    if len(sessions) != expected_sessions:
        raise ValueError(
            f"expected {expected_sessions} sessions, got {len(sessions)}"
        )
    rows = list(payload.get("rows") or [])
    if len(rows) != expected_rows:
        raise ValueError(f"expected {expected_rows} intrabar rows, got {len(rows)}")

    expected_contracts = _market_contract_by_date(market)
    if payload.get("contract_by_date") != expected_contracts:
        raise ValueError("intrabar contract_by_date does not match canonical market")

    quality = payload.get("quality") or {}
    required = {
        "rows": expected_rows,
        "duplicate_rows": 0,
        "invalid_ohlc_rows": 0,
        "wrong_contract_rows": 0,
        "complete_375_bar_sessions": expected_sessions,
        "failed_requests": 0,
    }
    for key, expected in required.items():
        if quality.get(key) != expected:
            raise ValueError(
                f"intrabar quality {key} expected {expected}, got {quality.get(key)}"
            )

    one = pd.DataFrame(rows).copy()
    one["timestamp"] = pd.to_datetime(one["timestamp"])
    one["date"] = one["timestamp"].dt.date.astype(str)
    if one["timestamp"].duplicated().any():
        raise ValueError("intrabar contains duplicate timestamps")
    counts = one.groupby("date").size().to_dict()
    if counts != {day: 375 for day in sessions}:
        raise ValueError("intrabar does not contain exactly 375 rows per session")
    if set(one["source"].astype(str)) != {"BREEZE"}:
        raise ValueError("intrabar source must be BREEZE")

    expected_instruments = one["date"].map(
        {day: f"NIFTY FUT {expiry}" for day, expiry in expected_contracts.items()}
    )
    if not one["instrument"].astype(str).eq(expected_instruments).all():
        raise ValueError("intrabar instrument identity mismatch")

    first = one.groupby("date")["timestamp"].min()
    last = one.groupby("date")["timestamp"].max()
    if any(value.strftime("%H:%M") != "09:15" for value in first):
        raise ValueError("intrabar session does not start at 09:15")
    if any(value.strftime("%H:%M") != "15:29" for value in last):
        raise ValueError("intrabar session does not end at 15:29")

    one["five_timestamp"] = one["timestamp"].dt.floor("5min")
    aggregate = (
        one.groupby("five_timestamp", as_index=False)
        .agg(
            futures_open=("open", "first"),
            futures_high=("high", "max"),
            futures_low=("low", "min"),
            futures_close=("close", "last"),
            futures_volume=("volume", "sum"),
            futures_open_interest=("open_interest", "last"),
            one_minute_count=("timestamp", "size"),
        )
    )
    market_frame = pd.DataFrame(market["canonical_market_rows"]).copy()
    market_frame["five_timestamp"] = pd.to_datetime(market_frame["timestamp"])
    columns = [
        "five_timestamp",
        "futures_open",
        "futures_high",
        "futures_low",
        "futures_close",
        "futures_volume",
        "futures_open_interest",
    ]
    joined = market_frame[columns].merge(
        aggregate,
        on="five_timestamp",
        how="left",
        suffixes=("_5m", "_1m"),
        validate="one_to_one",
    )
    if not joined["one_minute_count"].eq(5).all():
        raise ValueError("not every five-minute bar has five one-minute rows")
    for name in columns[1:]:
        left = joined[f"{name}_5m"].astype(float).to_numpy()
        right = joined[f"{name}_1m"].astype(float).to_numpy()
        if not np.allclose(left, right, rtol=0.0, atol=1e-9):
            raise ValueError(f"intrabar does not exactly reconcile {name}")

    return {
        "sessions": expected_sessions,
        "rows": expected_rows,
        "five_minute_rows_reconciled": int(len(joined)),
        "exact_ohlcv_open_interest_reconciliation": True,
        "exact_contract_reconciliation": True,
    }


def _add_development_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    group = result.groupby("date")

    prior_highs = pd.concat(
        [group["futures_high"].shift(lag) for lag in (1, 2, 3)],
        axis=1,
    )
    prior_lows = pd.concat(
        [group["futures_low"].shift(lag) for lag in (1, 2, 3)],
        axis=1,
    )
    prior_volumes = pd.concat(
        [group["futures_volume"].shift(lag) for lag in (1, 2, 3)],
        axis=1,
    )
    result["prior_3_high"] = prior_highs.max(axis=1)
    result["prior_3_low"] = prior_lows.min(axis=1)
    result["prior_3_volume_mean"] = prior_volumes.mean(axis=1)
    result["volume_vs_prior3_mean"] = (
        result["futures_volume"]
        / result["prior_3_volume_mean"].replace(0.0, np.nan)
    )

    has_prior = result["prior_3_high"].notna() & result["prior_3_low"].notna()
    up_breakout = (
        result["futures_high"] / result["prior_3_high"] - 1.0
    ) * 10000.0
    down_breakout = (
        result["prior_3_low"] / result["futures_low"] - 1.0
    ) * 10000.0
    result["up_breakout_bps"] = up_breakout.clip(lower=0.0).where(has_prior)
    result["down_breakout_bps"] = down_breakout.clip(lower=0.0).where(has_prior)
    result["close_vs_prior3_high_bps"] = (
        result["futures_close"] / result["prior_3_high"] - 1.0
    ) * 10000.0
    result["close_vs_prior3_low_bps"] = (
        result["futures_close"] / result["prior_3_low"] - 1.0
    ) * 10000.0

    for name, condition in {
        "failed_breakout_up": (
            (result["futures_high"] > result["prior_3_high"])
            & (result["futures_close"] <= result["prior_3_high"])
        ),
        "failed_breakout_down": (
            (result["futures_low"] < result["prior_3_low"])
            & (result["futures_close"] >= result["prior_3_low"])
        ),
        "closed_breakout_up": result["futures_close"] > result["prior_3_high"],
        "closed_breakout_down": result["futures_close"] < result["prior_3_low"],
    }.items():
        series = condition.astype("boolean")
        result[name] = series.where(has_prior, pd.NA)

    result["options_specific_fast_lead"] = _crossfit_residual(
        result,
        "options_gap_bps",
        numeric_controls=["spot_gap_bps", "futures_return_bps"],
        categorical_controls=[],
    )
    result["options_specific_fast_lead_abs"] = result[
        "options_specific_fast_lead"
    ].abs()
    return result


def _attach_execution_outcomes(
    frame: pd.DataFrame,
    intrabar: dict[str, Any],
) -> pd.DataFrame:
    result = frame.copy()
    one = pd.DataFrame(intrabar["rows"]).copy()
    one["timestamp"] = pd.to_datetime(one["timestamp"])
    one = one.set_index("timestamp").sort_index()

    result["entry_timestamp"] = result["timestamp"] + pd.Timedelta(minutes=5)
    result["entry_price"] = result["entry_timestamp"].map(one["open"])
    result["entry_gap_bps"] = (
        result["entry_price"] / result["futures_close"] - 1.0
    ) * 10000.0

    for horizon in FIXED_EXIT_HORIZONS_MINUTES:
        close_series = []
        high_series = []
        low_series = []
        for offset in range(5, 5 + horizon):
            target = result["timestamp"] + pd.to_timedelta(offset, unit="m")
            close_series.append(target.map(one["close"]))
            high_series.append(target.map(one["high"]))
            low_series.append(target.map(one["low"]))

        closes = pd.concat(close_series, axis=1)
        highs = pd.concat(high_series, axis=1)
        lows = pd.concat(low_series, axis=1)
        complete = closes.notna().sum(axis=1).eq(horizon)
        exit_price = closes.iloc[:, -1].where(complete)
        max_high = highs.max(axis=1).where(complete)
        min_low = lows.min(axis=1).where(complete)
        entry = result["entry_price"].where(complete)

        terminal = (exit_price / entry - 1.0) * 10000.0
        max_up = (max_high / entry - 1.0) * 10000.0
        max_down = (min_low / entry - 1.0) * 10000.0

        prefix = f"h{horizon}m"
        result[f"{prefix}_exit_price"] = exit_price
        result[f"{prefix}_terminal_bps"] = terminal
        result[f"{prefix}_max_up_bps"] = max_up
        result[f"{prefix}_max_down_bps"] = max_down
        result[f"{prefix}_long_mfe_bps"] = max_up
        result[f"{prefix}_long_mae_bps"] = max_down
        result[f"{prefix}_short_mfe_bps"] = -max_down
        result[f"{prefix}_short_mae_bps"] = -max_up
        result[f"{prefix}_long_terminal_bps"] = terminal
        result[f"{prefix}_short_terminal_bps"] = -terminal

    return result


def _build_cohort(
    *,
    label: str,
    market: dict[str, Any],
    vix: dict[str, Any],
    options: dict[str, Any],
    spot: dict[str, Any],
    intrabar: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    config = COHORTS[label]
    qa = validate_intrabar(
        intrabar,
        market,
        expected_sessions=int(config["sessions"]),
        expected_rows=int(config["one_minute_rows"]),
    )
    frame = build_frame(
        market,
        vix,
        options,
        spot,
        block_size=int(config["block_size"]),
    )
    if len(frame) != int(config["five_minute_rows"]):
        raise ValueError(
            f"{label} expected {config['five_minute_rows']} five-minute rows, "
            f"got {len(frame)}"
        )
    frame["cohort"] = label
    frame["cohort_block"] = frame["block"]
    frame = _add_development_features(frame)
    frame = _attach_execution_outcomes(frame, intrabar)
    return frame, qa


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    serial = frame.copy()
    for name in ("timestamp", "entry_timestamp", "futures_expiry"):
        if name in serial:
            serial[name] = serial[name].map(
                lambda value: value.isoformat() if pd.notna(value) else None
            )
    return json.loads(serial.to_json(orient="records"))


def build_dataset(
    cohort1: tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ],
    cohort2: tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ],
) -> dict[str, Any]:
    c1, qa1 = _build_cohort(
        label="cohort1",
        market=cohort1[0],
        vix=cohort1[1],
        options=cohort1[2],
        spot=cohort1[3],
        intrabar=cohort1[4],
    )
    c2, qa2 = _build_cohort(
        label="cohort2",
        market=cohort2[0],
        vix=cohort2[1],
        options=cohort2[2],
        spot=cohort2[3],
        intrabar=cohort2[4],
    )
    combined = pd.concat([c1, c2], ignore_index=True, sort=False)
    combined = combined.sort_values(["date", "timestamp"]).reset_index(drop=True)
    combined["development_event_id"] = np.arange(1, len(combined) + 1)

    scorable = {
        str(horizon): int(combined[f"h{horizon}m_terminal_bps"].notna().sum())
        for horizon in FIXED_EXIT_HORIZONS_MINUTES
    }
    expected_scorable = {
        str(horizon): sum(
            int(config["sessions"]) * (75 - horizon // 5)
            for config in COHORTS.values()
        )
        for horizon in FIXED_EXIT_HORIZONS_MINUTES
    }
    if scorable != expected_scorable:
        raise ValueError(
            f"fixed-horizon scorable row counts changed: {scorable} "
            f"!= {expected_scorable}"
        )

    keep = [
        "development_event_id",
        "cohort",
        "cohort_block",
        "timestamp",
        "date",
        "futures_open",
        "futures_high",
        "futures_low",
        "futures_close",
        "futures_volume",
        "futures_open_interest",
        "futures_instrument",
        "futures_return_bps",
        "current_abs_return_bps",
        "oi_change_bps",
        "range_3_bps",
        "range_6_bps",
        "close_location_3",
        "close_location_6",
        "net_return_3_bps",
        "net_return_6_bps",
        "path_length_3_bps",
        "path_length_6_bps",
        "volume_vs_prior3_mean",
        "prior_3_high",
        "prior_3_low",
        "up_breakout_bps",
        "down_breakout_bps",
        "close_vs_prior3_high_bps",
        "close_vs_prior3_low_bps",
        "failed_breakout_up",
        "failed_breakout_down",
        "closed_breakout_up",
        "closed_breakout_down",
        "vix_close",
        "vix_5m_change_bps",
        "vix_15m_change_bps",
        "spot_close",
        "options_gap_bps",
        "options_specific_fast_lead",
        "options_specific_fast_lead_abs",
        "futures_dte",
        "option_dte",
        "time_bucket_30m",
        "entry_timestamp",
        "entry_price",
        "entry_gap_bps",
    ]
    for horizon in FIXED_EXIT_HORIZONS_MINUTES:
        prefix = f"h{horizon}m"
        keep.extend(
            [
                f"{prefix}_exit_price",
                f"{prefix}_terminal_bps",
                f"{prefix}_max_up_bps",
                f"{prefix}_max_down_bps",
                f"{prefix}_long_mfe_bps",
                f"{prefix}_long_mae_bps",
                f"{prefix}_short_mfe_bps",
                f"{prefix}_short_mae_bps",
                f"{prefix}_long_terminal_bps",
                f"{prefix}_short_terminal_bps",
            ]
        )

    output = combined[keep].copy()
    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "corpus_role": CORPUS_ROLE,
        "research_only": DEVELOPMENT_RULES["research_only"],
        "candidate_frozen": DEVELOPMENT_RULES["candidate_frozen"],
        "blind_data_used": DEVELOPMENT_RULES["blind_data_used"],
        "implementation_allowed": DEVELOPMENT_RULES["implementation_allowed"],
        "thresholds_frozen": DEVELOPMENT_RULES["thresholds_frozen"],
        "sessions": sum(int(item["sessions"]) for item in COHORTS.values()),
        "five_minute_events": int(len(output)),
        "cohort_rows": {
            "cohort1": int(len(c1)),
            "cohort2": int(len(c2)),
        },
        "intrabar_qa": {
            "cohort1": qa1,
            "cohort2": qa2,
        },
        "entry": ENTRY,
        "fixed_exit_horizons_minutes": list(FIXED_EXIT_HORIZONS_MINUTES),
        "outcomes": OUTCOMES,
        "strategy_families": list(STRATEGY_FAMILIES),
        "feature_policy": FEATURE_POLICY,
        "scorable_events_by_horizon": scorable,
        "expected_scorable_events_by_horizon": expected_scorable,
        "events": _json_records(output),
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build combined short-swing development event dataset"
    )
    for cohort in ("cohort1", "cohort2"):
        for kind in ("market", "vix", "options", "spot", "intrabar"):
            parser.add_argument(
                f"--{cohort}-{kind}",
                type=Path,
                required=True,
            )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    c1 = tuple(
        _load(getattr(args, f"cohort1_{kind}"))
        for kind in ("market", "vix", "options", "spot", "intrabar")
    )
    c2 = tuple(
        _load(getattr(args, f"cohort2_{kind}"))
        for kind in ("market", "vix", "options", "spot", "intrabar")
    )
    report = build_dataset(c1, c2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "protocol_version": report["protocol_version"],
                "sessions": report["sessions"],
                "five_minute_events": report["five_minute_events"],
                "scorable_events_by_horizon": report[
                    "scorable_events_by_horizon"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
