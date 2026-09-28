"""Recorded development-only observations from the options lead/lag atlas.

These observations are discovery evidence only. They are not an O3 freeze and
must not be treated as blind validation or implementation approval.
"""

DEVELOPMENT_LEAD_LAG_ATLAS_V1 = {
    "research_type": "NIFTY_OPTIONS_LEAD_LAG_ATLAS_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "source": {
        "filename": "independent_nifty_options_research_development_80_sessions.json",
        "sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "sessions": 80,
        "underlying_rows": 6000,
        "raw_option_rows": 215550,
        "atm_pairs": 6000,
        "scorable_next_5m_rows": 5840,
        "futures_source": "BREEZE",
        "options_source": "BREEZE",
    },
    "feature": {
        "synthetic_forward_proxy": "current futures-referenced ATM strike + ATM CE close - ATM PE close",
        "lead_gap_move_bps": "current 5m synthetic-forward proxy return minus current 5m canonical futures return",
        "interpretation": (
            "A short-horizon call-put-parity-style relative-move proxy. It is not an exact "
            "theoretical forward because rates/dividends are not modeled."
        ),
    },
    "next_5m_development_observation": {
        "spearman": 0.1695929690736368,
        "mean_direction_aligned_return_bps": 0.7264871568563022,
        "median_direction_aligned_return_bps": 0.7418871618020129,
        "direction_hit_rate": 0.5539383561643836,
        "ols_with_current_futures_return": {
            "lead_gap_coefficient": 0.3926175080538489,
            "current_futures_return_coefficient": -0.010844351505829472,
            "r_squared": 0.02343829648482043,
        },
    },
    "chronological_block_stability": {
        "blocks": 8,
        "positive_spearman_blocks": "8/8",
        "positive_lead_gap_coefficient_blocks": "8/8",
        "positive_mean_direction_aligned_return_blocks": "8/8",
        "spearman_by_block": [
            0.16132587480392144,
            0.12693817759664883,
            0.17208575093895476,
            0.20050236300887722,
            0.18140360481597484,
            0.2766362812387637,
            0.13293781635518148,
            0.13354997434763743,
        ],
        "lead_gap_coefficient_by_block": [
            0.4416989096922744,
            0.3753637491306748,
            0.3891052207902716,
            0.44992478128330554,
            0.4672092294725032,
            0.5076766566417033,
            0.26724983377192646,
            0.31874468791636484,
        ],
    },
    "dte_stability": {
        "dtes": [0, 1, 4, 5, 6],
        "positive_spearman_dtes": "5/5",
        "spearman_by_dte": {
            "0": 0.13348513824129693,
            "1": 0.1235872198640953,
            "4": 0.20674235905312985,
            "5": 0.12326675650290661,
            "6": 0.24866340425588582,
        },
    },
    "magnitude_quartiles_development_only": {
        "warning": "descriptive quartiles only; no threshold is frozen",
        "top_abs_gap_quartile": {
            "rows": 1460,
            "mean_abs_lead_gap_bps": 3.6032506343699238,
            "mean_direction_aligned_next_5m_bps": 1.437706441983354,
            "median_direction_aligned_next_5m_bps": 1.4596257802335089,
            "direction_hit_rate": 0.6095890410958904,
            "positive_mean_blocks": "8/8",
        },
    },
    "lag_shape": {
        "individual_future_5m_bar_spearman": {
            "lag_1": 0.1695929690736368,
            "lag_2": 0.0033074841622851166,
            "lag_3": -0.02528572069931964,
            "lag_4": 0.02731675472566722,
            "lag_5": -0.002508465021017186,
            "lag_6": -0.014695919450569599,
        },
        "interpretation": (
            "The development relationship is concentrated in the immediately following 5-minute "
            "futures bar rather than persisting as a multi-bar directional effect."
        ),
    },
    "status": "PROMISING_DEVELOPMENT_ONLY_REQUIRES_MICROSTRUCTURE_VALIDATION_BEFORE_O3_FREEZE",
    "required_before_candidate_freeze": [
        "verify timestamp/bar semantics cannot mechanically create the one-bar lead",
        "challenge stale/last-trade option-close effects despite high ATM liquidity",
        "confirm incremental information beyond current futures state",
        "test simple sequence persistence without selecting a rescue filter",
        "freeze one interpretable feature and one primary horizon before any fresh blind",
    ],
}
