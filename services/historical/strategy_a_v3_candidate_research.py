"""Read-only Strategy A V3 candidate walk-forward research.

Consumes strategy_a_entry_timing.json.  This is a lightweight second-stage
research pass: it never calls a broker, writes candles, or changes production
configuration.

The candidate set is intentionally small and hypothesis-driven.  Its purpose is
to test whether replacing Strategy A V2's hard ADX floor with momentum-maturity
guards improves entry quality across chronological test blocks.

Unlike the earlier generic walk-forward selector, this selector can ABSTAIN:
if no candidate has enough triggered train rows and positive train expectancy,
no variant is selected for that fold.  We never promote the "least bad"
negative strategy merely because something must win a ranking.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Sequence


REQUIRED_COMPONENTS = (
    "ema_order",
    "di_direction",
    "adx",
    "ema_separation",
    "confirmation_direction",
    "confirmation_body",
    "confirmation_close_location",
    "confirmation_range",
    "sr_present",
    "sr_touch",
    "ema_or_vwap_near",
    "minimum_stop_distance",
    "maximum_stop_distance",
    "opposing_sr_room",
)


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    predicate: Callable[[dict[str, Any]], bool]


def _components(row: dict[str, Any]) -> dict[str, bool]:
    return {
        key: bool((row.get("baseline_components") or {}).get(key))
        for key in REQUIRED_COMPONENTS
    }


def _all_except(row: dict[str, Any], ignored: set[str]) -> bool:
    values = _components(row)
    return all(values[key] for key in REQUIRED_COMPONENTS if key not in ignored)


def _timing(row: dict[str, Any], key: str) -> float | None:
    value = (row.get("timing") or {}).get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _baseline(row: dict[str, Any]) -> bool:
    return bool(row.get("baseline_pass"))


def _adx18(row: dict[str, Any]) -> bool:
    if not _all_except(row, {"adx"}):
        return False
    try:
        return float(row.get("adx14")) >= 18.0
    except (TypeError, ValueError):
        return False


def _without_adx(row: dict[str, Any]) -> bool:
    return _all_except(row, {"adx"})


def _no_adx_reject_sharp_decay(row: dict[str, Any]) -> bool:
    if not _without_adx(row):
        return False
    delta = _timing(row, "adx_delta_2bars")
    return delta is not None and delta >= -2.0


def _no_adx_controlled_fade(row: dict[str, Any]) -> bool:
    if not _without_adx(row):
        return False
    delta = _timing(row, "adx_delta_2bars")
    return delta is not None and -2.0 <= delta < 0.0


def _no_adx_moderate_ema_slope(row: dict[str, Any]) -> bool:
    if not _without_adx(row):
        return False
    slope = _timing(row, "ema20_directional_slope_atr_1bar")
    return slope is not None and 0.05 <= slope < 0.15


def _no_adx_decay_and_slope_guard(row: dict[str, Any]) -> bool:
    if not _without_adx(row):
        return False
    delta = _timing(row, "adx_delta_2bars")
    slope = _timing(row, "ema20_directional_slope_atr_1bar")
    return (
        delta is not None
        and delta >= -2.0
        and slope is not None
        and 0.0 <= slope < 0.15
    )


def _close_location_20(row: dict[str, Any]) -> bool:
    if not _all_except(row, {"confirmation_close_location"}):
        return False
    value = row.get("confirmation_close_location_pct")
    try:
        return float(value) <= 0.20
    except (TypeError, ValueError):
        return False


def candidate_catalog() -> list[Candidate]:
    return [
        Candidate("baseline_v2", "Frozen Strategy A V2.", _baseline),
        Candidate("adx_floor_18", "Lower only the absolute ADX floor from 22 to 18.", _adx18),
        Candidate("without_adx_floor", "Remove the absolute ADX floor; retain all other V2 rules.", _without_adx),
        Candidate(
            "without_adx_reject_sharp_decay",
            "Remove absolute ADX floor but reject a 2-bar ADX collapse worse than -2 points.",
            _no_adx_reject_sharp_decay,
        ),
        Candidate(
            "without_adx_controlled_fade",
            "Remove absolute ADX floor and require 2-bar ADX change in [-2, 0), testing the pullback/maturity hypothesis.",
            _no_adx_controlled_fade,
        ),
        Candidate(
            "without_adx_moderate_ema_slope",
            "Remove absolute ADX floor and require directional EMA20 slope of 0.05-0.15 ATR per bar.",
            _no_adx_moderate_ema_slope,
        ),
        Candidate(
            "without_adx_decay_and_slope_guard",
            "Remove ADX floor; reject sharp ADX decay and require positive-but-not-steep EMA20 slope below 0.15 ATR.",
            _no_adx_decay_and_slope_guard,
        ),
        Candidate(
            "confirmation_close_location_20",
            "Keep V2 but tighten directional confirmation close location from 30% to 20%.",
            _close_location_20,
        ),
    ]


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    selected = list(rows)
    triggered = [
        row for row in selected
        if (row.get("label") or {}).get("trigger_status") == "TRIGGERED"
    ]
    scores = [
        float(row["label"]["t1_first_hit_r"])
        for row in triggered
        if row.get("label", {}).get("t1_first_hit_r") is not None
    ]
    mfe = [
        float(row["label"]["max_favorable_r"])
        for row in triggered
        if row.get("label", {}).get("max_favorable_r") is not None
    ]
    mae = [
        float(row["label"]["max_adverse_r"])
        for row in triggered
        if row.get("label", {}).get("max_adverse_r") is not None
    ]
    t1 = sum(bool(row["label"].get("hit_t1_before_stop")) for row in triggered)

    equity = peak = 0.0
    drawdown = 0.0
    for score in scores:
        equity += score
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)

    return {
        "selected_rows": len(selected),
        "triggered_rows": len(triggered),
        "trigger_rate_pct": round(100 * len(triggered) / len(selected), 2) if selected else 0.0,
        "mean_t1_first_hit_r": round(mean(scores), 6) if scores else None,
        "sum_t1_first_hit_r": round(sum(scores), 6) if scores else 0.0,
        "max_drawdown_t1_first_hit_r": round(drawdown, 6) if scores else None,
        "t1_before_stop_pct": round(100 * t1 / len(triggered), 2) if triggered else 0.0,
        "mean_mfe_r": round(mean(mfe), 6) if mfe else None,
        "mean_mae_r": round(mean(mae), 6) if mae else None,
    }


def _date_folds(
    rows: Sequence[dict[str, Any]],
    *,
    train_sessions: int,
    test_sessions: int,
) -> list[tuple[list[str], list[str]]]:
    dates = sorted({row["date"] for row in rows})
    folds: list[tuple[list[str], list[str]]] = []
    cursor = 0
    while cursor + train_sessions + test_sessions <= len(dates):
        train = dates[cursor: cursor + train_sessions]
        test = dates[cursor + train_sessions: cursor + train_sessions + test_sessions]
        folds.append((train, test))
        cursor += test_sessions
    return folds


def _candidate_rows(rows: Sequence[dict[str, Any]], candidate: Candidate) -> list[dict[str, Any]]:
    return [row for row in rows if candidate.predicate(row)]


def build_report(
    timing_report: dict[str, Any],
    *,
    train_sessions: int = 120,
    test_sessions: int = 40,
    min_train_triggers: int = 8,
) -> dict[str, Any]:
    rows = timing_report.get("enriched_rows") or []
    candidates = candidate_catalog()
    folds = _date_folds(rows, train_sessions=train_sessions, test_sessions=test_sessions)

    test_union: set[str] = set()
    fold_reports: list[dict[str, Any]] = []
    selected_test_rows: list[dict[str, Any]] = []

    for index, (train_dates, test_dates) in enumerate(folds, start=1):
        train_set, test_set = set(train_dates), set(test_dates)
        test_union.update(test_set)
        train_rows = [row for row in rows if row["date"] in train_set]
        test_rows = [row for row in rows if row["date"] in test_set]

        train_results = []
        eligible: list[tuple[tuple[float, float, int], Candidate, dict[str, Any]]] = []
        for candidate in candidates:
            metrics = _metrics(_candidate_rows(train_rows, candidate))
            train_results.append({"candidate": candidate.name, "metrics": metrics})
            expectancy = metrics.get("mean_t1_first_hit_r")
            count = int(metrics.get("triggered_rows") or 0)
            if (
                expectancy is not None
                and count >= min_train_triggers
                and float(expectancy) > 0.0
                and float(metrics.get("sum_t1_first_hit_r") or 0.0) > 0.0
            ):
                eligible.append((
                    (
                        float(expectancy),
                        float(metrics.get("max_drawdown_t1_first_hit_r") or 0.0),
                        count,
                    ),
                    candidate,
                    metrics,
                ))

        if eligible:
            eligible.sort(key=lambda item: item[0], reverse=True)
            chosen = eligible[0][1]
            chosen_train = eligible[0][2]
            chosen_test_rows = _candidate_rows(test_rows, chosen)
            selected_test_rows.extend(chosen_test_rows)
            chosen_test = _metrics(chosen_test_rows)
            selection = chosen.name
            reason = "POSITIVE_TRAIN_EXPECTANCY_WITH_MINIMUM_SAMPLE"
        else:
            chosen = None
            chosen_train = None
            chosen_test = _metrics([])
            selection = "ABSTAIN"
            reason = "NO_CANDIDATE_MET_POSITIVE_TRAIN_EXPECTANCY_AND_SAMPLE_REQUIREMENT"

        fold_reports.append({
            "fold": index,
            "train_start": train_dates[0],
            "train_end": train_dates[-1],
            "test_start": test_dates[0],
            "test_end": test_dates[-1],
            "selection": selection,
            "selection_reason": reason,
            "selected_train_metrics": chosen_train,
            "selected_test_metrics": chosen_test,
            "all_train_candidates": train_results,
        })

    fixed_oos = {}
    oos_rows = [row for row in rows if row["date"] in test_union]
    for candidate in candidates:
        fixed_oos[candidate.name] = {
            "description": candidate.description,
            "metrics": _metrics(_candidate_rows(oos_rows, candidate)),
            "folds": [],
        }
        for index, (_, test_dates) in enumerate(folds, start=1):
            test_set = set(test_dates)
            block = [row for row in rows if row["date"] in test_set]
            fixed_oos[candidate.name]["folds"].append({
                "fold": index,
                "metrics": _metrics(_candidate_rows(block, candidate)),
            })

    return {
        "research_type": "STRATEGY_A_V3_ENTRY_TIMING_CANDIDATE_WALK_FORWARD",
        "train_sessions": train_sessions,
        "test_sessions": test_sessions,
        "min_train_triggers": min_train_triggers,
        "fold_count": len(folds),
        "selection_policy": (
            "Choose only among candidates with at least min_train_triggers, "
            "positive train mean T1-first-hit R and positive train sum R; otherwise ABSTAIN."
        ),
        "candidate_hypotheses": [
            {"name": candidate.name, "description": candidate.description}
            for candidate in candidates
        ],
        "fixed_candidate_out_of_sample": fixed_oos,
        "adaptive_selection_out_of_sample": _metrics(selected_test_rows),
        "folds": fold_reports,
        "limitations": [
            "Candidates were motivated by prior exploratory research and are not a pristine unseen hypothesis set.",
            "T1-first-hit R is an underlying entry-quality research label, not production lifecycle R or option PnL.",
            "Rows can overlap and are not portfolio trades.",
            "Production Strategy A remains unchanged until a candidate survives lifecycle replay and paper/shadow validation.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward Strategy A V3 entry-timing candidates")
    parser.add_argument(
        "--timing",
        type=Path,
        default=Path("data") / "strategy_a_research" / "strategy_a_entry_timing.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_a_research" / "strategy_a_v3_candidate_walk_forward.json",
    )
    parser.add_argument("--train-sessions", type=int, default=120)
    parser.add_argument("--test-sessions", type=int, default=40)
    parser.add_argument("--min-train-triggers", type=int, default=8)
    args = parser.parse_args()

    timing_report = json.loads(args.timing.read_text(encoding="utf-8"))
    report = build_report(
        timing_report,
        train_sessions=args.train_sessions,
        test_sessions=args.test_sessions,
        min_train_triggers=args.min_train_triggers,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "research_type": report["research_type"],
        "fold_count": report["fold_count"],
        "adaptive_selection_out_of_sample": report["adaptive_selection_out_of_sample"],
        "output": str(args.output),
        "production_thresholds_changed": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
