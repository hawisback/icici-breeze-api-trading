"""Historical option-data coverage audit for the frozen Strategy C candidate.

This audit consumes the frozen Strategy C candidate manifest and checks whether
historical option metadata and native 1-minute option candles are available
around each fixed signal time.

It is intentionally NOT an option-PnL backtest:
- no future chain snapshot is substituted into signal time;
- no historical delta/bid/ask is invented;
- nearest-strike/nearest-expiry ranking is only a deterministic coverage proxy,
  not the production ContractSelector;
- presence of OHLC candles does not imply an executable fill.

The purpose is to decide whether historical option lifecycle replay is supported
by the local cache, or whether the frozen underlying candidate should advance
directly to paper/shadow forward validation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from services.historical.repository import HistoricalRepository
from services.instrument.repository import InstrumentRepository


CANDIDATE_ID = "STRATEGY_C_DI_CONTINUATION_V1_CANDIDATE"
DEFAULT_SOURCE = "BREEZE"
DEFAULT_MAX_EXPIRIES = 2
DEFAULT_STRIKES_PER_EXPIRY = 6
NEAR_SIGNAL_MINUTES = 2


def _aware(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_aware(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _aware(str(value))


def _option_right_matches(raw: Any, direction: str) -> bool:
    value = str(raw or "").upper()
    return value in ({direction, "CE"} if direction == "CALL" else {direction, "PE"})


def _metadata_valid_at(row: dict[str, Any], entry_time: datetime) -> bool:
    valid_from = _optional_aware(row.get("valid_from"))
    valid_to = _optional_aware(row.get("valid_to"))
    if valid_from is not None and valid_from > entry_time:
        return False
    if valid_to is not None and valid_to < entry_time:
        return False
    return True


def _temporal_bounds_status(row: dict[str, Any]) -> str:
    if row.get("valid_from") and row.get("valid_to"):
        return "BOTH_BOUNDS_PRESENT"
    if row.get("valid_from") or row.get("valid_to"):
        return "PARTIAL_BOUNDS_PRESENT"
    return "BOUNDS_UNKNOWN"


def _rank_option_contracts(
    metadata: Iterable[dict[str, Any]],
    signal: dict[str, Any],
    *,
    max_expiries: int = DEFAULT_MAX_EXPIRIES,
    strikes_per_expiry: int = DEFAULT_STRIKES_PER_EXPIRY,
) -> list[dict[str, Any]]:
    """Rank static metadata without consulting post-signal market outcomes."""
    entry_time = _aware(signal["entry_time"])
    signal_day = entry_time.date()
    direction = str(signal["direction"]).upper()
    underlying = float(signal["underlying_entry_price"])

    eligible: list[dict[str, Any]] = []
    for raw in metadata:
        row = dict(raw)
        if str(row.get("segment") or "").upper() != "OPTIONS":
            continue
        if str(row.get("underlying") or "").upper() != "NIFTY":
            continue
        if not _option_right_matches(row.get("option_right"), direction):
            continue
        expiry_raw = row.get("expiry")
        strike_raw = row.get("strike")
        if not expiry_raw or strike_raw is None:
            continue
        try:
            expiry = date.fromisoformat(str(expiry_raw))
            strike = float(strike_raw)
        except (TypeError, ValueError):
            continue
        if expiry < signal_day:
            continue
        if not _metadata_valid_at(row, entry_time):
            continue
        row["_expiry_date"] = expiry
        row["_strike_distance"] = abs(strike - underlying)
        row["_temporal_bounds_status"] = _temporal_bounds_status(row)
        eligible.append(row)

    expiries = sorted({row["_expiry_date"] for row in eligible})[:max_expiries]
    ranked: list[dict[str, Any]] = []
    rank = 0
    for expiry in expiries:
        group = [row for row in eligible if row["_expiry_date"] == expiry]
        group.sort(key=lambda row: (row["_strike_distance"], float(row["strike"]), str(row["instrument_id"])))
        for row in group[:strikes_per_expiry]:
            rank += 1
            clean = {key: value for key, value in row.items() if not key.startswith("_")}
            clean["coverage_proxy_rank"] = rank
            clean["strike_distance_points"] = round(float(row["_strike_distance"]), 6)
            clean["temporal_bounds_status"] = row["_temporal_bounds_status"]
            ranked.append(clean)
    return ranked


async def _load_option_metadata(repository: InstrumentRepository) -> list[dict[str, Any]]:
    async with repository.engine.connect() as conn:
        cursor = await conn.execute(
            """
            SELECT instrument_id, broker, exchange, segment, underlying, stock_code,
                   expiry, strike, option_right, lot_size, tick_size, broker_token,
                   tradable, valid_from, valid_to
            FROM instruments
            WHERE segment = 'OPTIONS' AND underlying = 'NIFTY'
            ORDER BY expiry ASC, strike ASC, option_right ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def _load_candles_near_signal(
    repository: HistoricalRepository,
    instrument_ids: list[str],
    entry_time: datetime,
    *,
    source: str,
) -> list[dict[str, Any]]:
    if not instrument_ids:
        return []
    start = entry_time - timedelta(minutes=NEAR_SIGNAL_MINUTES)
    end = entry_time + timedelta(minutes=NEAR_SIGNAL_MINUTES)
    placeholders = ",".join("?" for _ in instrument_ids)
    params: list[Any] = [
        *instrument_ids,
        "1m",
        source.upper(),
        start.isoformat(),
        end.isoformat(),
    ]
    async with repository.engine.connect() as conn:
        cursor = await conn.execute(
            f"""
            SELECT instrument_id, start_time, end_time, open, high, low, close,
                   volume, open_interest, source
            FROM historical_candles
            WHERE instrument_id IN ({placeholders})
              AND interval = ?
              AND source = ?
              AND start_time >= ?
              AND start_time <= ?
            ORDER BY instrument_id ASC, start_time ASC
            """,
            params,
        )
        return [dict(row) for row in await cursor.fetchall()]


