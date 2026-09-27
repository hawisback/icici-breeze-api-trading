"""Reproducible regime research for frozen independent-market hypotheses.

This module intentionally uses only the standard library so it can run beside the
historical data collector without adding an analysis dependency. It reproduces
the original IR discovery event semantics, including resetting prior-condition
state when executable eligibility begins.

Example:
    python -m services.historical.independent_regime_research \
      --dataset B2=data/independent_market_research_blind_02.json \
      --dataset B1=data/independent_market_research_blind_01.json \
      --dataset DISC=data/independent_market_research_10_sessions.json \
      --output data/independent_regime_research.json
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from statistics import median
from typing import Any, Callable

ENTRY_START = time(9, 30)
ENTRY_END = time(14, 45)
ATR_WINDOW = 14
ATR_MIN_PERIODS = 8
MIN_SIGNAL_GAP_BARS = 6
MAX_HOLD_BARS = 6


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rolling_mean(values: list[float], index: int, window: int, min_periods: int) -> float | None:
    start = max(0, index - window + 1)
    sample = values[start : index + 1]
    return sum(sample) / len(sample) if len(sample) >= min_periods else None


def build_feature_sessions(canonical_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in canonical_rows:
        if raw.get("futures_close") is None:
            continue
        ts = datetime.fromisoformat(raw["timestamp"])
        row = dict(raw)
        row["_dt"] = ts
        row["_date"] = ts.date().isoformat()
        sessions[row["_date"]].append(row)

    result: dict[str, list[dict[str, Any]]] = {}
    for day, rows in sorted(sessions.items()):
        rows.sort(key=lambda row: row["_dt"])
        if len(rows) != 75:
            continue

        tr_values: list[float] = []
        cumulative_pv = 0.0
        cumulative_volume = 0.0
        or15_low = min(float(row["futures_low"]) for row in rows[:3])
        or15_high = max(float(row["futures_high"]) for row in rows[:3])

        enriched: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            high = float(row["futures_high"])
            low = float(row["futures_low"])
            close = float(row["futures_close"])
            prev_close = float(rows[index - 1]["futures_close"]) if index else None
            tr = high - low
            if prev_close is not None:
                tr = max(tr, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)
            atr14 = _rolling_mean(tr_values, index, ATR_WINDOW, ATR_MIN_PERIODS)

            volume = float(row.get("futures_volume") or 0.0)
            typical = (high + low + close) / 3.0
            cumulative_pv += typical * volume
            cumulative_volume += volume
            vwap = cumulative_pv / cumulative_volume if cumulative_volume else None

            feature = dict(row)
            feature.update(
                {
                    "_index": index,
                    "tr": tr,
                    "atr14": atr14,
                    "mom3": (
                        close - float(rows[index - 3]["futures_close"])
                        if index >= 3
                        else None
                    ),
                    "oi3": (
                        float(row["futures_open_interest"])
                        - float(rows[index - 3]["futures_open_interest"])
                        if index >= 3
                        and row.get("futures_open_interest") is not None
                        and rows[index - 3].get("futures_open_interest") is not None
                        else None
                    ),
                    "vwap": vwap,
                    "or15_low": or15_low,
                    "or15_high": or15_high,
                }
            )
            enriched.append(feature)
        result[day] = enriched
    return result


def _condition(name: str, row: dict[str, Any]) -> bool:
    mom3 = row.get("mom3")
    oi3 = row.get("oi3")
    atr = row.get("atr14")
    close = float(row["futures_close"])

    if name == "H1":
        return mom3 is not None and oi3 is not None and mom3 < 0 and oi3 < 0
    if name == "H2":
        return (
            mom3 is not None
            and oi3 is not None
            and row.get("vwap") is not None
            and mom3 < 0
            and oi3 < 0
            and close > float(row["vwap"])
        )
    if name == "H3":
        return mom3 is not None and atr not in (None, 0) and mom3 / float(atr) >= 0.5
    if name == "H4":
        return mom3 is not None and mom3 < 0 and close < float(row["or15_low"])
    raise ValueError(f"Unknown hypothesis: {name}")


def _simulate_short(rows: list[dict[str, Any]], signal_index: int) -> dict[str, Any] | None:
    if signal_index + MAX_HOLD_BARS >= len(rows):
        return None
    signal = rows[signal_index]
    atr = signal.get("atr14")
    if atr in (None, 0):
        return None
    atr = float(atr)
    entry = float(rows[signal_index + 1]["futures_open"])
    stop = entry + atr
    target = entry - atr

    for index in range(signal_index + 1, signal_index + MAX_HOLD_BARS + 1):
        high = float(rows[index]["futures_high"])
        low = float(rows[index]["futures_low"])
        stop_hit = high >= stop
        target_hit = low <= target
        if stop_hit and target_hit:
            return {"ambiguous": True}
        if stop_hit:
            return {
                "ambiguous": False,
                "r": -1.0,
                "exit_reason": "STOP",
                "entry": entry,
                "exit": stop,
            }
        if target_hit:
            return {
                "ambiguous": False,
                "r": 1.0,
                "exit_reason": "TARGET",
                "entry": entry,
                "exit": target,
            }

    exit_price = float(rows[signal_index + MAX_HOLD_BARS]["futures_close"])
    return {
        "ambiguous": False,
        "r": (entry - exit_price) / atr,
        "exit_reason": "TIME",
        "entry": entry,
        "exit": exit_price,
    }


def extract_events(
    sessions: dict[str, list[dict[str, Any]]],
    hypothesis: str,
) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    ambiguous = 0

    for day, rows in sorted(sessions.items()):
        previous_condition = False
        last_signal_index = -10_000

        for index, row in enumerate(rows):
            ts: datetime = row["_dt"]
            executable = (
                ENTRY_START <= ts.time() <= ENTRY_END
                and row.get("atr14") is not None
                and index + MAX_HOLD_BARS < len(rows)
            )
            if not executable:
                # This reset is deliberate and reproduces the original discovery
                # manifest's 09:50 eligibility boundary.
                previous_condition = False
                continue

            condition = _condition(hypothesis, row)
            transitioned = condition and not previous_condition
            if transitioned and index - last_signal_index >= MIN_SIGNAL_GAP_BARS:
                simulation = _simulate_short(rows, index)
                if simulation and simulation.get("ambiguous"):
                    ambiguous += 1
                elif simulation:
                    atr = float(row["atr14"])
                    future_close = float(rows[index + MAX_HOLD_BARS]["futures_close"])
                    event = {
                        "hypothesis": hypothesis,
                        "date": day,
                        "timestamp": row["timestamp"],
                        "signal_index": index,
                        "futures_close": float(row["futures_close"]),
                        "atr14": atr,
                        "mom3": row.get("mom3"),
                        "oi3": row.get("oi3"),
                        "vwap": row.get("vwap"),
                        "or15_low": row["or15_low"],
                        "or15_high": row["or15_high"],
                        "or_state": (
                            "BELOW"
                            if float(row["futures_close"]) < float(row["or15_low"])
                            else (
                                "ABOVE"
                                if float(row["futures_close"]) > float(row["or15_high"])
                                else "INSIDE"
                            )
                        ),
                        "directional_30m_atr": (
                            float(row["futures_close"]) - future_close
                        )
                        / atr,
                        **simulation,
                    }
                    events.append(event)
                    last_signal_index = index
            previous_condition = condition

    return events, ambiguous


def _max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return drawdown


def metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: event["timestamp"])
    values = [float(event["r"]) for event in ordered]
    winners = [value for value in values if value > 0]
    losers = [value for value in values if value < 0]
    directional = [float(event["directional_30m_atr"]) for event in ordered]
    return {
        "episodes": len(ordered),
        "active_days": len({event["date"] for event in ordered}),
        "total_r": sum(values),
        "mean_r": _mean(values),
        "win_rate": (len(winners) / len(values)) if values else None,
        "profit_factor": (
            sum(winners) / abs(sum(losers)) if losers else (math.inf if winners else None)
        ),
        "max_drawdown_r": _max_drawdown(values),
        "mean_directional_30m_atr": _mean(directional),
        "median_directional_30m_atr": median(directional) if directional else None,
    }


def analyze_dataset(path: str | Path, label: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    sessions = build_feature_sessions(payload["canonical_market_rows"])

    report: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "session_dates": sorted(sessions),
        "hypotheses": {},
    }
    extracted: dict[str, list[dict[str, Any]]] = {}
    for hypothesis in ("H1", "H2", "H3", "H4"):
        events, ambiguous = extract_events(sessions, hypothesis)
        extracted[hypothesis] = events
        report["hypotheses"][hypothesis] = {
            "metrics": metrics(events),
            "ambiguous": ambiguous,
        }

    h1 = extracted["H1"]
    outside = [event for event in h1 if event["or_state"] != "INSIDE"]
    below = [event for event in h1 if event["or_state"] == "BELOW"]
    inside = [event for event in h1 if event["or_state"] == "INSIDE"]
    report["h1_regimes"] = {
        "OUTSIDE_OR15": metrics(outside),
        "BELOW_OR15": metrics(below),
        "INSIDE_OR15": metrics(inside),
    }
    return report


def combine_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    # Re-read event-level data so aggregate metrics use chronological R order.
    # Dataset-level reports intentionally remain compact.
    return {
        "datasets": reports,
        "frozen_next_candidates": {
            "IR-H5-DOWN-OI-OUTSIDE-OR15": (
                "H1 false->true event gated by signal-bar close outside OR15"
            ),
            "IR-H6-DOWN-OI-BELOW-OR15": (
                "H1 false->true event gated by signal-bar close below OR15 low"
            ),
        },
        "note": (
            "All supplied datasets are development data after inspection. Use a "
            "fully unseen older block for the next validation."
        ),
    }


def _parse_dataset(value: str) -> tuple[str, str]:
    if "=" not in value:
        path = value
        return Path(path).stem, path
    label, path = value.split("=", 1)
    if not label or not path:
        raise argparse.ArgumentTypeError("--dataset must be LABEL=PATH or PATH")
    return label, path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        action="append",
        required=True,
        help="Dataset as LABEL=PATH; repeat for multiple blocks.",
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    reports = []
    for value in args.dataset:
        label, path = _parse_dataset(value)
        reports.append(analyze_dataset(path, label))
    result = combine_reports(reports)

    rendered = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
