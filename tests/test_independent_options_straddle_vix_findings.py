from services.historical.independent_options_straddle_vix_findings import (
    STRADDLE_VIX_ATTRIBUTION_FINDING,
)


def test_straddle_vix_finding_remains_descriptive_only():
    finding = STRADDLE_VIX_ATTRIBUTION_FINDING
    assert finding["research_only"] is True
    assert finding["development_only"] is True
    assert finding["candidate_frozen"] is False
    assert finding["blind_data_used"] is False
    assert finding["implementation_allowed"] is False
    assert finding["decision"]["status"] == "DESCRIPTIVE_BEHAVIOR_NOT_FROZEN_AS_O3"
    assert finding["decision"]["blind_validation"] is False
    assert finding["decision"]["retune_into_candidate"] is False


def test_straddle_vix_finding_records_chronological_instability():
    finding = STRADDLE_VIX_ATTRIBUTION_FINDING["cross_fitted_attribution"]
    excursion = finding["future_30m_max_excursion"]
    terminal = finding["future_30m_absolute_terminal_return"]
    assert excursion["n"] == 5280
    assert excursion["positive_blocks"] == 6
    assert excursion["total_blocks"] == 8
    assert len(excursion["block_residual_spearman"]) == 8
    assert terminal["positive_blocks"] == 5
    assert terminal["total_blocks"] == 8
    assert len(terminal["block_residual_spearman"]) == 8
