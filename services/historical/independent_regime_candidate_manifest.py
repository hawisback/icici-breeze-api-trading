"""Frozen second-stage independent-market regime hypotheses.

Development corpus
------------------
This manifest is frozen after inspecting the original discovery block plus two
older validation blocks:

- 2026-08-12 through 2026-08-25 (August near-month future)
- 2026-08-27 through 2026-09-09 (September near-month future)
- 2026-09-10 through 2026-09-25 (September near-month future)

Because all three blocks have now been inspected, they are a development corpus,
not future validation data. Any candidate below must be tested next on a fully
unseen older block before implementation.

Exact event semantics
---------------------
These semantics intentionally reproduce the original IR discovery manifest:
- 5-minute NIFTY futures are the research/execution instrument.
- TR = max(high-low, abs(high-prev_close), abs(low-prev_close)).
- ATR14 = within-session rolling mean of TR, min_periods=8.
- MOM3 = futures_close[t] - futures_close[t-3].
- OI3 = futures_open_interest[t] - futures_open_interest[t-3].
- OR15 = high/low of the first three 5-minute bars.
- Entry window = 09:30 through 14:45.
- ATR availability makes 09:50 the first executable signal.
- At the beginning of executable eligibility, prior-condition state is reset
  false. This is required to reproduce the original frozen discovery metrics.
- Base H1 episode = H1 condition false->true while executable.
- Require at least 6 signal bars since the prior accepted signal.
- Entry = next 5-minute bar open.
- Stop = 1.0 * signal-bar ATR14.
- Target = 1.0 * signal-bar ATR14.
- Maximum hold = 6 bars; otherwise exit at sixth-bar close.

No tuning ranges are authorized by this manifest.
"""

from __future__ import annotations

REGIME_DEVELOPMENT_BLOCKS = {
    "IR-REGIME-DEV-01": {
        "session_dates": [
            "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17",
            "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21",
            "2026-08-24", "2026-08-25",
        ],
        "futures_expiry": "2026-08-25",
    },
    "IR-REGIME-DEV-02": {
        "session_dates": [
            "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01",
            "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07",
            "2026-09-08", "2026-09-09",
        ],
        "futures_expiry": "2026-09-29",
    },
    "IR-REGIME-DEV-03": {
        "session_dates": [
            "2026-09-10", "2026-09-11", "2026-09-15", "2026-09-16",
            "2026-09-17", "2026-09-18", "2026-09-22", "2026-09-23",
            "2026-09-24", "2026-09-25",
        ],
        "futures_expiry": "2026-09-29",
    },
}

COMMON_EXECUTION = {
    "entry_start": "09:30",
    "entry_end": "14:45",
    "atr_window": 14,
    "atr_min_periods": 8,
    "episode_min_gap_signal_bars": 6,
    "entry": "NEXT_BAR_OPEN",
    "target_atr": 1.0,
    "stop_atr": 1.0,
    "max_hold_bars": 6,
}

REGIME_CANDIDATES = {
    "IR-H5-DOWN-OI-OUTSIDE-OR15": {
        "status": "PASSED_BLIND_03_RETAIN_FOR_CONFIRMATION",
        "side": "SHORT",
        "base_event": ["MOM3 < 0", "OI3 < 0", "H1 false->true"],
        "signal_gate": "FUTURES_CLOSE < OR15_LOW OR FUTURES_CLOSE > OR15_HIGH",
        "interpretation": (
            "Qualify the original down-momentum/falling-OI event only when the "
            "signal bar is already outside the first-15-minute range. The failed "
            "August block concentrated its H1 losses inside OR15."
        ),
        "development": {
            "episodes": 64,
            "active_days": 22,
            "total_r": 11.408725,
            "mean_r": 0.178261,
            "win_rate": 0.578125,
            "profit_factor": 1.555515,
            "max_drawdown_r": -7.284943,
            "mean_directional_30m_atr": 0.241351,
            "blocks": {
                "2026-08-12_to_2026-08-25": {
                    "episodes": 29,
                    "total_r": 1.008742,
                    "mean_r": 0.034784,
                    "mean_directional_30m_atr": 0.026945,
                },
                "2026-08-27_to_2026-09-09": {
                    "episodes": 12,
                    "total_r": 1.427196,
                    "mean_r": 0.118933,
                    "mean_directional_30m_atr": 0.279881,
                },
                "2026-09-10_to_2026-09-25": {
                    "episodes": 23,
                    "total_r": 8.972787,
                    "mean_r": 0.390121,
                    "mean_directional_30m_atr": 0.491586,
                },
            },
        },
    },
    "IR-H6-DOWN-OI-BELOW-OR15": {
        "status": "FAILED_BLIND_03_DO_NOT_IMPLEMENT",
        "side": "SHORT",
        "base_event": ["MOM3 < 0", "OI3 < 0", "H1 false->true"],
        "signal_gate": "FUTURES_CLOSE < OR15_LOW",
        "interpretation": (
            "Stricter structural-confirmation variant: accept H1 only after price "
            "has broken below the first-15-minute low. Directional continuation "
            "was positive in each of the three inspected blocks, but activity is "
            "concentrated on fewer days."
        ),
        "development": {
            "episodes": 31,
            "active_days": 11,
            "total_r": 8.527260,
            "mean_r": 0.275073,
            "win_rate": 0.612903,
            "profit_factor": 2.151368,
            "max_drawdown_r": -2.324113,
            "mean_directional_30m_atr": 0.543864,
            "blocks": {
                "2026-08-12_to_2026-08-25": {
                    "episodes": 16,
                    "total_r": 0.969572,
                    "mean_r": 0.060598,
                    "mean_directional_30m_atr": 0.286268,
                },
                "2026-08-27_to_2026-09-09": {
                    "episodes": 4,
                    "total_r": 0.989873,
                    "mean_r": 0.247468,
                    "mean_directional_30m_atr": 0.280861,
                },
                "2026-09-10_to_2026-09-25": {
                    "episodes": 11,
                    "total_r": 6.567814,
                    "mean_r": 0.597074,
                    "mean_directional_30m_atr": 1.014187,
                },
            },
        },
    },
}