def _candle_flags(rows: list[dict[str, Any]], entry_time: datetime) -> dict[str, bool]:
    exact_close = False
    exact_post = False
    recent_completed = False
    near_any = bool(rows)
    for row in rows:
        start = _aware(row["start_time"])
        end = _aware(row["end_time"])
        if end == entry_time:
            exact_close = True
        if start == entry_time:
            exact_post = True
        if end <= entry_time and entry_time - end <= timedelta(minutes=NEAR_SIGNAL_MINUTES):
            recent_completed = True
    return {
        "near_signal_any": near_any,
        "recent_completed_at_or_before_signal": recent_completed,
        "exact_signal_close_candle": exact_close,
        "exact_post_signal_candle": exact_post,
    }


def _signal_result(
    signal: dict[str, Any],
    contracts: list[dict[str, Any]],
    candle_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    entry_time = _aware(signal["entry_time"])
    by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candle_rows:
        by_instrument[str(row["instrument_id"])].append(row)

    contract_rows: list[dict[str, Any]] = []
    for contract in contracts:
        instrument_id = str(contract["instrument_id"])
        flags = _candle_flags(by_instrument.get(instrument_id, []), entry_time)
        contract_rows.append({
            "coverage_proxy_rank": contract["coverage_proxy_rank"],
            "instrument_id": instrument_id,
            "expiry": contract.get("expiry"),
            "strike": contract.get("strike"),
            "option_right": contract.get("option_right"),
            "lot_size": contract.get("lot_size"),
            "strike_distance_points": contract.get("strike_distance_points"),
            "temporal_bounds_status": contract.get("temporal_bounds_status"),
            **flags,
        })

    ranked_proxy = contract_rows[0] if contract_rows else None
    any_flags = {
        key: any(row[key] for row in contract_rows)
        for key in (
            "near_signal_any",
            "recent_completed_at_or_before_signal",
            "exact_signal_close_candle",
            "exact_post_signal_candle",
        )
    }
    if not contracts:
        state = "NO_MATCHING_OPTION_METADATA"
    elif not any_flags["near_signal_any"]:
        state = "METADATA_ONLY_NO_NEARBY_1M"
    elif any_flags["exact_signal_close_candle"] and any_flags["exact_post_signal_candle"]:
        state = "NATIVE_1M_AROUND_SIGNAL"
    else:
        state = "PARTIAL_NATIVE_1M_AROUND_SIGNAL"

    return {
        "signal_id": signal["signal_id"],
        "date": signal["date"],
        "direction": signal["direction"],
        "entry_time": signal["entry_time"],
        "entry_time_ist": signal.get("entry_time_ist"),
        "underlying_entry_price": signal["underlying_entry_price"],
        "coverage_state": state,
        "matching_contracts_checked": len(contract_rows),
        "temporal_bounds_present_contracts": sum(
            row["temporal_bounds_status"] != "BOUNDS_UNKNOWN" for row in contract_rows
        ),
        "ranked_proxy_contract": ranked_proxy,
        "any_checked_contract": any_flags,
        "checked_contracts": contract_rows,
        "selection_note": (
            "Rank 1 is nearest-strike within nearest non-expired expiry from static metadata only. "
            "It is a coverage proxy, not production ContractSelector output."
        ),
    }


def _pct(count: int, total: int) -> float:
    return round(100.0 * count / total, 2) if total else 0.0


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    states = Counter(row["coverage_state"] for row in results)

    ranked_metadata = [row for row in results if row["ranked_proxy_contract"]]
    ranked_exact_close = sum(
        bool(row["ranked_proxy_contract"] and row["ranked_proxy_contract"]["exact_signal_close_candle"])
        for row in results
    )
    ranked_post = sum(
        bool(row["ranked_proxy_contract"] and row["ranked_proxy_contract"]["exact_post_signal_candle"])
        for row in results
    )
    any_exact_close = sum(row["any_checked_contract"]["exact_signal_close_candle"] for row in results)
    any_post = sum(row["any_checked_contract"]["exact_post_signal_candle"] for row in results)
    any_both = sum(
        row["any_checked_contract"]["exact_signal_close_candle"]
        and row["any_checked_contract"]["exact_post_signal_candle"]
        for row in results
    )
    temporal_bounds = sum(row["temporal_bounds_present_contracts"] > 0 for row in results)

    by_direction: dict[str, dict[str, Any]] = {}
    for direction in ("CALL", "PUT"):
        rows = [row for row in results if row["direction"] == direction]
        by_direction[direction] = {
            "signals": len(rows),
            "metadata_coverage": sum(bool(row["ranked_proxy_contract"]) for row in rows),
            "any_candidate_exact_signal_close": sum(
                row["any_checked_contract"]["exact_signal_close_candle"] for row in rows
            ),
            "any_candidate_exact_post_signal": sum(
                row["any_checked_contract"]["exact_post_signal_candle"] for row in rows
            ),
        }

    strike_distances = [
        float(row["ranked_proxy_contract"]["strike_distance_points"])
        for row in ranked_metadata
        if row["ranked_proxy_contract"].get("strike_distance_points") is not None
    ]

    return {
        "signals": total,
        "coverage_state_counts": dict(sorted(states.items())),
        "metadata_coverage_signals": len(ranked_metadata),
        "metadata_coverage_pct": _pct(len(ranked_metadata), total),
        "signals_with_any_temporal_metadata_bounds": temporal_bounds,
        "temporal_metadata_bounds_pct": _pct(temporal_bounds, total),
        "ranked_proxy_exact_signal_close_signals": ranked_exact_close,
        "ranked_proxy_exact_signal_close_pct": _pct(ranked_exact_close, total),
        "ranked_proxy_exact_post_signal_signals": ranked_post,
        "ranked_proxy_exact_post_signal_pct": _pct(ranked_post, total),
        "any_checked_contract_exact_signal_close_signals": any_exact_close,
        "any_checked_contract_exact_signal_close_pct": _pct(any_exact_close, total),
        "any_checked_contract_exact_post_signal_signals": any_post,
        "any_checked_contract_exact_post_signal_pct": _pct(any_post, total),
        "any_checked_contract_both_sides_of_signal_signals": any_both,
        "any_checked_contract_both_sides_of_signal_pct": _pct(any_both, total),
        "mean_rank1_strike_distance_points": round(mean(strike_distances), 6) if strike_distances else None,
        "by_direction": by_direction,
    }


async def run_audit(
    manifest: dict[str, Any],
    *,
    historical_db: Path,
    instruments_db: Path,
    source: str = DEFAULT_SOURCE,
    max_expiries: int = DEFAULT_MAX_EXPIRIES,
    strikes_per_expiry: int = DEFAULT_STRIKES_PER_EXPIRY,
) -> dict[str, Any]:
    spec = manifest.get("candidate_spec") or {}
    if spec.get("candidate_id") != CANDIDATE_ID:
        raise ValueError(
            f"Expected candidate_id {CANDIDATE_ID}, got {spec.get('candidate_id')!r}"
        )

    signals = list(manifest.get("signals") or [])
    historical = HistoricalRepository(historical_db)
    instruments = InstrumentRepository(instruments_db)
    metadata = await _load_option_metadata(instruments)

    results: list[dict[str, Any]] = []
    for signal in signals:
        ranked = _rank_option_contracts(
            metadata,
            signal,
            max_expiries=max_expiries,
            strikes_per_expiry=strikes_per_expiry,
        )
        candle_rows = await _load_candles_near_signal(
            historical,
            [str(row["instrument_id"]) for row in ranked],
            _aware(signal["entry_time"]),
            source=source,
        )
        results.append(_signal_result(signal, ranked, candle_rows))

    return {
        "research_type": "STRATEGY_C_OPTION_DATA_COVERAGE_AUDIT",
        "candidate_id": CANDIDATE_ID,
        "candidate_spec_fingerprint": manifest.get("candidate_spec_fingerprint"),
        "historical_db": str(historical_db.resolve()),
        "instruments_db": str(instruments_db.resolve()),
        "source": source.upper(),
        "coverage_proxy": {
            "max_expiries": max_expiries,
            "strikes_per_expiry": strikes_per_expiry,
            "ranking": "nearest non-expired expiry, then nearest strike",
            "production_selector_equivalent": False,
        },
        "aggregate": _aggregate(results),
        "signals": results,
        "limitations": [
            "This is a data-coverage audit, not an option-PnL backtest.",
            "Static instrument metadata is not treated as proof that a live chain snapshot was known at signal time.",
            "Missing valid_from/valid_to bounds are reported as unknown rather than assumed historical truth.",
            "Native 1m OHLC presence does not provide bid/ask, delta, quote age, or an executable fill.",
            "Nearest-expiry/nearest-strike ranking is a deterministic coverage proxy only and is not the production ContractSelector.",
            "No future option chain snapshot is substituted into signal time.",
            "Strategy C thresholds remain frozen and Strategy A V3 remains unchanged.",
        ],
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit historical option coverage for frozen Strategy C")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_c_di_continuation_v1_candidate_manifest.json",
    )
    parser.add_argument(
        "--historical-db",
        type=Path,
        default=Path("data") / "market" / "historical.db",
    )
    parser.add_argument(
        "--instruments-db",
        type=Path,
        default=Path("data") / "instruments" / "instruments.db",
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--max-expiries", type=int, default=DEFAULT_MAX_EXPIRIES)
    parser.add_argument("--strikes-per-expiry", type=int, default=DEFAULT_STRIKES_PER_EXPIRY)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_c_option_data_coverage_audit.json",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = asyncio.run(
        run_audit(
            manifest,
            historical_db=args.historical_db,
            instruments_db=args.instruments_db,
            source=args.source,
            max_expiries=max(1, args.max_expiries),
            strikes_per_expiry=max(1, args.strikes_per_expiry),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "research_type": report["research_type"],
                "candidate_id": report["candidate_id"],
                "aggregate": report["aggregate"],
                "coverage_proxy": report["coverage_proxy"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
