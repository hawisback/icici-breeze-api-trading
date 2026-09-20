"""Reproducible BREEZE-only Strategy B R1 signal-level baseline.

This script deliberately calls the committed production FeatureEngine and
VolatilityBreakoutStrategy.  It does not call the replay lifecycle, fabricate
option prices, or calculate trade-performance metrics.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import AutoTradingConfig, HistoricalReplaySource
from services.strategy.replay_metadata import build_data_fingerprint
from services.strategy.simulation import SimulationEngine
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy


HISTORICAL_DB = ROOT / "data" / "market" / "historical.db"
INSTRUMENT_DB = ROOT / "data" / "instruments" / "instruments.db"
IST = timezone(timedelta(hours=5, minutes=30))
SESSION_START = time(9, 15)
LAST_ALIGNED_START = time(15, 25)
OPTIONAL_STORED_EXTRA_START = time(15, 30)
SESSION_END = time(15, 30)
EXPECTED_ALIGNED_SLOTS = [
    (datetime.combine(date(2000, 1, 1), SESSION_START) + timedelta(minutes=5 * i)).time()
    for i in range(75)
]
EXPECTED_ALIGNED_SLOT_SET = set(EXPECTED_ALIGNED_SLOTS)


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def to_ist(value: datetime) -> datetime:
    return value.astimezone(IST)


def local_start(candle: Candle) -> datetime:
    return to_ist(candle.start_time)


def row_to_candle(row: sqlite3.Row) -> Candle:
    return Candle(
        instrument_id=row["instrument_id"],
        interval=row["interval"],
        start_time=parse_dt(row["start_time"]),
        end_time=parse_dt(row["end_time"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"] or 0),
        open_interest=int(row["open_interest"] or 0),
        source=row["source"],
    )


def load_candles(instrument_id: str | None = None) -> list[Candle]:
    connection = sqlite3.connect(HISTORICAL_DB)
    connection.row_factory = sqlite3.Row
    clauses = ["interval = '5m'", "source = 'BREEZE'"]
    params: list[Any] = []
    if instrument_id is not None:
        clauses.append("instrument_id = ?")
        params.append(instrument_id)
    rows = connection.execute(
        "SELECT * FROM historical_candles WHERE " + " AND ".join(clauses) + " ORDER BY start_time",
        params,
    ).fetchall()
    connection.close()
    return [row_to_candle(row) for row in rows]


def load_futures_instruments() -> list[dict[str, str]]:
    connection = sqlite3.connect(INSTRUMENT_DB)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT instrument_id, expiry
        FROM instruments
        WHERE segment = 'FUTURES' AND underlying = 'NIFTY' AND expiry IS NOT NULL
        ORDER BY expiry ASC, instrument_id ASC
        """
    ).fetchall()
    connection.close()
    return [{"instrument_id": row["instrument_id"], "expiry": row["expiry"]} for row in rows]


def in_stored_session(candle: Candle) -> bool:
    value = local_start(candle).time()
    return SESSION_START <= value <= OPTIONAL_STORED_EXTRA_START


def in_aligned_session(candle: Candle) -> bool:
    value = local_start(candle).time()
    return SESSION_START <= value <= LAST_ALIGNED_START


def group_by_session(candles: list[Candle], aligned_only: bool = False) -> dict[str, list[Candle]]:
    grouped: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        if (in_aligned_session(candle) if aligned_only else in_stored_session(candle)):
            grouped[local_start(candle).date().isoformat()].append(candle)
    for rows in grouped.values():
        rows.sort(key=lambda item: item.start_time)
    return dict(grouped)


def valid_candle(candle: Candle) -> bool:
    return (
        candle.source == "BREEZE"
        and candle.interval == "5m"
        and candle.low > 0
        and candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high
        and candle.end_time - candle.start_time == timedelta(minutes=5)
    )


def session_quality(rows: list[Candle]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda item: item.start_time)
    starts = [local_start(row).time() for row in ordered]
    regular = [row for row in ordered if local_start(row).time() <= LAST_ALIGNED_START]
    actual_regular = set(local_start(row).time() for row in regular)
    extras = sorted(set(starts) - EXPECTED_ALIGNED_SLOT_SET)
    malformed = [row for row in ordered if not valid_candle(row)]
    missing = sorted(EXPECTED_ALIGNED_SLOT_SET - actual_regular)
    complete = (
        len(regular) == len(EXPECTED_ALIGNED_SLOTS)
        and actual_regular == EXPECTED_ALIGNED_SLOT_SET
        and all(value == OPTIONAL_STORED_EXTRA_START for value in extras)
        and not malformed
    )
    durations = sorted({int((row.end_time - row.start_time).total_seconds()) for row in ordered})
    return {
        "stored_row_count": len(ordered),
        "aligned_completed_bar_count": len(regular),
        "unique_aligned_start_count": len(actual_regular),
        "earliest_start_ist": local_start(ordered[0]).isoformat() if ordered else None,
        "latest_start_ist": local_start(ordered[-1]).isoformat() if ordered else None,
        "latest_end_ist": to_ist(ordered[-1].end_time).isoformat() if ordered else None,
        "interval_durations_seconds": durations,
        "missing_aligned_slots_ist": [value.strftime("%H:%M") for value in missing],
        "extra_slots_ist": [value.strftime("%H:%M") for value in extras],
        "malformed_count": len(malformed),
        "complete_under_75_bar_convention": complete,
    }


