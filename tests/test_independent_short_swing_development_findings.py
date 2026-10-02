from services.historical.independent_short_swing_development_findings import (
    SHORT_SWING_DEVELOPMENT_FINDINGS_V1,
)


def test_short_swing_findings_preserve_research_guardrails():
    result = SHORT_SWING_DEVELOPMENT_FINDINGS_V1
    assert result["research_only"] is True
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["decision"] == "NO_CANDIDATE_FREEZE"
    assert "do not consume fresh blind data" in result["interpretation"].lower()


def test_short_swing_findings_lock_combined_corpus_identity():
    source = SHORT_SWING_DEVELOPMENT_FINDINGS_V1["source"]
    assert source["sessions"] == 152
    assert source["five_minute_events"] == 11400
    assert source["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert source["cohort1_options_sha256"] == (
        "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"
    )
    assert source["cohort2_options_sha256"] == (
        "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc"
    )


def test_failed_breakout_lead_is_not_promoted_after_cost_screen():
    finding = SHORT_SWING_DEVELOPMENT_FINDINGS_V1["family_findings"][
        "failed_breakout_reversal"
    ]
    assert finding["status"] == "DEVELOPMENT_LEAD_ONLY"
    assert (
        finding["futures_cost_screen"]["representative_current_roundtrip_cost_bps_before_slippage"]
        > finding["simple_rule_futures_gross"]["gross_mean_bps"]
    )
    option = finding["one_strike_itm_option_execution"]
    assert option["net_mean_points_plus_1_point_each_side"] > 0
    assert option["net_mean_points_plus_2_points_each_side"] < 0
    assert option["positive_block_means_plus_1_point_each_side"] == "6/14"


def test_rejected_implementations_stay_rejected():
    families = SHORT_SWING_DEVELOPMENT_FINDINGS_V1["family_findings"]
    assert families["breakout_continuation"]["status"] == "NOT_CROSS_COHORT_STABLE"
    assert families["nondirectional_long_straddle"]["status"] == "REJECTED"
    assert "TOO_SMALL" in families["short_mean_reversion"]["status"]
