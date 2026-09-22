"""Narrow read-only validation for the Strategy A V3 momentum hypothesis.

This pass fixes an exploratory-timing limitation: momentum deltas are computed
from the actual completed 15m futures bars immediately preceding each decision,
including 09:15/09:30 context for a 09:45 setup. It does not rely on timing
rows beginning at the entry window.

No broker calls, market-data writes, production configuration changes, or trade
persistence occur here.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from services.historical.strategy_a_data_audit import ENTRY_FIRST_END, ENTRY_LAST_END, IST, _default_db_path, _open_read_only
from services.historical.strategy_a_research import (
    _base_row,
    _feature_map,
    _load_canonical_stream,
    _session_dates,
    _summarize_rows,
    _trigger_label,
)
from services.strategy.models import StrategyDirection, StrategyTunablesConfig


REQUIRED = (
    "ema_order", "di_direction", "adx", "ema_separation",
    "confirmation_direction", "confirmation_body",
    "confirmation_close_location", "confirmation_range",
    "sr_present", "sr_touch", "ema_or_vwap_near",
    "minimum_stop_distance", "maximum_stop_distance", "opposing_sr_room",
)


def _all_except(row: dict[str, Any], ignored: set[str]) -> bool:
    c = row.get("baseline_components") or {}
    return all(bool(c.get(key)) for key in REQUIRED if key not in ignored)


def _momentum_context(
    current: Any,
    previous: Any,
    previous2: Any,
    direction: StrategyDirection,
) -> dict[str, float | None]:
    if previous is None or previous2 is None or current.atr14 <= 0:
        return {"adx_delta_2bars": None, "ema20_directional_slope_atr_1bar": None}
    slope = (current.ema20 - previous.ema20) / current.atr14
    if direction is StrategyDirection.PUT:
        slope = -slope
    return {
        "adx_delta_2bars": current.adx14 - previous2.adx14,
        "ema20_directional_slope_atr_1bar": slope,
    }


def _minute(row: dict[str, Any]) -> int:
    value = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).astimezone(IST)
    return value.hour * 60 + value.minute


def _variant_pass(row: dict[str, Any], name: str) -> bool:
    if name == "baseline_v2":
        return bool(row.get("baseline_pass"))
    if not _all_except(row, {"adx"}):
        return False
    if name == "without_adx_floor":
        return True

    ctx = row.get("momentum_context") or {}
    decay = ctx.get("adx_delta_2bars")
    slope = ctx.get("ema20_directional_slope_atr_1bar")
    if decay is None or slope is None:
        return False

    if name == "guard_d2_m2_slope_0_015":
        return decay >= -2.0 and 0.0 <= slope < 0.15
    if name == "guard_d2_m25_slope_0_015":
        return decay >= -2.5 and 0.0 <= slope < 0.15
    if name == "guard_d2_m2_slope_0025_015":
        return decay >= -2.0 and 0.025 <= slope < 0.15
    if name == "guard_d2_m2_slope_0_020":
        return decay >= -2.0 and 0.0 <= slope < 0.20
    if name == "guard_d2_m2_slope_0_015_start_1000":
        return decay >= -2.0 and 0.0 <= slope < 0.15 and _minute(row) >= 10 * 60
    if name == "guard_d2_m2_slope_0_015_start_1015":
        return decay >= -2.0 and 0.0 <= slope < 0.15 and _minute(row) >= 10 * 60 + 15
    raise ValueError(f"unknown variant: {name}")


VARIANTS = (
    "baseline_v2",
    "without_adx_floor",
    "guard_d2_m2_slope_0_015",
    "guard_d2_m25_slope_0_015",
    "guard_d2_m2_slope_0025_015",
    "guard_d2_m2_slope_0_020",
    "guard_d2_m2_slope_0_015_start_1000",
    "guard_d2_m2_slope_0_015_start_1015",
)


def build_validation(
    db_path: Path,
    *,
    sessions: int = 0,
    source: str = "BREEZE",
) -> dict[str, Any]:
    config = StrategyTunablesConfig()
    conn = _open_read_only(db_path)
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    try:
        dates = _session_dates(conn, sessions=sessions, source=source)
        for day in dates:
            stream = _load_canonical_stream(conn, day, source=source)
            if not stream:
                skipped.append({"date": day.isoformat(), "reason": "NO_CANONICAL_STREAM"})
                continue
            features = _feature_map(stream)
            index_by_end = {bar.end_time: i for i, bar in enumerate(stream)}
            session_bars = [bar for bar in stream if bar.end_time.astimezone(IST).date() == day]
            decision_bars = []
            for bar in session_bars:
                local = bar.end_time.astimezone(IST)
                minute = local.hour * 60 + local.minute
                first = ENTRY_FIRST_END.hour * 60 + ENTRY_FIRST_END.minute
                last = ENTRY_LAST_END.hour * 60 + ENTRY_LAST_END.minute
                if first <= minute <= last and minute % 15 == 0:
                    decision_bars.append(bar)
            if len(decision_bars) != 21:
                skipped.append({"date": day.isoformat(), "reason": f"INCOMPLETE_DECISION_WINDOW:{len(decision_bars)}/21"})
                continue

            for bar in decision_bars:
                idx = index_by_end[bar.end_time]
                current = features[bar.end_time]
                previous = features.get(stream[idx - 1].end_time) if idx >= 1 else None
                previous2 = features.get(stream[idx - 2].end_time) if idx >= 2 else None
                future = [item for item in session_bars if item.end_time > bar.end_time]
                for direction in (StrategyDirection.CALL, StrategyDirection.PUT):
                    row = _base_row(day, current, direction, config)
                    row["momentum_context"] = _momentum_context(current, previous, previous2, direction)
                    row["label"] = _trigger_label(
                        row=row,
                        future_bars=future,
                        future_features=features,
                        config=config,
                    )
                    rows.append(row)
    finally:
        conn.close()

    rows.sort(key=lambda r: (r["timestamp"], r["direction"]))
    dates = sorted({r["date"] for r in rows})

    # Six chronological 40-session test blocks after 120-session training,
    # matching the earlier V3 walk-forward structure when enough dates exist.
    folds: list[list[str]] = []
    cursor = 0
    while cursor + 160 <= len(dates):
        folds.append(dates[cursor + 120: cursor + 160])
        cursor += 40

    variants: dict[str, Any] = {}
    for name in VARIANTS:
        selected = [r for r in rows if _variant_pass(r, name)]
        variants[name] = {
            "all_sessions": _summarize_rows(selected),
            "folds": [
                {
                    "fold": i + 1,
                    "metrics": _summarize_rows([
                        r for r in rows
                        if r["date"] in set(test_dates) and _variant_pass(r, name)
                    ]),
                }
                for i, test_dates in enumerate(folds)
            ],
            "triggered_dates": sorted({
                r["date"] for r in selected
                if (r.get("label") or {}).get("trigger_status") == "TRIGGERED"
            }),
        }

    return {
        "research_type": "STRATEGY_A_V3_MOMENTUM_CONTEXT_VALIDATION",
        "source": source.upper(),
        "sessions_found": len(dates),
        "directional_rows": len(rows),
        "momentum_context": (
            "ADX 2-bar delta and EMA20 directional 1-bar slope are computed "
            "from the actual preceding completed 15m futures bars, including "
            "pre-entry-window bars such as 09:15/09:30."
        ),
        "variants": variants,
        "skipped_sessions": skipped,
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Strategy A V3 momentum guards with true pre-entry context")
    parser.add_argument("--db-path", type=Path, default=_default_db_path())
    parser.add_argument("--sessions", type=int, default=0, help="0 means all cached sessions")
    parser.add_argument("--source", choices=("BREEZE", "KITE", "LIVE", "MIXED"), default="BREEZE")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_a_research" / "strategy_a_v3_momentum_validation.json",
    )
    args = parser.parse_args()
    report = build_validation(args.db_path, sessions=args.sessions, source=args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    compact = {
        "research_type": report["research_type"],
        "sessions_found": report["sessions_found"],
        "directional_rows": report["directional_rows"],
        "variants": {
            name: payload["all_sessions"]
            for name, payload in report["variants"].items()
        },
        "output": str(args.output),
    }
    print(json.dumps(compact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
