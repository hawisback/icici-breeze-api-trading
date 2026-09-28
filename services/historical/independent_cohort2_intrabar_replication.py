"""Frozen Cohort-2 one-minute timing replication for the options fast-lead signal.

This stage is allowed only after both frozen five-minute options fast-lead checks
replicate in Cohort 2. It reuses the already-defined signal and timing windows;
it does not select thresholds, tune horizons, or promote a trading candidate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_cohort2_protocol import (
    EXPECTED,
    PROTOCOL_VERSION,
    SESSION_DATES,
    futures_expiry_for_day,
    validate_auxiliary,
    validate_market,
    validate_options,
)
from services.historical.independent_cohort2_replication import (
    _crossfit_residual,
    _spearman,
    build_frame,
)
from services.historical.independent_options_intrabar_timing_findings import (
    DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1,
)

RESEARCH_TYPE = "NIFTY_DEVELOPMENT_COHORT_2_INTRABAR_REPLICATION_V1"
INTRABAR_PROTOCOL_VERSION = "DEVELOPMENT_COHORT_2_INTRABAR_V1"
BLOCK_SIZE = 12
PRIMARY_CHECKS = (
    "boundary_from_signal_close_to_next_minute_open",
    "minute_1_open_to_close",
    "cumulative_minute_5",
)


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _block_values(
    frame: pd.DataFrame,
    predictor: str,
    outcome: str,
) -> list[float]:
    values: list[float] = []
    for _, group in frame.dropna(subset=[predictor, outcome]).groupby("block"):
        values.append(_spearman(group[predictor], group[outcome]))
    return values


def _timing_metric(
    frame: pd.DataFrame,
    outcome: str,
) -> dict[str, Any]:
    sample = frame.dropna(subset=["signal", outcome, "block"]).copy()
    sample["aligned"] = np.sign(sample["signal"]) * sample[outcome]
    blocks = _block_values(sample, "signal", outcome)
    mean_by_block = sample.groupby("block")["aligned"].mean().tolist()
    return {
        "rows": int(len(sample)),
        "spearman": _spearman(sample["signal"], sample[outcome]),
        "block_spearman": blocks,
        "positive_correlation_blocks": int(sum(value > 0 for value in blocks)),
        "negative_correlation_blocks": int(sum(value < 0 for value in blocks)),
        "total_blocks": int(len(blocks)),
        "mean_direction_aligned_bps": float(sample["aligned"].mean()),
        "median_direction_aligned_bps": float(sample["aligned"].median()),
        "direction_hit_rate": float((sample["aligned"] > 0).mean()),
        "block_mean_direction_aligned_bps": [float(value) for value in mean_by_block],
        "positive_mean_blocks": int(sum(value > 0 for value in mean_by_block)),
    }


def _directional_check(
    *,
    metric: str,
    cohort1_spearman: float,
    cohort2: dict[str, Any],
) -> dict[str, Any]:
    c1_sign = _sign(float(cohort1_spearman))
    c2_sign = _sign(float(cohort2["spearman"]))
    if c1_sign > 0:
        same_sign_blocks = int(cohort2["positive_correlation_blocks"])
    elif c1_sign < 0:
        same_sign_blocks = int(cohort2["negative_correlation_blocks"])
    else:
        same_sign_blocks = 0
    return {
        "metric": metric,
        "cohort1_spearman": float(cohort1_spearman),
        "cohort2_spearman": float(cohort2["spearman"]),
        "cohort2_to_cohort1_abs_effect_ratio": (
            abs(float(cohort2["spearman"])) / abs(float(cohort1_spearman))
            if float(cohort1_spearman) != 0.0
            else None
        ),
        "same_pooled_sign": c1_sign != 0 and c1_sign == c2_sign,
        "cohort2_same_sign_blocks": same_sign_blocks,
        "cohort2_total_blocks": int(cohort2["total_blocks"]),
        "directional_replication": (
            c1_sign != 0
            and c1_sign == c2_sign
            and same_sign_blocks >= 5
        ),
    }


def _validate_gate(replication: dict[str, Any]) -> None:
    if replication.get("research_type") != "NIFTY_DEVELOPMENT_COHORT_2_REPLICATION_V1":
        raise ValueError("unexpected Cohort-2 replication result type")
    if replication.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Cohort-2 replication protocol version mismatch")
    checks = {
        item["metric"]: bool(item.get("directional_replication"))
        for item in replication.get("replication_checks", [])
    }
    required = (
        "options_fast_lead.raw",
        "options_fast_lead.spot_attributed_crossfit",
    )
    if not all(checks.get(name) for name in required):
        raise ValueError("frozen five-minute fast-lead gate did not pass")


def validate_intrabar(
    payload: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("intrabar session_dates do not exactly match frozen Cohort 2")
    rows = list(payload.get("rows") or [])
    if len(rows) != EXPECTED["one_minute_rows_if_intrabar_stage_runs"]:
        raise ValueError(f"intrabar expected 27000 rows, got {len(rows)}")

    expected_contracts = {
        day: futures_expiry_for_day(day)
        for day in SESSION_DATES
    }
    if payload.get("contract_by_date") != expected_contracts:
        raise ValueError("intrabar contract_by_date does not match frozen futures schedule")

    quality = payload.get("quality") or {}
    required_quality = {
        "rows": EXPECTED["one_minute_rows_if_intrabar_stage_runs"],
        "duplicate_rows": 0,
        "invalid_ohlc_rows": 0,
        "wrong_contract_rows": 0,
        "complete_375_bar_sessions": EXPECTED["sessions"],
        "failed_requests": 0,
    }
    for key, expected in required_quality.items():
        if quality.get(key) != expected:
            raise ValueError(
                f"intrabar quality {key} expected {expected}, got {quality.get(key)}"
            )

    one = pd.DataFrame(rows).copy()
    one["timestamp"] = pd.to_datetime(one["timestamp"])
    if one["timestamp"].duplicated().any():
        raise ValueError("intrabar contains duplicate timestamps")
    one["date"] = one["timestamp"].dt.date.astype(str)
    if one.groupby("date").size().to_dict() != {day: 375 for day in SESSION_DATES}:
        raise ValueError("intrabar does not contain exactly 375 bars per frozen session")

    if set(one["source"].astype(str)) != {"BREEZE"}:
        raise ValueError("intrabar source must be BREEZE")
    expected_instrument = one["date"].map(
        {day: f"NIFTY FUT {expiry}" for day, expiry in expected_contracts.items()}
    )
    if not one["instrument"].astype(str).eq(expected_instrument).all():
        raise ValueError("intrabar instrument identity mismatch")

    first_minutes = one.groupby("date")["timestamp"].min()
    last_minutes = one.groupby("date")["timestamp"].max()
    if any(ts.strftime("%H:%M") != "09:15" for ts in first_minutes):
        raise ValueError("intrabar session does not start at 09:15")
    if any(ts.strftime("%H:%M") != "15:29" for ts in last_minutes):
        raise ValueError("intrabar session does not end at 15:29")

    five = one.copy()
    five["five_timestamp"] = five["timestamp"].dt.floor("5min")
    aggregated = (
        five.groupby("five_timestamp", as_index=False)
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
    expected_columns = [
        "five_timestamp",
        "futures_open",
        "futures_high",
        "futures_low",
        "futures_close",
        "futures_volume",
        "futures_open_interest",
    ]
    joined = market_frame[expected_columns].merge(
        aggregated,
        on="five_timestamp",
        how="left",
        suffixes=("_5m", "_1m"),
        validate="one_to_one",
    )
    if len(joined) != EXPECTED["five_minute_rows"]:
        raise ValueError("intrabar reconciliation row count mismatch")
    if not joined["one_minute_count"].eq(5).all():
        raise ValueError("not every five-minute bar has exactly five one-minute bars")
    for name in (
        "futures_open",
        "futures_high",
        "futures_low",
        "futures_close",
        "futures_volume",
        "futures_open_interest",
    ):
        left = joined[f"{name}_5m"].astype(float).to_numpy()
        right = joined[f"{name}_1m"].astype(float).to_numpy()
        if not np.allclose(left, right, rtol=0.0, atol=1e-9, equal_nan=False):
            raise ValueError(f"intrabar does not exactly reconcile {name}")

    return {
        "sessions": EXPECTED["sessions"],
        "rows": len(one),
        "five_minute_bars_reconciled": len(joined),
        "one_minute_rows_per_five_minute_bar": 5,
        "exact_ohlcv_open_interest_reconciliation": True,
        "exact_contract_reconciliation": True,
    }


def _attach_intrabar_returns(
    signals: pd.DataFrame,
    intrabar: dict[str, Any],
) -> pd.DataFrame:
    result = signals.copy()
    one = pd.DataFrame(intrabar["rows"]).copy()
    one["timestamp"] = pd.to_datetime(one["timestamp"])
    one = one.set_index("timestamp").sort_index()

    for minute in range(1, 6):
        target = result["timestamp"] + pd.to_timedelta(4 + minute, unit="m")
        result[f"minute_{minute}_open"] = target.map(one["open"])
        result[f"minute_{minute}_close"] = target.map(one["close"])
        result[f"minute_{minute}_open_to_close_bps"] = (
            result[f"minute_{minute}_close"]
            / result[f"minute_{minute}_open"]
            - 1.0
        ) * 10000.0
        result[f"cumulative_minute_{minute}_bps"] = (
            result[f"minute_{minute}_close"]
            / result["minute_1_open"]
            - 1.0
        ) * 10000.0

    result["boundary_bps"] = (
        result["minute_1_open"] / result["futures_close"] - 1.0
    ) * 10000.0
    result["minute_2_open_to_minute_5_close_bps"] = (
        result["minute_5_close"] / result["minute_2_open"] - 1.0
    ) * 10000.0
    required = [
        "minute_1_open",
        "minute_1_close",
        "minute_2_open",
        "minute_5_close",
    ]
    if result[required].isna().any().any():
        raise ValueError("intrabar timing alignment is incomplete")
    return result


def build_timing_frame(
    market: dict[str, Any],
    vix: dict[str, Any],
    options: dict[str, Any],
    spot: dict[str, Any],
    intrabar: dict[str, Any],
) -> pd.DataFrame:
    frame = build_frame(
        market,
        vix,
        options,
        spot,
        block_size=BLOCK_SIZE,
    )
    valid = frame.dropna(
        subset=[
            "options_gap_bps",
            "spot_gap_bps",
            "futures_return_bps",
            "next_close_return_bps",
        ]
    ).copy()
    valid["signal"] = _crossfit_residual(
        valid,
        "options_gap_bps",
        numeric_controls=["spot_gap_bps", "futures_return_bps"],
        categorical_controls=[],
    )
    signals = valid[
        ["timestamp", "date", "block", "futures_close", "signal"]
    ].dropna(subset=["signal"]).copy()
    return _attach_intrabar_returns(signals, intrabar)


def build_report(
    replication_result: dict[str, Any],
    market: dict[str, Any],
    vix: dict[str, Any],
    options: dict[str, Any],
    spot: dict[str, Any],
    intrabar: dict[str, Any],
) -> dict[str, Any]:
    _validate_gate(replication_result)
    validate_market(market)
    validate_options(options)
    validate_auxiliary(vix, "vix")
    validate_auxiliary(spot, "spot")
    intrabar_qa = validate_intrabar(intrabar, market)

    frame = build_timing_frame(market, vix, options, spot, intrabar)
    expected_signals = EXPECTED["sessions"] * 73
    if len(frame) != expected_signals:
        raise ValueError(
            f"expected {expected_signals} scorable timing signals, got {len(frame)}"
        )

    boundary = _timing_metric(frame, "boundary_bps")
    minute_metrics = {
        f"minute_{minute}": _timing_metric(
            frame, f"minute_{minute}_open_to_close_bps"
        )
        for minute in range(1, 6)
    }
    cumulative_metrics = {
        f"minute_{minute}": _timing_metric(
            frame, f"cumulative_minute_{minute}_bps"
        )
        for minute in range(1, 6)
    }
    delayed = _timing_metric(frame, "minute_2_open_to_minute_5_close_bps")

    c1 = DEVELOPMENT_OPTIONS_INTRABAR_TIMING_V1
    checks = [
        _directional_check(
            metric="boundary_from_signal_close_to_next_minute_open",
            cohort1_spearman=float(
                c1["boundary_from_signal_close_to_next_minute_open"]["spearman"]
            ),
            cohort2=boundary,
        ),
        _directional_check(
            metric="minute_1_open_to_close",
            cohort1_spearman=float(c1["minute_open_to_close"]["minute_1"]["spearman"]),
            cohort2=minute_metrics["minute_1"],
        ),
        _directional_check(
            metric="cumulative_minute_5",
            cohort1_spearman=float(
                c1["cumulative_from_earliest_next_minute_open"]["minute_5_spearman"]
            ),
            cohort2=cumulative_metrics["minute_5"],
        ),
    ]
    primary_replication = all(item["directional_replication"] for item in checks)

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "intrabar_protocol_version": INTRABAR_PROTOCOL_VERSION,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "threshold_optimization": False,
        "intrabar_gate_passed_before_collection": True,
        "frozen_primary_rule": (
            "For each of boundary, minute-1 open-to-close, and cumulative "
            "minute-5 timing relationships: same pooled sign as Cohort 1 and "
            "same sign in at least 5 of 6 Cohort-2 blocks."
        ),
        "delayed_entry_rule": (
            "Report the minute-2-open to minute-5-close relationship and its "
            "Cohort2/Cohort1 magnitude ratio descriptively only; no post-hoc "
            "near-zero cutoff is introduced."
        ),
        "intrabar_qa": intrabar_qa,
        "signals": {
            "definition": (
                "leave-one-12-session-block-out residual of ATM options-implied "
                "relative move after spot gap and current futures return attribution"
            ),
            "definition_changed": False,
            "threshold_selected": False,
            "scorable_signals": int(len(frame)),
        },
        "cohort2": {
            "boundary_from_signal_close_to_next_minute_open": boundary,
            "minute_open_to_close": minute_metrics,
            "cumulative_from_earliest_next_minute_open": cumulative_metrics,
            "latency_challenge": {
                "entry_minute_1_open_to_minute_5_close": cumulative_metrics["minute_5"],
                "entry_minute_2_open_to_minute_5_close": delayed,
            },
        },
        "replication_checks": checks,
        "primary_timing_relationship_replicated": primary_replication,
        "cohort1_latency_reference": {
            "entry_minute_1_open_to_minute_5_close_spearman": float(
                c1["latency_challenge"]["entry_minute_1_open_to_minute_5_close"][
                    "spearman"
                ]
            ),
            "entry_minute_2_open_to_minute_5_close_spearman": float(
                c1["latency_challenge"]["entry_minute_2_open_to_minute_5_close"][
                    "spearman"
                ]
            ),
        },
        "decision_guardrail": (
            "This is structural timing replication only. Do not select a signal "
            "threshold, rescue failed timing relationships, freeze O3, or promote "
            "a trading implementation from this result."
        ),
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run frozen Cohort-2 one-minute options fast-lead timing replication"
    )
    parser.add_argument("--replication-result", type=Path, required=True)
    parser.add_argument("--cohort2-market", type=Path, required=True)
    parser.add_argument("--cohort2-vix", type=Path, required=True)
    parser.add_argument("--cohort2-options", type=Path, required=True)
    parser.add_argument("--cohort2-spot", type=Path, required=True)
    parser.add_argument("--cohort2-intrabar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = build_report(
        _load(args.replication_result),
        _load(args.cohort2_market),
        _load(args.cohort2_vix),
        _load(args.cohort2_options),
        _load(args.cohort2_spot),
        _load(args.cohort2_intrabar),
    )
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
                "intrabar_protocol_version": report["intrabar_protocol_version"],
                "primary_timing_relationship_replicated": report[
                    "primary_timing_relationship_replicated"
                ],
                "replication_checks": report["replication_checks"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
