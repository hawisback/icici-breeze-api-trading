from pathlib import Path

import pytest

import services.historical.independent_market_structure_atlas_characterization as characterization
from services.historical.independent_market_structure_atlas_characterization_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    REGIMES,
    RELATIONSHIPS,
)


def test_characterization_protocol_is_same_corpus_not_validation():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_ATLAS_PATTERN_CHARACTERIZATION_V1"
    assert set(RELATIONSHIPS) == {
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
    }
    assert REGIMES == {
        "pre_tuesday_expiry_era": ["2025-01-01", "2025-08-31"],
        "tuesday_expiry_era": ["2025-09-01", "2026-09-18"],
    }
    assert GUARDRAILS["same_corpus_characterization"] is True
    assert GUARDRAILS["independent_validation"] is False
    assert GUARDRAILS["threshold_optimization"] is False
    assert GUARDRAILS["quartiles_are_descriptive_not_trading_thresholds"] is True


def test_quartile_characterization_reports_monotone_shape():
    frame = characterization.pd.DataFrame(
        {
            "date": [f"2025-01-{i:02d}" for i in range(1, 17)],
            "x": list(range(1, 17)),
            "y": [value * 3.0 for value in range(1, 17)],
        }
    )
    result = characterization._quartile_characterization(frame, "x", "y")

    assert result["observations"] == 16
    assert [row["sessions"] for row in result["buckets"]] == [4, 4, 4, 4]
    assert result["adjacent_outcome_means_strictly_increasing"] is True
    assert result["adjacent_outcome_medians_strictly_increasing"] is True
    assert result["q4_to_q1_outcome_mean_ratio"] > 1.0
    assert result["q4_minus_q1_outcome_mean_bps"] > 0.0


def test_regime_characterization_reports_both_frozen_eras():
    frame = characterization.pd.DataFrame(
        {
            "date": [
                "2025-01-02",
                "2025-02-03",
                "2025-08-29",
                "2025-09-01",
                "2026-01-02",
                "2026-09-07",
            ],
            "x": [1, 2, 3, 1, 2, 3],
            "y": [2, 4, 6, 3, 6, 9],
        }
    )
    result = characterization._regime_characterization(frame, "x", "y")

    assert result["pre_tuesday_expiry_era"]["observations"] == 3
    assert result["tuesday_expiry_era"]["observations"] == 3
    assert result["pre_tuesday_expiry_era"]["spearman"] == pytest.approx(1.0)
    assert result["tuesday_expiry_era"]["spearman"] == pytest.approx(1.0)


def test_characterization_refuses_changed_source_database_sha(
    tmp_path: Path,
):
    db = tmp_path / "historical.db"
    db.write_bytes(b"not-the-recorded-database")
    with pytest.raises(ValueError, match="source database SHA changed"):
        characterization.analyze_database(db)


def test_characterization_guardrails_remain_nontrading():
    assert GUARDRAILS["blind_validation"] is False
    assert GUARDRAILS["candidate_freeze"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["pnl_scored"] is False
    assert GUARDRAILS["directional_entry_exit_rule"] is False
    assert GUARDRAILS["strategy_d_remains_paused"] is True
