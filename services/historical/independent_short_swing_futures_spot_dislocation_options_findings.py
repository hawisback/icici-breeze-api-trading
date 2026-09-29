"""Run frozen exact-option implementation for futures-spot dislocation reversion.

Consumes only the inspected development event corpus and the two inspected option
datasets with recorded SHA256 identities. No blind data, contract stitching,
alternate strikes, time/DTE filters, or stop/target search are permitted.
"""
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
    _add_dislocation_features,
    _non_overlapping,
)
from services.historical.independent_short_swing_futures_spot_dislocation_options_protocol import (
    COST_MODEL,
    GUARDRAILS,
    OPTION_IMPLEMENTATION,
    PASS_CRITERIA,
    PROTOCOL_VERSION,
    SOURCE,
    STRUCTURAL_RULE_FAMILY,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_FUTURES_SPOT_DISLOCATION_OPTIONS_FINDINGS_V1"
EXPECTED_OPTION_SHA256 = {
    "cohort1": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
    "cohort2": "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc",
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
        "futures_return_bps", "spot_close", "options_specific_fast_lead",
        "entry_timestamp",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"event schema missing {missing}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
    frame["entry_timestamp"] = pd.to_datetime(frame["entry_timestamp"]).dt.tz_localize(None)
    for name in ("futures_close", "futures_return_bps", "spot_close", "options_specific_fast_lead"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return _add_dislocation_features(
        frame.sort_values(["date", "timestamp"]).reset_index(drop=True)
    )


def _thresholds(frame: pd.DataFrame) -> dict[int, float]:
    values = frame["abs_return_dislocation_bps"].dropna().to_numpy(dtype=float)
    return {
        p: float(np.percentile(values, p))
        for p in STRUCTURAL_RULE_FAMILY["abs_return_dislocation_percentile_min"]
    }


def _signals(frame: pd.DataFrame, percentile: int, horizon: int, threshold: float) -> pd.DataFrame:
    mask = (
        frame["abs_return_dislocation_bps"].ge(threshold)
        & frame["direction"].ne(0)
        & frame["options_specific_fast_lead"].notna()
        & (frame["options_specific_fast_lead"] * frame["direction"]).gt(0)
    )
    selected = frame.loc[mask].copy()
    return _non_overlapping(selected, horizon_minutes=horizon)


def _attach_contracts(
    signals: pd.DataFrame,
    options: pd.DataFrame,
    contracts: dict[str, str],
    strike_variant: str,
    horizon: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    lookup = options.set_index(["timestamp", "expiry", "strike", "right"])
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
        if not (math.isfinite(entry_price) and math.isfinite(exit_price) and entry_price > 0 and exit_price >= 0):
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
    exchange = (entry + exit_) * float(COST_MODEL["options_exchange_transaction_rate_each_side"])
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
    frame["net_points"] = frame["gross_points"].to_numpy(dtype=float) - costs - 2.0 * slippage
    cohort_means = {}
    for cohort in ("cohort1", "cohort2"):
        vals = frame.loc[frame["cohort"] == cohort, "net_points"]
        cohort_means[cohort] = float(vals.mean()) if len(vals) else None
    block_means = frame.groupby(["cohort", "cohort_block"])["net_points"].mean()
    positive_blocks = int((block_means > 0).sum())
    return {
        "trades": int(len(frame)),
        "sessions_traded": int(frame["date"].nunique()),
        "gross_mean_points": float(frame["gross_points"].mean()),
        "mean_cost_points_before_slippage": float(np.mean(costs)),
        "pooled_net_mean_points": float(frame["net_points"].mean()),
        "cohort1_net_mean_points": cohort_means["cohort1"],
        "cohort2_net_mean_points": cohort_means["cohort2"],
        "win_rate_net": float((frame["net_points"] > 0).mean()),
        "positive_chronological_blocks": positive_blocks,
        "session_cluster_bootstrap_net_mean_95pct_points": _bootstrap(frame, "net_points", seed),
    }


def _cell_pass(summaries: dict[str, Any]) -> tuple[bool, list[str]]:
    primary = summaries["1.0"]
    severe = summaries["2.0"]
    failures: list[str] = []
    if primary["cohort1_net_mean_points"] <= 0 or primary["cohort2_net_mean_points"] <= 0:
        failures.append("primary_slippage_not_positive_both_cohorts")
    if severe["pooled_net_mean_points"] <= 0:
        failures.append("two_point_slippage_pooled_not_positive")
    if primary["positive_chronological_blocks"] < int(
        PASS_CRITERIA["minimum_positive_chronological_blocks_out_of_14_at_primary_slippage"]
    ):
        failures.append("primary_slippage_block_stability")
    lower = primary["session_cluster_bootstrap_net_mean_95pct_points"][0]
    if lower is None or lower <= 0:
        failures.append("primary_slippage_bootstrap_lower_not_positive")
    return not failures, failures


def run(events: dict[str, Any], option_payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    frame = _event_frame(events)
    thresholds = _thresholds(frame)
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
    for percentile in STRUCTURAL_RULE_FAMILY["abs_return_dislocation_percentile_min"]:
        for horizon in STRUCTURAL_RULE_FAMILY["fixed_exit_minutes"]:
            signals = _signals(frame, percentile, horizon, thresholds[percentile])
            for strike in OPTION_IMPLEMENTATION["strike_variants"]:
                trades = _attach_contracts(signals, options, contracts, strike, horizon)
                summaries = {}
                for slip in OPTION_IMPLEMENTATION["slippage_points_per_side"]:
                    seed_counter += 1
                    summaries[str(float(slip))] = _summary(
                        trades, float(slip), BOOTSTRAP_SEED + seed_counter
                    )
                passed, failures = _cell_pass(summaries)
                cells.append({
                    "cell_id": f"p{percentile}_h{horizon}_{strike}",
                    "percentile": percentile,
                    "threshold_bps": thresholds[percentile],
                    "exit_minutes": horizon,
                    "strike_variant": strike,
                    "slippage_summaries": summaries,
                    "option_cell_pass": passed,
                    "option_cell_failures": failures,
                })

    strike_pass_counts = {
        strike: sum(
            cell["option_cell_pass"] and cell["strike_variant"] == strike
            for cell in cells
        )
        for strike in OPTION_IMPLEMENTATION["strike_variants"]
    }
    neighborhood_passes = [
        strike for strike, count in strike_pass_counts.items() if count >= 3
    ]
    decision = (
        "OPTION_IMPLEMENTATION_PASS_CANDIDATE_FREEZE_PROTOCOL_REQUIRED"
        if neighborhood_passes
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
        "structural_rule_family": STRUCTURAL_RULE_FAMILY,
        "option_implementation": OPTION_IMPLEMENTATION,
        "pass_criteria": PASS_CRITERIA,
        "derived_abs_return_dislocation_thresholds_bps": {str(k): v for k, v in thresholds.items()},
        "cells": cells,
        "strike_pass_counts_out_of_4": strike_pass_counts,
        "neighborhood_passes": neighborhood_passes,
        "decision": decision,
        "guardrails": GUARDRAILS,
        "next_research_decision": (
            "If neighborhood_passes is non-empty, freeze a candidate/validation protocol "
            "before any blind data are loaded. Otherwise reject without rescue."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen futures-spot dislocation exact-option screen")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--cohort1-options", type=Path, required=True)
    parser.add_argument("--cohort2-options", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    events = _load_json_with_sha(args.events, str(SOURCE["event_dataset_sha256"]))
    option_payloads = {
        "cohort1": _load_json_with_sha(args.cohort1_options, EXPECTED_OPTION_SHA256["cohort1"]),
        "cohort2": _load_json_with_sha(args.cohort2_options, EXPECTED_OPTION_SHA256["cohort2"]),
    }
    report = run(events, option_payloads)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "decision": report["decision"],
        "strike_pass_counts_out_of_4": report["strike_pass_counts_out_of_4"],
        "neighborhood_passes": report["neighborhood_passes"],
    }, indent=2))


if __name__ == "__main__":
    main()
