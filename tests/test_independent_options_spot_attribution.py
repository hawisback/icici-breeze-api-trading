from services.historical.independent_options_spot_attribution import (
    OPTIONS_DEVELOPMENT_SHA256,
    SPOT_DEVELOPMENT_SHA256,
)
from services.historical.independent_options_spot_attribution_findings import (
    DEVELOPMENT_OPTIONS_SPOT_ATTRIBUTION_V1,
)


def test_spot_attribution_preserves_development_only_guardrails():
    result = DEVELOPMENT_OPTIONS_SPOT_ATTRIBUTION_V1
    assert result["source"]["options_sha256"] == OPTIONS_DEVELOPMENT_SHA256
    assert result["source"]["spot_sha256"] == SPOT_DEVELOPMENT_SHA256
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False


def test_options_gap_remains_incremental_after_spot_attribution():
    result = DEVELOPMENT_OPTIONS_SPOT_ATTRIBUTION_V1
    pooled = result["pooled_attribution"]
    assert pooled["options_gap_coefficient_positive_blocks"] == "8/8"
    assert pooled["incremental_r2_options_positive_blocks"] == "8/8"
    assert pooled["combined_model"]["options_gap_coefficient"] > 0.0
    assert pooled["incremental_r2_options_over_spot"] > 0.0
    assert (
        result["crossfit_incremental_options_component"]["positive_spearman_blocks"]
        == "8/8"
    )


def test_timestamp_and_multi_strike_challenges_do_not_show_one_bar_shift_or_atm_artifact():
    result = DEVELOPMENT_OPTIONS_SPOT_ATTRIBUTION_V1
    timing = result["timestamp_alignment_challenge"]
    assert timing["synthetic_return_vs_same_bar_spot_return_spearman"] > 0.9
    assert abs(timing["synthetic_return_vs_previous_bar_spot_return_spearman"]) < 0.05
    assert abs(timing["synthetic_return_vs_next_bar_spot_return_spearman"]) < 0.05

    multi = result["multi_strike_staleness_challenge"]
    assert multi["positive_raw_blocks_each_width"] == "8/8"
    assert multi["positive_crossfit_blocks_each_width"] == "8/8"
    assert min(multi["raw_spearman_by_half_width"].values()) > 0.16
    assert min(multi["crossfit_incremental_spearman_by_half_width"].values()) > 0.12


def test_executable_timing_survives_but_does_not_freeze_o3():
    result = DEVELOPMENT_OPTIONS_SPOT_ATTRIBUTION_V1
    execution = result["execution_timing_challenge"]
    assert execution["next_open_to_close_positive_spearman_blocks"] == "8/8"
    assert execution["crossfit_component_vs_next_open_to_next_close_spearman"] > 0.0
    assert execution["next_open_to_close_mean_direction_aligned_bps"] > 0.0
    assert result["status"] == "PROMISING_INCREMENTAL_MICROSTRUCTURE_LEAD_BUT_NOT_YET_O3"
