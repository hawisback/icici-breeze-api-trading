"""Frozen candidates from the broad independent NIFTY futures discovery pass.

Freeze boundary
---------------
Development corpus: 80 complete 5-minute NIFTY futures sessions from
2026-05-18 through 2026-09-09, using corrected near-month futures contracts
where required.  No session older than 2026-05-18 may be inspected before
these definitions are evaluated on the next chronological blind block.

These are research hypotheses, not production strategy settings.
"""

FREEZE_VERSION = "BROAD_DISCOVERY_V1"
DEVELOPMENT_WINDOW = {
    "start": "2026-05-18",
    "end": "2026-09-09",
    "complete_sessions": 80,
}

COMMON_EXECUTION = {
    "bar_interval": "5m",
    "signal_window_start": "09:50",
    "signal_window_end": "14:45",
    "atr_period": 14,
    "atr_min_periods": 8,
    "episode_cooldown_bars": 6,
    "entry": "next_bar_open",
    "diagnostic_stop_atr": 1.0,
    "diagnostic_target_atr": 1.0,
    "diagnostic_max_hold_bars": 6,
    "same_bar_stop_target": "ambiguous_without_finer_data",
}

DIRECTIONAL_CANDIDATES = {
    "B1_MORNING_UPPER_RANGE_IMPULSE_EXHAUSTION": {
        "direction": "SHORT",
        "time_start": "09:50",
        "time_end_exclusive": "12:30",
        "conditions": {
            "mom3_over_atr_min": 0.50,
            "session_range_position_min": 0.75,
        },
        "primary_label": "reversal_return_30m_atr",
        "secondary_diagnostic": "1atr_target_vs_1atr_stop_max_30m",
        "development_summary": {
            "episodes": 181,
            "days": 61,
            "mean_reversal_30m_atr": 0.176,
            "positive_10_session_blocks": 7,
            "total_10_session_blocks": 8,
            "diagnostic_total_r": 29.98,
            "diagnostic_mean_r_per_event": 0.166,
        },
    },
}

REGIME_CANDIDATES = {
    "B2_HIGH_RVOL_EXPANSION": {
        "direction": None,
        "conditions": {"rvol20_min": 1.50},
        "primary_label": "next_30m_max_excursion_atr",
        "intended_use": "movement/target-size regime, not direction",
        "development_summary": {
            "positive_lift_10_session_blocks": 6,
            "total_10_session_blocks": 8,
            "approx_mean_block_lift_atr": 0.20,
        },
    },
    "B3_RISING_VIX_EXPANSION": {
        "direction": None,
        "conditions": {"vix_change_6_min": 0.06},
        "primary_label": "next_30m_max_excursion_atr",
        "intended_use": "movement/target-size regime, not direction",
        "development_summary": {
            "positive_lift_10_session_blocks": 7,
            "total_10_session_blocks": 8,
            "approx_mean_block_lift_atr": 0.063,
        },
    },
}

RETIRED_OR_NOT_FROZEN = {
    "DOWN_OI_CONTINUATION": "unstable across corrected broad corpus",
    "OR_BREAKOUT_CONTINUATION": "unstable across corrected broad corpus",
    "GENERIC_VWAP_CONTINUATION": "unstable across corrected broad corpus",
    "EXPANSION_BAR_CONTINUATION": "unstable across corrected broad corpus",
    "FAILED_OR_BREAK": "not stable enough to freeze",
    "LOW_VOL_BREAKOUT": "not stable enough to freeze",
}

NEXT_BLIND_PROTOCOL = {
    "direction": "older_than_development_window",
    "sessions": 10,
    "end_before": "2026-05-18",
    "no_parameter_changes_before_scoring": True,
    "no_new_candidate_mining_on_blind_block": True,
    "implementation_allowed_from_this_freeze": False,
}