def select_contract(instruments: list[dict[str, str]], session_date: str) -> dict[str, str] | None:
    return next((item for item in instruments if item["expiry"] >= session_date), None)


def clean_rows(rows: list[Candle]) -> list[Candle]:
    return [row for row in rows if valid_candle(row)]


def bucket(timestamp: datetime) -> str:
    current = to_ist(timestamp).time()
    buckets = [
        (time(9, 25), time(10, 0), "09:25-10:00"),
        (time(10, 0), time(11, 0), "10:00-11:00"),
        (time(11, 0), time(12, 0), "11:00-12:00"),
        (time(12, 0), time(13, 0), "12:00-13:00"),
        (time(13, 0), time(14, 0), "13:00-14:00"),
        (time(14, 0), time(14, 45), "14:00-14:45"),
    ]
    for start, end, label in buckets:
        if start <= current < end or (label == "14:00-14:45" and current == end):
            return label
    return "outside_entry_buckets"


def script_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def git_info() -> dict[str, Any]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return {"commit_sha": sha, "working_tree_status_at_run": status}


def config_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def artifact_path(start: str, end: str) -> Path:
    base = ROOT / "data" / f"strategy_b_r1_signal_baseline_breeze_{start}_{end}_reconciled"
    candidate = base.with_suffix(".json")
    version = 2
    while candidate.exists():
        candidate = base.with_name(base.name + f"_v{version}").with_suffix(".json")
        version += 1
    return candidate


def terminal_reason_for_blocker(blocker: str, reason: str) -> str | None:
    if blocker == "BOX_EXPIRED":
        return "expired"
    if blocker == "STRUCTURAL_EXPANSION":
        return "structural_invalidation"
    if blocker == "BREAKOUT_OVEREXTENDED":
        return "breakout_overextended"
    if blocker in {"INSUFFICIENT_CONFIRMATION_DATA", "CONFIRMATION_SCORE_LOW"}:
        return "confirmation_rejected"
    if blocker == "RISK_TOO_HIGH":
        return "risk_rejected"
    if "Outside Strategy B entry window" in reason:
        return "session_or_end_window_reset"
    if blocker in {"RESET", "MISSING"}:
        return "data_or_config_reset"
    return None


