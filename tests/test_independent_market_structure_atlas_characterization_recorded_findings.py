from services.historical.independent_market_structure_atlas_characterization_recorded_findings import (
    MARKET_STRUCTURE_ATLAS_CHARACTERIZATION_RECORDED_FINDINGS_V1,
)


def test_characterization_record_is_sha_bound():
    record = MARKET_STRUCTURE_ATLAS_CHARACTERIZATION_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "3095df65d788403e01ac6760be63749de55f14f81a82560af9dae3e9a7511293"
    )
    assert record["source"]["source_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )
    assert record["source"]["accepted_complete_sessions"] == 412


def test_opening_range_characterization_is_monotone_and_cross_era_positive():
    result = MARKET_STRUCTURE_ATLAS_CHARACTERIZATION_RECORDED_FINDINGS_V1[
        "opening_range_vs_remaining_range"
    ]
    assert result["means_strictly_increasing"] is True
    assert result["medians_strictly_increasing"] is True
    assert result["q4_to_q1_outcome_mean_ratio"] == 1.9542017725543634
    assert result["q4_minus_q1_outcome_mean_bps"] == 51.44503254860164
    assert result["era_spearman"]["pre_tuesday_expiry_era"] > 0.0
    assert result["era_spearman"]["tuesday_expiry_era"] > 0.0


def test_daily_persistence_characterization_is_monotone_but_regime_sensitive():
    result = MARKET_STRUCTURE_ATLAS_CHARACTERIZATION_RECORDED_FINDINGS_V1[
        "daily_range_persistence"
    ]
    assert result["means_strictly_increasing"] is True
    assert result["medians_strictly_increasing"] is True
    assert result["q4_to_q1_outcome_mean_ratio"] == 1.57666954417698
    assert result["q4_minus_q1_outcome_mean_bps"] == 40.82602484329276
    assert result["era_spearman"]["pre_tuesday_expiry_era"] == (
        0.14680421855343298
    )
    assert result["era_spearman"]["tuesday_expiry_era"] == (
        0.5305004791269032
    )


def test_characterization_record_does_not_promote_quartile_thresholds():
    guardrails = MARKET_STRUCTURE_ATLAS_CHARACTERIZATION_RECORDED_FINDINGS_V1[
        "guardrails"
    ]
    assert guardrails["same_corpus_characterization"] is True
    assert guardrails["independent_validation"] is False
    assert guardrails["candidate_freeze"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["threshold_optimization"] is False
    assert guardrails["quartiles_are_descriptive_not_trading_thresholds"] is True
    assert guardrails["strategy_d_remains_paused"] is True
