"""Build the corrected 232-session inspected short-swing development corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_cohort2_replication import (
    _crossfit_residual,
    build_frame,
)
from services.historical.independent_cohort3_protocol import (
    validate_auxiliary,
    validate_intrabar as validate_cohort3_intrabar_shape,
    validate_market,
    validate_options,
)
from services.historical.independent_short_swing_development_v2_protocol import (
    COHORTS,
    CORPUS_ROLE,
    EXPECTED,
    FEATURE_SEMANTICS_V2,
    FROZEN_INPUTS,
    GUARDRAILS,
    OUTCOME_POLICY,
    PROTOCOL_VERSION,
)
from services.historical.independent_short_swing_event_dataset import (
    _attach_execution_outcomes,
    validate_intrabar,
)

RESEARCH_TYPE = "NIFTY_SHORT_SWING_DEVELOPMENT_EVENTS_V2"
HORIZONS = (5, 10, 15, 30)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_frozen(path: Path, expected_sha256: str) -> dict[str, Any]:
    actual = _sha256(path)
    if actual != expected_sha256:
        raise ValueError(f"SHA256 changed for {path}: {actual} != {expected_sha256}")
    return _load(path)


def _full_row_stat(
    parts: list[pd.Series],
    operation: str,
) -> tuple[pd.Series, pd.Series]:
    table = pd.concat(parts, axis=1)
    full = table.notna().all(axis=1)
    if operation == "max":
        value = table.max(axis=1, skipna=False)
    elif operation == "min":
        value = table.min(axis=1, skipna=False)
    elif operation == "mean":
        value = table.mean(axis=1, skipna=False)
    else:
        raise ValueError(f"unsupported operation {operation}")
    return value.where(full), full


def _rolling_return_sum(
    frame: pd.DataFrame,
    returns: pd.Series,
    window: int,
) -> pd.Series:
    temp = pd.DataFrame({
        "date": frame["date"],
        "value": returns.abs(),
    })
    return (
        temp["value"]
        .groupby(temp["date"])
        .rolling(window=window, min_periods=window)
        .sum()
        .reset_index(level=0, drop=True)
    )


def _recompute_state_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Recompute V2 state features with exact same-session full windows."""
    result = frame.copy()
    result = result.sort_values(
        ["cohort", "date", "timestamp"]
    ).reset_index(drop=True)
    result["block"] = pd.to_numeric(
        result["cohort_block"], errors="raise"
    ).astype(int)

    group = result.groupby("date", sort=False)
    previous_close = group["futures_close"].shift(1)
    result["futures_return_bps"] = (
        result["futures_close"] / previous_close - 1.0
    ) * 10000.0
    result["current_abs_return_bps"] = result["futures_return_bps"].abs()

    previous_oi = group["futures_open_interest"].shift(1)
    result["oi_change_bps"] = (
        result["futures_open_interest"] / previous_oi - 1.0
    ) * 10000.0

    for window in (3, 6):
        start_open = group["futures_open"].shift(window - 1)
        highs, high_full = _full_row_stat(
            [group["futures_high"].shift(lag) for lag in range(window)],
            "max",
        )
        lows, low_full = _full_row_stat(
            [group["futures_low"].shift(lag) for lag in range(window)],
            "min",
        )
        full = high_full & low_full & start_open.notna()
        highs = highs.where(full)
        lows = lows.where(full)
        start = start_open.where(full)

        result[f"range_{window}_bps"] = (
            (highs - lows) / start * 10000.0
        ).where(full)
        width = (highs - lows).replace(0.0, np.nan)
        result[f"close_location_{window}"] = (
            2.0 * (result["futures_close"] - lows) / width - 1.0
        ).where(full)
        result[f"net_return_{window}_bps"] = (
            (result["futures_close"] / start - 1.0) * 10000.0
        ).where(full)
        result[f"path_length_{window}_bps"] = _rolling_return_sum(
            result, result["futures_return_bps"], window
        )

    prior_high, prior_full_h = _full_row_stat(
        [group["futures_high"].shift(lag) for lag in (1, 2, 3)],
        "max",
    )
    prior_low, prior_full_l = _full_row_stat(
        [group["futures_low"].shift(lag) for lag in (1, 2, 3)],
        "min",
    )
    prior_volume_mean, prior_full_v = _full_row_stat(
        [group["futures_volume"].shift(lag) for lag in (1, 2, 3)],
        "mean",
    )
    prior_full = prior_full_h & prior_full_l & prior_full_v
    result["prior_3_high"] = prior_high.where(prior_full)
    result["prior_3_low"] = prior_low.where(prior_full)
    result["prior_3_volume_mean"] = prior_volume_mean.where(prior_full)
    result["volume_vs_prior3_mean"] = (
        result["futures_volume"]
        / result["prior_3_volume_mean"].replace(0.0, np.nan)
    ).where(prior_full)

    up_breakout = (
        result["futures_high"] / result["prior_3_high"] - 1.0
    ) * 10000.0
    down_breakout = (
        result["prior_3_low"] / result["futures_low"] - 1.0
    ) * 10000.0
    result["up_breakout_bps"] = up_breakout.clip(lower=0.0).where(prior_full)
    result["down_breakout_bps"] = down_breakout.clip(lower=0.0).where(prior_full)
    result["close_vs_prior3_high_bps"] = (
        result["futures_close"] / result["prior_3_high"] - 1.0
    ) * 10000.0
    result["close_vs_prior3_low_bps"] = (
        result["futures_close"] / result["prior_3_low"] - 1.0
    ) * 10000.0

    breakout_conditions = {
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
    }
    for name, condition in breakout_conditions.items():
        result[name] = condition.astype("boolean").where(prior_full, pd.NA)

    result["vix_5m_change_bps"] = (
        result["vix_close"] / group["vix_close"].shift(1) - 1.0
    ) * 10000.0
    result["vix_15m_change_bps"] = (
        result["vix_close"] / group["vix_close"].shift(3) - 1.0
    ) * 10000.0
    spot_return_bps = (
        result["spot_close"] / group["spot_close"].shift(1) - 1.0
    ) * 10000.0
    result["spot_gap_bps"] = spot_return_bps - result["futures_return_bps"]

    residual = pd.Series(np.nan, index=result.index, dtype=float)
    for cohort, index in result.groupby("cohort", sort=False).groups.items():
        cohort_frame = result.loc[index].copy()
        cohort_residual = _crossfit_residual(
            cohort_frame,
            "options_gap_bps",
            numeric_controls=["spot_gap_bps", "futures_return_bps"],
            categorical_controls=[],
        )
        residual.loc[index] = cohort_residual.loc[index]
    result["options_specific_fast_lead"] = residual
    result["options_specific_fast_lead_abs"] = residual.abs()
    return result


