"""Frozen descriptive findings for ATM-straddle versus INDIA VIX attribution.

Development-only research record. This module deliberately does not define a
candidate, trading signal, or implementation permission.
"""

STRADDLE_VIX_ATTRIBUTION_FINDING = {
    "finding_id": "ATM_STRADDLE_MOVEMENT_STATE_VIX_ATTRIBUTION",
    "research_only": True,
    "development_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "data_sha256": {
        "options": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "futures": "3724809a5ddc1c0dce05990b6b405442bb299406f13af585b589a7b522cdc21e",
        "india_vix": "c2167725d89e2bf2b30843a44dcec2a1fdab7aeb48b60773ed12b9245f4523ac",
    },
    "qa": {
        "sessions": 80,
        "vix_rows": 6000,
        "complete_75_bar_vix_sessions": 80,
        "duplicate_vix_rows": 0,
        "invalid_vix_ohlc_rows": 0,
    },
    "feature": {
        "name": "atm_straddle_premium_fraction_of_futures",
        "definition": "(ATM_CE_close + ATM_PE_close) / NIFTY_futures_close",
        "atm_rounding": "50-point half-up",
        "option_contract": "explicit expiry selected by development options corpus",
    },
    "raw_spearman": {
        "straddle_vs_future_30m_max_excursion": 0.2760686397819873,
        "vix_vs_future_30m_max_excursion": 0.44961967725569973,
        "straddle_vs_vix": 0.32288695763854364,
    },
    "exact_dte_x_30m_normalized_spearman": {
        "straddle_vs_future_30m_max_excursion": 0.3739022344311284,
        "vix_vs_future_30m_max_excursion": 0.45262848829547614,
        "straddle_vs_vix": 0.6252748646418976,
    },
    "cross_fitted_attribution": {
        "controls": [
            "exact DTE x 30-minute time bucket",
            "INDIA VIX level",
            "INDIA VIX 5-minute change",
            "INDIA VIX 15-minute change",
            "recent 15-minute futures absolute movement",
            "current futures absolute 5-minute movement",
            "futures volume",
            "futures absolute OI change",
        ],
        "future_30m_max_excursion": {
            "n": 5280,
            "pooled_residual_spearman": 0.1573681613104501,
            "block_residual_spearman": [
                0.20867799805033363,
                0.22026183798318266,
                -0.06860918163056139,
                0.16263894920870717,
                0.07233675509314343,
                -0.020041326582893485,
                0.0013397225847208516,
                0.0669503164816515,
            ],
            "positive_blocks": 6,
            "total_blocks": 8,
        },
        "future_30m_absolute_terminal_return": {
            "n": 5280,
            "pooled_residual_spearman": 0.08848223901366659,
            "block_residual_spearman": [
                0.14027200580016358,
                0.10515050435252273,
                -0.1321910956887162,
                0.006579237075633992,
                0.030142777270755067,
                -0.0651628092476212,
                -0.06864399262958489,
                0.019908134858811974,
            ],
            "positive_blocks": 5,
            "total_blocks": 8,
        },
    },
    "decision": {
        "status": "DESCRIPTIVE_BEHAVIOR_NOT_FROZEN_AS_O3",
        "reason": (
            "ATM straddle level contains movement-state information, but INDIA "
            "VIX is the stronger raw benchmark and the straddle's incremental "
            "relationship is not chronologically stable after cross-fitted VIX "
            "and futures-state attribution."
        ),
        "blind_validation": False,
        "retune_into_candidate": False,
    },
}
