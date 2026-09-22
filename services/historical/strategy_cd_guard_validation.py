"""Second-stage guard validation for Strategy C/D discovery output.

Consumes the JSON emitted by strategy_cd_multitimeframe_discovery.py.  This
module does not resimulate candles or change production configuration.  It
tests a deliberately small, interpretable set of entry-time guards against the
already-recorded futures lifecycle outcomes.

Important: these guards are motivated by exploratory results from the same
historical sample.  Chronological folds measure stability, not pristine unseen
validation.  Paper/shadow and future holdout evidence remain required.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class GuardCandidate:
    name: str
    family: str
    description: str
    predicate: Callable[[dict[str, Any]], bool]


def _feature(row: dict[str, Any], key: str) -> float | None:
    value = (row.get("research_features") or {}).get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hour(row: dict[str, Any]) -> int:
    value = datetime.fromisoformat(str(row["entry_time_ist"]).replace("Z", "+00:00"))
    return value.hour


def _all(_: dict[str, Any]) -> bool:
    return True


def _fast_2m(row: dict[str, Any]) -> bool:
    value = _feature(row, "trigger_delay_minutes")
    return value is not None and value <= 2.0


def _risk_at_least(row: dict[str, Any], threshold: float) -> bool:
    value = _feature(row, "risk_atr")
    return value is not None and value >= threshold


def _context_sep_at_most(row: dict[str, Any], threshold: float) -> bool:
    value = _feature(row, "context_15m_ema_separation_atr")
    return value is not None and value <= threshold


def _and(*parts: Callable[[dict[str, Any]], bool]) -> Callable[[dict[str, Any]], bool]:
    return lambda row: all(part(row) for part in parts)


def candidate_catalog() -> list[GuardCandidate]:
    return [
        GuardCandidate(
            "di_baseline",
            "di_continuation",
            "Unfiltered DI continuation discovery family.",
            _all,
        ),
        GuardCandidate(
            "di_fast_2m",
            "di_continuation",
            "Require completed 1m trigger within two minutes of the completed 5m setup.",
            _fast_2m,
        ),
        GuardCandidate(
            "di_risk_ge_090",
            "di_continuation",
            "Require structural entry risk of at least 0.90 setup ATR.",
            lambda row: _risk_at_least(row, 0.90),
        ),
        GuardCandidate(
            "di_risk_ge_100",
            "di_continuation",
            "Require structural entry risk of at least 1.00 setup ATR.",
            lambda row: _risk_at_least(row, 1.00),
        ),
        GuardCandidate(
            "di_fast_risk090",
            "di_continuation",
            "Require trigger <=2m and structural risk >=0.90 ATR.",
            _and(_fast_2m, lambda row: _risk_at_least(row, 0.90)),
        ),
        GuardCandidate(
            "di_fast_risk090_sep150",
            "di_continuation",
            "Fast/risk guard plus completed 15m EMA separation <=1.50 ATR.",
            _and(
                _fast_2m,
                lambda row: _risk_at_least(row, 0.90),
                lambda row: _context_sep_at_most(row, 1.50),
            ),
        ),
        GuardCandidate(
            "di_fast_risk090_sep180",
            "di_continuation",
            "Fast/risk guard plus completed 15m EMA separation <=1.80 ATR.",
            _and(
                _fast_2m,
                lambda row: _risk_at_least(row, 0.90),
                lambda row: _context_sep_at_most(row, 1.80),
            ),
        ),
        GuardCandidate(
            "di_fast_risk090_sep200",
            "di_continuation",
            "Fast/risk guard plus completed 15m EMA separation <=2.00 ATR.",
            _and(
                _fast_2m,
                lambda row: _risk_at_least(row, 0.90),
                lambda row: _context_sep_at_most(row, 2.00),
            ),
        ),
        GuardCandidate(
            "mr_baseline",
            "controlled_vwap_mean_reversion",
            "Unfiltered controlled VWAP mean-reversion discovery family.",
            _all,
        ),
        GuardCandidate(
            "mr_fast_2m",
            "controlled_vwap_mean_reversion",
            "Require completed 1m trigger within two minutes of the completed 5m setup.",
            _fast_2m,
        ),
    ]


def _max_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    equity = peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 6)


def _max_losing_streak(values: Sequence[float]) -> int:
    current = best = 0
    for value in values:
        if value < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _metrics(rows: Sequence[dict[str, Any]], *, usable_sessions: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["entry_time"])
    values = [float(row["realized_r"]) for row in ordered]
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    trades_per_session = len(values) / usable_sessions if usable_sessions else 0.0
    return {
        "trades": len(values),
        "trades_per_session": round(trades_per_session, 6),
        "trades_per_10_sessions": round(10 * trades_per_session, 3),
        "win_rate_pct": round(100 * sum(value > 0 for value in values) / len(values), 2) if values else 0.0,
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
        "max_drawdown_r": _max_drawdown(values),
        "max_losing_streak": _max_losing_streak(values),
        "mean_mfe_r": round(mean(float(row["mfe_r"]) for row in ordered), 6) if ordered else None,
        "mean_mae_r": round(mean(float(row["mae_r"]) for row in ordered), 6) if ordered else None,
        "direction_counts": {
            "CALL": sum(row["direction"] == "CALL" for row in ordered),
            "PUT": sum(row["direction"] == "PUT" for row in ordered),
        },
    }


def _folds(dates: Sequence[str], *, train: int = 120, test: int = 40) -> list[tuple[list[str], list[str]]]:
    unique = sorted(set(dates))
    result: list[tuple[list[str], list[str]]] = []
    cursor = 0
    while cursor + train + test <= len(unique):
        result.append((
            unique[cursor:cursor + train],
            unique[cursor + train:cursor + train + test],
        ))
        cursor += test
    return result


def _family_rows(report: dict[str, Any], family: str) -> list[dict[str, Any]]:
    return list(((report.get("families") or {}).get(family) or {}).get("trades") or [])


def _candidate_rows(
    report: dict[str, Any],
    candidate: GuardCandidate,
) -> list[dict[str, Any]]:
    return [
        row
        for row in _family_rows(report, candidate.family)
        if candidate.predicate(row)
    ]


def _overlap(
    rows: Sequence[dict[str, Any]],
    *,
    v3_dates: set[str],
) -> dict[str, Any] | None:
    if not v3_dates:
        return None
    non_v3 = [row for row in rows if row["date"] not in v3_dates]
    return {
        "v3_signal_days": len(v3_dates),
        "trades_on_v3_signal_days": sum(row["date"] in v3_dates for row in rows),
        "trades_on_non_v3_days": len(non_v3),
        "non_v3_total_r": round(sum(float(row["realized_r"]) for row in non_v3), 6),
        "non_v3_mean_r": round(mean(float(row["realized_r"]) for row in non_v3), 6) if non_v3 else None,
        "note": "Day-level overlap only; not exact simultaneous-position overlap.",
    }


def _robustness_grid(report: dict[str, Any], dates: Sequence[str]) -> list[dict[str, Any]]:
    rows = _family_rows(report, "di_continuation")
    folds = _folds(dates)
    result: list[dict[str, Any]] = []
    for risk in (0.85, 0.90, 0.95):
        for separation in (1.50, 1.80, 2.00):
            selected = [
                row for row in rows
                if _fast_2m(row)
                and _risk_at_least(row, risk)
                and _context_sep_at_most(row, separation)
            ]
            fold_metrics = []
            positive = negative = 0
            for index, (_, test_dates) in enumerate(folds, start=1):
                test_set = set(test_dates)
                block = [row for row in selected if row["date"] in test_set]
                metrics = _metrics(block, usable_sessions=len(test_dates))
                if metrics["trades"]:
                    if float(metrics["mean_r"]) > 0:
                        positive += 1
                    elif float(metrics["mean_r"]) < 0:
                        negative += 1
                fold_metrics.append({
                    "fold": index,
                    "test_start": test_dates[0],
                    "test_end": test_dates[-1],
                    "metrics": metrics,
                })
            result.append({
                "risk_floor_atr": risk,
                "context_15m_ema_separation_cap_atr": separation,
                "all_sessions": _metrics(selected, usable_sessions=len(dates)),
                "positive_test_folds": positive,
                "negative_test_folds": negative,
                "folds": fold_metrics,
            })
    return result


def build_report(discovery: dict[str, Any]) -> dict[str, Any]:
    dates = list(discovery.get("usable_dates") or [])
    usable_sessions = int(discovery.get("usable_sessions") or len(dates))
    folds = _folds(dates)
    v3_dates = set(discovery.get("strategy_a_v3_signal_dates") or [])
    candidates: dict[str, Any] = {}

    for candidate in candidate_catalog():
        selected = _candidate_rows(discovery, candidate)
        fold_reports = []
        for index, (_, test_dates) in enumerate(folds, start=1):
            test_set = set(test_dates)
            block = [row for row in selected if row["date"] in test_set]
            fold_reports.append({
                "fold": index,
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "metrics": _metrics(block, usable_sessions=len(test_dates)),
            })

        last_fold_end = folds[-1][1][-1] if folds else None
        tail = [
            row for row in selected
            if last_fold_end is not None and row["date"] > last_fold_end
        ]
        candidates[candidate.name] = {
            "family": candidate.family,
            "description": candidate.description,
            "all_sessions": _metrics(selected, usable_sessions=usable_sessions),
            "chronological_test_folds": fold_reports,
            "post_last_fold_tail": {
                "after": last_fold_end,
                "metrics": _metrics(
                    tail,
                    usable_sessions=sum(day > last_fold_end for day in dates) if last_fold_end else 0,
                ),
            },
            "strategy_a_v3_same_day_overlap": _overlap(selected, v3_dates=v3_dates),
        }

    return {
        "research_type": "STRATEGY_CD_GUARD_VALIDATION",
        "source_research_type": discovery.get("research_type"),
        "usable_sessions": usable_sessions,
        "fold_count": len(folds),
        "focal_hypothesis": {
            "candidate": "di_fast_risk090_sep180",
            "rationale": (
                "Center of a deliberately coarse robustness neighborhood: "
                "1m trigger delay <=2 minutes, structural risk >=0.90 setup ATR, "
                "and completed 15m EMA separation <=1.80 ATR."
            ),
        },
        "candidates": candidates,
        "di_robustness_grid": _robustness_grid(discovery, dates),
        "limitations": [
            "Guard hypotheses were motivated by exploratory analysis of this same historical sample; folds measure temporal stability, not pristine unseen validation.",
            "Underlying futures R only; not executable historical option PnL.",
            "The post-last-fold tail can contain very few trades and must not be over-interpreted.",
            "No Strategy A V3 production thresholds or lifecycle rules are changed.",
            "Forward paper/shadow validation and option-execution validation remain required before deployment.",
        ],
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Strategy C/D entry-time guard hypotheses")
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_cd_multitimeframe_discovery.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_cd_guard_validation.json",
    )
    args = parser.parse_args()
    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    report = build_report(discovery)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    compact = {
        "research_type": report["research_type"],
        "usable_sessions": report["usable_sessions"],
        "fold_count": report["fold_count"],
        "focal": report["candidates"]["di_fast_risk090_sep180"],
        "output": str(args.output),
    }
    print(json.dumps(compact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
