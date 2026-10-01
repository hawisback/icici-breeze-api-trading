"""Corrected Strategy F4 exploration using Relative Volatility Index (0..100)."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.strategy_f4_relative_volatility_index_protocol import (
    COST_MODEL,
    EXECUTION,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    RELATIVE_VOLATILITY_INDEX,
    ROBUSTNESS_SCREEN,
    SOURCE_COMPATIBILITY,
    STRATEGY_ID,
    STRATEGY_NAME,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F4_RELATIVE_VOLATILITY_INDEX10_EXPLORATION_BACKTEST_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=int(length), adjust=False).mean()


def _indicator_frames(
    option_rows: list[dict[str, Any]],
) -> dict[tuple[str, int, str], pd.DataFrame]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in option_rows:
        grouped[
            (str(row["expiry"]), int(row["strike"]), str(row["right"]))
        ].append(row)

    result: dict[tuple[str, int, str], pd.DataFrame] = {}
    std_len = int(RELATIVE_VOLATILITY_INDEX["stddev_length"])
    ema_len = int(RELATIVE_VOLATILITY_INDEX["directional_ema_length"])
    ddof = int(RELATIVE_VOLATILITY_INDEX["stddev_ddof"])

    for key, rows in grouped.items():
        frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        close = frame["close"].astype(float)

        fast = close.ewm(span=int(MACD["fast_length"]), adjust=False).mean()
        slow = close.ewm(span=int(MACD["slow_length"]), adjust=False).mean()
        frame["macd"] = fast - slow
        frame["macd_signal"] = frame["macd"].ewm(
            span=int(MACD["signal_length"]), adjust=False
        ).mean()
        frame["macd_prev"] = frame["macd"].shift(1)
        frame["macd_signal_prev"] = frame["macd_signal"].shift(1)

        stddev = close.rolling(std_len, min_periods=std_len).std(ddof=ddof)
        change = close.diff()

        # TradingView source-compatible Relative Volatility Index:
        # upper input = 0 when change <= 0, otherwise stddev
        # lower input = 0 when change > 0, otherwise stddev
        upper_input = stddev.where(change > 0.0, 0.0)
        lower_input = stddev.where(change <= 0.0, 0.0)

        upper = _ema(upper_input, ema_len)
        lower = _ema(lower_input, ema_len)
        denom = upper + lower
        neutral = float(RELATIVE_VOLATILITY_INDEX["zero_denominator_value"])
        rvi = 100.0 * upper / denom
        rvi = rvi.where(denom != 0.0, neutral)

        frame["relative_volatility_index"] = rvi
        result[key] = frame

    return result


def _signal_events(
    daily_contracts: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frames = _indicator_frames(option_rows)
    min_prior = int(MACD["minimum_prior_option_bars_before_session"])
    force_exit = time.fromisoformat(WINDOW["force_exit_time"])
    events: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []

    for selected in daily_contracts:
        day = date.fromisoformat(str(selected["date"]))
        month = str(selected.get("month") or day.strftime("%Y-%m"))

        for right in OPTION_SELECTION["rights"]:
            key = (
                str(selected["expiry"]),
                int(selected["strike"]),
                str(right),
            )
            frame = frames.get(key)
            if frame is None or frame.empty:
                insufficient.append({
                    "date": day.isoformat(),
                    "right": right,
                    "reason": "NO_OPTION_ROWS",
                })
                continue

            indices = list(frame.index[frame["timestamp"].dt.date == day])
            if not indices:
                insufficient.append({
                    "date": day.isoformat(),
                    "right": right,
                    "reason": "NO_SESSION_ROWS",
                })
                continue

            prior_bars = int(indices[0])
            if prior_bars < min_prior:
                insufficient.append({
                    "date": day.isoformat(),
                    "right": right,
                    "reason": "INSUFFICIENT_INDICATOR_WARMUP",
                    "prior_bars": prior_bars,
                    "required_prior_bars": min_prior,
                })
                continue

            for idx in indices:
                if idx <= 0:
                    continue
                row = frame.loc[idx]
                if (
                    pd.isna(row["macd_prev"])
                    or pd.isna(row["macd_signal_prev"])
                    or pd.isna(row["relative_volatility_index"])
                ):
                    continue

                bullish = (
                    float(row["macd_prev"]) <= float(row["macd_signal_prev"])
                    and float(row["macd"]) > float(row["macd_signal"])
                )
                bearish = (
                    float(row["macd_prev"]) >= float(row["macd_signal_prev"])
                    and float(row["macd"]) < float(row["macd_signal"])
                )
                if not (bullish or bearish):
                    continue

                bar_start = pd.Timestamp(row["timestamp"]).to_pydatetime()
                event_time = bar_start + timedelta(minutes=5)
                if event_time.time() > force_exit:
                    continue

                events.append({
                    "date": day.isoformat(),
                    "month": month,
                    "event_timestamp": event_time.isoformat(),
                    "bar_start": bar_start.isoformat(),
                    "expiry": key[0],
                    "strike": key[1],
                    "right": right,
                    "event": "BULLISH_CROSS" if bullish else "BEARISH_CROSS",
                    "option_close": float(row["close"]),
                    "macd": float(row["macd"]),
                    "macd_signal": float(row["macd_signal"]),
                    "relative_volatility_index": float(
                        row["relative_volatility_index"]
                    ),
                })

    events.sort(key=lambda x: (x["event_timestamp"], x["right"], x["event"]))
    return events, insufficient


def _candidate_name(threshold: int | None) -> str:
    return "BASELINE" if threshold is None else f"RVI_GE_{int(threshold)}"


def _eligible(event: dict[str, Any], threshold: int | None) -> bool:
    if event["event"] != "BULLISH_CROSS":
        return False
    if threshold is None:
        return True
    return float(event["relative_volatility_index"]) >= float(threshold)


def _trade_intents(
    daily_contracts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    threshold: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    force_exit = time.fromisoformat(WINDOW["force_exit_time"])
    candidate = _candidate_name(threshold)
    events_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_day[str(event["date"])].append(event)
    selected_by_day = {str(x["date"]): x for x in daily_contracts}

    intents: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for day, selected in sorted(selected_by_day.items()):
        current: dict[str, Any] | None = None
        day_events = events_by_day.get(day, [])
        timestamps = sorted({str(x["event_timestamp"]) for x in day_events})

        for ts in timestamps:
            event_dt = datetime.fromisoformat(ts)
            same = [x for x in day_events if x["event_timestamp"] == ts]

            if current is not None:
                exit_event = next(
                    (
                        x for x in same
                        if x["right"] == current["right"]
                        and x["event"] == "BEARISH_CROSS"
                    ),
                    None,
                )
                if exit_event is not None:
                    intents.append({
                        **current,
                        "exit_timestamp": ts,
                        "exit_reason": "HELD_OPTION_RAW_BEARISH_MACD_CROSS",
                    })
                    current = None

            if event_dt.time() >= force_exit or current is not None:
                continue

            eligible = [x for x in same if _eligible(x, threshold)]
            if len(eligible) > 1:
                ambiguous.append({
                    "candidate": candidate,
                    "date": day,
                    "event_timestamp": ts,
                    "reason": "SIMULTANEOUS_CE_PE_ELIGIBLE_ENTRY",
                    "count": len(eligible),
                })
                continue
            if len(eligible) != 1:
                continue

            entry = eligible[0]
            current = {
                "candidate": candidate,
                "threshold": threshold,
                "date": day,
                "month": str(
                    selected.get("month")
                    or date.fromisoformat(day).strftime("%Y-%m")
                ),
                "entry_timestamp": ts,
                "right": str(entry["right"]),
                "expiry": str(entry["expiry"]),
                "strike": int(entry["strike"]),
                "entry_option_signal_close": float(entry["option_close"]),
                "entry_macd": float(entry["macd"]),
                "entry_macd_signal": float(entry["macd_signal"]),
                "entry_relative_volatility_index": float(
                    entry["relative_volatility_index"]
                ),
                "daily_spot_0915_open": float(selected["spot_0915_open"]),
            }

        if current is not None:
            entry_dt = datetime.fromisoformat(str(current["entry_timestamp"]))
            force_dt = datetime.combine(
                entry_dt.date(), force_exit, tzinfo=entry_dt.tzinfo
            )
            intents.append({
                **current,
                "exit_timestamp": force_dt.isoformat(),
                "exit_reason": "FORCE_EXIT_15_20",
            })

    return intents, ambiguous


def _lookup(
    option_rows: list[dict[str, Any]],
) -> dict[tuple[str, str, int, str], dict[str, Any]]:
    return {
        (
            str(row["timestamp"]),
            str(row["expiry"]),
            int(row["strike"]),
            str(row["right"]),
        ): row
        for row in option_rows
    }


def _costs(entry_open: float, exit_open: float, slip: float) -> dict[str, float]:
    qty = int(OPTION_SELECTION["lot_size"])
    entry_fill = entry_open + slip
    exit_fill = max(0.0, exit_open - slip)
    buy = entry_fill * qty
    sell = exit_fill * qty
    turnover = buy + sell

    brokerage = 2.0 * float(COST_MODEL["brokerage_per_order_inr"])
    exchange = turnover * float(COST_MODEL["exchange_charge_rate"])
    stt = sell * float(COST_MODEL["stt_sell_rate"])
    sebi = turnover * float(COST_MODEL["sebi_charge_rate"])
    stamp = buy * float(COST_MODEL["stamp_buy_rate"])
    gst = (brokerage + exchange + sebi) * float(COST_MODEL["gst_rate"])
    explicit = brokerage + exchange + stt + sebi + stamp + gst
    gross = (exit_fill - entry_fill) * qty
    return {
        "gross_pnl_inr": round(gross, 2),
        "explicit_costs_inr": round(explicit, 2),
        "net_pnl_inr": round(gross - explicit, 2),
    }


def _score(
    intents: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lookup = _lookup(option_rows)
    slips = [
        float(x)
        for x in COST_MODEL["slippage_sensitivity_points_each_side"]
    ]
    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for n, intent in enumerate(intents, start=1):
        base = (
            str(intent["expiry"]),
            int(intent["strike"]),
            str(intent["right"]),
        )
        entry = lookup.get((str(intent["entry_timestamp"]), *base))
        exit_ = lookup.get((str(intent["exit_timestamp"]), *base))
        missing = []
        if entry is None:
            missing.append("ENTRY_OPTION_BAR")
        if exit_ is None:
            missing.append("EXIT_OPTION_BAR")
        if missing:
            skipped.append({
                "trade_number": n,
                **intent,
                "skip_reasons": missing,
            })
            continue

        entry_open = float(entry["open"])
        exit_open = float(exit_["open"])
        if not (
            math.isfinite(entry_open)
            and math.isfinite(exit_open)
            and entry_open > 0
            and exit_open >= 0
        ):
            skipped.append({
                "trade_number": n,
                **intent,
                "skip_reasons": ["INVALID_OPTION_OPEN"],
            })
            continue

        entry_dt = datetime.fromisoformat(str(intent["entry_timestamp"]))
        exit_dt = datetime.fromisoformat(str(intent["exit_timestamp"]))
        trades.append({
            "trade_number": n,
            **intent,
            "entry_option_open": entry_open,
            "exit_option_open": exit_open,
            "hold_minutes": int((exit_dt - entry_dt).total_seconds() // 60),
            "primary_cost_model": _costs(entry_open, exit_open, 0.0),
            "slippage_sensitivity": {
                f"{slip:.2f}": _costs(entry_open, exit_open, slip)
                for slip in slips
            },
        })
    return trades, skipped


def _pf(values: list[float]) -> float | None:
    pos = sum(x for x in values if x > 0)
    neg = -sum(x for x in values if x < 0)
    if neg == 0:
        return None if pos == 0 else float("inf")
    return pos / neg


def _summary(
    trades: list[dict[str, Any]],
    slip_key: str = "0.00",
) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl_inr": 0.0,
            "profit_factor": None,
            "win_rate_pct": None,
        }

    vals = [
        float(t["slippage_sensitivity"][slip_key]["net_pnl_inr"])
        for t in trades
    ]
    pf = _pf(vals)
    return {
        "trades": len(trades),
        "wins": sum(v > 0 for v in vals),
        "losses": sum(v < 0 for v in vals),
        "win_rate_pct": round(
            sum(v > 0 for v in vals) / len(vals) * 100.0, 2
        ),
        "net_pnl_inr": round(sum(vals), 2),
        "average_net_pnl_inr": round(sum(vals) / len(vals), 2),
        "median_net_pnl_inr": round(float(pd.Series(vals).median()), 2),
        "profit_factor": (
            None
            if pf is None
            else ("INF" if math.isinf(pf) else round(float(pf), 4))
        ),
    }


def _candidate_report(
    candidate: str,
    threshold: int | None,
    trades: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    ambiguous: list[dict[str, Any]],
) -> dict[str, Any]:
    by_month = {
        month: [t for t in trades if t["month"] == month]
        for month in WINDOW["months"]
    }
    return {
        "candidate": candidate,
        "threshold": threshold,
        "quality": {
            "intended_trades": len(trades) + len(skipped),
            "scorable_trades": len(trades),
            "skipped_trades": len(skipped),
            "ambiguous_entry_timestamps": len(ambiguous),
            "trade_price_coverage_pct": (
                round(
                    len(trades) / (len(trades) + len(skipped)) * 100.0,
                    2,
                )
                if trades or skipped
                else None
            ),
        },
        "pooled": {
            "0.00": _summary(trades, "0.00"),
            "0.50": _summary(trades, "0.50"),
            "1.00": _summary(trades, "1.00"),
        },
        "by_month": {
            month: {
                "0.00": _summary(month_trades, "0.00"),
                "0.50": _summary(month_trades, "0.50"),
                "1.00": _summary(month_trades, "1.00"),
            }
            for month, month_trades in by_month.items()
        },
        "by_month_side": {
            month: {
                right: {
                    "0.00": _summary(
                        [t for t in month_trades if t["right"] == right],
                        "0.00",
                    ),
                    "0.50": _summary(
                        [t for t in month_trades if t["right"] == right],
                        "0.50",
                    ),
                    "1.00": _summary(
                        [t for t in month_trades if t["right"] == right],
                        "1.00",
                    ),
                }
                for right in ("CE", "PE")
            }
            for month, month_trades in by_month.items()
        },
        "ce_0_00": _summary(
            [t for t in trades if t["right"] == "CE"], "0.00"
        ),
        "pe_0_00": _summary(
            [t for t in trades if t["right"] == "PE"], "0.00"
        ),
    }


def _screen(report: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    pooled = report["pooled"]
    by_month = report["by_month"]

    if int(pooled["0.00"]["trades"]) < int(
        ROBUSTNESS_SCREEN["minimum_trades_pooled"]
    ):
        reasons.append("pooled_trade_count_below_minimum")

    for month in WINDOW["months"]:
        m = by_month[month]
        if int(m["0.00"]["trades"]) < int(
            ROBUSTNESS_SCREEN["minimum_trades_each_month"]
        ):
            reasons.append(f"{month}_trade_count_below_minimum")
        if float(m["0.00"]["net_pnl_inr"]) <= 0:
            reasons.append(f"{month}_net_not_positive")
        pf = m["0.00"]["profit_factor"]
        if pf is None:
            reasons.append(f"{month}_profit_factor_unavailable")
        elif pf != "INF" and float(pf) <= 1.0:
            reasons.append(f"{month}_profit_factor_not_above_one")
        if float(m["0.50"]["net_pnl_inr"]) <= 0:
            reasons.append(f"{month}_half_point_net_not_positive")

    if float(pooled["1.00"]["net_pnl_inr"]) <= 0:
        reasons.append("pooled_one_point_net_not_positive")

    return {
        "passed": not reasons,
        "failures": reasons,
        "contract": ROBUSTNESS_SCREEN,
    }


def _stable_regions(
    threshold_reports: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    thresholds = [
        int(x) for x in RELATIVE_VOLATILITY_INDEX["thresholds"]
    ]
    passed = {
        t
        for t in thresholds
        if threshold_reports[f"RVI_GE_{t}"]["robustness_screen"]["passed"]
    }

    regions: list[dict[str, Any]] = []
    for left, right in zip(thresholds[:-1], thresholds[1:]):
        if left in passed and right in passed:
            regions.append({
                "lower": left,
                "upper": right,
                "step": right - left,
                "interpretation": (
                    "adjacent thresholds both passed the frozen robustness screen"
                ),
            })
    return regions


def backtest(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    source_protocol = str(payload.get("protocol_version"))
    if source_protocol not in SOURCE_COMPATIBILITY["accepted_raw_market_protocols"]:
        raise ValueError(
            f"unsupported raw market protocol for corrected F4: {source_protocol}"
        )
    if payload.get("strategy_outcomes_scored") is not False:
        raise ValueError("F4 raw market artifact must be outcome-unscored")

    daily_contracts = list(payload.get("daily_contracts") or [])
    option_rows = list(payload.get("option_rows") or [])
    events, insufficient = _signal_events(daily_contracts, option_rows)

    candidate_specs: list[tuple[str, int | None]] = [("BASELINE", None)]
    candidate_specs.extend(
        (f"RVI_GE_{int(t)}", int(t))
        for t in RELATIVE_VOLATILITY_INDEX["thresholds"]
    )

    reports: dict[str, Any] = {}
    for candidate, threshold in candidate_specs:
        intents, ambiguous = _trade_intents(
            daily_contracts, events, threshold
        )
        trades, skipped = _score(intents, option_rows)
        report = _candidate_report(
            candidate,
            threshold,
            trades,
            skipped,
            ambiguous,
        )
        report["robustness_screen"] = _screen(report)
        reports[candidate] = report

    threshold_reports = {
        name: report
        for name, report in reports.items()
        if name != "BASELINE"
    }
    passing_thresholds = [
        report["threshold"]
        for report in threshold_reports.values()
        if report["robustness_screen"]["passed"]
    ]
    stable_regions = _stable_regions(threshold_reports)

    bullish_entries = [e for e in events if e["event"] == "BULLISH_CROSS"]
    rvi_values = np.array(
        [float(e["relative_volatility_index"]) for e in bullish_entries],
        dtype=float,
    )

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "source_raw_market_protocol_version": source_protocol,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "research_only": True,
        "source_market_sha256": source_sha256,
        "window": WINDOW,
        "indicator": RELATIVE_VOLATILITY_INDEX,
        "quality": {
            "selected_sessions": len(daily_contracts),
            "selected_sessions_by_month": {
                month: sum(
                    str(
                        x.get("month")
                        or date.fromisoformat(str(x["date"])).strftime("%Y-%m")
                    ) == month
                    for x in daily_contracts
                )
                for month in WINDOW["months"]
            },
            "option_macd_cross_events": len(events),
            "raw_bullish_macd_entry_opportunities": len(bullish_entries),
            "insufficient_warmup_side_sessions": len(insufficient),
        },
        "entry_rvi_distribution": {
            "p10": round(float(np.quantile(rvi_values, 0.10)), 4),
            "p25": round(float(np.quantile(rvi_values, 0.25)), 4),
            "p50": round(float(np.quantile(rvi_values, 0.50)), 4),
            "p75": round(float(np.quantile(rvi_values, 0.75)), 4),
            "p90": round(float(np.quantile(rvi_values, 0.90)), 4),
        },
        "candidates": reports,
        "passing_thresholds": passing_thresholds,
        "stable_threshold_regions": stable_regions,
        "decision": (
            "F4_RELATIVE_VOLATILITY_INDEX_HAS_STABLE_THRESHOLD_REGION_REQUIRES_HOLDOUT"
            if stable_regions
            else (
                "F4_RELATIVE_VOLATILITY_INDEX_HAS_ISOLATED_PASS_REQUIRES_CAUTION"
                if passing_thresholds
                else "F4_RELATIVE_VOLATILITY_INDEX_NO_ROBUST_THRESHOLD_50_TO_80"
            )
        ),
        "insufficient_warmup": insufficient,
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Explore corrected Strategy F4 MACD + Relative Volatility Index(10)"
        )
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f4_relative_volatility_index10_exploration_2026_07_09.json"
        ),
    )
    args = parser.parse_args()

    source_sha = _sha256(args.market)
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    report = backtest(payload, source_sha256=source_sha)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()

    threshold_summary = {
        name: {
            "threshold": data["threshold"],
            "pooled": data["pooled"],
            "by_month": data["by_month"],
            "ce_0_00": data["ce_0_00"],
            "pe_0_00": data["pe_0_00"],
            "screen": data["robustness_screen"],
        }
        for name, data in report["candidates"].items()
    }

    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_market_sha256": source_sha,
        "source_raw_market_protocol_version": report[
            "source_raw_market_protocol_version"
        ],
        "quality": report["quality"],
        "entry_rvi_distribution": report["entry_rvi_distribution"],
        "candidate_summary": threshold_summary,
        "passing_thresholds": report["passing_thresholds"],
        "stable_threshold_regions": report["stable_threshold_regions"],
        "decision": report["decision"],
    }, indent=2))


if __name__ == "__main__":
    main()
