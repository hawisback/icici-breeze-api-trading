"""Coverage-first audit for historical option validation.

The report intentionally stops before option P&L when the production contract
selector cannot be reconstructed from historical data.  It never fabricates
option quotes or substitutes a selector.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


def _records_for_variant(report: dict[str, Any], label: str) -> list[dict[str, Any]]:
    if label == "baseline_full_strategy_a":
        return report["records"]["full_strategy_a"]
    elif label == "frozen_40_to_60_full_strategy_a":
        return report["replay_results"]["experimental_full_records"]
    elif label == "frozen_40_to_60_put_only":
        return report["replay_results"]["experimental_put_records"]
    else:
        raise ValueError(label)


def _underlying_r_bucket(value: Any) -> str:
    if value is None:
        return "UNRESOLVED"
    r = float(value)
    if r < 0:
        return "underlying_loser"
    if r < 0.5:
        return "0_to_0.5R"
    if r < 1.0:
        return "0.5_to_1R"
    if r <= 2.0:
        return "1_to_2R"
    return "above_2R"


def _counts(report: dict[str, Any], label: str, source_path: str) -> dict[str, Any]:
    records = _records_for_variant(report, label)
    by_direction = Counter(str(row.get("direction", "UNKNOWN")) for row in records)
    by_r_bucket = Counter(_underlying_r_bucket(row.get("realized_r")) for row in records)
    stats = {
        "signals": len(records),
        "resolved": sum(row.get("lifecycle_status") == "RESOLVED" for row in records),
        "ambiguous": sum(bool(row.get("ambiguous")) for row in records),
    }
    # No contract is marked reconstructable unless every production selector
    # input is available at the historical signal time.  In particular, a
    # candle close is not treated as an ask and no spread is inferred.
    coverage_by_direction = {
        direction: {
            "signals": by_direction.get(direction, 0),
            "reconstructable_option_contracts": 0,
            "missing_or_unreconstructable_historical_option_data": by_direction.get(direction, 0),
            "coverage_pct": 0.0,
            "selected_contracts": [],
        }
        for direction in ("CALL", "PUT")
    }
    return {
        "variant": label,
        "source_artifact": source_path,
        "total_strategy_signals": stats["signals"],
        "underlying_resolved": stats["resolved"],
        "underlying_ambiguous": stats["ambiguous"],
        "direction_counts": {
            "CALL": by_direction.get("CALL", 0),
            "PUT": by_direction.get("PUT", 0),
        },
        "coverage_by_direction": coverage_by_direction,
        "underlying_r_bucket_counts": dict(sorted(by_r_bucket.items())),
        "reconstructable_option_contracts": 0,
        "missing_or_unreconstructable_historical_option_data": stats["signals"],
        "rejected_by_historical_contract_selection": 0,
        "ambiguous_trades": stats["ambiguous"],
        "option_ambiguous_trades": 0,
        "selected_contracts": [],
        "coverage_pct": 0.0,
        "status": "STOPPED_BEFORE_OPTION_PNL",
    }


def _database_inventory(historical_db: Path, instruments_db: Path) -> dict[str, Any]:
    historical = sqlite3.connect(historical_db)
    # The historical and instrument stores are intentionally separate SQLite
    # databases, so use the option instrument IDs from the second connection.
    instruments = sqlite3.connect(instruments_db)
    option_ids = [row[0] for row in instruments.execute(
        "SELECT instrument_id FROM instruments WHERE underlying='NIFTY' AND segment='OPTIONS'"
    )]
    option_expiries = [row[0] for row in instruments.execute(
        "SELECT DISTINCT expiry FROM instruments WHERE underlying='NIFTY' AND segment='OPTIONS' ORDER BY expiry"
    )]
    option_lots = [row[0] for row in instruments.execute(
        "SELECT DISTINCT lot_size FROM instruments WHERE underlying='NIFTY' AND segment='OPTIONS' ORDER BY lot_size"
    )]
    instruments.close()

    option_candle_count = 0
    option_candles_by_interval: list[tuple[Any, ...]] = []
    option_instruments_with_candles = 0
    if option_ids:
        placeholders = ",".join("?" for _ in option_ids)
        option_candle_count = historical.execute(
            f"SELECT COUNT(*) FROM historical_candles WHERE instrument_id IN ({placeholders})",
            option_ids,
        ).fetchone()[0]
        option_candles_by_interval = [tuple(row) for row in historical.execute(
            f"SELECT interval, source, COUNT(*) FROM historical_candles WHERE instrument_id IN ({placeholders}) GROUP BY interval, source ORDER BY interval, source",
            option_ids,
        )]
        option_instruments_with_candles = historical.execute(
            f"SELECT COUNT(DISTINCT instrument_id) FROM historical_candles WHERE instrument_id IN ({placeholders})",
            option_ids,
        ).fetchone()[0]
    source_intervals = [tuple(row) for row in historical.execute(
        "SELECT source, interval, COUNT(*) FROM historical_candles GROUP BY source, interval ORDER BY source, interval"
    )]
    historical.close()
    return {
        "historical_option_candle_rows_persisted": option_candle_count,
        "historical_option_candles_by_interval_source": option_candles_by_interval,
        "historical_option_instruments_with_candles": option_instruments_with_candles,
        "historical_source_interval_counts": source_intervals,
        "nifty_option_instrument_count": len(option_ids),
        "nifty_option_expiries_in_instrument_master": option_expiries,
        "nifty_option_lot_sizes_in_instrument_master": option_lots,
        "historical_option_chain_snapshots_persisted": 0,
        "historical_option_bid_ask_snapshots_persisted": 0,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    frozen = json.loads(args.frozen.read_text(encoding="utf-8"))
    report = {
        "status": "STOPPED_BEFORE_OPTION_PNL",
        "reason": (
            "Coverage is zero because the production contract selector cannot be "
            "replayed from the available historical artifacts. The store has no "
            "historical NIFTY option chain snapshots, bid/ask snapshots, or option "
            "candles for the replay period. Breeze can return OHLCV/OI when an "
            "explicit contract is supplied, but that is insufficient to identify "
            "the production-selected contract without inventing an ask or spread."
        ),
        "execution_safety": {
            "live_trading_enabled": False,
            "default_trading_mode": "PAPER",
            "orders_placed": False,
            "strategy_a_thresholds_changed": False,
        },
        "historical_environment": {
            "date_range": {"start": "2025-01-01", "end": "2026-09-18"},
            "source": "BREEZE",
            "quality_approved_sessions": 401,
            "dataset_fingerprint": frozen["data_fingerprint"],
        },
        "production_contract_selection_audit": {
            "direction_to_right": {
                "BULLISH": "CALL / CE",
                "BEARISH": "PUT / PE",
            },
            "expiry": {
                "live_path": "OptionChainService.get_chain filters instrument expiries to expiry >= today and selects the earliest remaining expiry when no expiry is supplied",
                "historical_reconstruction": "UNAVAILABLE: no point-in-time historical chain/instrument-validity snapshot exists; the current instrument master only contains current 2026 expiries",
            },
            "atm_and_strike_relationship": {
                "atm": "nearest available chain strike to spot",
                "call_candidates": "available strikes whose index distance from ATM is -1 through +4 (one ITM, ATM, four OTM)",
                "put_candidates": "available strikes whose index distance from ATM is -1 through +4 after reversing put-side direction (one ITM, ATM, four OTM)",
                "strike_spacing": "Uses the available chain strike ordering; no fixed rupee spacing is assumed",
                "historical_reconstruction": "UNAVAILABLE without the historical chain strike universe at signal time",
            },
            "premium": {
                "minimum_ask": 15.0,
                "maximum_ask": 70.0,
                "historical_reconstruction": "UNAVAILABLE: Breeze historical candles provide OHLC, not executable historical ask quotes",
            },
            "liquidity": {
                "minimum_open_interest": 10000,
                "volume_recorded": True,
                "explicit_minimum_volume": None,
                "historical_reconstruction": "OI/volume are available in direct historical candles where a contract is already known, but no complete point-in-time chain exists for selector ranking",
            },
            "spread": {
                "maximum_bid_ask_spread_pct": 3.0,
                "formula": "100 * (ask - bid) / ((ask + bid) / 2)",
                "historical_reconstruction": "UNAVAILABLE: no historical bid/ask snapshots",
            },
            "quote_and_metadata_rejections": [
                "missing instrument_id or expiry",
                "non-positive lot size",
                "bid <= 0 or bid > ask",
                "ask <= 0",
                "ask above premium cap",
                "ask below premium floor",
                "OI below 10,000",
                "spread above 3%",
            ],
            "ranking": [
                "closest to ATM / lowest OTM distance",
                "highest ask not exceeding premium cap",
                "tightest spread",
                "highest open interest",
            ],
            "lot_size": "Read from selected instrument metadata; historical contract-specific metadata is unavailable for the replay period",
            "entry_execution": "Production creates the trade from the selected executable ask",
            "exit_execution": "Production resolves the current option price and submits an exit using bid when available; historical bid is unavailable",
        },
        "position_manager_audit": {
            "option_hard_stop_present": True,
            "option_hard_stop_pct": 25.0,
            "production_behavior": "PositionManager exits when current_option_price > 0 and current_option_price <= entry_option_price * 0.75",
            "underlying_state_exits": [
                "immediate Strategy A thesis invalidation on completed 5-minute close",
                "structural spot stop",
                "adverse health score exits",
                "breakeven/profit-lock/runner trailing stop exits",
                "15:20 IST session square-off",
            ],
            "option_premium_exit": "OPTION_HARD_STOP_HIT; checked using the historical option price before structural/session/trailing checks in PositionManager",
            "underlying_replay_behavior": "The underlying-only lifecycle replay passes current_option_price=0.0, so the production option hard stop is not evaluated there",
            "option_validation_behavior": "Not run because no production-selected historical option contract was reconstructable",
        },
        "database_inventory": _database_inventory(args.historical_db, args.instruments_db),
        "historical_option_probe": {
            "source": "BREEZE",
            "status": "READ_ONLY_PROBE_SUCCEEDED",
            "contract": {"expiry": "2025-01-30", "strike": 23000, "right": "PE"},
            "interval": "1minute",
            "probe_window_utc": {"start": "2025-01-10T03:45:00Z", "end": "2025-01-10T10:00:00Z"},
            "rows_returned": 46,
            "persisted": False,
            "fields": ["timestamp", "OHLC", "volume", "open_interest"],
            "bid_ask_available": False,
            "note": "Direct contract retrieval works when expiry, strike, and right are supplied; this does not establish production selector coverage and the probe was intentionally not persisted as a candidate trade contract.",
        },
        "coverage": [
            _counts(baseline, "baseline_full_strategy_a", str(args.baseline)),
            _counts(frozen, "frozen_40_to_60_full_strategy_a", str(args.frozen)),
            _counts(frozen, "frozen_40_to_60_put_only", str(args.frozen)),
        ],
        "option_execution": {
            "status": "NOT_RUN_COVERAGE_STOP",
            "ideal_historical_execution": "NOT_COMPUTED",
            "conservative_slippage_execution": "NOT_COMPUTED",
            "gross_results": "NOT_COMPUTED",
            "net_results": "NOT_COMPUTED",
            "costs": {
                "status": "NOT_APPLIED",
                "components": [
                    "brokerage",
                    "exchange_transaction_charges",
                    "STT",
                    "GST",
                    "SEBI_charges",
                    "stamp_duty",
                    "slippage",
                ],
                "fee_schedule_date": None,
                "note": "No fee schedule was hard-coded because no option trade reached execution simulation.",
            },
            "position_sizing": "NOT_COMPUTED",
            "one_lot_normalization": "NOT_COMPUTED",
            "monthly_quarterly_stability": "NOT_COMPUTED",
            "underlying_r_mapping": "NOT_COMPUTED",
            "expiry_strike_premium_analysis": "NOT_COMPUTED",
        },
        "next_required_data_to_resume": [
            "historical NIFTY option contract master with expiry, strike, right, validity, and lot size",
            "historical chain-level bid/ask/OI/volume snapshots at each candidate entry",
            "incremental persisted 1-minute OHLCV/OI candles for every selector candidate contract",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=Path("data/extended_strategy_a_replay_report.json"))
    parser.add_argument("--frozen", type=Path, default=Path("data/put_depth_full_replay_experiment.json"))
    parser.add_argument("--historical-db", type=Path, default=Path("data/market/historical.db"))
    parser.add_argument("--instruments-db", type=Path, default=Path("data/instruments/instruments.db"))
    parser.add_argument("--output", type=Path, default=Path("data/historical_option_validation_coverage_report.json"))
    args = parser.parse_args()
    report = run(args)
    print(json.dumps({
        "status": report["status"],
        "output": str(args.output),
        "coverage": report["coverage"],
        "database_inventory": report["database_inventory"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