def main() -> None:
    if not HISTORICAL_DB.exists() or not INSTRUMENT_DB.exists():
        raise SystemExit("Required BREEZE historical/instrument database is missing")

    production_config = AutoTradingConfig()
    tunables = production_config.tunables
    session_config = production_config.session
    expected = {
        "compression_lookback": 8,
        "bb_width_percentile": 25.0,
        "box_max_height_atr": 1.30,
        "box_max_age_bars": 8,
        "breakout_buffer_atr": 0.05,
        "breakout_max_extension_atr": 0.75,
        "strat_b_min_confirmation": 3,
        "rvol_threshold": 1.20,
    }
    effective = {
        "compression_lookback": tunables.compression_lookback_bars,
        "bb_width_percentile": tunables.bb_width_percentile_threshold,
        "box_max_height_atr": tunables.box_max_height_atr,
        "box_max_age_bars": tunables.box_max_age_bars,
        "breakout_buffer_atr": tunables.breakout_buffer_atr,
        "breakout_max_extension_atr": tunables.breakout_max_extension_atr,
        "strat_b_min_confirmation": tunables.strat_b_min_confirmation,
        "rvol_threshold": tunables.rvol_threshold,
    }
    if effective != expected:
        raise SystemExit(f"Production R1 configuration mismatch: {effective} != {expected}")

    instruments = load_futures_instruments()
    future_ids = {item["instrument_id"] for item in instruments}
    spot_all = load_candles("INST-NIFTY-INDEX")
    futures_all = [row for row in load_candles() if row.instrument_id in future_ids]
    spot_sessions = group_by_session(spot_all)
    spot_aligned_sessions = group_by_session(spot_all, aligned_only=True)
    futures_by_instrument: dict[str, list[Candle]] = defaultdict(list)
    for row in futures_all:
        futures_by_instrument[row.instrument_id].append(row)
    for rows in futures_by_instrument.values():
        rows.sort(key=lambda item: item.start_time)

    sessions = sorted(spot_sessions)
    spot_quality = {session: session_quality(spot_sessions[session]) for session in sessions}
    selected_contracts = {session: select_contract(instruments, session) for session in sessions}
    selected_futures_sessions: dict[str, list[Candle]] = {}
    futures_quality: dict[str, dict[str, Any]] = {}
    for session in sessions:
        contract = selected_contracts[session]
        rows = []
        if contract:
            rows = [
                row for row in futures_by_instrument.get(contract["instrument_id"], [])
                if local_start(row).date().isoformat() == session and in_stored_session(row)
            ]
        selected_futures_sessions[session] = sorted(rows, key=lambda item: item.start_time)
        futures_quality[session] = session_quality(selected_futures_sessions[session])

    spot_complete = [session for session in sessions if spot_quality[session]["complete_under_75_bar_convention"]]
    selected_futures_available = [session for session in sessions if selected_futures_sessions[session]]
    selected_futures_complete = [
        session for session in sessions if futures_quality[session]["complete_under_75_bar_convention"]
    ]
    no_selected_futures = [session for session in sessions if not selected_futures_sessions[session]]
    partial_futures = [
        session for session in selected_futures_available if not futures_quality[session]["complete_under_75_bar_convention"]
    ]
    timestamp_mismatch = []
    usable_sessions = []
    for session in sessions:
        spot = spot_aligned_sessions.get(session, [])
        futures = [row for row in selected_futures_sessions[session] if in_aligned_session(row)]
        if not spot_quality[session]["complete_under_75_bar_convention"]:
            continue
        if not futures_quality[session]["complete_under_75_bar_convention"]:
            continue
        if [row.start_time for row in spot] != [row.start_time for row in futures]:
            timestamp_mismatch.append(session)
            continue
        usable_sessions.append(session)
    if not usable_sessions:
        raise SystemExit("No clean paired BREEZE sessions under the verified 75-bar convention")

    funnel: Counter[str] = Counter()
    diagnostic_blockers: Counter[str] = Counter()
    attempt_outcomes: Counter[str] = Counter()
    terminal_outcomes: Counter[str] = Counter()
    signals_by_date: Counter[str] = Counter()
    signals_by_week: Counter[str] = Counter()
    signals_by_direction: Counter[str] = Counter()
    signals_by_bucket: Counter[str] = Counter()
    signals_by_age: Counter[str] = Counter()
    signals_by_score: Counter[str] = Counter()
    signals_by_extension: Counter[str] = Counter()
    signal_records: list[dict[str, Any]] = []
    box_records: dict[str, dict[str, Any]] = {}
    signal_ids: set[str] = set()
    duplicate_signal_ids: list[str] = []
    duplicate_consumed_boxes: list[str] = []
    timestamp_anomalies: list[dict[str, Any]] = []
    reference_anomalies: list[dict[str, Any]] = []
    box_lock_breakout_anomalies: list[dict[str, Any]] = []
    cutoff_anomalies: list[dict[str, Any]] = []
    expired_box_signal_anomalies: list[dict[str, Any]] = []
    futures_readiness_anomalies: list[dict[str, Any]] = []
    duplicate_bar_evaluations: list[dict[str, Any]] = []
    used_spot: dict[tuple[str, str], Candle] = {}
    used_futures: dict[tuple[str, str], Candle] = {}
    consumed_boxes: set[str] = set()
    attempt_ages: list[int] = []
    attempt_extensions: list[float] = []
    raw_scores: list[int] = []
    effective_scores: list[int] = []
    box_heights: list[float] = []
    box_bb_percentiles: list[float] = []
    oi_wall_penalties = 0
    option_chain_observations = 0

    for session in usable_sessions:
        spot_rows = spot_aligned_sessions[session]
        contract = selected_contracts[session]
        assert contract is not None
        futures_source = futures_by_instrument[contract["instrument_id"]]
        session_start = datetime.combine(date.fromisoformat(session), SESSION_START, tzinfo=IST).astimezone(timezone.utc)
        warmup_start = session_start - timedelta(days=7)
        spot_warmup = clean_rows([row for row in spot_all if warmup_start <= row.start_time < session_start])
        futures_warmup = clean_rows([row for row in futures_source if warmup_start <= row.start_time < session_start])
        for row in spot_warmup + spot_rows:
            used_spot[(row.instrument_id, row.start_time.isoformat())] = row
        for row in futures_warmup + selected_futures_sessions[session]:
            used_futures[(row.instrument_id, row.start_time.isoformat())] = row

        # This is the same configuration flow as production/simulation:
        # AutoTradingConfig().tunables -> Strategy B constructor. No overrides.
        strategy = VolatilityBreakoutStrategy(
            rvol_threshold=tunables.rvol_threshold,
            adx_threshold=tunables.adx_threshold,
            min_confirmation_score=tunables.strat_b_min_confirmation,
            box_max_height_atr=tunables.box_max_height_atr,
            bb_width_percentile_threshold=tunables.bb_width_percentile_threshold,
            lookback_bars=tunables.compression_lookback_bars,
            max_age_bars=tunables.box_max_age_bars,
            breakout_buffer_atr=tunables.breakout_buffer_atr,
            max_extension_atr=tunables.breakout_max_extension_atr,
            entry_start=session_config.strategy_b_no_new_trade_before,
            entry_end=session_config.no_new_trade_after,
        )
        running_spot = list(spot_warmup)
        running_futures: list[Candle] = list(futures_warmup)
        evaluated_starts: set[str] = set()

        for bar_index, bar in enumerate(spot_rows):
            if bar.start_time.isoformat() in evaluated_starts:
                duplicate_bar_evaluations.append({"session": session, "bar": bar.start_time.isoformat()})
            evaluated_starts.add(bar.start_time.isoformat())
            running_spot.append(bar)
            running_futures.extend(row for row in selected_futures_sessions[session] if row.start_time == bar.start_time)
            running_futures.sort(key=lambda item: item.start_time)
            if any(row.end_time > bar.end_time for row in running_spot + running_futures):
                raise RuntimeError(f"Future data exposed at {session} {bar.end_time.isoformat()}")

            macro = SimulationEngine.resample_to_15m(running_spot, "INST-NIFTY-INDEX")
            features = FeatureEngine.compute_all_features(
                running_spot,
                macro,
                running_futures,
                option_chain=None,
                spot_price=bar.close,
                as_of=bar.end_time,
            )
            option_chain_observations += 1
            funnel["completed_5m_bars_processed"] += 1

            before_box = strategy.locked_box
            before_key = f"{session}:{before_box.created_bar_time}" if before_box else None
            diagnostics = strategy.diagnose(features, running_spot)
            for diagnostic in diagnostics:
                blocker = str((diagnostic.phase_summary or {}).get("primary_blocker") or "")
                if blocker:
                    diagnostic_blockers[blocker] += 1
            if before_box is None and any(
                diagnostic.phase_state in {"SEARCHING_COMPRESSION", "BOX_LOCKED"} for diagnostic in diagnostics
            ):
                funnel["compression_evaluations"] += 1

            signal = strategy.evaluate(features, running_spot)
            after_box = strategy.locked_box
            after_key = f"{session}:{after_box.created_bar_time}" if after_box else None

            if before_box is None and after_box is not None:
                lock_summary = next(
                    (diagnostic.phase_summary for diagnostic in diagnostics if diagnostic.phase_state == "BOX_LOCKED"),
                    {},
                )
                box_key = after_key
                box_records[box_key] = {
                    "box_id": box_key,
                    "session": session,
                    "created_bar_time": after_box.created_bar_time,
                    "lock_bar_index": bar_index,
                    "box_high": after_box.box_high,
                    "box_low": after_box.box_low,
                    "box_height_atr": (after_box.box_height / after_box.atr_at_lock) if after_box.atr_at_lock else None,
                    "atr_at_lock": after_box.atr_at_lock,
                    "bb_width_percentile": after_box.bb_width_at_lock,
                    "attempts": [],
                    "terminal_outcome": None,
                }
                funnel["valid_boxes_locked"] += 1
                box_heights.append(float(box_records[box_key]["box_height_atr"]))
                box_bb_percentiles.append(float(after_box.bb_width_at_lock))
                if lock_summary.get("box", {}).get("created_bar_time") not in (None, after_box.created_bar_time):
                    box_records[box_key]["lock_trace_anomaly"] = True

            active_box_key = before_key
            direction_summary: dict[str, Any] = {}
            if before_box is not None:
                for diagnostic in diagnostics:
                    summary = diagnostic.phase_summary or {}
                    if summary.get("direction") in {"BULLISH", "BEARISH"}:
                        direction_summary = summary
                        break
                    if summary.get("primary_blocker") in {
                        "BREAKOUT_OVEREXTENDED", "INSUFFICIENT_CONFIRMATION_DATA",
                        "CONFIRMATION_SCORE_LOW", "RISK_TOO_HIGH", "STRUCTURAL_EXPANSION", "BOX_EXPIRED",
                        "RESET", "Outside Strategy B entry window",
                    }:
                        direction_summary = summary
                        break
                blocker = str(direction_summary.get("primary_blocker") or "")
                direction = direction_summary.get("direction")
                is_attempt = direction in {"BULLISH", "BEARISH"} or blocker in {
                    "BREAKOUT_OVEREXTENDED", "INSUFFICIENT_CONFIRMATION_DATA",
                    "CONFIRMATION_SCORE_LOW", "RISK_TOO_HIGH",
                }
                if is_attempt:
                    attempt_outcome = "qualified_signal" if signal is not None else blocker.lower() or "unspecified_rejection"
                    attempt = {
                        "bar_time": bar.end_time.isoformat(),
                        "direction": direction,
                        "outcome": attempt_outcome,
                        "box_age_bars": direction_summary.get("box_age_bars"),
                        "breakout_extension_atr": (direction_summary.get("extension") or {}).get("breakout_extension_atr"),
                        "raw_confirmation_score": direction_summary.get("raw_confirmation_score"),
                        "effective_confirmation_score": direction_summary.get("effective_confirmation_score"),
                        "oi_wall_penalty": direction_summary.get("oi_wall_penalty", 0),
                    }
                    box_records[active_box_key]["attempts"].append(attempt)
                    funnel["CALL_breakout_attempts" if direction == "BULLISH" else "PUT_breakout_attempts"] += 1
                    attempt_outcomes[attempt_outcome] += 1
                    if attempt["box_age_bars"] is not None:
                        attempt_ages.append(int(attempt["box_age_bars"]))
                    if attempt["breakout_extension_atr"] is not None:
                        attempt_extensions.append(float(attempt["breakout_extension_atr"]))
                    if attempt["raw_confirmation_score"] is not None:
                        raw_scores.append(int(attempt["raw_confirmation_score"]))
                    if attempt["effective_confirmation_score"] is not None:
                        effective_scores.append(int(attempt["effective_confirmation_score"]))
                    if attempt["oi_wall_penalty"]:
                        oi_wall_penalties += 1
                    if blocker == "BREAKOUT_OVEREXTENDED":
                        funnel["overextended_breakout_rejections"] += 1
                    elif blocker == "INSUFFICIENT_CONFIRMATION_DATA":
                        funnel["insufficient_confirmation_data_rejections"] += 1
                    elif blocker == "CONFIRMATION_SCORE_LOW":
                        funnel["confirmation_score_rejections"] += 1
                    elif blocker == "RISK_TOO_HIGH":
                        funnel["risk_gate_rejections"] += 1

            if signal is not None:
                funnel["qualified_CALL_signals" if signal.direction.value == "BULLISH" else "qualified_PUT_signals"] += 1
                funnel["total_qualified_strategy_b_signals"] += 1
                signal_id = signal.signal_id
                if signal_id in signal_ids:
                    duplicate_signal_ids.append(signal_id)
                signal_ids.add(signal_id)
                snapshot = signal.features_snapshot
                signal_box = str(f"{session}:{snapshot.get('box_created_time')}")
                if signal_box in consumed_boxes:
                    duplicate_consumed_boxes.append(signal_box)
                consumed_boxes.add(signal_box)
                if signal.timestamp != bar.end_time:
                    timestamp_anomalies.append({"signal_id": signal_id, "signal": signal.timestamp.isoformat(), "bar": bar.end_time.isoformat()})
                if signal.spot_reference_price != bar.close or snapshot.get("entry_reference_spot") != bar.close:
                    reference_anomalies.append({"signal_id": signal_id, "signal_reference": signal.spot_reference_price, "entry_reference": snapshot.get("entry_reference_spot"), "bar_close": bar.close})
                if snapshot.get("box_created_time") == bar.end_time.isoformat():
                    box_lock_breakout_anomalies.append({"signal_id": signal_id, "bar": bar.end_time.isoformat()})
                local_signal = to_ist(signal.timestamp)
                if local_signal.time() < time(9, 25) or local_signal.time() > time(14, 45):
                    cutoff_anomalies.append({"signal_id": signal_id, "ist_time": local_signal.isoformat()})
                if not features.breakout_data_ready or not running_futures or running_futures[-1].end_time != bar.end_time:
                    futures_readiness_anomalies.append({"signal_id": signal_id, "bar": bar.end_time.isoformat(), "breakout_data_ready": features.breakout_data_ready, "futures_latest": running_futures[-1].end_time.isoformat() if running_futures else None})
                record = {
                    "signal_id": signal_id,
                    "trading_date": session,
                    "week": f"{local_signal.isocalendar().year}-W{local_signal.isocalendar().week:02d}",
                    "timestamp": signal.timestamp.isoformat(),
                    "timestamp_ist": local_signal.isoformat(),
                    "direction": signal.direction.value,
                    "option_type": signal.option_type.value,
                    "spot_reference_price": signal.spot_reference_price,
                    "breakout_candle": {"start_time": bar.start_time.isoformat(), "end_time": bar.end_time.isoformat(), "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close},
                    "box_created_time": snapshot.get("box_created_time"),
                    "entry_reference_spot": snapshot.get("entry_reference_spot"),
                    "box_high": snapshot.get("box_high"),
                    "box_low": snapshot.get("box_low"),
                    "atr_at_lock": snapshot.get("atr_at_lock"),
                    "box_age_at_breakout": direction_summary.get("box_age_bars"),
                    "confirmation_score_raw": snapshot.get("raw_confirmation_score"),
                    "confirmation_score_effective": snapshot.get("effective_confirmation_score"),
                    "oi_wall_penalty": snapshot.get("oi_wall_penalty", 0),
                    "confirmation_factors": snapshot.get("confirmation_factors"),
                    "oi_wall_detected": snapshot.get("oi_wall_detected"),
                    "breakout_extension_atr": snapshot.get("breakout_extension_atr"),
                    "structural_stop": signal.structural_stop,
                    "initial_r_points": signal.r_points,
                    "historical_source": "BREEZE",
                    "selected_futures_contract": contract,
                    "option_chain_available": False,
                }
                signal_records.append(record)
                signals_by_date[session] += 1
                signals_by_week[record["week"]] += 1
                signals_by_direction[record["direction"]] += 1
                signals_by_bucket[bucket(signal.timestamp)] += 1
                age = record["box_age_at_breakout"]
                if age is not None:
                    signals_by_age[str(age)] += 1
                    if int(age) > tunables.box_max_age_bars:
                        expired_box_signal_anomalies.append({"signal_id": signal_id, "box_age_at_breakout": age})
                score = record["confirmation_score_effective"]
                if score is not None:
                    signals_by_score[str(score)] += 1
                extension = float(record["breakout_extension_atr"] or 0)
                band = "<0.25" if extension < 0.25 else "0.25-<0.50" if extension < 0.50 else "0.50-<0.75" if extension < 0.75 else ">=0.75"
                signals_by_extension[band] += 1

            if before_box is not None and after_box is None and active_box_key in box_records and box_records[active_box_key]["terminal_outcome"] is None:
                blocker = str(direction_summary.get("primary_blocker") or "")
                reason = str(direction_summary.get("blocker_detail") or "")
                if signal is not None:
                    terminal = "consumed_signal"
                else:
                    terminal = terminal_reason_for_blocker(blocker, reason) or "other_reset"
                box_records[active_box_key]["terminal_outcome"] = terminal
                box_records[active_box_key]["terminal_bar_time"] = bar.end_time.isoformat()
                terminal_outcomes[terminal] += 1

        if strategy.locked_box is not None:
            key = f"{session}:{strategy.locked_box.created_bar_time}"
            record = box_records[key]
            if record["terminal_outcome"] is None:
                record["terminal_outcome"] = "session_or_end_window_reset"
                record["terminal_bar_time"] = spot_rows[-1].end_time.isoformat()
                terminal_outcomes["session_or_end_window_reset"] += 1
            strategy.reset(spot_rows[-1].end_time)

    for record in box_records.values():
        if record["terminal_outcome"] is None:
            record["terminal_outcome"] = "unresolved_analysis_state"
            terminal_outcomes["unresolved_analysis_state"] += 1

    terminal_outcome_names = {
        "consumed_signal", "expired", "structural_invalidation", "breakout_overextended",
        "confirmation_rejected", "risk_rejected", "session_or_end_window_reset",
        "data_or_config_reset", "other_reset", "unresolved_analysis_state",
    }
    detailed_terminal_counts = {name: terminal_outcomes.get(name, 0) for name in sorted(terminal_outcome_names)}
    invalidated_count = sum(
        count for name, count in detailed_terminal_counts.items()
        if name not in {"consumed_signal", "expired", "session_or_end_window_reset"}
    )
    boxes_locked = len(box_records)
    reconciliation = {
        "boxes_locked": boxes_locked,
        "boxes_consumed": detailed_terminal_counts["consumed_signal"],
        "boxes_expired": detailed_terminal_counts["expired"],
        "boxes_session_or_end_window_reset": detailed_terminal_counts["session_or_end_window_reset"],
        "boxes_invalidated_other_terminal": invalidated_count,
        "invariant_holds": boxes_locked == detailed_terminal_counts["consumed_signal"] + detailed_terminal_counts["expired"] + detailed_terminal_counts["session_or_end_window_reset"] + invalidated_count,
        "detailed_terminal_outcomes": detailed_terminal_counts,
        "attempt_outcomes": dict(attempt_outcomes),
        "note": "Breakout attempts are recorded separately from final box terminal outcomes; production resets the box for overextension, confirmation, risk, and structural-expansion rejection.",
    }

    used_spot_rows = list(used_spot.values())
    used_futures_rows = list(used_futures.values())
    used_contracts = sorted({(item["instrument_id"], item["expiry"]) for item in selected_contracts.values() if item})
    data_fingerprint = build_data_fingerprint(
        source=HistoricalReplaySource.BREEZE,
        start_date=usable_sessions[0],
        end_date=usable_sessions[-1],
        spot_candles=used_spot_rows,
        futures_candles=used_futures_rows,
        source_diagnostics={
            "spot": {"requested_source": "BREEZE", "selected_count": len(used_spot_rows)},
            "futures": {"requested_source": "BREEZE", "selected_count": len(used_futures_rows)},
        },
        futures_contracts=[{"instrument_id": instrument_id, "expiry": expiry} for instrument_id, expiry in used_contracts],
        missing_data=[],
    )
    configuration = {
        "analysis": "strategy_b_signal_level_historical_baseline_reconciled",
        "production_config_path": "AutoTradingConfig().tunables -> VolatilityBreakoutStrategy kwargs",
        "threshold_overrides": None,
        "tunables": tunables.model_dump(mode="json"),
        "session": session_config.model_dump(mode="json"),
        "historical_source": "BREEZE",
        "session_boundaries_ist": {"first_start": "09:15", "last_aligned_start": "15:25", "aligned_completed_bars": 75, "optional_stored_extra_start": "15:30", "stored_extra_end": "15:35"},
        "futures_selection_rule": "earliest NIFTY FUTURES contract with expiry >= session date",
        "option_chain": "unavailable; option_chain=None passed to production FeatureEngine",
    }
    configuration_hash = config_fingerprint(configuration)
    correctness = {
        "duplicate_signal_ids": duplicate_signal_ids,
        "multiple_signals_from_one_consumed_box": duplicate_consumed_boxes,
        "signal_timestamp_mismatches": timestamp_anomalies,
        "signal_reference_mismatches": reference_anomalies,
        "box_lock_candle_breakouts": box_lock_breakout_anomalies,
        "outside_entry_window_signals": cutoff_anomalies,
        "expired_box_signals": expired_box_signal_anomalies,
        "missing_futures_readiness_signals": futures_readiness_anomalies,
        "duplicate_completed_bar_evaluations": duplicate_bar_evaluations,
    }
    correctness["all_clear"] = not any(correctness.values())
    git = git_info()
    report = {
        "report_type": "SIGNAL_LEVEL_HISTORICAL_BASELINE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git": git,
        "analysis_script": {"path": str(Path(__file__).relative_to(ROOT)), "sha256": script_sha256()},
        "strategy_revision": "Strategy B R1 committed completed-bar signal engine",
        "configuration_fingerprint": configuration_hash,
        "configuration": configuration,
        "historical_source": "BREEZE",
        "requested_date_range": {"start": sessions[0], "end": sessions[-1]},
        "actual_date_range_used": {"start": usable_sessions[0], "end": usable_sessions[-1]},
        "timestamp_convention_evidence": {
            "stored_normal_session_rows": 76,
            "stored_first_start_ist": "09:15",
            "stored_last_start_ist": "15:30",
            "stored_last_end_ist": "15:35",
            "bar_duration_seconds": 300,
            "aligned_strategy_session_rows": 75,
            "aligned_last_start_ist": "15:25",
            "aligned_last_end_ist": "15:30",
            "interpretation": "The 15:30-start stored row is outside the 09:15-15:30 completed decision session and is not required for Strategy B alignment.",
        },
        "data_window": {
            "earliest_available_nifty_spot_5m_date": sessions[0],
            "latest_available_nifty_spot_5m_date": sessions[-1],
            "distinct_spot_sessions": len(sessions),
            "spot_complete_sessions_75_bar_convention": len(spot_complete),
            "spot_partial_sessions": len(sessions) - len(spot_complete),
            "spot_5m_candle_count_all_breeze": len(spot_all),
            "futures_5m_candle_count_all_breeze": len(futures_all),
            "selected_futures_sessions_with_any_rows": len(selected_futures_available),
            "selected_futures_complete_sessions_75_bar_convention": len(selected_futures_complete),
            "sessions_with_sufficient_aligned_futures": len(usable_sessions),
            "sessions_excluded_no_selected_futures_data": no_selected_futures,
            "sessions_excluded_partial_futures_data": partial_futures,
            "sessions_excluded_timestamp_mismatch": timestamp_mismatch,
            "session_quality_by_date": {session: {"spot": spot_quality[session], "selected_futures": futures_quality[session], "selected_contract": selected_contracts[session]} for session in sessions},
        },
        "funnel": {
            "sessions_processed": len(usable_sessions),
            "completed_5m_bars_processed": funnel["completed_5m_bars_processed"],
            "compression_evaluations": funnel["compression_evaluations"],
            "valid_boxes_locked": funnel["valid_boxes_locked"],
            "boxes_expired": detailed_terminal_counts["expired"],
            "boxes_structurally_invalidated": detailed_terminal_counts["structural_invalidation"],
            "CALL_breakout_attempts": funnel["CALL_breakout_attempts"],
            "PUT_breakout_attempts": funnel["PUT_breakout_attempts"],
            "overextended_breakout_rejections": funnel["overextended_breakout_rejections"],
            "insufficient_confirmation_data_rejections": funnel["insufficient_confirmation_data_rejections"],
            "confirmation_score_rejections": funnel["confirmation_score_rejections"],
            "OI_wall_penalties_applied": oi_wall_penalties,
            "risk_gate_rejections": funnel["risk_gate_rejections"],
            "qualified_CALL_signals": funnel["qualified_CALL_signals"],
            "qualified_PUT_signals": funnel["qualified_PUT_signals"],
            "total_qualified_strategy_b_signals": funnel["total_qualified_strategy_b_signals"],
        },
        "funnel_counting_notes": {
            "funnel_events": "Compression, box locks, breakout attempts, rejections, and qualified signals are counted as event transitions.",
            "diagnostic_blocker_occurrences": "Repeated primary blockers from read-only diagnostics are separate descriptive counts and are not treated as mutually exclusive funnel stages.",
            "diagnostic_blocker_occurrences_by_reason": dict(diagnostic_blockers),
        },
        "box_reconciliation": reconciliation,
        "distribution": {
            "signals_by_date": dict(sorted(signals_by_date.items())),
            "signals_by_week": dict(sorted(signals_by_week.items())),
            "signals_by_direction": dict(signals_by_direction),
            "signals_per_session": round(len(signal_records) / len(usable_sessions), 4),
            "signals_by_time_of_day_ist": dict(sorted(signals_by_bucket.items())),
            "signals_by_box_age_at_breakout": dict(sorted(signals_by_age.items(), key=lambda item: int(item[0]))),
            "signals_by_effective_confirmation_score": dict(sorted(signals_by_score.items())),
            "signals_by_breakout_extension_band": dict(sorted(signals_by_extension.items())),
        },
        "averages": {
            "bars_from_box_lock_to_breakout_attempt": {"average": statistics.mean(attempt_ages) if attempt_ages else None, "median": statistics.median(attempt_ages) if attempt_ages else None},
            "box_height_at_lock_atr": statistics.mean(box_heights) if box_heights else None,
            "bb_percentile_at_lock": statistics.mean(box_bb_percentiles) if box_bb_percentiles else None,
            "breakout_extension_atr_attempts": statistics.mean(attempt_extensions) if attempt_extensions else None,
            "raw_confirmation_score_attempts": statistics.mean(raw_scores) if raw_scores else None,
            "effective_confirmation_score_attempts": statistics.mean(effective_scores) if effective_scores else None,
        },
        "option_chain_and_data_limitations": {
            "option_chain_available_historically": False,
            "option_chain_observations": option_chain_observations,
            "handling": "option_chain=None was passed to production FeatureEngine; no OI-wall evidence was fabricated, so OI-wall penalties are zero.",
            "futures_contract_selection": "point-in-time earliest NIFTY futures expiry on or after session date; this is historical contract selection, not live-equivalent option execution.",
        },
        "correctness_checks": correctness,
        "replay_performance_metrics": {"status": "NOT_REPORTED", "reason": "Strategy B lifecycle replay is not yet verified; no fills, exits, PnL, R, win rate, drawdown, or option performance were calculated."},
        "data_fingerprint": data_fingerprint.model_dump(mode="json"),
        "signal_records": signal_records,
        "box_records": list(box_records.values()),
    }
    destination = artifact_path(usable_sessions[0], usable_sessions[-1])
    destination.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "artifact": str(destination),
        "git_sha": git["commit_sha"],
        "analysis_script_sha256": report["analysis_script"]["sha256"],
        "configuration_fingerprint": configuration_hash,
        "data_fingerprint": data_fingerprint.dataset_hash,
        "sessions": len(usable_sessions),
        "funnel": report["funnel"],
        "box_reconciliation": reconciliation,
        "correctness_all_clear": correctness["all_clear"],
    }, indent=2))


if __name__ == "__main__":
    main()
