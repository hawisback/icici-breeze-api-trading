from services.historical.independent_short_swing_v2_hypothesis_pause import (
    GUARDRAILS,
    NEXT_RESEARCH_GATE,
    PAUSE_VERSION,
    POST_V2_STUDIES,
    V2_EVENT_SHA256,
)


def test_v2_hypothesis_pause_is_frozen_after_three_rejections():
    assert PAUSE_VERSION == "SHORT_SWING_V2_HYPOTHESIS_MINING_PAUSE_V1"
    assert V2_EVENT_SHA256 == (
        "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
    )
    assert len(POST_V2_STUDIES) == 3
    assert all(
        item["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
        for item in POST_V2_STUDIES
    )


def test_v2_pause_blocks_ad_hoc_fourth_hypothesis_and_blind_data():
    assert (
        NEXT_RESEARCH_GATE[
            "ad_hoc_fourth_hypothesis_on_same_v2_outcomes_allowed"
        ]
        is False
    )
    assert NEXT_RESEARCH_GATE["fresh_blind_data_allowed"] is False
    assert NEXT_RESEARCH_GATE["candidate_frozen"] is False
    assert NEXT_RESEARCH_GATE["implementation_allowed"] is False
    assert NEXT_RESEARCH_GATE["strategy_d_remains_paused"] is True


def test_v2_pause_forbids_post_hoc_rescues():
    assert GUARDRAILS["no_retest_of_rejected_studies"] is True
    assert GUARDRAILS["no_direction_flip_rescues"] is True
    assert GUARDRAILS["no_threshold_rescues"] is True
    assert GUARDRAILS["no_filter_rescues"] is True
    assert GUARDRAILS["no_horizon_rescues"] is True
    assert (
        GUARDRAILS[
            "no_ad_hoc_hypothesis_generation_from_observed_v2_results"
        ]
        is True
    )
    assert GUARDRAILS["strategy_d_remains_paused"] is True
