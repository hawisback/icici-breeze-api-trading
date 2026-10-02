"""Run frozen exact-option implementation for low-volume price-shock reversal."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_short_swing_futures_spot_dislocation_findings import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    _non_overlapping,
)
from services.historical.independent_short_swing_low_volume_price_shock_reversal_options_protocol import (
    COST_MODEL,
    GUARDRAILS,
    OPTION_IMPLEMENTATION,
    PASS_CRITERIA,
    PROTOCOL_VERSION,
    SOURCE,
    STRUCTURAL_RULE,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_LOW_VOLUME_PRICE_SHOCK_REVERSAL_OPTIONS_FINDINGS_V1"
EXPECTED_OPTION_SHA256 = {
    "cohort1": str(SOURCE["cohort1_options_sha256"]),
    "cohort2": str(SOURCE["cohort2_options_sha256"]),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_with_sha(path: Path, expected: str) -> dict[str, Any]:
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(f"SHA256 changed for {path}: {actual} != {expected}")
    return json.loads(path.read_text(encoding="utf-8"))


def _atm_strike(price: float, step: int = 50) -> int:
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"invalid futures close {price!r}")
    return int(math.floor(price / step + 0.5) * step)


def _option_frame(payload: dict[str, Any], cohort: str) -> pd.DataFrame:
    rows = list(payload.get("option_candles") or [])
    if not rows:
        raise ValueError(f"{cohort} option_candles missing")
    frame = pd.DataFrame(rows).copy()
    required = {"timestamp", "expiry", "strike", "right", "open", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{cohort} option schema missing {missing}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
    frame["date"] = frame["timestamp"].dt.date.astype(str)
    frame["strike"] = pd.to_numeric(frame["strike"], errors="raise").astype(int)
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["cohort"] = cohort
    keys = ["timestamp", "expiry", "strike", "right"]
    if frame.duplicated(keys).any():
        raise ValueError(f"{cohort} option dataset has duplicate exact contracts")
    contracts = payload.get("contract_by_date") or {}
    if set(frame["date"]) - set(contracts):
        raise ValueError(f"{cohort} option rows contain unexpected session dates")
    return frame


def _event_frame(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("blind_data_used") is not False:
        raise ValueError("event input must remain inspected development data")
    frame = pd.DataFrame(payload["events"]).copy()
    required = {
        "cohort", "cohort_block", "timestamp", "date", "futures_close",
        "futures_return_bps", "current_abs_return_bps", "volume_vs_prior3_mean",
        "entry_timestamp", "h5m_terminal_bps",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"event schema missing {missing}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
    frame["entry_timestamp"] = pd.to_datetime(frame["entry_timestamp"]).dt.tz_localize(None)
    for name in (
        "futures_close", "futures_return_bps", "current_abs_return_bps",
        "volume_vs_prior3_mean", "h5m_terminal_bps",
    ):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame["direction"] = -np.sign(frame["futures_return_bps"])
    return frame.sort_values(["cohort", "date", "timestamp"]).reset_index(drop=True)


def _validate_threshold(frame: pd.DataFrame) -> float:
    values = frame["current_abs_return_bps"].dropna().to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    derived = float(np.percentile(
        values, float(STRUCTURAL_RULE["current_abs_return_percentile_min"])
    ))
    frozen = float(STRUCTURAL_RULE["derived_current_abs_return_threshold_bps"])
    if not math.isclose(derived, frozen, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(f"frozen return threshold changed: {derived} != {frozen}")
    return frozen


def _signals(frame: pd.DataFrame, threshold: float) -> pd.DataFrame:
    mask = (
        frame["current_abs_return_bps"].ge(threshold)
        & frame["volume_vs_prior3_mean"].le(
            float(STRUCTURAL_RULE["volume_vs_prior3_mean_max"])
        )
        & frame["volume_vs_prior3_mean"].notna()
        & frame["direction"].ne(0)
        & frame["h5m_terminal_bps"].notna()
    )
    selected = frame.loc[mask].copy()
    return _non_overlapping(
        selected,
        horizon_minutes=int(STRUCTURAL_RULE["fixed_exit_minutes"]),
    )


def _attach_contracts(
    signals: pd.DataFrame,
    options: pd.DataFrame,
    contracts: dict[str, str],
    strike_variant: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    lookup = options.set_index(["timestamp", "expiry", "strike", "right"])
    horizon = int(STRUCTURAL_RULE["fixed_exit_minutes"])
    for signal in signals.itertuples(index=False):
        direction = int(signal.direction)
        right = "CE" if direction > 0 else "PE"
        atm = _atm_strike(float(signal.futures_close))
        if strike_variant == "ATM":
            strike = atm
        elif strike_variant == "one_strike_ITM":
            strike = atm - 50 if direction > 0 else atm + 50
        else:
            raise ValueError(f"unknown strike variant {strike_variant}")
        expiry = contracts.get(str(signal.date))
        if not expiry:
            raise ValueError(f"missing option expiry for {signal.date}")
        entry_ts = pd.Timestamp(signal.entry_timestamp)
        exit_ts = entry_ts + pd.Timedelta(minutes=horizon - 5)
        entry_key = (entry_ts, expiry, strike, right)
        exit_key = (exit_ts, expiry, strike, right)
        try:
            entry_row = lookup.loc[entry_key]
            exit_row = lookup.loc[exit_key]
        except KeyError as exc:
            raise ValueError(
                f"missing exact option contract candle for {signal.date} "
                f"{strike} {right} {expiry}: {exc}"
            ) from exc
        entry_price = float(entry_row["open"])
        exit_price = float(exit_row["close"])
        if not (
            math.isfinite(entry_price) and math.isfinite(exit_price)
            and entry_price > 0 and exit_price >= 0
        ):
            raise ValueError("invalid exact option execution price")
        rows.append({
            "cohort": signal.cohort,
            "cohort_block": int(signal.cohort_block),
            "date": str(signal.date),
            "signal_timestamp": pd.Timestamp(signal.timestamp),
            "entry_timestamp": entry_ts,
            "exit_timestamp": exit_ts,
            "direction": direction,
            "expiry": expiry,
            "strike": strike,
            "right": right,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_points": exit_price - entry_price,
        })
    return pd.DataFrame(rows)


def _cost_points(entry: np.ndarray, exit_: np.ndarray) -> np.ndarray:
    lot = float(COST_MODEL["lot_size"])
    brokerage = 2.0 * float(COST_MODEL["brokerage_per_order_rupees"]) / lot
    stt = exit_ * float(COST_MODEL["options_stt_sell_premium_rate"])
    exchange = (entry + exit_) * float(
        COST_MODEL["options_exchange_transaction_rate_each_side"]
    )
    sebi = (entry + exit_) * float(COST_MODEL["sebi_turnover_rate_each_side"])
    stamp = entry * float(COST_MODEL["options_stamp_buy_rate"])
    gst = float(COST_MODEL["gst_rate"]) * (brokerage + exchange + sebi)
    return brokerage + stt + exchange + sebi + stamp + gst


def _bootstrap(trades: pd.DataFrame, column: str, seed: int) -> list[float | None]:
    if trades.empty:
        return [None, None]
    grouped = trades.groupby("date")[column]
    sums = grouped.sum().to_numpy(dtype=float)
    counts = grouped.size().to_numpy(dtype=float)
    if len(sums) < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(sums), size=(BOOTSTRAP_DRAWS, len(sums)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return [float(lo), float(hi)]


def _summary(trades: pd.DataFrame, slippage: float, seed: int) -> dict[str, Any]:
    frame = trades.copy()
    entry = frame["entry_price"].to_numpy(dtype=float)
    exit_ = frame["exit_price"].to_numpy(dtype=float)
    costs = _cost_points(entry, exit_)
    frame["net_points"] = (
        frame["gross_points"].to_numpy(dtype=float) - costs - 2.0 * slippage
    )
    cohort_means = {}
    for cohort in ("cohort1", "cohort2"):
        values = frame.loc[frame["cohort"] == cohort, "net_points"]
        cohort_means[cohort] = float(values.mean()) if len(values) else None
    block_means = frame.groupby(["cohort", "cohort_block"])["net_points"].mean()
    return {
        "trades": int(len(frame)),
        "sessions_traded": int(frame["date"].nunique()),
        "gross_mean_points": float(frame["gross_points"].mean()),
        "mean_cost_points_before_slippage": float(np.mean(costs)),
        "pooled_net_mean_points": float(frame["net_points"].mean()),
        "cohort1_net_mean_points": cohort_means["cohort1"],
        "cohort2_net_mean_points": cohort_means["cohort2"],
        "win_rate_net": float((frame["net_points"] > 0).mean()),
        "positive_chronological_blocks": int((block_means > 0).sum()),
        "session_cluster_bootstrap_net_mean_95pct_points": _bootstrap(
            frame, "net_points", seed
        ),
    }


def _implementation_pass(summaries: dict[str, Any]) -> tuple[bool, list[str]]:
    primary = summaries["1.0"]
    severe = summaries["2.0"]
    failures: list[str] = []
    if (
        primary["cohort1_net_mean_points"] <= 0
        or primary["cohort2_net_mean_points"] <= 0
    ):
        failures.append("primary_slippage_not_positive_both_cohorts")
    if severe["pooled_net_mean_points"] <= 0:
        failures.append("two_point_slippage_pooled_not_positive")
    if primary["positive_chronological_blocks"] < int(
        PASS_CRITERIA[
            "minimum_positive_chronological_blocks_out_of_14_at_primary_slippage"
        ]
    ):
        failures.append("primary_slippage_block_stability")
    lower = primary["session_cluster_bootstrap_net_mean_95pct_points"][0]
    if lower is None or lower <= 0:
        failures.append("primary_slippage_bootstrap_lower_not_positive")
    return not failures, failures


def run(
    events: dict[str, Any],
    option_payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    frame = _event_frame(events)
    threshold = _validate_threshold(frame)
    signals = _signals(frame, threshold)
    expected = int(STRUCTURAL_RULE["expected_structural_signals"])
    if len(signals) != expected:
        raise ValueError(
            f"expected {expected} frozen structural signals, got {len(signals)}"
        )

    option_frames = []
    contracts: dict[str, str] = {}
    for cohort, payload in option_payloads.items():
        option_frames.append(_option_frame(payload, cohort))
        for day, expiry in (payload.get("contract_by_date") or {}).items():
            if day in contracts and contracts[day] != expiry:
                raise ValueError(f"conflicting option expiry for {day}")
            contracts[day] = expiry
    options = pd.concat(option_frames, ignore_index=True)

    cells = []
    seed_counter = 0
    variants = [
        str(OPTION_IMPLEMENTATION["primary_strike_variant"]),
        str(OPTION_IMPLEMENTATION["robustness_strike_variant"]),
    ]
    for strike in variants:
        trades = _attach_contracts(signals, options, contracts, strike)
        summaries = {}
        for slip in OPTION_IMPLEMENTATION["slippage_points_per_side"]:
            seed_counter += 1
            summaries[str(float(slip))] = _summary(
                trades, float(slip), BOOTSTRAP_SEED + 600 + seed_counter
            )
        passed, failures = _implementation_pass(summaries)
        cells.append({
            "strike_variant": strike,
            "role": "primary" if strike == "ATM" else "robustness_non_rescuing",
            "slippage_summaries": summaries,
            "implementation_pass": passed,
            "implementation_failures": failures,
        })

    primary = next(cell for cell in cells if cell["role"] == "primary")
    robustness = next(
        cell for cell in cells if cell["role"] == "robustness_non_rescuing"
    )
    decision = (
        "OPTION_IMPLEMENTATION_PASS_CANDIDATE_FREEZE_PROTOCOL_REQUIRED"
        if primary["implementation_pass"]
        else "REJECTED_NO_CANDIDATE_FREEZE"
    )
    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "source": SOURCE,
        "expected_option_sha256": EXPECTED_OPTION_SHA256,
        "structural_rule": STRUCTURAL_RULE,
        "option_implementation": OPTION_IMPLEMENTATION,
        "pass_criteria": PASS_CRITERIA,
        "validated_current_abs_return_threshold_bps": threshold,
        "structural_signal_count": int(len(signals)),
        "cells": cells,
        "primary_ATM_pass": bool(primary["implementation_pass"]),
        "robustness_ITM_pass": bool(robustness["implementation_pass"]),
        "robustness_ITM_can_rescue_primary": False,
        "decision": decision,
        "guardrails": GUARDRAILS,
        "next_research_decision": (
            "If primary_ATM_pass is true, freeze a candidate/validation protocol "
            "before any blind data are loaded. If ATM fails, reject without rescue "
            "regardless of the one-strike-ITM robustness result."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run frozen low-volume shock exact-option screen"
    )
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--cohort1-options", type=Path, required=True)
    parser.add_argument("--cohort2-options", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    events = _load_json_with_sha(args.events, str(SOURCE["event_dataset_sha256"]))
    option_payloads = {
        "cohort1": _load_json_with_sha(
            args.cohort1_options, EXPECTED_OPTION_SHA256["cohort1"]
        ),
        "cohort2": _load_json_with_sha(
            args.cohort2_options, EXPECTED_OPTION_SHA256["cohort2"]
        ),
    }
    report = run(events, option_payloads)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "decision": report["decision"],
        "primary_ATM_pass": report["primary_ATM_pass"],
        "robustness_ITM_pass": report["robustness_ITM_pass"],
    }, indent=2))


if __name__ == "__main__":
    main()
