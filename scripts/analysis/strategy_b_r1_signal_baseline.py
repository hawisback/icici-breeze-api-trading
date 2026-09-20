"""Generate a BREEZE-only, signal-level Strategy B R1 historical baseline.

This analysis deliberately calls the production FeatureEngine and
VolatilityBreakoutStrategy.  It does not call SimulationEngine's replay
lifecycle, create option fills, or calculate trade performance.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from statistics import mean, median
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import HistoricalReplaySource
from services.strategy.replay_metadata import build_data_fingerprint
from services.strategy.simulation import SimulationEngine
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy

HISTORICAL_DB = ROOT / "data" / "market" / "historical.db"
INSTRUMENT_DB = ROOT / "data" / "instruments" / "instruments.db"
IST = timezone(timedelta(hours=5, minutes=30))
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
EXPECTED_SLOTS = [SESSION_START]
while EXPECTED_SLOTS[-1] < SESSION_END:
    current = datetime.combine(date(2000, 1, 1), EXPECTED_SLOTS[-1]) + timedelta(minutes=5)
    EXPECTED_SLOTS.append(current.time())
EXPECTED_SLOT_TIMES = set(EXPECTED_SLOTS)
EXPECTED_CANDLES = len(EXPECTED_SLOTS)

R1_PARAMETERS: dict[str, Any] = {
    "bb_width_percentile_threshold": 25.0,
    "bb_width_percentile_lookback": 60,
    "box_max_height_atr": 1.30,
    "lookback_bars": 8,
    "max_age_bars": 8,
    "breakout_buffer_atr": 0.05,
    "max_extension_atr": 0.75,
    "min_confirmation_score": 3,
    "min_available_confirmations": 3,
    "rvol_threshold": 1.20,
    "entry_start_ist": "09:25",
    "entry_end_ist": "14:45",
    "completed_bar_only": True,
    "breakout_polling": "not used; retained compatibility fields do not influence entry",
    "option_chain_input": None,
}


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def to_ist(value: datetime) -> datetime:
    return value.astimezone(IST)


def local_date(candle: Candle) -> str:
    return to_ist(candle.start_time).date().isoformat()


def in_session(candle: Candle) -> bool:
    local = to_ist(candle.start_time)
    return SESSION_START <= local.time() <= SESSION_END


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
    query = (
        "SELECT * FROM historical_candles WHERE "
        + " AND ".join(clauses)
        + " ORDER BY start_time ASC"
    )
    rows = connection.execute(query, params).fetchall()
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


def group_by_session(candles: list[Candle]) -> dict[str, list[Candle]]:
    grouped: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        if in_session(candle):
            grouped[local_date(candle)].append(candle)
    for rows in grouped.values():
        rows.sort(key=lambda candle: candle.start_time)
    return dict(grouped)


def quality(rows: list[Candle]) -> dict[str, Any]:
    starts = [to_ist(row.start_time).time() for row in rows]
    expected = set(EXPECTED_SLOT_TIMES)
    actual = set(starts)
    malformed = []
    for row in rows:
        valid_ohlc = row.low > 0 and row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high
        valid_duration = row.end_time - row.start_time == timedelta(minutes=5)
        if not valid_ohlc or not valid_duration:
            malformed.append({
                "start_time": row.start_time.isoformat(),
                "valid_ohlc": valid_ohlc,
                "valid_duration": valid_duration,
            })
    missing = sorted((expected - actual), key=lambda value: value.isoformat())
    extra = sorted((actual - expected), key=lambda value: value.isoformat())
    contiguous = not missing and not extra and len(rows) == EXPECTED_CANDLES
    return {
        "count": len(rows),
        "missing_slots_ist": [value.strftime("%H:%M") for value in missing],
        "extra_slots_ist": [value.strftime("%H:%M") for value in extra],
        "malformed_count": len(malformed),
        "malformed": malformed[:10],
        "complete": contiguous and not malformed,
    }


def selected_contract(instruments: list[dict[str, str]], session_date: str) -> dict[str, str] | None:
    candidates = [item for item in instruments if item["expiry"] >= session_date]
    return candidates[0] if candidates else None


def clean_rows(rows: list[Candle]) -> list[Candle]:
    return [
        row for row in rows
        if row.source == "BREEZE"
        and row.interval == "5m"
        and row.low > 0
        and row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high
        and row.end_time - row.start_time == timedelta(minutes=5)
    ]


def bucket(timestamp: datetime) -> str:
    local = to_ist(timestamp).time()
    boundaries = [
        (time(9, 25), time(10, 0), "09:25-10:00"),
        (time(10, 0), time(11, 0), "10:00-11:00"),
        (time(11, 0), time(12, 0), "11:00-12:00"),
        (time(12, 0), time(13, 0), "12:00-13:00"),
        (time(13, 0), time(14, 0), "13:00-14:00"),
        (time(14, 0), time(14, 45), "14:00-14:45"),
    ]
    for start, end, label in boundaries:
        if start <= local < end or (label == "14:00-14:45" and local == end):
            return label
    return "outside_entry_buckets"


def add_count(counter: Counter[str], key: str) -> None:
    counter[key] += 1


def choose_artifact(start: str, end: str) -> Path:
    base = ROOT / "data" / f"strategy_b_r1_signal_baseline_breeze_{start}_{end}"
    candidate = base.with_suffix(".json")
    index = 2
    while candidate.exists():
        candidate = base.with_name(base.name + f"_v{index}").with_suffix(".json")
        index += 1
    return candidate


def git_info() -> dict[str, Any]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return {"commit_sha": sha, "working_tree_modified_files": status}


def configuration_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    if not HISTORICAL_DB.exists():
        raise SystemExit(f"Missing historical database: {HISTORICAL_DB}")
    if not INSTRUMENT_DB.exists():
        raise SystemExit(f"Missing instrument database: {INSTRUMENT_DB}")

    instruments = load_futures_instruments()
    future_ids = {item["instrument_id"] for item in instruments}
    spot_all = load_candles("INST-NIFTY-INDEX")
    futures_all = [row for row in load_candles() if row.instrument_id in future_ids]
    futures_by_instrument: dict[str, list[Candle]] = defaultdict(list)
    for row in futures_all:
        futures_by_instrument[row.instrument_id].append(row)
    for rows in futures_by_instrument.values():
        rows.sort(key=lambda candle: candle.start_time)

    spot_sessions = group_by_session(spot_all)
    all_dates = sorted(spot_sessions)
    if args.start:
        all_dates = [value for value in all_dates if value >= args.start]
    if args.end:
        all_dates = [value for value in all_dates if value <= args.end]
    if not all_dates:
        raise SystemExit("No BREEZE NIFTY spot sessions found in the requested range")

    spot_quality = {session: quality(spot_sessions[session]) for session in all_dates}
    selected_contracts: dict[str, dict[str, str] | None] = {
        session: selected_contract(instruments, session) for session in all_dates
    }
    futures_sessions: dict[str, list[Candle]] = {}
    futures_quality: dict[str, dict[str, Any]] = {}
    paired_coverage: list[str] = []
    usable_sessions: list[str] = []
    for session in all_dates:
        contract = selected_contracts[session]
        rows = group_by_session(futures_by_instrument.get(contract["instrument_id"], [])) if contract else []
        futures_sessions[session] = rows.get(session, [])
        futures_quality[session] = quality(futures_sessions[session])
        if futures_sessions[session]:
            paired_coverage.append(session)
        if (
            spot_quality[session]["complete"]
            and futures_quality[session]["complete"]
            and [row.start_time for row in spot_sessions[session]] == [row.start_time for row in futures_sessions[session]]
        ):
            usable_sessions.append(session)

    if not usable_sessions:
        raise SystemExit("No clean paired BREEZE spot/futures sessions found")

    used_spot: dict[tuple[str, str], Candle] = {}
    used_futures: dict[tuple[str, str], Candle] = {}
    funnel: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    signal_records: list[dict[str, Any]] = []
    signals_by_date: Counter[str] = Counter()
    signals_by_direction: Counter[str] = Counter()
    signals_by_bucket: Counter[str] = Counter()
    signals_by_week: Counter[str] = Counter()
    signals_by_age: Counter[str] = Counter()
    signals_by_score: Counter[str] = Counter()
    signals_by_extension: Counter[str] = Counter()
    duplicate_signal_ids: list[str] = []
    duplicate_consumed_boxes: list[str] = []
    timestamp_anomalies: list[dict[str, Any]] = []
    reference_anomalies: list[dict[str, Any]] = []
    box_lock_breakout_anomalies: list[dict[str, Any]] = []
    cutoff_anomalies: list[dict[str, Any]] = []
    expired_box_signal_anomalies: list[dict[str, Any]] = []
    futures_readiness_anomalies: list[dict[str, Any]] = []
    signal_ids: set[str] = set()
    consumed_boxes: set[str] = set()
    box_records: dict[str, dict[str, Any]] = {}
    attempt_ages: list[int] = []
    attempt_extensions: list[float] = []
    effective_scores: list[float] = []
    box_heights: list[float] = []
    box_bb_percentiles: list[float] = []
    option_chain_observations = 0
    option_chain_missing_observations = 0
    oi_wall_penalties = 0

    for session_index, session in enumerate(usable_sessions):
        spot_rows = spot_sessions[session]
        futures_rows = futures_sessions[session]
        contract = selected_contracts[session]
        assert contract is not None
        session_start = datetime.combine(date.fromisoformat(session), SESSION_START, tzinfo=IST).astimezone(timezone.utc)
        warmup_start = session_start - timedelta(days=7)
        spot_warmup = clean_rows([
            row for row in spot_all
            if warmup_start <= row.start_time < session_start
        ])
        futures_source = futures_by_instrument[contract["instrument_id"]]
        futures_warmup = clean_rows([
            row for row in futures_source
            if warmup_start <= row.start_time < session_start
        ])
        for row in spot_warmup + spot_rows:
            used_spot[(row.instrument_id, row.start_time.isoformat())] = row
        for row in futures_warmup + futures_rows:
            used_futures[(row.instrument_id, row.start_time.isoformat())] = row

        strategy = VolatilityBreakoutStrategy(
            rvol_threshold=R1_PARAMETERS["rvol_threshold"],
            min_confirmation_score=R1_PARAMETERS["min_confirmation_score"],
            min_available_confirmations=R1_PARAMETERS["min_available_confirmations"],
            bb_width_percentile_threshold=R1_PARAMETERS["bb_width_percentile_threshold"],
            bb_width_percentile_lookback=R1_PARAMETERS["bb_width_percentile_lookback"],
            box_max_height_atr=R1_PARAMETERS["box_max_height_atr"],
            lookback_bars=R1_PARAMETERS["lookback_bars"],
            max_age_bars=R1_PARAMETERS["max_age_bars"],
            breakout_buffer_atr=R1_PARAMETERS["breakout_buffer_atr"],
            max_extension_atr=R1_PARAMETERS["max_extension_atr"],
            entry_start=R1_PARAMETERS["entry_start_ist"],
            entry_end=R1_PARAMETERS["entry_end_ist"],
        )
        running_spot = list(spot_warmup)
        running_futures = list(futures_warmup)
        for bar_index, bar in enumerate(spot_rows):
            running_spot.append(bar)
            running_futures.extend(row for row in futures_rows if row.start_time == bar.start_time)
            macro = SimulationEngine.resample_to_15m(running_spot, "INST-NIFTY-INDEX")
            features = FeatureEngine.compute_all_features(
                running_spot,
                macro,
                running_futures,
                option_chain=None,
                spot_price=bar.close,
                as_of=bar.end_time,
            )
            if option_chain_missing_observations >= 0:
                option_chain_missing_observations += 1
            option_chain_observations += 1
            if features.derivatives_score_components.get("bull", {}).get("call_oi_wall_penalty", 0) < 0:
                oi_wall_penalties += 1
            if features.derivatives_score_components.get("bear", {}).get("call_oi_wall_bonus", 0) > 0:
                oi_wall_penalties += 1

            diagnostics = strategy.diagnose(features, running_spot)
            diag_by_direction = {diag.direction.value: diag for diag in diagnostics}
            seen_bar_reasons: set[str] = set()
            for diag in diagnostics:
                blocker = str((diag.phase_summary or {}).get("primary_blocker") or "")
                if blocker and blocker not in seen_bar_reasons:
                    rejection_reasons[blocker] += 1
                    seen_bar_reasons.add(blocker)

            summary = diagnostics[0].phase_summary or {}
            if summary.get("candidate_box_bars") == R1_PARAMETERS["lookback_bars"]:
                funnel["compression_candidates"] += 1
            lock_diags = [diag for diag in diagnostics if diag.phase_state == "BOX_LOCKED"]
            if lock_diags:
                funnel["valid_boxes_locked"] += 1
                lock_summary = lock_diags[0].phase_summary or {}
                box = lock_summary.get("box") or {}
                lock_time = bar.end_time.isoformat()
                box_records[lock_time] = {
                    "lock_bar_index": bar_index,
                    "created_bar_time": lock_time,
                    "box_height_atr": lock_summary.get("box_height_atr"),
                    "bb_width_percentile": lock_summary.get("bb_width_percentile"),
                    "session": session,
                }
                if lock_summary.get("box_height_atr") is not None:
                    box_heights.append(float(lock_summary["box_height_atr"]))
                if lock_summary.get("bb_width_percentile") is not None:
                    box_bb_percentiles.append(float(lock_summary["bb_width_percentile"]))
            unique_phases = {(diag.phase_state, (diag.phase_summary or {}).get("primary_blocker")) for diag in diagnostics}
            if any(blocker == "BOX_EXPIRED" for _, blocker in unique_phases):
                funnel["boxes_expired"] += 1
            if any(blocker == "STRUCTURAL_EXPANSION" for _, blocker in unique_phases):
                funnel["boxes_structurally_invalidated"] += 1

            attempt_directions: list[tuple[str, dict[str, Any]]] = []
            summary_direction_seen = False
            for diag in diagnostics:
                phase_summary = diag.phase_summary or {}
                blocker = str(phase_summary.get("primary_blocker") or "")
                summary_direction = phase_summary.get("direction")
                if summary_direction in {"BULLISH", "BEARISH"}:
                    if not summary_direction_seen:
                        attempt_directions.append((summary_direction, phase_summary))
                        summary_direction_seen = True
                elif blocker == "BREAKOUT_OVEREXTENDED" and diag.phase_state == "RESET":
                    attempt_directions.append((diag.direction.value, phase_summary))
            for direction, phase_summary in attempt_directions:
                funnel["CALL_breakout_attempts" if direction == "BULLISH" else "PUT_breakout_attempts"] += 1
                if phase_summary.get("box_age_bars") is not None:
                    attempt_ages.append(int(phase_summary["box_age_bars"]))
                extension = (phase_summary.get("extension") or {}).get("breakout_extension_atr")
                if extension is not None:
                    attempt_extensions.append(float(extension))
                confirmation = phase_summary.get("confirmation") or {}
                if confirmation.get("score") is not None:
                    effective_scores.append(float(confirmation["score"]))
                blocker = str(phase_summary.get("primary_blocker") or "")
                if blocker == "BREAKOUT_OVEREXTENDED":
                    funnel["overextended_breakout_rejections"] += 1
                elif blocker == "INSUFFICIENT_CONFIRMATION_DATA":
                    funnel["insufficient_confirmation_data_rejections"] += 1
                elif blocker == "CONFIRMATION_SCORE_LOW":
                    funnel["confirmation_score_rejections"] += 1
                elif blocker == "RISK_TOO_HIGH":
                    funnel["risk_gate_rejections"] += 1

            signal = strategy.evaluate(features, running_spot)
            funnel["completed_5m_bars_processed"] += 1
            if signal is None:
                continue
            funnel["qualified_CALL_signals" if signal.direction.value == "BULLISH" else "qualified_PUT_signals"] += 1
            funnel["total_qualified_strategy_b_signals"] += 1
            signal_id = signal.signal_id
            if signal_id in signal_ids:
                duplicate_signal_ids.append(signal_id)
            signal_ids.add(signal_id)
            box_time = signal.features_snapshot.get("box_created_time")
            if box_time in consumed_boxes:
                duplicate_consumed_boxes.append(str(box_time))
            consumed_boxes.add(str(box_time))
            if signal.timestamp != bar.end_time:
                timestamp_anomalies.append({"signal_id": signal_id, "signal": signal.timestamp.isoformat(), "bar": bar.end_time.isoformat()})
            if signal.spot_reference_price != bar.close or signal.features_snapshot.get("entry_reference_spot") != bar.close:
                reference_anomalies.append({"signal_id": signal_id, "signal_reference": signal.spot_reference_price, "entry_reference": signal.features_snapshot.get("entry_reference_spot"), "bar_close": bar.close})
            if box_time == bar.end_time.isoformat():
                box_lock_breakout_anomalies.append({"signal_id": signal_id, "bar": bar.end_time.isoformat()})
            local_signal_time = to_ist(signal.timestamp)
            if local_signal_time.time() < time(9, 25) or local_signal_time.time() > time(14, 45):
                cutoff_anomalies.append({"signal_id": signal_id, "ist_time": local_signal_time.isoformat()})
            if not features.breakout_data_ready or not running_futures or running_futures[-1].end_time != bar.end_time:
                futures_readiness_anomalies.append({"signal_id": signal_id, "bar": bar.end_time.isoformat(), "breakout_data_ready": features.breakout_data_ready, "futures_latest": running_futures[-1].end_time.isoformat() if running_futures else None})
            record = {
                "signal_id": signal_id,
                "trading_date": session,
                "week": f"{local_signal_time.isocalendar().year}-W{local_signal_time.isocalendar().week:02d}",
                "timestamp": signal.timestamp.isoformat(),
                "timestamp_ist": local_signal_time.isoformat(),
                "direction": signal.direction.value,
                "option_type": signal.option_type.value,
                "spot_reference_price": signal.spot_reference_price,
                "breakout_candle": {"start_time": bar.start_time.isoformat(), "end_time": bar.end_time.isoformat(), "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close},
                "box_created_time": box_time,
                "entry_reference_spot": signal.features_snapshot.get("entry_reference_spot"),
                "box_high": signal.features_snapshot.get("box_high"),
                "box_low": signal.features_snapshot.get("box_low"),
                "atr_at_lock": signal.features_snapshot.get("atr_at_lock"),
                "box_age_at_breakout": (diag_by_direction[signal.direction.value].phase_summary or {}).get("box_age_bars"),
                "confirmation_score_effective": signal.features_snapshot.get("confirmation_score"),
                "confirmation_factors": signal.features_snapshot.get("confirmation_factors"),
                "oi_wall_detected": signal.features_snapshot.get("oi_wall_detected"),
                "breakout_extension_atr": signal.features_snapshot.get("breakout_extension_atr"),
                "structural_stop": signal.structural_stop,
                "initial_r_points": signal.r_points,
                "historical_source": "BREEZE",
                "selected_futures_contract": contract,
                "option_chain_available": False,
            }
            signal_records.append(record)
            signals_by_date[session] += 1
            signals_by_direction[record["direction"]] += 1
            signals_by_bucket[bucket(signal.timestamp)] += 1
            signals_by_week[record["week"]] += 1
            age = record["box_age_at_breakout"]
            if age is not None and int(age) > R1_PARAMETERS["max_age_bars"]:
                expired_box_signal_anomalies.append({"signal_id": signal_id, "box_age_at_breakout": age})
            if age is not None:
                signals_by_age[str(age)] += 1
            signals_by_score[str(record["confirmation_score_effective"])] += 1
            extension = float(record["breakout_extension_atr"] or 0)
            extension_band = "<0.25"
            if extension >= 0.75:
                extension_band = ">=0.75"
            elif extension >= 0.50:
                extension_band = "0.50-<0.75"
            elif extension >= 0.25:
                extension_band = "0.25-<0.50"
            signals_by_extension[extension_band] += 1

    if funnel["completed_5m_bars_processed"] == 0:
        raise SystemExit("No bars were processed")

    all_quality = {
        "spot": {
            "sessions": len(all_dates),
            "complete_sessions": sum(spot_quality[d]["complete"] for d in all_dates),
            "partial_sessions": sum(not spot_quality[d]["complete"] for d in all_dates),
            "quality_by_session": spot_quality,
        },
        "selected_futures": {
            "sessions_with_coverage": len(paired_coverage),
            "complete_sessions": sum(futures_quality[d]["complete"] for d in all_dates),
            "partial_sessions": sum(not futures_quality[d]["complete"] for d in all_dates if futures_sessions[d]),
            "quality_by_session": futures_quality,
        },
        "usable_paired_sessions": len(usable_sessions),
        "sessions_excluded": [
            {
                "date": session,
                "spot": spot_quality[session],
                "futures": futures_quality[session],
                "selected_futures_contract": selected_contracts[session],
                "reason": "not complete paired BREEZE spot/futures coverage or timestamp mismatch",
            }
            for session in all_dates if session not in usable_sessions
        ],
    }

    major_gaps = []
    for session in all_dates:
        for role, item in (("spot", spot_quality[session]), ("selected_futures", futures_quality[session])):
            if item["missing_slots_ist"] or item["extra_slots_ist"] or item["malformed_count"]:
                major_gaps.append({"date": session, "role": role, "missing": len(item["missing_slots_ist"]), "extra": len(item["extra_slots_ist"]), "malformed": item["malformed_count"], "missing_slots_ist": item["missing_slots_ist"][:12]})
    major_gaps.sort(key=lambda item: (-item["missing"] - item["extra"] - item["malformed"], item["date"], item["role"]))

    used_spot_rows = list(used_spot.values())
    used_futures_rows = list(used_futures.values())
    used_contracts = sorted({(item["instrument_id"], item["expiry"]) for item in selected_contracts.values() if item})
    source_diagnostics = {
        "spot": {"requested_source": "BREEZE", "selected_count": len(used_spot_rows), "selected_after_filter": {"BREEZE": len(used_spot_rows)}, "missing_selected_source": False},
        "futures": {"requested_source": "BREEZE", "selected_count": len(used_futures_rows), "selected_after_filter": {"BREEZE": len(used_futures_rows)}, "missing_selected_source": False},
    }
    data_fp = build_data_fingerprint(
        source=HistoricalReplaySource.BREEZE,
        start_date=usable_sessions[0],
        end_date=usable_sessions[-1],
        spot_candles=used_spot_rows,
        futures_candles=used_futures_rows,
        source_diagnostics=source_diagnostics,
        futures_contracts=[{"instrument_id": instrument_id, "expiry": expiry} for instrument_id, expiry in used_contracts],
        missing_data=[],
    )
    config_payload = {
        "analysis": "strategy_b_signal_level_historical_baseline",
        "strategy_revision": "Strategy B R1 completed-bar signal engine",
        "parameters": R1_PARAMETERS,
        "historical_source": "BREEZE",
        "session_boundaries_ist": {"start": "09:15", "last_start": "15:30", "expected_candles": EXPECTED_CANDLES},
        "futures_selection_rule": "earliest NIFTY FUTURES contract with expiry >= session date",
        "option_chain": "not available; passed as None to production FeatureEngine",
    }
    git = git_info()
    report = {
        "report_type": "SIGNAL_LEVEL_HISTORICAL_BASELINE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git": git,
        "strategy_revision": "Strategy B R1 completed-bar signal engine",
        "configuration_fingerprint": configuration_fingerprint(config_payload),
        "configuration": config_payload,
        "historical_source": "BREEZE",
        "requested_date_range": {"start": all_dates[0], "end": all_dates[-1]},
        "actual_date_range_used": {"start": usable_sessions[0], "end": usable_sessions[-1]},
        "data_window": {
            "earliest_available_nifty_spot_5m_date": all_dates[0],
            "latest_available_nifty_spot_5m_date": all_dates[-1],
            "distinct_spot_sessions": len(all_dates),
            "spot_5m_candle_count_all_breeze": len(spot_all),
            "futures_5m_candle_count_all_breeze": len(futures_all),
            "selected_futures_5m_candle_count_used_with_warmup": len(used_futures_rows),
            "sessions_with_both_spot_and_selected_futures_coverage": len(paired_coverage),
            "expected_candles_per_complete_session": EXPECTED_CANDLES,
            "data_quality": all_quality,
            "major_gaps_or_malformed_sessions": major_gaps[:25],
        },
        "funnel": {
            "sessions_processed": len(usable_sessions),
            "completed_5m_bars_processed": funnel["completed_5m_bars_processed"],
            "compression_candidates": funnel["compression_candidates"],
            "valid_boxes_locked": funnel["valid_boxes_locked"],
            "boxes_expired": funnel["boxes_expired"],
            "boxes_structurally_invalidated": funnel["boxes_structurally_invalidated"],
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
        "rejection_attribution": {
            "top_rejection_reasons": dict(rejection_reasons.most_common()),
            "counting_rule": "unique primary blocker per direction diagnostic per completed candle; descriptive only",
        },
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
            "bars_from_box_lock_to_breakout_attempt": {"average": round(mean(attempt_ages), 4) if attempt_ages else None, "median": median(attempt_ages) if attempt_ages else None},
            "box_height_at_lock_atr": round(mean(box_heights), 4) if box_heights else None,
            "bb_percentile_at_lock": round(mean(box_bb_percentiles), 4) if box_bb_percentiles else None,
            "breakout_extension_atr_attempts": round(mean(attempt_extensions), 4) if attempt_extensions else None,
            "raw_confirmation_score": None,
            "effective_confirmation_score_attempts": round(mean(effective_scores), 4) if effective_scores else None,
            "raw_confirmation_score_note": "Production diagnostics expose effective passed factors, not pre-OI-wall raw score; no raw score was reconstructed.",
        },
        "option_chain_and_futures_limitations": {
            "option_chain_observations": option_chain_observations,
            "option_chain_missing_observations": option_chain_missing_observations,
            "option_chain_available_historically": False,
            "handling": "option_chain=None was passed to production FeatureEngine; OI-wall penalty count is therefore zero, while futures-based and candle-based confirmations continued to be evaluated by production code.",
            "futures_contract_selection": "point-in-time earliest NIFTY futures expiry on or after session date",
        },
        "correctness_checks": {
            "duplicate_signal_ids": duplicate_signal_ids,
            "multiple_signals_from_one_consumed_box": duplicate_consumed_boxes,
            "signal_timestamp_mismatches": timestamp_anomalies,
            "signal_reference_mismatches": reference_anomalies,
            "box_lock_candle_breakouts": box_lock_breakout_anomalies,
            "outside_entry_window_signals": cutoff_anomalies,
            "expired_box_signals": expired_box_signal_anomalies,
            "missing_futures_readiness_signals": futures_readiness_anomalies,
            "all_clear": not any((duplicate_signal_ids, duplicate_consumed_boxes, timestamp_anomalies, reference_anomalies, box_lock_breakout_anomalies, cutoff_anomalies, expired_box_signal_anomalies, futures_readiness_anomalies)),
        },
        "replay_performance_metrics": {
            "status": "NOT_REPORTED",
            "reason": "Strategy B is not yet verified through the Strategy-B-aware replay manifest/lifecycle path; no fills, exits, PnL, R, win rate, drawdown, or option performance were fabricated.",
        },
        "data_fingerprint": data_fp.model_dump(mode="json"),
        "signal_records": signal_records,
    }
    artifact = choose_artifact(usable_sessions[0], usable_sessions[-1])
    artifact.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "artifact": str(artifact),
        "actual_date_range_used": report["actual_date_range_used"],
        "usable_sessions": len(usable_sessions),
        "funnel": report["funnel"],
        "correctness_all_clear": report["correctness_checks"]["all_clear"],
        "configuration_fingerprint": report["configuration_fingerprint"],
        "data_fingerprint": report["data_fingerprint"]["dataset_hash"],
    }, indent=2))


if __name__ == "__main__":
    main()
