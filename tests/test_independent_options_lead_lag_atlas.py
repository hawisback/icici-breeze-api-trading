import pandas as pd

from services.historical.independent_options_lead_lag_atlas import (
    DEVELOPMENT_SHA256,
    _atm_strike,
)
from services.historical.independent_options_lead_lag_findings import (
    DEVELOPMENT_LEAD_LAG_ATLAS_V1,
)


def test_lead_lag_atlas_uses_frozen_development_corpus_only():
    finding = DEVELOPMENT_LEAD_LAG_ATLAS_V1
    assert finding["source"]["sha256"] == DEVELOPMENT_SHA256
    assert finding["source"]["sessions"] == 80
    assert finding["candidate_frozen"] is False
    assert finding["blind_data_used"] is False
    assert finding["implementation_allowed"] is False


def test_atm_rounding_is_half_up():
    values = pd.Series([26024.9, 26025.0, 26025.1, 26074.9, 26075.0])
    assert _atm_strike(values).tolist() == [26000, 26050, 26050, 26050, 26100]


def test_development_lead_lag_observation_is_cross_block_not_candidate_freeze():
    finding = DEVELOPMENT_LEAD_LAG_ATLAS_V1
    stability = finding["chronological_block_stability"]
    assert stability["positive_spearman_blocks"] == "8/8"
    assert stability["positive_lead_gap_coefficient_blocks"] == "8/8"
    assert stability["positive_mean_direction_aligned_return_blocks"] == "8/8"
    assert finding["next_5m_development_observation"]["spearman"] > 0.0
    assert (
        finding["next_5m_development_observation"]["ols_with_current_futures_return"][
            "lead_gap_coefficient"
        ]
        > 0.0
    )
    assert finding["status"] == (
        "PROMISING_DEVELOPMENT_ONLY_REQUIRES_MICROSTRUCTURE_VALIDATION_BEFORE_O3_FREEZE"
    )


def test_lead_lag_shape_is_concentrated_in_immediate_next_bar():
    profile = DEVELOPMENT_LEAD_LAG_ATLAS_V1["lag_shape"][
        "individual_future_5m_bar_spearman"
    ]
    assert profile["lag_1"] > 0.15
    assert abs(profile["lag_2"]) < 0.05
    assert abs(profile["lag_3"]) < 0.05
    assert abs(profile["lag_4"]) < 0.05
    assert abs(profile["lag_5"]) < 0.05
    assert abs(profile["lag_6"]) < 0.05
