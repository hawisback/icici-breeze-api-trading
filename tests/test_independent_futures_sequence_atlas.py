from services.historical.independent_futures_sequence_atlas import (
    DEVELOPMENT_FUTURES_SHA256,
    HORIZONS_MINUTES,
    WINDOWS,
)
from services.historical.independent_futures_sequence_findings import (
    FUTURES_SEQUENCE_ATLAS_FINDING_V1,
)


def test_sequence_atlas_design_is_fixed_and_development_only():
    finding = FUTURES_SEQUENCE_ATLAS_FINDING_V1
    assert finding["source"]["futures_sha256"] == DEVELOPMENT_FUTURES_SHA256
    assert list(WINDOWS) == [3, 6]
    assert list(HORIZONS_MINUTES) == [10, 15, 30, 60]
    assert finding["candidate_frozen"] is False
    assert finding["blind_data_used"] is False
    assert finding["implementation_allowed"] is False
    assert finding["target_sizing_restarted"] is False


def test_recent_futures_range_is_stable_movement_state_not_candidate():
    movement = FUTURES_SEQUENCE_ATLAS_FINDING_V1["movement_state"]
    for horizon in ("10m", "15m", "30m", "60m"):
        assert (
            movement["three_bar_range_vs_future_max_excursion"][horizon][
                "positive_blocks"
            ]
            == "8/8"
        )
        assert (
            movement["six_bar_range_vs_future_max_excursion"][horizon][
                "positive_blocks"
            ]
            == "8/8"
        )
    controlled = movement["cross_fitted_three_bar_range_after_context"]
    assert controlled["future_30m_max_excursion_residual_spearman"] > 0.2
    assert controlled["positive_blocks"] == "8/8"
    assert movement["candidate_status"] == (
        "DESCRIPTIVE_ONLY_TARGET_SIZING_REMAINS_PAUSED"
    )


def test_vix_adds_to_range_but_atm_straddle_is_not_stable_incrementally():
    movement = FUTURES_SEQUENCE_ATLAS_FINDING_V1["movement_state"]
    vix = movement["vix_incremental_over_three_bar_range_and_futures_state"]
    straddle = movement["atm_straddle_incremental_over_range_vix_and_futures_state"]
    assert vix["future_30m_max_excursion_residual_spearman"] > 0.3
    assert vix["positive_blocks"] == "8/8"
    assert straddle["positive_blocks"] == "5/8"


def test_directional_mean_reversion_is_recorded_but_not_promoted():
    directional = FUTURES_SEQUENCE_ATLAS_FINDING_V1["directional_state"]
    assert directional["future_10m_return_spearman"] < 0.0
    assert directional["negative_blocks"] == "8/8"
    assert directional["controlled_standardized_coefficient_bps"] < 0.0
    assert directional["controlled_negative_blocks"] == "8/8"
    assert (
        directional["absolute_close_location_quartiles_descriptive_only"][
            "top_quartile_mean_reversal_aligned_10m_bps"
        ]
        < 1.0
    )
    assert directional["candidate_status"] == "DESCRIPTIVE_ONLY_NOT_O3"


def test_sequence_atlas_does_not_consume_blind_or_mine_more_filters():
    decision = FUTURES_SEQUENCE_ATLAS_FINDING_V1["decision"]
    assert decision["status"] == "NO_NEW_CANDIDATE_FREEZE"
    assert decision["consume_fresh_blind_block"] is False
    assert decision["mine_more_filters_from_same_80_sessions"] is False
