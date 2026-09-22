"""Strategy A swing-opportunity discovery over cached NIFTY futures.

This is a research-only pass designed for a higher-frequency companion to the
existing V3 trend-pullback strategy. It scans every completed 15-minute futures
bar in the entry window, measures future favorable/adverse excursion, and
compares a small set of interpretable swing structures.

The goal is not to maximize in-sample profit. The report builds a
frequency/edge frontier: trades per 10 usable sessions versus expectancy,
win-rate proxy, drawdown, and chronological fold stability.

No production configuration is changed and no broker is called.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable, Sequence

from services.historical.strategy_a_data_audit import ENTRY_FIRST_END, ENTRY_LAST_END, IST, _default_db_path, _open_read_only
from services.historical.strategy_a_research import _feature_map, _load_canonical_stream, _session_dates


@dataclass(frozen=True)
class SwingCandidate:
    name: str
    description: str
    predicate: Callable[[dict[str, Any]], bool]


def _minute(value: datetime) -> int:
    local = value.astimezone(IST)
    return local.hour * 60 + local.minute


def _in_window(value: datetime) -> bool:
    minute = _minute(value)
    start = ENTRY_FIRST_END.hour * 60 + ENTRY_FIRST_END.minute
    end = ENTRY_LAST_END.hour * 60 + ENTRY_LAST_END.minute
    return start <= minute <= end and minute % 15 == 0


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _row_features(current: Any, previous: Any | None, direction: str) -> dict[str, float | bool | str | None]:
    atr = float(current.atr14 or 0.0)
    sign = 1.0 if direction == "CALL" else -1.0
    range_points = max(0.0, float(current.high - current.low))
    body_signed = sign * float(current.close - current.open)
    ema20_slope = None
    adx_delta_1 = None
    if previous is not None and atr > 0:
        ema20_slope = sign * (float(current.ema20) - float(previous.ema20)) / atr
        adx_delta_1 = float(current.adx14) - float(previous.adx14)

    previous_close = float(previous.close) if previous is not None else None
    previous_high = float(previous.high) if previous is not None else None
    previous_low = float(previous.low) if previous is not None else None
    previous_ema20 = float(previous.ema20) if previous is not None else None
    previous_vwap = float(previous.session_vwap) if previous is not None else None

    if direction == "CALL":
        trend_order = current.ema20 > current.ema50
        di_direction = current.plus_di14 > current.minus_di14
        vwap_side = current.close >= current.session_vwap
        ema20_side = current.close >= current.ema20
        sr_level = current.support
        opposing = current.resistance
        di_spread = current.plus_di14 - current.minus_di14
        previous_ema_side = (
            previous_close is not None and previous_ema20 is not None
            and previous_close < previous_ema20
        )
        previous_vwap_side = (
            previous_close is not None and previous_vwap is not None
            and previous_close < previous_vwap
        )
        ema_reclaim_now = bool(previous_ema_side and current.close >= current.ema20)
        vwap_reclaim_now = bool(previous_vwap_side and current.close >= current.session_vwap)
        previous_bar_break = bool(previous_high is not None and current.close > previous_high)
        toward_vwap = bool(current.close < current.session_vwap and current.close > current.open)
    else:
        trend_order = current.ema20 < current.ema50
        di_direction = current.minus_di14 > current.plus_di14
        vwap_side = current.close <= current.session_vwap
        ema20_side = current.close <= current.ema20
        sr_level = current.resistance
        opposing = current.support
        di_spread = current.minus_di14 - current.plus_di14
        previous_ema_side = (
            previous_close is not None and previous_ema20 is not None
            and previous_close > previous_ema20
        )
        previous_vwap_side = (
            previous_close is not None and previous_vwap is not None
            and previous_close > previous_vwap
        )
        ema_reclaim_now = bool(previous_ema_side and current.close <= current.ema20)
        vwap_reclaim_now = bool(previous_vwap_side and current.close <= current.session_vwap)
        previous_bar_break = bool(previous_low is not None and current.close < previous_low)
        toward_vwap = bool(current.close > current.session_vwap and current.close < current.open)

    return {
        "direction": direction,
        "trend_order": bool(trend_order),
        "di_direction": bool(di_direction),
        "vwap_side": bool(vwap_side),
        "ema20_side": bool(ema20_side),
        "ema_reclaim": ema_reclaim_now,
        "vwap_reclaim": vwap_reclaim_now,
        "previous_bar_break": previous_bar_break,
        "toward_vwap": toward_vwap,
        "adx14": float(current.adx14),
        "adx_delta_1": adx_delta_1,
        "di_spread": float(di_spread),
        "ema20_slope_atr": ema20_slope,
        "ema_separation_atr": (
            abs(float(current.ema20) - float(current.ema50)) / atr
            if atr > 0 else None
        ),
        "close_to_ema20_atr": (
            abs(float(current.close) - float(current.ema20)) / atr
            if atr > 0 else None
        ),
        "close_to_vwap_atr": (
            abs(float(current.close) - float(current.session_vwap)) / atr
            if atr > 0 else None
        ),
        "body_ratio": _safe_div(abs(float(current.close - current.open)), range_points),
        "directional_body_atr": _safe_div(max(0.0, body_signed), atr),
        "range_atr": _safe_div(range_points, atr),
        "sr_distance_atr": (
            abs(float(current.close) - float(sr_level)) / atr
            if sr_level is not None and atr > 0 else None
        ),
        "opposing_room_atr": (
            sign * (float(opposing) - float(current.close)) / atr
            if opposing is not None and atr > 0 else None
        ),
        "close": float(current.close),
        "atr14": atr,
    }


def _path_label(
    *,
    current: Any,
    future: Sequence[Any],
    direction: str,
    horizon_bars: int,
) -> dict[str, Any]:
    atr = float(current.atr14 or 0.0)
    entry = float(current.close)
    selected = list(future[:horizon_bars])
    if atr <= 0 or len(selected) < horizon_bars:
        return {
            "horizon_bars": horizon_bars,
            "mfe_atr": None,
            "mae_atr": None,
            "close_return_atr": None,
            "target_060_before_stop_040": False,
            "target_075_before_stop_050": False,
            "target_100_before_stop_075": False,
        }

    if direction == "CALL":
        favorable = max(float(bar.high) - entry for bar in selected)
        adverse = max(entry - float(bar.low) for bar in selected)
        close_return = float(selected[-1].close) - entry
        target_hit = lambda bar, target: float(bar.high) >= entry + target * atr
        stop_hit = lambda bar, stop: float(bar.low) <= entry - stop * atr
    else:
        favorable = max(entry - float(bar.low) for bar in selected)
        adverse = max(float(bar.high) - entry for bar in selected)
        close_return = entry - float(selected[-1].close)
        target_hit = lambda bar, target: float(bar.low) <= entry - target * atr
        stop_hit = lambda bar, stop: float(bar.high) >= entry + stop * atr

    def target_before_stop(target: float, stop: float) -> bool:
        for bar in selected:
            hit_t = target_hit(bar, target)
            hit_s = stop_hit(bar, stop)
            if hit_t and hit_s:
                return False  # conservative same-bar ordering
            if hit_s:
                return False
            if hit_t:
                return True
        return False

    return {
        "horizon_bars": horizon_bars,
        "mfe_atr": round(favorable / atr, 6),
        "mae_atr": round(adverse / atr, 6),
        "close_return_atr": round(close_return / atr, 6),
        "target_060_before_stop_040": target_before_stop(0.60, 0.40),
        "target_075_before_stop_050": target_before_stop(0.75, 0.50),
        "target_100_before_stop_075": target_before_stop(1.00, 0.75),
    }


def _build_rows(db_path: Path, *, source: str, sessions: int) -> tuple[list[dict[str, Any]], list[str]]:
    conn = _open_read_only(db_path)
    rows: list[dict[str, Any]] = []
    usable_dates: list[str] = []
    try:
        dates = _session_dates(conn, sessions=sessions, source=source)
        for day in dates:
            stream = _load_canonical_stream(conn, day, source=source)
            if not stream:
                continue
            fmap = _feature_map(stream)
            session = [
                bar for bar in stream
                if bar.end_time.astimezone(IST).date() == day and _in_window(bar.end_time)
            ]
            if not session:
                continue
            usable_dates.append(day.isoformat())
            index_by_end = {bar.end_time: i for i, bar in enumerate(stream)}
            for bar in session:
                idx = index_by_end[bar.end_time]
                current = fmap[bar.end_time]
                previous = fmap.get(stream[idx - 1].end_time) if idx >= 1 else None
                forced_exit_minute = time(15, 15).hour * 60 + time(15, 15).minute
                future = [
                    fmap[item.end_time]
                    for item in stream[idx + 1:]
                    if item.end_time.astimezone(IST).date() == day
                    and _minute(item.end_time) <= forced_exit_minute
                ]
                for direction in ("CALL", "PUT"):
                    row = {
                        "date": day.isoformat(),
                        "timestamp": current.candle_timestamp.isoformat(),
                        "timestamp_ist": current.candle_timestamp.astimezone(IST).isoformat(),
                        **_row_features(current, previous, direction),
                        "labels": {
                            "30m": _path_label(current=current, future=future, direction=direction, horizon_bars=2),
                            "60m": _path_label(current=current, future=future, direction=direction, horizon_bars=4),
                            "90m": _path_label(current=current, future=future, direction=direction, horizon_bars=6),
                        },
                    }
                    rows.append(row)
    finally:
        conn.close()
    return rows, sorted(set(usable_dates))


def _num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if value is not None else None


def _candidates() -> list[SwingCandidate]:
    def trend(row: dict[str, Any]) -> bool:
        slope = _num(row, "ema20_slope_atr")
        sep = _num(row, "ema_separation_atr")
        return bool(
            row["trend_order"]
            and row["di_direction"]
            and slope is not None and slope > 0
            and sep is not None and sep >= 0.05
        )

    def trend_vwap(row: dict[str, Any]) -> bool:
        return trend(row) and bool(row["vwap_side"])

    def pullback_continue(row: dict[str, Any]) -> bool:
        ema_dist = _num(row, "close_to_ema20_atr")
        vwap_dist = _num(row, "close_to_vwap_atr")
        return bool(
            trend(row)
            and row["directional_body_atr"] >= 0
            and (
                (ema_dist is not None and ema_dist <= 0.40)
                or (vwap_dist is not None and vwap_dist <= 0.40)
            )
        )

    def momentum_continue(row: dict[str, Any]) -> bool:
        slope = _num(row, "ema20_slope_atr")
        return bool(
            trend_vwap(row)
            and slope is not None and 0.0 < slope < 0.25
            and row["body_ratio"] >= 0.30
            and row["directional_body_atr"] > 0
        )

    def strong_di(row: dict[str, Any]) -> bool:
        return bool(
            trend_vwap(row)
            and row["di_spread"] >= 5.0
            and row["body_ratio"] >= 0.25
        )

    def ema_reclaim(row: dict[str, Any]) -> bool:
        ema_dist = _num(row, "close_to_ema20_atr")
        return bool(
            trend(row)
            and row["ema20_side"]
            and ema_dist is not None and ema_dist <= 0.25
            and row["directional_body_atr"] > 0
        )

    def sr_bounce(row: dict[str, Any]) -> bool:
        sr_dist = _num(row, "sr_distance_atr")
        return bool(
            trend(row)
            and sr_dist is not None and sr_dist <= 0.35
            and row["directional_body_atr"] > 0
        )

    def ema_reclaim_cross(row: dict[str, Any]) -> bool:
        return bool(
            row["trend_order"]
            and row["ema_reclaim"]
            and row["directional_body_atr"] > 0
            and row["body_ratio"] >= 0.25
        )

    def vwap_reclaim_cross(row: dict[str, Any]) -> bool:
        return bool(
            row["vwap_reclaim"]
            and row["directional_body_atr"] > 0
            and row["body_ratio"] >= 0.25
            and row["di_spread"] > 0
        )

    def micro_breakout(row: dict[str, Any]) -> bool:
        slope = _num(row, "ema20_slope_atr")
        return bool(
            row["previous_bar_break"]
            and row["di_direction"]
            and slope is not None and slope > -0.05
            and row["directional_body_atr"] >= 0.15
        )

    def controlled_vwap_reversion(row: dict[str, Any]) -> bool:
        vwap_dist = _num(row, "close_to_vwap_atr")
        return bool(
            row["toward_vwap"]
            and vwap_dist is not None and 0.20 <= vwap_dist <= 0.80
            and row["body_ratio"] >= 0.30
            and row["adx14"] < 30.0
        )

    return [
        SwingCandidate("trend_core", "EMA20/50 + DI agreement + positive directional EMA20 slope.", trend),
        SwingCandidate("trend_vwap", "Trend core plus price on the directional side of session VWAP.", trend_vwap),
        SwingCandidate("pullback_continue", "Trend core with price within 0.40 ATR of EMA20 or VWAP.", pullback_continue),
        SwingCandidate("momentum_continue", "Trend/VWAP continuation with moderate slope and directional body.", momentum_continue),
        SwingCandidate("strong_di", "Trend/VWAP continuation with DI spread >= 5 and modest body.", strong_di),
        SwingCandidate("ema_reclaim", "Trend core, directional EMA20 side, within 0.25 ATR, directional body.", ema_reclaim),
        SwingCandidate("sr_bounce", "Trend core within 0.35 ATR of confirmed directional S/R with directional body.", sr_bounce),
        SwingCandidate("ema_reclaim_cross", "Directional EMA20 reclaim inside the broader EMA20/50 trend.", ema_reclaim_cross),
        SwingCandidate("vwap_reclaim_cross", "Directional session-VWAP reclaim with DI support and body confirmation.", vwap_reclaim_cross),
        SwingCandidate("micro_breakout", "Close beyond the prior 15m bar in the directional DI/slope context.", micro_breakout),
        SwingCandidate("controlled_vwap_reversion", "Counter-extension swing back toward VWAP when ADX is below 30.", controlled_vwap_reversion),
    ]


def _max_drawdown(values: Sequence[float]) -> float:
    equity = peak = 0.0
    dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return round(dd, 6)


def _score_row(row: dict[str, Any], *, horizon: str, target_key: str) -> float:
    """Return a conservative stop-normalized R proxy for one horizon.

    If target is hit before stop, record target/stop R. If neither barrier is
    established as a target-before-stop success, use the horizon close return
    normalized by the stop and clipped to [-1R, target_R]. This avoids treating
    a quiet late-horizon observation as a full stop while still preventing
    optimistic beyond-target credit.
    """
    label = row["labels"][horizon]
    target_stop = {
        "target_060_before_stop_040": (0.60, 0.40),
        "target_075_before_stop_050": (0.75, 0.50),
        "target_100_before_stop_075": (1.00, 0.75),
    }
    target, stop = target_stop[target_key]
    target_r = target / stop
    if bool(label[target_key]):
        return target_r
    close_return_atr = label.get("close_return_atr")
    if close_return_atr is None:
        raise ValueError("score requested without a complete horizon label")
    return max(-1.0, min(target_r, float(close_return_atr) / stop))


def _dedupe_daily(rows: Sequence[dict[str, Any]], *, max_per_day: int = 2) -> list[dict[str, Any]]:
    # Research execution proxy: chronological, at most max_per_day candidates,
    # and never both directions on the exact same completed bar.
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: (r["timestamp"], r["direction"])):
        day = str(row["date"])
        chosen = by_day[day]
        if len(chosen) >= max_per_day:
            continue
        if any(item["timestamp"] == row["timestamp"] for item in chosen):
            continue
        chosen.append(row)
    result: list[dict[str, Any]] = []
    for day in sorted(by_day):
        result.extend(by_day[day])
    return result


def _summarize(
    rows: Sequence[dict[str, Any]],
    *,
    usable_sessions: int,
    horizon: str,
    target_key: str,
) -> dict[str, Any]:
    complete_rows = [
        row for row in rows
        if (row.get("labels") or {}).get(horizon, {}).get("mfe_atr") is not None
    ]
    selected = _dedupe_daily(complete_rows)
    scores = [_score_row(row, horizon=horizon, target_key=target_key) for row in selected]
    wins = [value for value in scores if value > 0]
    months: dict[str, list[float]] = defaultdict(list)
    for row, score in zip(selected, scores):
        months[str(row["date"])[:7]].append(score)
    return {
        "trades": len(selected),
        "trades_per_10_sessions": round(len(selected) / usable_sessions * 10, 3) if usable_sessions else 0.0,
        "win_rate_pct": round(len(wins) / len(scores) * 100, 2) if scores else 0.0,
        "mean_r_proxy": round(mean(scores), 6) if scores else None,
        "sum_r_proxy": round(sum(scores), 6),
        "max_drawdown_r_proxy": _max_drawdown(scores),
        "median_mfe_atr": round(median([row["labels"][horizon]["mfe_atr"] for row in selected]), 6) if selected else None,
        "median_mae_atr": round(median([row["labels"][horizon]["mae_atr"] for row in selected]), 6) if selected else None,
        "profitable_months": sum(sum(values) > 0 for values in months.values()),
        "active_months": len(months),
        "direction_counts": {
            "CALL": sum(row["direction"] == "CALL" for row in selected),
            "PUT": sum(row["direction"] == "PUT" for row in selected),
        },
    }


def _folds(dates: Sequence[str], train: int = 120, test: int = 40) -> list[list[str]]:
    unique = sorted(set(dates))
    result: list[list[str]] = []
    cursor = 0
    while cursor + train + test <= len(unique):
        result.append(unique[cursor + train:cursor + train + test])
        cursor += test
    return result


def build_report(
    db_path: Path,
    *,
    source: str = "BREEZE",
    sessions: int = 0,
) -> dict[str, Any]:
    rows, dates = _build_rows(db_path, source=source, sessions=sessions)
    candidate_reports: dict[str, Any] = {}
    horizons = (
        ("30m", "target_060_before_stop_040"),
        ("60m", "target_075_before_stop_050"),
        ("90m", "target_100_before_stop_075"),
    )
    date_folds = _folds(dates)

    for candidate in _candidates():
        selected = [row for row in rows if candidate.predicate(row)]
        payload: dict[str, Any] = {
            "description": candidate.description,
            "raw_candidate_rows": len(selected),
            "profiles": {},
        }
        for horizon, target_key in horizons:
            profile = _summarize(
                selected,
                usable_sessions=len(dates),
                horizon=horizon,
                target_key=target_key,
            )
            fold_metrics = []
            for idx, fold_dates in enumerate(date_folds, start=1):
                fold_set = set(fold_dates)
                fold_rows = [row for row in selected if row["date"] in fold_set]
                metrics = _summarize(
                    fold_rows,
                    usable_sessions=len(fold_dates),
                    horizon=horizon,
                    target_key=target_key,
                )
                fold_metrics.append({"fold": idx, "metrics": metrics})
            profile["folds"] = fold_metrics
            profile["positive_active_folds"] = sum(
                (item["metrics"].get("trades") or 0) > 0
                and float(item["metrics"].get("sum_r_proxy") or 0.0) > 0
                for item in fold_metrics
            )
            profile["active_folds"] = sum(
                (item["metrics"].get("trades") or 0) > 0
                for item in fold_metrics
            )
            payload["profiles"][f"{horizon}:{target_key}"] = profile
        candidate_reports[candidate.name] = payload

    frontier = []
    for name, payload in candidate_reports.items():
        for profile_name, metrics in payload["profiles"].items():
            frontier.append({
                "candidate": name,
                "profile": profile_name,
                "trades_per_10_sessions": metrics["trades_per_10_sessions"],
                "trades": metrics["trades"],
                "win_rate_pct": metrics["win_rate_pct"],
                "mean_r_proxy": metrics["mean_r_proxy"],
                "sum_r_proxy": metrics["sum_r_proxy"],
                "max_drawdown_r_proxy": metrics["max_drawdown_r_proxy"],
                "positive_active_folds": metrics["positive_active_folds"],
                "active_folds": metrics["active_folds"],
            })
    frontier.sort(key=lambda row: (row["trades_per_10_sessions"], row["mean_r_proxy"] or -999), reverse=True)

    return {
        "report_type": "STRATEGY_A_HIGHER_FREQUENCY_SWING_DISCOVERY",
        "source": source.upper(),
        "sessions_found": len(dates),
        "directional_rows": len(rows),
        "date_range": {"first": dates[0] if dates else None, "last": dates[-1] if dates else None},
        "research_objective": (
            "Find interpretable NIFTY-futures swing structures that trade materially more "
            "often than V3 while retaining positive expectancy across chronological folds."
        ),
        "execution_proxy": (
            "At most two candidate entries per session and at most one direction per completed "
            "15m bar. Each 30/60/90-minute label requires the full horizon before the 15:15 "
            "intraday force-exit boundary. Outcomes use conservative target-before-stop labels, "
            "not executable option P&L."
        ),
        "frequency_edge_frontier": frontier,
        "candidates": candidate_reports,
        "production_config_changed": False,
        "broker_called": False,
        "market_data_written": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover higher-frequency Strategy A swing structures")
    parser.add_argument("--db-path", type=Path, default=_default_db_path())
    parser.add_argument("--source", choices=("BREEZE", "KITE", "LIVE", "MIXED"), default="BREEZE")
    parser.add_argument("--sessions", type=int, default=0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_a_swing_discovery.json",
    )
    args = parser.parse_args()
    report = build_report(args.db_path, source=args.source, sessions=args.sessions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "report_type": report["report_type"],
        "sessions_found": report["sessions_found"],
        "directional_rows": report["directional_rows"],
        "top_frontier": report["frequency_edge_frontier"][:20],
        "output": str(args.output),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
