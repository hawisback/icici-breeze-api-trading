"""Recorded Jul-Sep 2026 F5 option-support stop findings."""

STRATEGY_F5_OPTION_SUPPORT_STOP_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_OPTION_SUPPORT_STOP_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_OPTION_SUPPORT_STOP_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "ca4c9b5f4c3c1474662b718ef0bc923fdef0c37413f9be83983fe4c0dd18db24"
        ),
        "f5_market_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "target_bearish_PE_trades": 84,
    },
    "baseline": {
        "trades": 84,
        "wins": 29,
        "losses": 55,
        "net_pnl_inr": 27242.21,
        "average_net_pnl_inr": 324.31,
    },
    "candidates": {
        "SIGNAL_BAR_LOW": {
            "breaks": 30,
            "broken_baseline_winners": 8,
            "broken_baseline_activated": 8,
            "winner_untouched_pct": 72.41,
            "activated_untouched_pct": 76.47,
            "average_support_distance_pct_vs_entry": -5.8572,
            "net_delta_vs_baseline_zero_slippage_inr": -3842.56,
            "monthly_delta_zero_slippage_inr": {
                "2026-07": 331.03,
                "2026-08": -3852.30,
                "2026-09": -321.29,
            },
            "passed": False,
        },
        "RECENT_10M_LOW": {
            "breaks": 4,
            "broken_baseline_winners": 2,
            "broken_baseline_activated": 3,
            "winner_untouched_pct": 93.10,
            "activated_untouched_pct": 91.18,
            "average_support_distance_pct_vs_entry": -13.0076,
            "net_delta_vs_baseline_zero_slippage_inr": -1862.86,
            "passed": False,
        },
        "CONFIRMED_SWING_LOW_2X2": {
            "breaks": 2,
            "broken_baseline_winners": 2,
            "broken_baseline_activated": 2,
            "winner_untouched_pct": 93.10,
            "activated_untouched_pct": 94.12,
            "average_support_distance_pct_vs_entry": -14.4397,
            "net_delta_vs_baseline_zero_slippage_inr": -1979.70,
            "passed": False,
        },
    },
    "decomposition": {
        "SIGNAL_BAR_LOW": {
            "triggered_losers": 22,
            "pnl_delta_from_triggered_losers_inr": 2868.94,
            "triggered_winners": 8,
            "pnl_delta_from_triggered_winners_inr": -6711.50,
            "interpretation": (
                "The local support break does identify many losers and saves "
                "money on them, but the value destroyed by recovery winners is "
                "more than twice the value saved on losers."
            ),
        },
        "RECENT_10M_LOW": {
            "triggered_losers": 2,
            "pnl_delta_from_triggered_losers_inr": 116.84,
            "triggered_winners": 2,
            "pnl_delta_from_triggered_winners_inr": -1979.70,
        },
        "CONFIRMED_SWING_LOW_2X2": {
            "triggered_losers": 0,
            "triggered_winners": 2,
            "pnl_delta_from_triggered_winners_inr": -1979.70,
        },
    },
    "interpretation": (
        "Option support is not a reliable invalidation boundary for this F5 "
        "development population. The closest support triggers often but removes "
        "too many recovery winners; deeper support triggers rarely and is even "
        "less selective. Combined with the failed NIFTY resistance and fixed "
        "percentage stop studies, simple one-dimensional price structure is "
        "not separating failed trades from recoveries well enough."
    ),
    "decision": "NO_SUPPORT_RESISTANCE_STOP_PROMOTED",
    "next_step": (
        "Identify a catastrophic stop boundary from pre-trail adverse excursion "
        "of successful bearish-PE trades: derive exactly one candidate from the "
        "95-percent winner and trail-activation preservation frontier using "
        "intrabar 1-minute option lows, then require fresh validation. Do not "
        "retune support/resistance definitions."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_option_support_stop_promoted": True,
        "no_support_resistance_retuning": True,
        "next_stop_candidate_must_be_preservation_derived_not_pnl_optimized": True,
        "strategy_d_remains_paused": True,
    },
}
