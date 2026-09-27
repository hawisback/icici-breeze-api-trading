"""Recorded blind results for frozen independent options-behavior candidates.

These are immutable observations from pre-specified blind scoring. They do not
change the frozen candidate definition, thresholds, or implementation status.
"""

OPTIONS_BLIND_08 = {
    "candidate_id": "O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION",
    "source": {
        "filename": "independent_nifty_options_research_blind_08.json",
        "sha256": "cfb9fb78a85e6171ae4c93c3257e4ec8e356f7a5096982540208ecdc67e943ab",
        "start_date": "2026-02-09",
        "end_date": "2026-02-20",
        "sessions": 10,
        "underlying_rows": 750,
        "raw_option_rows": 27000,
        "dynamic_option_rows": 13500,
        "dynamic_contracts_per_timestamp": 18,
        "failed_requests": 0,
    },
    "scoring": {
        "threshold_source": "frozen DEVELOPMENT_CORPUS only",
        "threshold_changes": False,
        "candidate_definition_changes": False,
        "directional_claim": False,
        "episode_rule": "false-to-true state transition within a session",
        "excursion_semantics": (
            "maximum absolute excursion from signal-bar futures close using future futures "
            "high/low over the full horizon; signal bar excluded"
        ),
    },
    "primary_30m": {
        "scorable_episodes": 15,
        "sessions_with_episodes": 8,
        "control_bars": 141,
        "episode_mean_excursion_bps": 14.211044920024117,
        "control_mean_excursion_bps": 12.878081681513489,
        "mean_lift_bps": 1.3329632385106276,
        "episode_median_excursion_bps": 11.703591952905157,
        "control_median_excursion_bps": 10.047159340937917,
        "median_lift_bps": 1.6564326119672401,
        "pre_specified_direction_matched_mean": True,
        "pre_specified_direction_matched_median": True,
    },
    "secondary_15m": {
        "scorable_episodes": 15,
        "control_bars": 144,
        "mean_lift_bps": 2.2723549151835876,
        "median_lift_bps": 1.9439406862686859,
    },
    "secondary_60m": {
        "scorable_episodes": 14,
        "control_bars": 128,
        "mean_lift_bps": 0.0007535112240795172,
        "median_lift_bps": -1.9117785671851628,
    },
    "interpretation": (
        "The frozen primary 30-minute movement-regime effect was positive on both mean and "
        "median in this first blind block, but smaller than development. The 60-minute secondary "
        "result was mixed. One blind block is support, not confirmation."
    ),
    "candidate_status_after_blind": "FROZEN_RESEARCH_ONLY_FIRST_BLIND_SUPPORT",
    "implementation_allowed": False,
    "retune_from_blind_allowed": False,
}
