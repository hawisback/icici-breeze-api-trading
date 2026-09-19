"""Run the unchanged deterministic Strategy A replay over an extended range.

This is an analysis/reporting command.  It does not alter Strategy A, its
thresholds, PositionManager, live trading, or replay lifecycle code.  The
controlled PUT-only view is formed from the full replay's PUT manifests, which
keeps PUT signal discovery and lifecycle inputs identical while excluding CALL
entries from the experiment output.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import mean, median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from libs.config.settings import get_platform_settings
from libs.contracts.models import Candle
from services.historical.repository import HistoricalRepository
from services.historical.service import HistoricalService
from services.instrument.repository import InstrumentRepository
from services.instrument.service import InstrumentService
from services.strategy.models import HistoricalReplaySource, SimulationRequest
from services.strategy.replay_lifecycle import build_lifecycle_report
from services.strategy.replay_manifest import ReplayManifestRecord
from services.strategy.replay_metadata import (
    ReplayConfigurationSnapshot,
    build_data_fingerprint,
    configuration_fingerprint,
)
from services.strategy.simulation import SimulationEngine

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
EXPECTED_CANDLES = 76
MIN_RELIABLE_CANDLES = 75
FIVE_MINUTES = timedelta(minutes=5)


def _date_range(start: date, end: date) -> list[str]:
    result: list[str] = []
    cursor = start
    while cursor <= end:
        result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def _local_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp).astimezone(IST).date().isoformat()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _bucket_time(timestamp: datetime) -> str:
    hour = timestamp.astimezone(IST).hour
    return {
        9: "09:20-09:59",
        10: "10:00-10:59",
        11: "11:00-11:59",
        12: "12:00-12:59",
        13: "13:00-13:59",
    }.get(hour, "14:00-14:45")


def _records(value: Iterable[dict[str, Any] | ReplayManifestRecord]) -> list[ReplayManifestRecord]:
    return [item if isinstance(item, ReplayManifestRecord) else ReplayManifestRecord.model_validate(item) for item in value]


def _stats(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    resolved = [row for row in records if row.lifecycle_status == "RESOLVED" and row.realized_r is not None]
    rs = [float(row.realized_r) for row in resolved]
    winners = [value for value in rs if value > 0]
    losers = [value for value in rs if value < 0]
    return {
        "signals": len(records),
        "resolved": len(resolved),
        "ambiguous": sum(1 for row in records if row.lifecycle_status == "AMBIGUOUS"),
        "unresolved": sum(1 for row in records if row.lifecycle_status == "UNRESOLVED"),
        "trades": len(resolved),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate_pct": round(len(winners) / len(rs) * 100, 2) if rs else 0.0,
        "average_winner_r": round(mean(winners), 4) if winners else 0.0,
        "average_loser_r": round(mean(losers), 4) if losers else 0.0,
        "average_r": round(mean(rs), 4) if rs else 0.0,
        "median_r": round(median(rs), 4) if rs else 0.0,
        "profit_factor": round(sum(winners) / abs(sum(losers)), 4) if losers else 0.0,
        "cumulative_r": round(sum(rs), 4),
        "max_consecutive_losses": _max_consecutive_losses(rs),
    }


def _max_consecutive_losses(values: list[float]) -> int:
    best = current = 0
    for value in values:
        if value < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _grouped_stats(records: list[ReplayManifestRecord], key_fn) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ReplayManifestRecord]] = defaultdict(list)
    for row in records:
        grouped[str(key_fn(row))].append(row)
    return {key: _stats(group) for key, group in sorted(grouped.items())}


def _monthly(records: list[ReplayManifestRecord]) -> tuple[dict[str, Any], dict[str, Any]]:
    monthly = _grouped_stats(records, lambda row: row.trading_date[:7])
    active = [value for value in monthly.values() if value["trades"]]
    profitable = sum(1 for value in active if value["cumulative_r"] > 0)
    losing = sum(1 for value in active if value["cumulative_r"] < 0)
    summary = {
        "number_of_months": len(active),
        "profitable_months": profitable,
        "losing_months": losing,
        "percentage_profitable_months": round(profitable / len(active) * 100, 2) if active else 0.0,
    }
    return monthly, summary


def _quarter(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    return {
        key: {
            field: value[field]
            for field in ("trades", "average_r", "profit_factor", "cumulative_r")
        }
        for key, value in _grouped_stats(records, lambda row: f"{row.trading_date[:4]}-Q{(int(row.trading_date[5:7]) - 1) // 3 + 1}").items()
    }


def _chronological_splits(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda row: row.simulated_entry_timestamp)
    if not ordered:
        return {}
    n = len(ordered)
    split_points = {
        "first_50_pct": (0, (n + 1) // 2),
        "second_50_pct": ((n + 1) // 2, n),
    }
    if n >= 6:
        first = (n + 2) // 3
        second = (2 * n + 2) // 3
        split_points.update({"first_third": (0, first), "second_third": (first, second), "third_third": (second, n)})
    output = {}
    for label, (start, end) in split_points.items():
        subset = ordered[start:end]
        stats = _stats(subset)
        stats["date_range"] = {
            "start": subset[0].trading_date if subset else None,
            "end": subset[-1].trading_date if subset else None,
        }
        output[label] = stats
    return output


def _drawdown(records: list[ReplayManifestRecord]) -> dict[str, Any]:
    rows = sorted(
        [row for row in records if row.lifecycle_status == "RESOLVED" and row.realized_r is not None],
        key=lambda row: row.simulated_entry_timestamp,
    )
    equity = 0.0
    peak = 0.0
    peak_date: datetime | None = rows[0].simulated_entry_timestamp if rows else None
    open_drawdown: dict[str, Any] | None = None
    episodes: list[dict[str, Any]] = []
    for row in rows:
        timestamp = row.simulated_entry_timestamp
        value = float(row.realized_r or 0.0)
        equity += value
        if equity < peak:
            if open_drawdown is None:
                open_drawdown = {
                    "start": peak_date,
                    "peak_equity": peak,
                    "trough": timestamp,
                    "trough_equity": equity,
                    "recovery": None,
                }
            elif equity < open_drawdown["trough_equity"]:
                open_drawdown["trough"] = timestamp
                open_drawdown["trough_equity"] = equity
        else:
            if open_drawdown is not None:
                open_drawdown["recovery"] = timestamp
                episodes.append(open_drawdown)
                open_drawdown = None
            peak = equity
            peak_date = timestamp
    if open_drawdown is not None:
        episodes.append(open_drawdown)
    max_episode = max(episodes, key=lambda item: item["peak_equity"] - item["trough_equity"], default=None)
    longest_episode = max(
        episodes,
        key=lambda item: ((item["recovery"] or rows[-1].simulated_entry_timestamp) - item["start"]).total_seconds(),
        default=None,
    )
    max_drawdown = (max_episode["peak_equity"] - max_episode["trough_equity"]) if max_episode else 0.0
    longest_seconds = (
        ((longest_episode["recovery"] or rows[-1].simulated_entry_timestamp) - longest_episode["start"]).total_seconds()
        if longest_episode else 0.0
    )
    return {
        "maximum_peak_to_trough_drawdown_r": round(max_drawdown, 4),
        "drawdown_start": max_episode["start"].isoformat() if max_episode and max_episode["start"] else None,
        "drawdown_trough": max_episode["trough"].isoformat() if max_episode else None,
        "recovery_date_if_recovered": max_episode["recovery"].isoformat() if max_episode and max_episode["recovery"] else None,
        "longest_period_below_previous_equity_peak": {
            "start": longest_episode["start"].isoformat() if longest_episode and longest_episode["start"] else None,
            "end": longest_episode["recovery"].isoformat() if longest_episode and longest_episode["recovery"] else None,
            "recovered": bool(longest_episode and longest_episode["recovery"]),
            "duration_days": round(longest_seconds / 86400, 4),
        },
        "final_cumulative_r": round(equity, 4),
    }


def _numeric_summary(rows: list[ReplayManifestRecord], value_fn) -> dict[str, Any]:
    values = []
    for row in rows:
        value = value_fn(row)
        if value is not None:
            values.append(float(value))
    return {
        "n": len(values),
        "mean": round(mean(values), 4) if values else None,
        "median": round(median(values), 4) if values else None,
    }


def _regime_comparison(records: list[ReplayManifestRecord], monthly: dict[str, Any]) -> dict[str, Any]:
    july = [row for row in records if row.trading_date.startswith("2026-07")]
    profitable_months = [month for month, value in monthly.items() if value["cumulative_r"] > 0 and month != "2026-07"]
    profitable = [row for row in records if row.trading_date[:7] in profitable_months]

    def feature(row: ReplayManifestRecord, key: str) -> float | None:
        value = row.entry_features.get(key)
        if value is None and key == "impulse_atr":
            size = row.entry_features.get("impulse_size")
            atr = row.entry_features.get("atr") or row.atr_at_entry
            value = (float(size) / float(atr)) if size is not None and atr else None
        return float(value) if value is not None else None

    numeric = {
        "adx": lambda row: feature(row, "adx"),
        "impulse_atr": lambda row: feature(row, "impulse_atr") or feature(row, "impulse_size_atr"),
        "pullback_depth": lambda row: feature(row, "pullback_depth"),
        "confirmations": lambda row: row.confirmation_passed,
        "structural_r": lambda row: row.initial_risk_atr,
    }
    result = {
        "july_2026": {key: _numeric_summary(july, fn) for key, fn in numeric.items()},
        "profitable_months": {key: _numeric_summary(profitable, fn) for key, fn in numeric.items()},
        "profitable_months_included": profitable_months,
        "sample_sizes": {"july_2026": len(july), "profitable_months": len(profitable)},
    }
    result["july_2026"]["time_of_day"] = dict(Counter(_bucket_time(row.simulated_entry_timestamp) for row in july))
    result["profitable_months"]["time_of_day"] = dict(Counter(_bucket_time(row.simulated_entry_timestamp) for row in profitable))
    return result


def _coverage(
    db_path: Path,
    instrument_db_path: Path,
    start: date,
    end: date,
) -> tuple[dict[str, Any], list[str], list[Candle], list[Candle]]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT instrument_id, start_time, end_time, open, high, low, close,
               volume, open_interest, source
        FROM historical_candles
        WHERE interval = '5m' AND source = 'BREEZE'
          AND (instrument_id = 'INST-NIFTY-INDEX' OR instrument_id LIKE 'INST-NIFTY-FUT-%')
        ORDER BY start_time
        """
    ).fetchall()
    conn.close()
    instrument_conn = sqlite3.connect(instrument_db_path)
    contracts = instrument_conn.execute(
        "SELECT instrument_id, expiry FROM instruments WHERE segment = 'FUTURES' AND underlying = 'NIFTY' AND expiry IS NOT NULL ORDER BY expiry"
    ).fetchall()
    instrument_conn.close()
    contract_by_id = {instrument_id: expiry for instrument_id, expiry in contracts}

    spot_by_day: dict[str, list[tuple]] = defaultdict(list)
    futures_by_contract_day: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    spot_candles: list[Candle] = []
    futures_candles: list[Candle] = []
    invalid_ohlc = 0
    duplicate_keys = 0
    seen_keys: set[tuple[str, str]] = set()
    for row in rows:
        instrument_id, start_time, end_time, open_price, high, low, close, volume, oi, source = row
        local_day = _local_date(start_time)
        if not (start.isoformat() <= local_day <= end.isoformat()):
            continue
        key = (instrument_id, start_time)
        if key in seen_keys:
            duplicate_keys += 1
        seen_keys.add(key)
        if low > high or not (low <= open_price <= high) or not (low <= close <= high) or volume < 0 or oi < 0:
            invalid_ohlc += 1
        candle = Candle(
            instrument_id=instrument_id,
            interval="5m",
            start_time=_parse_timestamp(start_time),
            end_time=_parse_timestamp(end_time),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            open_interest=oi,
            source=source,
        )
        if instrument_id == "INST-NIFTY-INDEX":
            spot_candles.append(candle)
            spot_by_day[local_day].append(row)
        else:
            futures_candles.append(candle)
            futures_by_contract_day[(instrument_id, local_day)].append(row)

    expected_weekdays = [day for day in _date_range(start, end) if date.fromisoformat(day).weekday() < 5]
    spot_days = set(spot_by_day)
    selected_contract: dict[str, tuple[str, str]] = {}
    for day in expected_weekdays:
        candidates = [(instrument_id, expiry) for instrument_id, expiry in contracts if expiry >= day]
        if candidates:
            selected_contract[day] = candidates[0]
    futures_days = {day for day, (instrument_id, expiry) in selected_contract.items() if futures_by_contract_day.get((instrument_id, day))}

    def session_issues(day: str, rows_for_day: list[tuple]) -> list[str]:
        issues: list[str] = []
        count = len(rows_for_day)
        if count < MIN_RELIABLE_CANDLES:
            issues.append(f"partial_session:{count}/{EXPECTED_CANDLES}")
        timestamps = sorted(_parse_timestamp(row[1]) for row in rows_for_day)
        if any(current - previous > FIVE_MINUTES for previous, current in zip(timestamps, timestamps[1:])):
            issues.append("timestamp_gap")
        return issues

    quality: dict[str, dict[str, Any]] = {}
    usable_dates: list[str] = []
    partial_spot: list[dict[str, Any]] = []
    partial_futures: list[dict[str, Any]] = []
    timestamp_gaps: list[dict[str, Any]] = []
    for day in expected_weekdays:
        spot_rows = spot_by_day.get(day, [])
        fut_id, fut_expiry = selected_contract.get(day, (None, None))
        futures_rows = futures_by_contract_day.get((fut_id, day), []) if fut_id else []
        spot_issues = session_issues(day, spot_rows)
        futures_issues = session_issues(day, futures_rows)
        if 0 < len(spot_rows) < EXPECTED_CANDLES:
            partial_spot.append({"date": day, "candles": len(spot_rows), "expected_candles": EXPECTED_CANDLES})
        if 0 < len(futures_rows) < EXPECTED_CANDLES:
            partial_futures.append({"date": day, "candles": len(futures_rows), "expected_candles": EXPECTED_CANDLES, "instrument_id": fut_id, "expiry": fut_expiry})
        if "timestamp_gap" in spot_issues or "timestamp_gap" in futures_issues:
            timestamp_gaps.append({"date": day, "spot": "timestamp_gap" in spot_issues, "futures": "timestamp_gap" in futures_issues})
        issues = ([] if spot_rows else ["missing_spot"]) + ([] if futures_rows else ["missing_futures"]) + spot_issues + futures_issues
        quality[day] = {"spot_candles": len(spot_rows), "futures_candles": len(futures_rows), "futures_instrument_id": fut_id, "futures_expiry": fut_expiry, "issues": sorted(set(issues))}
        if not issues:
            usable_dates.append(day)

    expiry_counts = Counter()
    for (instrument_id, day), values in futures_by_contract_day.items():
        if day in usable_dates:
            expiry_counts[contract_by_id.get(instrument_id, instrument_id)] += len(values)
    missing_spot = sorted(set(expected_weekdays) - spot_days)
    missing_futures = sorted(set(expected_weekdays) - futures_days)
    all_local_days = [_local_date(row[1]) for row in rows if start.isoformat() <= _local_date(row[1]) <= end.isoformat()]
    coverage = {
        "requested_date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "received_date_range": {"start": min(all_local_days) if all_local_days else None, "end": max(all_local_days) if all_local_days else None},
        "trading_sessions": {
            "weekday_candidates": len(expected_weekdays),
            "spot_sessions": len(spot_days),
            "futures_sessions_for_selected_contract": len(futures_days),
            "paired_reliable_sessions": len(usable_dates),
            "excluded_unreliable_sessions": len(expected_weekdays) - len(usable_dates),
        },
        "spot_candles": len(spot_candles),
        "futures_candles": len(futures_candles),
        "futures_expiries_used": dict(sorted(expiry_counts.items())),
        "missing_sessions": {"spot": missing_spot, "futures": missing_futures},
        "partial_sessions": {"spot": partial_spot, "futures": partial_futures},
        "invalid_ohlc": invalid_ohlc,
        "duplicate_candles": duplicate_keys,
        "timestamp_gaps": timestamp_gaps,
        "futures_volume": {"available": sum(1 for row in futures_candles if row.volume != 0), "total": len(futures_candles)},
        "futures_open_interest": {"available": sum(1 for row in futures_candles if row.open_interest != 0), "total": len(futures_candles)},
        "session_quality_rule": "Require paired BREEZE spot and selected-expiry futures sessions with at least 75 five-minute candles and no intraday timestamp gap; do not fill missing data.",
        "session_quality": quality,
    }
    return coverage, usable_dates, spot_candles, futures_candles


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_platform_settings()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    coverage, usable_dates, spot_candles, futures_candles = _coverage(
        settings.historical_db_path,
        settings.instruments_db_path,
        start,
        end,
    )
    historical = HistoricalService(
        repository=HistoricalRepository(db_path=settings.historical_db_path),
        broker_gateway=None,
        instrument_service=InstrumentService(repository=InstrumentRepository(db_path=settings.instruments_db_path)),
    )
    await historical.initialize()
    await historical.instrument_service.initialize()
    engine = SimulationEngine(historical_service=historical)
    full_records: list[ReplayManifestRecord] = []
    metadata_items: list[dict[str, Any]] = []
    session_results: list[dict[str, Any]] = []
    for index, day in enumerate(usable_dates, start=1):
        result = await engine.run_day_simulation(SimulationRequest(
            date=day,
            historical_source=HistoricalReplaySource.BREEZE,
            bypass_entry_window=False,
        ))
        day_records = _records(result.replay_manifests)
        full_records.extend(day_records)
        metadata_items.append(result.replay_metadata)
        session_results.append({
            "date": day,
            "signals": len(day_records),
            "resolved": sum(1 for row in day_records if row.lifecycle_status == "RESOLVED"),
            "ambiguous": sum(1 for row in day_records if row.lifecycle_status == "AMBIGUOUS"),
            "configuration_fingerprint": result.replay_metadata.get("configuration_fingerprint"),
            "dataset_hash": result.replay_metadata.get("data_fingerprint", {}).get("dataset_hash"),
            "progress": f"{index}/{len(usable_dates)}",
        })
    full_records.sort(key=lambda row: row.simulated_entry_timestamp)
    put_records = [row for row in full_records if row.direction == "PUT"]
    full_report = build_lifecycle_report(full_records, {"sessions": len(usable_dates)})
    put_report = build_lifecycle_report(put_records, {"sessions": len(usable_dates), "variant": "PUT_ONLY_CALL_ENTRIES_DISABLED"})
    full_report["statistics"] = _stats(full_records)
    put_report["statistics"] = _stats(put_records)
    monthly, month_summary = _monthly(put_records)

    if metadata_items:
        snapshot = ReplayConfigurationSnapshot.model_validate(metadata_items[0]["configuration_snapshot"]).model_copy(update={
            "replay_start_date": start.isoformat(),
            "replay_end_date": end.isoformat(),
        })
        config_hash = configuration_fingerprint(snapshot)
    else:
        raise RuntimeError("No reliable sessions were available for replay")
    contract_rows = sorted({
        (row.instrument_id, row.expiry)
        for row in []
    })
    instrument_conn = sqlite3.connect(settings.instruments_db_path)
    contract_rows = instrument_conn.execute(
        "SELECT instrument_id, expiry FROM instruments WHERE segment = 'FUTURES' AND underlying = 'NIFTY' AND expiry IS NOT NULL ORDER BY expiry"
    ).fetchall()
    instrument_conn.close()
    selected_contracts = [{"instrument_id": instrument_id, "expiry": expiry} for instrument_id, expiry in contract_rows if any(value["futures_expiry"] == expiry for value in coverage["session_quality"].values())]
    source_diagnostics = {
        "spot": {"requested_source": "BREEZE", "available_before_filter": {"BREEZE": len(spot_candles)}, "selected_after_filter": {"BREEZE": len(spot_candles)}, "selected_count": len(spot_candles), "missing_selected_source": not spot_candles},
        "futures": {"requested_source": "BREEZE", "available_before_filter": {"BREEZE": len(futures_candles)}, "selected_after_filter": {"BREEZE": len(futures_candles)}, "selected_count": len(futures_candles), "missing_selected_source": not futures_candles},
    }
    data_fp = build_data_fingerprint(
        source=HistoricalReplaySource.BREEZE,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        spot_candles=spot_candles,
        futures_candles=futures_candles,
        source_diagnostics=source_diagnostics,
        futures_contracts=selected_contracts,
        missing_data=sorted({f"{kind}:{day}" for kind, days in coverage["missing_sessions"].items() for day in days}),
    )
    metadata = {
        "configuration_snapshot": snapshot.model_dump(mode="json"),
        "configuration_fingerprint": config_hash,
        "data_fingerprint": data_fp.model_dump(mode="json"),
        "historical_source": "BREEZE",
        "bypass_entry_window": False,
        "experiment_variant": "PUT_ONLY_CALL_ENTRIES_DISABLED",
        "strategy_a_call_entries_disabled": True,
        "missing_data": data_fp.missing_data,
    }
    drawdown = _drawdown(put_records)
    report = {
        "experiment": {
            "name": "Extended deterministic Strategy A historical replay",
            "requested_date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "historical_source": "BREEZE",
            "threshold_overrides": "none",
            "bypass_entry_window": False,
            "quality_excluded_sessions": len(coverage["session_quality"]) - len(usable_dates),
            "no_permanent_strategy_changes": True,
        },
        "extended_data_coverage": coverage,
        "backfill_validation": (
            json.loads(args.backfill_report.read_text(encoding="utf-8"))
            if args.backfill_report.exists() else None
        ),
        "full_strategy_a": {"results": full_report, "statistics": _stats(full_records)},
        "put_only": {
            "results": put_report,
            "statistics": _stats(put_records),
            "monthly": monthly,
            "monthly_summary": month_summary,
            "quarterly": _quarter(put_records),
            "chronological_stability": _chronological_splits(put_records),
            "maximum_drawdown": drawdown,
            "existing_segment_analysis": put_report["segments"],
            "july_like_regime_comparison": _regime_comparison(put_records, monthly),
        },
        "metadata": metadata,
        "signal_ids": {
            "full_strategy_a": [row.replay_signal_id for row in full_records],
            "put_only": [row.replay_signal_id for row in put_records],
        },
        "signal_comparison": {
            "full_signal_count": len(full_records),
            "put_signal_count": len(put_records),
            "put_ids_matching_full_put_ids": [row.replay_signal_id for row in put_records],
            "call_ids_in_put_only": [],
        },
        "session_results": session_results,
        "records": {
            "full_strategy_a": [row.model_dump(mode="json") for row in full_records],
            "put_only": [row.model_dump(mode="json") for row in put_records],
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run extended deterministic Strategy A reports")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-09-18")
    parser.add_argument("--output", type=Path, default=Path("data/extended_strategy_a_replay_report.json"))
    parser.add_argument("--backfill-report", type=Path, default=Path("data/extended_breeze_backfill_report.json"))
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "reliable_sessions": report["extended_data_coverage"]["trading_sessions"]["paired_reliable_sessions"],
        "full_signals": report["full_strategy_a"]["statistics"]["signals"],
        "put_signals": report["put_only"]["statistics"]["signals"],
        "put_average_r": report["put_only"]["statistics"]["average_r"],
        "put_cumulative_r": report["put_only"]["statistics"]["cumulative_r"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