UNCHANGED_CONTROLS_FOR_NEXT_BLIND = {
    "IR-H2-DOWN-OI-ABOVE-VWAP": {
        "reason": "Previously frozen; positive simulated R in each inspected block.",
        "no_rule_changes": True,
    },
    "IR-H3-UP-IMPULSE-REVERSAL": {
        "reason": "Previously frozen; positive simulated R in each inspected block.",
        "no_rule_changes": True,
    },
}

REJECTED_REGIME_IDEAS = {
    "H1_INSIDE_OR15_LONG_REVERSAL": (
        "Not frozen: profitable mainly in the August failure block and negative "
        "in the two later blocks, so it is a regime explanation rather than a "
        "stable candidate."
    ),
    "H1_PLUS_VIX_FILTERS": (
        "Not frozen: VIX filters did not improve H1 consistently across all "
        "three blocks."
    ),
    "H1_PLUS_RVOL_FILTERS": (
        "Not frozen: relative-volume filters were not directionally stable "
        "across all three blocks."
    ),
}


BLIND_VALIDATION_03 = {
    "session_dates": [
        "2026-07-29", "2026-07-30", "2026-07-31", "2026-08-03",
        "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07",
        "2026-08-10", "2026-08-11",
    ],
    "futures_expiry": "2026-08-25",
    "IR-H5-DOWN-OI-OUTSIDE-OR15": {
        "episodes": 30,
        "active_days": 8,
        "total_r": 5.983758,
        "mean_r": 0.199459,
        "win_rate": 0.566667,
        "profit_factor": 1.554530,
        "max_drawdown_r": -3.026756,
        "mean_directional_30m_atr": 0.137112,
        "day_cluster_bootstrap_mean_r_95pct": [-0.153846, 0.589620],
        "result": "PASS_DIRECTION_AND_SIMULATED_R_BUT_CI_CROSSES_ZERO",
    },
    "IR-H6-DOWN-OI-BELOW-OR15": {
        "episodes": 9,
        "active_days": 2,
        "total_r": -4.0,
        "mean_r": -0.444444,
        "win_rate": 0.222222,
        "profit_factor": 0.333333,
        "max_drawdown_r": -4.0,
        "mean_directional_30m_atr": -0.146735,
        "day_cluster_bootstrap_mean_r_95pct": [-0.5, -0.333333],
        "result": "FAIL",
    },
    "IR-H2-DOWN-OI-ABOVE-VWAP": {
        "episodes": 29,
        "active_days": 7,
        "total_r": 9.983758,
        "mean_r": 0.344268,
        "win_rate": 0.655172,
        "profit_factor": 2.135721,
        "max_drawdown_r": -2.026756,
        "mean_directional_30m_atr": 0.189477,
        "day_cluster_bootstrap_mean_r_95pct": [0.032936, 0.689833],
        "result": "PASS_UNCHANGED_CONTROL",
    },
    "IR-H3-UP-IMPULSE-REVERSAL": {
        "episodes": 57,
        "active_days": 10,
        "total_r": 7.088505,
        "mean_r": 0.124360,
        "win_rate": 0.526316,
        "profit_factor": 1.350779,
        "max_drawdown_r": -7.822368,
        "mean_directional_30m_atr": 0.025402,
        "day_cluster_bootstrap_mean_r_95pct": [-0.207749, 0.450680],
        "result": "POSITIVE_SIMULATED_R_WEAK_DIRECTIONAL_EFFECT",
    },
    "post_hoc_observation_not_a_candidate": (
        "Within H5, ABOVE_OR15 contributed +9.983758R on 21 episodes while "
        "BELOW_OR15 contributed -4.0R on 9 episodes. This was observed only "
        "after opening Blind 03 and must not be promoted without a new freeze "
        "and another untouched block."
    ),
}


