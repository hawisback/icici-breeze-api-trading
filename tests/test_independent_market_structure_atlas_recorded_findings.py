from services.historical.independent_market_structure_atlas_recorded_findings import (
    MARKET_STRUCTURE_ATLAS_RECORDED_FINDINGS_V1,
)


def test_atlas_record_is_sha_bound_and_exact():
    record = MARKET_STRUCTURE_ATLAS_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "df787501fcf8513f5204e6c442912e0e71938664cff602d021623676d58db439"
    )
    assert record["source"]["source_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )
    assert record["source"]["accepted_complete_sessions"] == 412
    assert record["source"]["rejected_sessions"] == 1


def test_atlas_record_pins_two_frozen_label_patterns():
    record = MARKET_STRUCTURE_ATLAS_RECORDED_FINDINGS_V1
    assert record["patterns_meeting_frozen_labels"] == [
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
    ]

    opening = record["opening_range_vs_remaining_range"]
    assert opening["pooled_spearman"] == 0.5086691594128394
    assert opening["same_sign_blocks"] == 6
    assert opening["bootstrap_95pct"] == [
        0.43403894017378525,
        0.5756271108979191,
    ]

    persistence = record["daily_range_persistence"]
    assert persistence["pooled_spearman"] == 0.39743756384484685
    assert persistence["same_sign_blocks"] == 6
    assert persistence["bootstrap_95pct"] == [
        0.3106894303727101,
        0.4771450729887842,
    ]


def test_atlas_record_does_not_promote_nonlabels():
    record = MARKET_STRUCTURE_ATLAS_RECORDED_FINDINGS_V1
    assert record["intraday_volatility_seasonality"][
        "combined_frozen_exploratory_label"
    ] is False
    assert record["nonlabels"]["overnight_gap_vs_session_range"][
        "same_sign_blocks"
    ] == 4
    assert record["nonlabels"]["weekday_range_seasonality"][
        "blocks_matching_pooled_max_category"
    ] == 1
    assert record["nonlabels"]["expiry_distance_range_structure"][
        "blocks_matching_pooled_max_category"
    ] == 3


def test_atlas_record_remains_exploratory_and_nontrading():
    guardrails = MARKET_STRUCTURE_ATLAS_RECORDED_FINDINGS_V1["guardrails"]
    assert guardrails["blind_validation"] is False
    assert guardrails["candidate_freeze"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["threshold_optimization"] is False
    assert guardrails["directional_entry_exit_rule"] is False
    assert guardrails["known_magnitude_thesis_retested"] is False
    assert guardrails["strategy_d_remains_paused"] is True
