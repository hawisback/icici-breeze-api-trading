"""Characterize the two strongest frozen market-structure atlas discoveries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from services.historical.independent_market_structure_atlas import (
    _read_breeze_rows,
    _spearman,
    _validate_and_prepare_sessions,
)
from services.historical.independent_market_structure_atlas_characterization_protocol import (
    CORPUS_ROLE,
    GUARDRAILS,
    PROTOCOL_VERSION,
    REGIMES,
    RELATIONSHIPS,
    SOURCE,
)

RESEARCH_TYPE = "NIFTY_BREEZE_ATLAS_PATTERN_CHARACTERIZATION_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quartile_characterization(
    sessions: pd.DataFrame,
    predictor: str,
    outcome: str,
) -> dict[str, Any]:
    usable = sessions[["date", predictor, outcome]].dropna().copy()
    labels = ["Q1", "Q2", "Q3", "Q4"]
    usable["quartile"], edges = pd.qcut(
        usable[predictor],
        q=4,
        labels=labels,
        retbins=True,
        duplicates="raise",
    )

    buckets: list[dict[str, Any]] = []
    for label in labels:
        chunk = usable.loc[usable["quartile"] == label]
        buckets.append(
            {
                "quartile": label,
                "sessions": int(len(chunk)),
                "predictor_mean_bps": float(chunk[predictor].mean()),
                "outcome_mean_bps": float(chunk[outcome].mean()),
                "outcome_median_bps": float(chunk[outcome].median()),
            }
        )

    means = [row["outcome_mean_bps"] for row in buckets]
    medians = [row["outcome_median_bps"] for row in buckets]
    return {
        "observations": int(len(usable)),
        "predictor_quartile_edges_bps": [float(value) for value in edges],
        "buckets": buckets,
        "adjacent_outcome_means_strictly_increasing": all(
            later > earlier for earlier, later in zip(means, means[1:])
        ),
        "adjacent_outcome_medians_strictly_increasing": all(
            later > earlier for earlier, later in zip(medians, medians[1:])
        ),
        "q4_to_q1_outcome_mean_ratio": float(means[-1] / means[0]),
        "q4_minus_q1_outcome_mean_bps": float(means[-1] - means[0]),
    }


def _regime_characterization(
    sessions: pd.DataFrame,
    predictor: str,
    outcome: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, (start, end) in REGIMES.items():
        chunk = sessions.loc[
            (sessions["date"] >= start)
            & (sessions["date"] <= end),
            ["date", predictor, outcome],
        ].dropna()
        result[label] = {
            "first_date": start,
            "last_date": end,
            "observations": int(len(chunk)),
            "spearman": _spearman(
                chunk[predictor].to_numpy(dtype=float),
                chunk[outcome].to_numpy(dtype=float),
            ),
        }
    return result


def analyze_database(db_path: Path) -> dict[str, Any]:
    digest = _sha256(db_path)
    if digest != SOURCE["database_sha256"]:
        raise ValueError(
            "source database SHA changed; this frozen same-corpus "
            "characterization must use the recorded atlas database"
        )

    raw = _read_breeze_rows(db_path)
    _, sessions, rejected = _validate_and_prepare_sessions(raw)

    results: dict[str, Any] = {}
    for name, spec in RELATIONSHIPS.items():
        predictor = str(spec["predictor"])
        outcome = str(spec["outcome"])
        usable = sessions[[predictor, outcome]].dropna()
        results[name] = {
            "pooled_spearman": _spearman(
                usable[predictor].to_numpy(dtype=float),
                usable[outcome].to_numpy(dtype=float),
            ),
            "quartile_characterization": _quartile_characterization(
                sessions,
                predictor,
                outcome,
            ),
            "regime_characterization": _regime_characterization(
                sessions,
                predictor,
                outcome,
            ),
        }

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "corpus_role": CORPUS_ROLE,
        "research_only": True,
        "source_database": str(db_path),
        "source_database_sha256": digest,
        "qa": {
            "accepted_complete_sessions": int(len(sessions)),
            "rejected_sessions": int(len(rejected)),
            "rejections": rejected,
        },
        "results": results,
        "interpretation_policy": (
            "This characterizes shape and era stability on the same historical "
            "corpus that produced the discoveries. It is not independent "
            "validation and quartile edges are not trading thresholds."
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Characterize frozen Breeze atlas discoveries"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/market/historical.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/independent_market_structure_atlas_characterization.json"
        ),
    )
    args = parser.parse_args()
    report = analyze_database(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "source_database_sha256": report["source_database_sha256"],
                "accepted_complete_sessions": report["qa"][
                    "accepted_complete_sessions"
                ],
                "relationships": {
                    name: {
                        "pooled_spearman": result["pooled_spearman"],
                        "q4_to_q1_outcome_mean_ratio": result[
                            "quartile_characterization"
                        ]["q4_to_q1_outcome_mean_ratio"],
                        "means_strictly_increasing": result[
                            "quartile_characterization"
                        ]["adjacent_outcome_means_strictly_increasing"],
                        "regime_spearman": {
                            regime: values["spearman"]
                            for regime, values in result[
                                "regime_characterization"
                            ].items()
                        },
                    }
                    for name, result in report["results"].items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