BLIND_VALIDATION_04_PROTOCOL = {
    "status": "FROZEN_BEFORE_DATA_COLLECTION",
    "end_date": "2026-07-28",
    "sessions": 10,
    "futures_expiry": "2026-07-28",
    "primary_candidates": [
        "IR-H5-DOWN-OI-OUTSIDE-OR15",
        "IR-H2-DOWN-OI-ABOVE-VWAP",
    ],
    "secondary_control": "IR-H3-UP-IMPULSE-REVERSAL",
    "retired": ["IR-H6-DOWN-OI-BELOW-OR15"],
    "prohibited_post_hoc_gate": "H1_ABOVE_OR15",
    "execution": COMMON_EXECUTION,
    "decision_rule": (
        "Score the frozen candidates unchanged. Do not mine Blind 04 for a new "
        "filter before recording the predeclared results."
    ),
}


BLIND_VALIDATION_04 = {
    "session_dates": [
        "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20",
        "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24",
        "2026-07-27", "2026-07-28",
    ],
    "futures_expiry": "2026-07-28",
    "IR-H5-DOWN-OI-OUTSIDE-OR15": {
        "episodes": 25,
        "active_days": 8,
        "total_r": -1.285176,
        "mean_r": -0.051407,
        "win_rate": 0.44,
        "profit_factor": 0.890277,
        "max_drawdown_r": -3.572295,
        "mean_directional_30m_atr": 0.020180,
        "day_cluster_bootstrap_mean_r_95pct": [-0.442864, 0.285714],
        "result": "FAIL_CONFIRMATION",
    },
    "IR-H2-DOWN-OI-ABOVE-VWAP": {
        "episodes": 23,
        "active_days": 8,
        "total_r": -3.673455,
        "mean_r": -0.159715,
        "win_rate": 0.434783,
        "profit_factor": 0.647465,
        "max_drawdown_r": -5.259358,
        "mean_directional_30m_atr": -0.045721,
        "day_cluster_bootstrap_mean_r_95pct": [-0.363612, 0.024166],
        "result": "FAIL_CONFIRMATION",
    },
    "IR-H3-UP-IMPULSE-REVERSAL": {
        "episodes": 53,
        "active_days": 10,
        "total_r": 8.015170,
        "mean_r": 0.151230,
        "win_rate": 0.584906,
        "profit_factor": 1.439007,
        "max_drawdown_r": -3.119494,
        "mean_directional_30m_atr": 0.180298,
        "day_cluster_bootstrap_mean_r_95pct": [-0.028009, 0.341163],
        "result": "SECONDARY_CONTROL_POSITIVE_NOT_PROMOTED_POST_HOC",
    },
    "ambiguous_intrabar_events": 0,
    "post_hoc_observation_not_a_candidate": (
        "H1 OR15 subgroups reversed relative to Blind 03: BELOW_OR15 was mildly "
        "positive while ABOVE_OR15 was negative. This reinforces that neither "
        "post-hoc subgroup should be promoted from these inspected blocks."
    ),
}

REGIME_RESEARCH_DECISION_AFTER_BLIND_04 = {
    "IR-H5-DOWN-OI-OUTSIDE-OR15": "DO_NOT_IMPLEMENT; failed confirmation block",
    "IR-H2-DOWN-OI-ABOVE-VWAP": "DO_NOT_IMPLEMENT; failed confirmation block",
    "IR-H6-DOWN-OI-BELOW-OR15": "RETIRED; failed Blind 03",
    "IR-H3-UP-IMPULSE-REVERSAL": (
        "RESEARCH_ONLY; positive Blind 04 secondary control, but do not promote "
        "based on a block where it was not a primary candidate"
    ),
    "next_step": (
        "Return to strategy-independent feature/regime discovery using the now "
        "larger historical corpus. Any new hypothesis requires a new freeze and "
        "a later untouched validation block before implementation."
    ),
}
