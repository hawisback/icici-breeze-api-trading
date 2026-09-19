"""Deterministic metadata and fingerprints for historical replay.

This module is replay infrastructure only.  It describes the inputs supplied
to the existing simulation path and never calculates a signal or trade state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.models import Candle
from services.strategy.models import (
    HistoricalReplaySource,
    SessionTimersConfig,
    StrategyTunablesConfig,
    ThresholdOverrides,
)


class ReplayConfigurationSnapshot(BaseModel):
    """Immutable effective replay settings used for one session replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    replay_start_date: str
    replay_end_date: str
    instrument_id: str
    historical_source: HistoricalReplaySource
    bypass_entry_window: bool
    strategy_a_enabled: bool
    threshold_overrides: dict[str, Any]
    strategy_a: dict[str, Any]
    entry_window: dict[str, str]
    setup_window: dict[str, int]
    warmup: dict[str, Any]
    indicator_warmup_requirements: dict[str, int]
    futures_selection_rule: str


class ReplayDataFingerprint(BaseModel):
    """Counts, coverage, source diagnostics, and hash for selected inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: HistoricalReplaySource
    spot_candle_count: int
    futures_candle_count: int
    spot_earliest_timestamp: str | None = None
    spot_latest_timestamp: str | None = None
    futures_earliest_timestamp: str | None = None
    futures_latest_timestamp: str | None = None
    requested_date_range: dict[str, str]
    observed_date_range: dict[str, str | None]
    futures_contracts: list[dict[str, str | None]] = Field(default_factory=list)
    source_diagnostics: dict[str, Any] = Field(default_factory=dict)
    missing_data: list[str] = Field(default_factory=list)
    dataset_hash: str


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(f"Unsupported fingerprint value: {type(value)!r}")


def _canonical_candle(role: str, candle: Candle) -> dict[str, Any]:
    return {
        "role": role,
        "instrument_id": candle.instrument_id,
        "interval": candle.interval,
        "start_time": candle.start_time.astimezone(timezone.utc).isoformat(),
        "end_time": candle.end_time.astimezone(timezone.utc).isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "open_interest": candle.open_interest,
        "source": candle.source,
    }


def _sorted_candles(role: str, candles: Iterable[Candle]) -> list[dict[str, Any]]:
    rows = [_canonical_candle(role, candle) for candle in candles]
    return sorted(rows, key=lambda row: (
        row["role"],
        row["instrument_id"],
        row["interval"],
        row["start_time"],
        row["source"],
    ))


def _timestamp_range(candles: list[Candle]) -> tuple[str | None, str | None]:
    if not candles:
        return None, None
    ordered = sorted(candles, key=lambda candle: candle.start_time)
    return (
        ordered[0].start_time.astimezone(timezone.utc).isoformat(),
        ordered[-1].end_time.astimezone(timezone.utc).isoformat(),
    )


def build_configuration_snapshot(
    *,
    start_date: str,
    end_date: str,
    instrument_id: str,
    historical_source: HistoricalReplaySource,
    bypass_entry_window: bool,
    strategy_a_enabled: bool,
    overrides: ThresholdOverrides,
    tunables: StrategyTunablesConfig,
    session: SessionTimersConfig,
) -> ReplayConfigurationSnapshot:
    """Capture effective values without changing strategy construction."""

    # These are the existing Strategy A constructor/decision defaults.  They
    # are recorded here, not applied here, so the strategy remains authoritative.
    def effective(name: str, default: Any) -> Any:
        value = getattr(overrides, name, None)
        return default if value is None else value

    strategy_a = {
        "adx_threshold": effective("adx_threshold", tunables.adx_threshold),
        "rvol_threshold": effective("rvol_threshold", tunables.rvol_threshold),
        "ema_slope_threshold": effective("ema_slope_threshold", tunables.ema_slope_threshold),
        "min_impulse_atr": effective("min_impulse_atr", 0.70),
        "pullback_duration_min_bars": 1,
        "pullback_duration_max_bars": 9,
        # Keep the legacy keys for readers of older metadata while recording
        # the production candidate's directional bands explicitly.
        "min_pullback_depth": effective("min_pullback_depth", tunables.call_pullback_min_depth),
        "max_pullback_depth": effective("max_pullback_depth", tunables.call_pullback_max_depth),
        "call_pullback_min_depth": effective("min_pullback_depth", tunables.call_pullback_min_depth),
        "call_pullback_max_depth": effective("max_pullback_depth", tunables.call_pullback_max_depth),
        "put_pullback_min_depth": effective("min_pullback_depth", tunables.put_pullback_min_depth),
        "put_pullback_max_depth": effective("max_pullback_depth", tunables.put_pullback_max_depth),
        "put_pullback_max_exclusive": True,
        "retest_tolerance_atr": effective("retest_tolerance_atr", 0.45),
        "breakout_buffer_atr": effective("breakout_buffer_atr", 0.02),
        "breakout_confirm_polls": effective("breakout_confirm_polls", 1),
        "min_available_confirmations": effective("min_available_confirmations", 2),
        "min_confirmation_score": effective("min_confirmation_score", tunables.min_confirmation_score),
        "derivatives_threshold": {
            "bullish": effective("bull_derivatives_score", 1.0),
            "bearish": effective("bear_derivatives_score", 1.0),
        },
        "structural_r_max_atr": 1.60,
        "initial_stop_offset_atr": 0.15,
    }
    return ReplayConfigurationSnapshot(
        replay_start_date=start_date,
        replay_end_date=end_date,
        instrument_id=instrument_id,
        historical_source=historical_source,
        bypass_entry_window=bypass_entry_window,
        strategy_a_enabled=strategy_a_enabled,
        threshold_overrides=overrides.model_dump(mode="json"),
        strategy_a=strategy_a,
        entry_window={
            "no_new_trade_before_ist": session.no_new_trade_before,
            "no_new_trade_after_ist": session.no_new_trade_after,
        },
        setup_window={
            "max_pre_cutoff_candles": 4,
            "max_post_cutoff_candles": 15,
        },
        warmup={
            "calendar_days": 7,
            "session_start_ist": "09:15",
            "session_end_ist": "15:30",
            "provider_fallback": False,
            "synthetic_fallback": False,
        },
        indicator_warmup_requirements={
            "minimum_5m_candles": 29,
            "minimum_15m_candles": 50,
            "minimum_futures_5m_candles": 15,
        },
        futures_selection_rule=(
            "Select the earliest NIFTY FUTURES contract with expiry >= replay date; "
            "replay only its selected historical source candles."
        ),
    )


def configuration_fingerprint(snapshot: ReplayConfigurationSnapshot) -> str:
    canonical = json.dumps(
        snapshot.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_data_fingerprint(
    *,
    source: HistoricalReplaySource,
    start_date: str,
    end_date: str,
    spot_candles: list[Candle],
    futures_candles: list[Candle],
    source_diagnostics: dict[str, Any],
    futures_contracts: list[dict[str, str | None]],
    missing_data: list[str],
) -> ReplayDataFingerprint:
    spot_earliest, spot_latest = _timestamp_range(spot_candles)
    futures_earliest, futures_latest = _timestamp_range(futures_candles)
    all_candles = spot_candles + futures_candles
    rows = _sorted_candles("spot", spot_candles) + _sorted_candles("futures", futures_candles)
    canonical = {
        "source": source.value,
        "requested_date_range": {"start": start_date, "end": end_date},
        "futures_contracts": sorted(futures_contracts, key=lambda item: (
            item.get("instrument_id") or "",
            item.get("expiry") or "",
        )),
        "candles": rows,
    }
    dataset_hash = hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=_json_default,
        ).encode("utf-8")
    ).hexdigest()
    observed = _timestamp_range(all_candles)
    return ReplayDataFingerprint(
        source=source,
        spot_candle_count=len(spot_candles),
        futures_candle_count=len(futures_candles),
        spot_earliest_timestamp=spot_earliest,
        spot_latest_timestamp=spot_latest,
        futures_earliest_timestamp=futures_earliest,
        futures_latest_timestamp=futures_latest,
        requested_date_range={"start": start_date, "end": end_date},
        observed_date_range={"earliest": observed[0], "latest": observed[1]},
        futures_contracts=futures_contracts,
        source_diagnostics=source_diagnostics,
        missing_data=sorted(set(missing_data)),
        dataset_hash=dataset_hash,
    )


def combine_replay_metadata(metadata_items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build deterministic range metadata from per-session replay results."""
    items = list(metadata_items)
    if not items:
        raise ValueError("at least one replay metadata item is required")

    snapshots = [
        ReplayConfigurationSnapshot.model_validate(item["configuration_snapshot"])
        for item in items
    ]
    start_date = min(snapshot.replay_start_date for snapshot in snapshots)
    end_date = max(snapshot.replay_end_date for snapshot in snapshots)
    aggregate_snapshot = snapshots[0].model_copy(update={
        "replay_start_date": start_date,
        "replay_end_date": end_date,
    })
    config_hash = configuration_fingerprint(aggregate_snapshot)

    data_items = [item["data_fingerprint"] for item in items]
    source = HistoricalReplaySource(data_items[0]["source"])
    spot_count = sum(int(item["spot_candle_count"]) for item in data_items)
    futures_count = sum(int(item["futures_candle_count"]) for item in data_items)

    def min_present(key: str) -> str | None:
        values = [item[key] for item in data_items if item.get(key)]
        return min(values) if values else None

    def max_present(key: str) -> str | None:
        values = [item[key] for item in data_items if item.get(key)]
        return max(values) if values else None

    def nested_min(container: str, key: str) -> str | None:
        values = [
            item.get(container, {}).get(key)
            for item in data_items
            if item.get(container, {}).get(key)
        ]
        return min(values) if values else None

    def nested_max(container: str, key: str) -> str | None:
        values = [
            item.get(container, {}).get(key)
            for item in data_items
            if item.get(container, {}).get(key)
        ]
        return max(values) if values else None

    diagnostics: dict[str, Any] = {}
    for role in ("spot", "futures"):
        available: dict[str, int] = {}
        selected: dict[str, int] = {}
        for item in data_items:
            role_diag = item.get("source_diagnostics", {}).get(role, {})
            for source_name, count in role_diag.get("available_before_filter", {}).items():
                available[source_name] = available.get(source_name, 0) + int(count)
            for source_name, count in role_diag.get("selected_after_filter", {}).items():
                selected[source_name] = selected.get(source_name, 0) + int(count)
        diagnostics[role] = {
            "requested_source": source.value,
            "available_before_filter": dict(sorted(available.items())),
            "selected_after_filter": dict(sorted(selected.items())),
            "selected_count": sum(selected.values()),
            "missing_selected_source": any(
                item.get("source_diagnostics", {}).get(role, {}).get("missing_selected_source", False)
                for item in data_items
            ),
        }

    contracts = {
        (contract.get("instrument_id"), contract.get("expiry")): contract
        for item in data_items
        for contract in item.get("futures_contracts", [])
    }
    per_session_hashes = [
        {
            "date": item["requested_date_range"]["start"],
            "dataset_hash": item["dataset_hash"],
        }
        for item in data_items
    ]
    canonical = {
        "source": source.value,
        "requested_date_range": {"start": start_date, "end": end_date},
        "sessions": sorted(per_session_hashes, key=lambda item: item["date"]),
    }
    aggregate_data_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    aggregate_data = ReplayDataFingerprint(
        source=source,
        spot_candle_count=spot_count,
        futures_candle_count=futures_count,
        spot_earliest_timestamp=min_present("spot_earliest_timestamp"),
        spot_latest_timestamp=max_present("spot_latest_timestamp"),
        futures_earliest_timestamp=min_present("futures_earliest_timestamp"),
        futures_latest_timestamp=max_present("futures_latest_timestamp"),
        requested_date_range={"start": start_date, "end": end_date},
        observed_date_range={
            "earliest": nested_min("observed_date_range", "earliest"),
            "latest": nested_max("observed_date_range", "latest"),
        },
        futures_contracts=sorted(contracts.values(), key=lambda item: (
            item.get("instrument_id") or "",
            item.get("expiry") or "",
        )),
        source_diagnostics=diagnostics,
        missing_data=sorted({
            missing
            for item in data_items
            for missing in item.get("missing_data", [])
        }),
        dataset_hash=aggregate_data_hash,
    )
    return {
        "configuration_snapshot": aggregate_snapshot.model_dump(mode="json"),
        "configuration_fingerprint": config_hash,
        "data_fingerprint": aggregate_data.model_dump(mode="json"),
        "historical_source": source.value,
        "bypass_entry_window": aggregate_snapshot.bypass_entry_window,
        "missing_data": aggregate_data.missing_data,
    }