def _outcome_columns() -> list[str]:
    columns = ["entry_timestamp", "entry_price", "entry_gap_bps"]
    for horizon in HORIZONS:
        prefix = f"h{horizon}m"
        columns.extend([
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
        ])
    return columns


def _event_columns() -> list[str]:
    columns = [
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
    ]
    columns.extend(_outcome_columns())
    return columns


def _build_cohort3(
    market: dict[str, Any],
    vix: dict[str, Any],
    options: dict[str, Any],
    spot: dict[str, Any],
    intrabar: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    validate_market(market)
    validate_auxiliary(vix, "vix")
    validate_options(options)
    validate_auxiliary(spot, "spot")
    validate_cohort3_intrabar_shape(intrabar)

    qa = validate_intrabar(
        intrabar,
        market,
        expected_sessions=int(COHORTS["cohort3"]["sessions"]),
        expected_rows=int(COHORTS["cohort3"]["one_minute_rows"]),
    )
    frame = build_frame(
        market,
        vix,
        options,
        spot,
        block_size=int(COHORTS["cohort3"]["block_size"]),
    )
    if len(frame) != int(COHORTS["cohort3"]["five_minute_rows"]):
        raise ValueError("Cohort 3 five-minute row count changed")
    frame["cohort"] = "cohort3"
    frame["cohort_block"] = frame["block"].astype(int)
    frame = _attach_execution_outcomes(frame, intrabar)
    return frame, qa


def _parse_event_frame(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("blind_data_used") is not False:
        raise ValueError("V1 event input must remain inspected development data")
    if int(payload.get("sessions", -1)) != 152:
        raise ValueError("V1 event input session count changed")
    if int(payload.get("five_minute_events", -1)) != 11400:
        raise ValueError("V1 event input row count changed")
    frame = pd.DataFrame(payload["events"]).copy()
    if len(frame) != 11400:
        raise ValueError("V1 event rows changed")
    counts = frame["cohort"].value_counts().to_dict()
    if counts != {"cohort1": 6000, "cohort2": 5400}:
        raise ValueError(f"V1 cohort row counts changed: {counts}")
    return frame


def _normalize_timestamps(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"])
    result["entry_timestamp"] = pd.to_datetime(result["entry_timestamp"])
    result["date"] = result["timestamp"].dt.date.astype(str)
    return result


def _assert_window_counts(frame: pd.DataFrame) -> dict[str, int]:
    observed = {
        "current_return": int(frame["futures_return_bps"].notna().sum()),
        "range_3": int(frame["range_3_bps"].notna().sum()),
        "range_6": int(frame["range_6_bps"].notna().sum()),
        "prior_3": int(frame["prior_3_high"].notna().sum()),
        "path_length_3": int(frame["path_length_3_bps"].notna().sum()),
        "path_length_6": int(frame["path_length_6_bps"].notna().sum()),
    }
    expected = EXPECTED["pure_window_eligible_rows"]
    if observed != expected:
        raise ValueError(
            f"V2 full-window row counts changed: {observed} != {expected}"
        )
    return observed


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    serial = frame.copy()
    for name in ("timestamp", "entry_timestamp"):
        serial[name] = serial[name].map(
            lambda value: value.isoformat() if pd.notna(value) else None
        )
    return json.loads(serial.to_json(orient="records"))


def build_dataset(
    base_v1: dict[str, Any],
    *,
    manifest: dict[str, Any],
    cohort3_market: dict[str, Any],
    cohort3_vix: dict[str, Any],
    cohort3_options: dict[str, Any],
    cohort3_spot: dict[str, Any],
    cohort3_intrabar: dict[str, Any],
) -> dict[str, Any]:
    legacy = _normalize_timestamps(_parse_event_frame(base_v1))
    legacy_outcomes_before = legacy[_outcome_columns()].copy(deep=True)

    c3, c3_intrabar_qa = _build_cohort3(
        cohort3_market,
        cohort3_vix,
        cohort3_options,
        cohort3_spot,
        cohort3_intrabar,
    )
    c3 = _normalize_timestamps(c3)

    combined = pd.concat([legacy, c3], ignore_index=True, sort=False)
    combined = _recompute_state_features(combined)

    legacy_after = combined.loc[
        combined["cohort"].isin(["cohort1", "cohort2"])
    ].sort_values(["date", "timestamp"]).reset_index(drop=True)
    legacy_before_sorted = legacy.sort_values(
        ["date", "timestamp"]
    ).reset_index(drop=True)
    if not legacy_before_sorted[_outcome_columns()].equals(
        legacy_after[_outcome_columns()]
    ):
        raise ValueError("V1 execution outcomes changed during V2 feature correction")
    if not legacy_outcomes_before.shape[0] == 11400:
        raise ValueError("unexpected V1 outcome row count")

    combined = combined.sort_values(["date", "timestamp"]).reset_index(drop=True)
    combined["development_event_id"] = np.arange(1, len(combined) + 1)
    if len(combined) != int(EXPECTED["five_minute_events"]):
        raise ValueError("expanded development event count changed")
    if int(combined["date"].nunique()) != int(EXPECTED["sessions"]):
        raise ValueError("expanded development session count changed")

    cohort_rows = combined["cohort"].value_counts().to_dict()
    expected_rows = {
        key: int(value["five_minute_rows"]) for key, value in COHORTS.items()
    }
    if cohort_rows != expected_rows:
        raise ValueError(f"expanded cohort row counts changed: {cohort_rows}")

    scorable = {
        str(horizon): int(combined[f"h{horizon}m_terminal_bps"].notna().sum())
        for horizon in HORIZONS
    }
    if scorable != EXPECTED["scorable_events_by_horizon"]:
        raise ValueError(
            f"expanded fixed-horizon scorable counts changed: {scorable}"
        )
    window_counts = _assert_window_counts(combined)

    required = _event_columns()
    missing = sorted(set(required) - set(combined.columns))
    if missing:
        raise ValueError(f"expanded event columns missing: {missing}")
    output = combined[required].copy()

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "corpus_role": CORPUS_ROLE,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "sessions": int(EXPECTED["sessions"]),
        "five_minute_events": int(len(output)),
        "cohort_rows": {
            cohort: int((output["cohort"] == cohort).sum())
            for cohort in COHORTS
        },
        "chronological_blocks": int(EXPECTED["chronological_blocks"]),
        "scorable_events_by_horizon": scorable,
        "expected_scorable_events_by_horizon": EXPECTED[
            "scorable_events_by_horizon"
        ],
        "feature_window_counts": window_counts,
        "feature_semantics_v2": FEATURE_SEMANTICS_V2,
        "outcome_policy": OUTCOME_POLICY,
        "legacy_v1_execution_outcomes_preserved": True,
        "cohort3_intrabar_qa": c3_intrabar_qa,
        "source_manifest": manifest,
        "guardrails": GUARDRAILS,
        "events": _json_records(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build corrected 232-session short-swing development events"
    )
    parser.add_argument("--base-v1-events", type=Path, required=True)
    parser.add_argument("--cohort3-manifest", type=Path, required=True)
    parser.add_argument("--cohort3-market", type=Path, required=True)
    parser.add_argument("--cohort3-vix", type=Path, required=True)
    parser.add_argument("--cohort3-options", type=Path, required=True)
    parser.add_argument("--cohort3-spot", type=Path, required=True)
    parser.add_argument("--cohort3-intrabar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base_v1 = _load_frozen(
        args.base_v1_events,
        FROZEN_INPUTS["cohort1_cohort2_event_v1"]["sha256"],
    )
    manifest = _load_frozen(
        args.cohort3_manifest,
        FROZEN_INPUTS["cohort3_source_manifest"]["sha256"],
    )
    frozen_sources = FROZEN_INPUTS["cohort3_source_manifest"]["sources"]
    paths = {
        "market": args.cohort3_market,
        "vix": args.cohort3_vix,
        "options": args.cohort3_options,
        "spot": args.cohort3_spot,
        "intrabar": args.cohort3_intrabar,
    }
    loaded_sources = {
        name: _load_frozen(path, str(frozen_sources[name]))
        for name, path in paths.items()
    }

    manifest_sources = manifest.get("sources") or {}
    for name, expected_sha in frozen_sources.items():
        manifest_sha = str((manifest_sources.get(name) or {}).get("sha256"))
        if manifest_sha != expected_sha:
            raise ValueError(
                f"manifest source hash changed for {name}: "
                f"{manifest_sha} != {expected_sha}"
            )
    if str((manifest.get("audit") or {}).get("sha256")) != str(
        FROZEN_INPUTS["cohort3_source_manifest"]["audit_sha256"]
    ):
        raise ValueError("Cohort-3 audit hash changed inside manifest")

    report = build_dataset(
        base_v1,
        manifest=manifest,
        cohort3_market=loaded_sources["market"],
        cohort3_vix=loaded_sources["vix"],
        cohort3_options=loaded_sources["options"],
        cohort3_spot=loaded_sources["spot"],
        cohort3_intrabar=loaded_sources["intrabar"],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "protocol_version": report["protocol_version"],
        "sessions": report["sessions"],
        "five_minute_events": report["five_minute_events"],
        "cohort_rows": report["cohort_rows"],
        "scorable_events_by_horizon": report["scorable_events_by_horizon"],
        "feature_window_counts": report["feature_window_counts"],
        "legacy_v1_execution_outcomes_preserved": (
            report["legacy_v1_execution_outcomes_preserved"]
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
