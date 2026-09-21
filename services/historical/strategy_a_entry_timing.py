"""Read-only Strategy A entry-timing and momentum-maturity research.

Consumes strategy_a_research_candidates.json produced by the historical
research harness. It performs no broker calls and does not touch the candle DB.

The purpose is to separate a directionally valid setup from a timely entry by
studying momentum acceleration/decay, price stretch, trend age, trigger delay,
and time of day. Outputs are descriptive research evidence, not production
threshold changes.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Sequence

from services.historical.strategy_a_data_audit import IST


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trend_pass(row: dict[str, Any]) -> bool:
    c = row.get("baseline_components") or {}
    return all(bool(c.get(k)) for k in ("ema_order", "di_direction", "adx", "ema_separation"))


def _pre_risk_pass(row: dict[str, Any]) -> bool:
    c = row.get("baseline_components") or {}
    keys = (
        "ema_order", "di_direction", "adx", "ema_separation",
        "confirmation_direction", "confirmation_body",
        "confirmation_close_location", "confirmation_range",
        "sr_present", "sr_touch", "ema_or_vwap_near",
    )
    return all(bool(c.get(k)) for k in keys)


def _di_spread(row: dict[str, Any]) -> float | None:
    plus, minus = _f(row.get("plus_di14")), _f(row.get("minus_di14"))
    if plus is None or minus is None or plus + minus <= 0:
        return None
    raw = plus - minus if row["direction"] == "CALL" else minus - plus
    return raw / (plus + minus)


def _extension(row: dict[str, Any], key: str) -> float | None:
    close, ref, atr = _f(row.get("close")), _f(row.get(key)), _f(row.get("atr14"))
    if close is None or ref is None or atr is None or atr <= 0:
        return None
    raw = close - ref if row["direction"] == "CALL" else ref - close
    return raw / atr


def enrich_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda r: (r["date"], r["direction"], r["timestamp"]))
    history: dict[tuple[str, str], list[dict[str, Any]]] = {}
    enriched: list[dict[str, Any]] = []

    for original in ordered:
        row = dict(original)
        key = (row["date"], row["direction"])
        prior_rows = history.setdefault(key, [])
        p1 = prior_rows[-1] if prior_rows else None
        p2 = prior_rows[-2] if len(prior_rows) >= 2 else None

        adx = _f(row.get("adx14"))
        adx1 = _f(p1.get("adx14")) if p1 else None
        adx2 = _f(p2.get("adx14")) if p2 else None
        spread = _di_spread(row)
        spread1 = _di_spread(p1) if p1 else None
        atr = _f(row.get("atr14"))
        ema = _f(row.get("ema20"))
        ema1 = _f(p1.get("ema20")) if p1 else None

        ema_slope = None
        if ema is not None and ema1 is not None and atr and atr > 0:
            raw = ema - ema1
            if row["direction"] == "PUT":
                raw = -raw
            ema_slope = raw / atr

        streak = 0
        if _trend_pass(row):
            streak = 1
            if p1 is not None and _trend_pass(p1):
                streak += int((p1.get("timing") or {}).get("trend_streak_bars", 0))

        trigger_delay = None
        label = row.get("label") or {}
        if label.get("trigger_timestamp"):
            setup_ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            trig_ts = datetime.fromisoformat(str(label["trigger_timestamp"]).replace("Z", "+00:00"))
            trigger_delay = int(round((trig_ts - setup_ts).total_seconds() / 900.0))

        local = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).astimezone(IST)
        minute = local.hour * 60 + local.minute
        if minute <= 10 * 60 + 45:
            time_bucket = "EARLY_0945_1045"
        elif minute <= 12 * 60 + 30:
            time_bucket = "MID_AM_1100_1230"
        elif minute <= 14 * 60:
            time_bucket = "EARLY_PM_1245_1400"
        else:
            time_bucket = "LATE_1415_1445"

        row["timing"] = {
            "adx_delta_1bar": None if adx is None or adx1 is None else adx - adx1,
            "adx_delta_2bars": None if adx is None or adx2 is None else adx - adx2,
            "directional_di_spread": spread,
            "directional_di_spread_delta_1bar": None if spread is None or spread1 is None else spread - spread1,
            "ema20_directional_slope_atr_1bar": ema_slope,
            "directional_extension_from_ema20_atr": _extension(row, "ema20"),
            "directional_extension_from_vwap_atr": _extension(row, "session_vwap"),
            "trend_streak_bars": streak,
            "trigger_delay_bars": trigger_delay,
            "time_bucket": time_bucket,
            "pre_risk_pass": _pre_risk_pass(row),
        }
        enriched.append(row)
        prior_rows.append(row)

    return sorted(enriched, key=lambda r: (r["timestamp"], r["direction"]))


def _summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    selected = list(rows)
    triggered = [r for r in selected if (r.get("label") or {}).get("trigger_status") == "TRIGGERED"]
    scores = [float(r["label"]["t1_first_hit_r"]) for r in triggered if r["label"].get("t1_first_hit_r") is not None]
    mfe = [float(r["label"]["max_favorable_r"]) for r in triggered if r["label"].get("max_favorable_r") is not None]
    mae = [float(r["label"]["max_adverse_r"]) for r in triggered if r["label"].get("max_adverse_r") is not None]
    t1 = sum(bool(r["label"].get("hit_t1_before_stop")) for r in triggered)
    return {
        "candidate_rows": len(selected),
        "triggered_rows": len(triggered),
        "trigger_rate_pct": round(100 * len(triggered) / len(selected), 2) if selected else 0.0,
        "t1_before_stop": t1,
        "t1_before_stop_pct": round(100 * t1 / len(triggered), 2) if triggered else 0.0,
        "mean_t1_first_hit_r": round(mean(scores), 6) if scores else None,
        "sum_t1_first_hit_r": round(sum(scores), 6) if scores else 0.0,
        "mean_mfe_r": round(mean(mfe), 6) if mfe else None,
        "mean_mae_r": round(mean(mae), 6) if mae else None,
        "direction_distribution": dict(sorted(Counter(r["direction"] for r in triggered).items())),
    }


def _num_bucket(value: float | None, edges: Sequence[float], labels: Sequence[str]) -> str:
    if value is None:
        return "UNAVAILABLE"
    for i, edge in enumerate(edges):
        if value < edge:
            return labels[i]
    return labels[-1]


def _age_bucket(value: Any) -> str:
    try:
        age = int(value)
    except (TypeError, ValueError):
        return "UNAVAILABLE"
    if age <= 1:
        return "1_BAR"
    if age <= 3:
        return "2_3_BARS"
    if age <= 6:
        return "4_6_BARS"
    return "7_PLUS_BARS"


def _delay_bucket(value: Any) -> str:
    if value is None:
        return "NOT_TRIGGERED"
    bars = int(value)
    if bars <= 1:
        return "FIRST_VALIDITY_BAR"
    if bars == 2:
        return "SECOND_VALIDITY_BAR"
    return "LATER_THAN_EXPECTED"


def _group(rows: Sequence[dict[str, Any]], extractor: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(extractor(row), []).append(row)
    return {name: _summary(values) for name, values in sorted(groups.items())}


def build_entry_timing_report(candidate_report: dict[str, Any]) -> dict[str, Any]:
    rows = enrich_rows(candidate_report.get("rows") or [])
    pre_risk = [r for r in rows if (r.get("timing") or {}).get("pre_risk_pass")]
    baseline = [r for r in rows if r.get("baseline_pass")]

    def t(row: dict[str, Any], key: str) -> Any:
        return (row.get("timing") or {}).get(key)

    dimensions: dict[str, Callable[[dict[str, Any]], str]] = {
        "adx_change_1bar": lambda r: _num_bucket(_f(t(r, "adx_delta_1bar")), (-1.0, 0.0, 1.0), ("DECELERATING_LT_-1", "DECELERATING_-1_TO_0", "ACCELERATING_0_TO_1", "ACCELERATING_GE_1")),
        "adx_change_2bars": lambda r: _num_bucket(_f(t(r, "adx_delta_2bars")), (-2.0, 0.0, 2.0), ("FADING_LT_-2", "FADING_-2_TO_0", "BUILDING_0_TO_2", "BUILDING_GE_2")),
        "directional_di_spread": lambda r: _num_bucket(_f(t(r, "directional_di_spread")), (0.10, 0.20, 0.35), ("WEAK_LT_0.10", "MODEST_0.10_0.20", "STRONG_0.20_0.35", "VERY_STRONG_GE_0.35")),
        "di_spread_change_1bar": lambda r: _num_bucket(_f(t(r, "directional_di_spread_delta_1bar")), (-0.05, 0.0, 0.05), ("WEAKENING_LT_-0.05", "WEAKENING_-0.05_TO_0", "STRENGTHENING_0_TO_0.05", "STRENGTHENING_GE_0.05")),
        "ema20_slope_atr_1bar": lambda r: _num_bucket(_f(t(r, "ema20_directional_slope_atr_1bar")), (0.0, 0.05, 0.15), ("AGAINST_OR_FLAT_LT_0", "SHALLOW_0_0.05", "HEALTHY_0.05_0.15", "STEEP_GE_0.15")),
        "extension_from_ema20_atr": lambda r: _num_bucket(_f(t(r, "directional_extension_from_ema20_atr")), (0.10, 0.25, 0.50), ("AT_OR_BEHIND_LT_0.10", "NEAR_0.10_0.25", "EXTENDED_0.25_0.50", "VERY_EXTENDED_GE_0.50")),
        "extension_from_vwap_atr": lambda r: _num_bucket(_f(t(r, "directional_extension_from_vwap_atr")), (0.25, 0.50, 1.00), ("NEAR_LT_0.25", "MODERATE_0.25_0.50", "EXTENDED_0.50_1.00", "VERY_EXTENDED_GE_1.00")),
        "trend_streak_bars": lambda r: _age_bucket(t(r, "trend_streak_bars")),
        "trigger_delay": lambda r: _delay_bucket(t(r, "trigger_delay_bars")),
        "time_of_day": lambda r: str(t(r, "time_bucket") or "UNAVAILABLE"),
        "confirmation_body_ratio": lambda r: _num_bucket(_f(r.get("confirmation_body_ratio")), (0.45, 0.60, 0.75), ("LOW_LT_0.45", "MEDIUM_0.45_0.60", "STRONG_0.60_0.75", "VERY_STRONG_GE_0.75")),
        "confirmation_range_atr": lambda r: _num_bucket(_f(r.get("confirmation_range_atr")), (0.75, 1.00, 1.25), ("COMPACT_LT_0.75", "NORMAL_0.75_1.00", "LARGE_1.00_1.25", "VERY_LARGE_GE_1.25")),
    }

    def cohort(name: str, cohort_rows: list[dict[str, Any]], definition: str) -> dict[str, Any]:
        return {
            "definition": definition,
            "metrics": _summary(cohort_rows),
            "dimensions": {dim: _group(cohort_rows, extractor) for dim, extractor in dimensions.items()},
        }

    return {
        "research_type": "STRATEGY_A_V2_ENTRY_TIMING_MOMENTUM_RESEARCH",
        "source": candidate_report.get("source"),
        "source_directional_rows": len(rows),
        "purpose": "Test whether entries weaken as momentum fades, price stretches, trend age matures, trigger delay increases, or the session gets late.",
        "cohorts": {
            "pre_risk": cohort("pre_risk", pre_risk, "Trend + confirmation + confluence pass; structural-risk gates deliberately excluded to preserve sample size."),
            "baseline_ready": cohort("baseline_ready", baseline, "All frozen Strategy A V2 entry gates pass."),
        },
        "derived_fields": [
            "ADX delta over 1 and 2 decision bars",
            "directional DI spread and its 1-bar change",
            "directional EMA20 slope normalized by ATR",
            "directional extension from EMA20 and VWAP",
            "consecutive trend-qualified decision bars",
            "trigger delay in 15-minute bars",
            "time-of-day bucket",
        ],
        "interpretation_guardrails": [
            "Buckets are descriptive probes, not production thresholds.",
            "Trend streak begins at the first research decision bar in the session and can understate trend age present before 09:45.",
            "Rows are overlapping counterfactual opportunities, not portfolio trades.",
            "Small buckets must not become rules without chronological out-of-sample confirmation.",
            "Prefer robust improvements in expectancy, MFE and MAE across neighboring buckets over a single best win-rate bucket.",
        ],
        "enriched_rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Strategy A entry timing and momentum maturity")
    parser.add_argument("--candidates", type=Path, default=Path("data") / "strategy_a_research" / "strategy_a_research_candidates.json")
    parser.add_argument("--output", type=Path, default=Path("data") / "strategy_a_research" / "strategy_a_entry_timing.json")
    args = parser.parse_args()

    report = build_entry_timing_report(json.loads(args.candidates.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "research_type": report["research_type"],
        "source_directional_rows": report["source_directional_rows"],
        "pre_risk_metrics": report["cohorts"]["pre_risk"]["metrics"],
        "baseline_ready_metrics": report["cohorts"]["baseline_ready"]["metrics"],
        "output": str(args.output),
        "production_thresholds_changed": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
