"""Recorded futures-spot return-dislocation structural development findings.

The structural hypothesis was frozen before outcome inspection. Exact-option
implementation rules were frozen separately after this pass and before exact
option P&L inspection. No blind data are used.
"""

FUTURES_SPOT_DISLOCATION_STRUCTURAL_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_FUTURES_SPOT_DISLOCATION_RECORDED_V1",
    "protocol_version": "SHORT_SWING_FUTURES_SPOT_DISLOCATION_V1",
    "options_protocol_version": "SHORT_SWING_FUTURES_SPOT_DISLOCATION_OPTIONS_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "decision": "STRUCTURAL_PASS_OPTIONS_IMPLEMENTATION_PENDING",
    "source": {
        "event_dataset_sha256": (
            "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
        ),
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "derived_abs_return_dislocation_thresholds_bps": {
        "80": 2.6823991352897063,
        "90": 3.623050885835169,
    },
    "structural_gate_passes": [
        "dp80_opt_off_h5",
        "dp80_opt_off_h10",
        "dp80_opt_reversion_direction_agreement_h5",
        "dp80_opt_reversion_direction_agreement_h10",
        "dp80_opt_reversion_direction_agreement_h15",
        "dp80_opt_reversion_direction_agreement_h30",
        "dp90_opt_off_h5",
        "dp90_opt_off_h10",
        "dp90_opt_reversion_direction_agreement_h5",
        "dp90_opt_reversion_direction_agreement_h10",
        "dp90_opt_reversion_direction_agreement_h15",
    ],
    "robust_option_neighborhood": {
        "rule_family": "p80/p90 x options reversion agreement x 5m/10m",
        "all_four_cells_pass_structural_gate": True,
        "cells": {
            "p80_h5": {
                "trades": 1434,
                "pooled_mean_bps": 1.0174816874184798,
                "cohort1_mean_bps": 1.1635843675632271,
                "cohort2_mean_bps": 0.5942603367817935,
                "positive_blocks": 14,
                "bootstrap_95pct_bps": [0.7287868329117125, 1.3080465616380745],
            },
            "p80_h10": {
                "trades": 1147,
                "pooled_mean_bps": 0.9566635800184831,
                "cohort1_mean_bps": 0.9525071683743406,
                "cohort2_mean_bps": 0.9677384915559107,
                "positive_blocks": 11,
                "bootstrap_95pct_bps": [0.4332875513406205, 1.4470492463946907],
            },
            "p90_h5": {
                "trades": 688,
                "pooled_mean_bps": 1.3612656676231105,
                "cohort1_mean_bps": 1.499521403166607,
                "cohort2_mean_bps": 0.7563968246203124,
                "positive_blocks": 13,
                "bootstrap_95pct_bps": [0.9708987454471726, 1.7492500629827887],
            },
            "p90_h10": {
                "trades": 582,
                "pooled_mean_bps": 1.445881351930756,
                "cohort1_mean_bps": 1.440297245311039,
                "cohort2_mean_bps": 1.4673801624166667,
                "positive_blocks": 13,
                "bootstrap_95pct_bps": [0.7608029677556931, 2.177499860970882],
            },
        },
    },
    "cost_interpretation": (
        "The futures structural effect is statistically/stability-positive but "
        "its gross mean is far below the reference futures roundtrip cost. It is "
        "therefore not a futures implementation candidate. The separately frozen "
        "exact long-option implementation must be tested before any candidate can "
        "advance."
    ),
    "next_step": (
        "Run only the frozen p80/p90 x 5m/10m exact ATM and one-strike-ITM long-option "
        "implementation with 0/1/2 points slippage per side. Do not inspect blind data."
    ),
}
