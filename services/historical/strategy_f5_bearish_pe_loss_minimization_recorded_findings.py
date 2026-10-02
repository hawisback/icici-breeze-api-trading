"""Recorded Jul-Sep 2026 F5 bearish-PE loss-minimization findings."""

STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "bcd44b7a243baf99d333f4eb77b92081603dd21b3650ce88042a0efb80d97ef6"
        ),
        "f5_market_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "f5_backtest_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "breadth_market_sha256": (
            "fbb7b7b80d3c46bf3934cdefb688ab5453a82d2379b5a59328c6de7a8c7d1ca4"
        ),
        "target_bearish_PE_trades": 84,
        "breadth_confirmed_target_trades": 64,
    },
    "baseline": {
        "trades": 84,
        "wins": 29,
        "losses": 55,
        "win_rate_pct": 34.52,
        "net_pnl_inr": 27242.21,
        "average_net_pnl_inr": 324.31,
        "gross_loss_inr": 24661.45,
        "loss_concentration": {
            "trades_for_25pct_gross_loss": 5,
            "trades_for_50pct_gross_loss": 13,
            "trades_for_75pct_gross_loss": 26,
        },
    },
    "early_path_anatomy": {
        "six_minutes": {
            "winner_median_mfe_pct": 2.6552,
            "loser_median_mfe_pct": 0.2755,
            "winner_median_mae_pct": -0.7695,
            "loser_median_mae_pct": -1.9536,
            "winner_no_positive_close_pct": 21.74,
            "loser_no_positive_close_pct": 43.18,
        },
        "ten_minutes": {
            "winner_median_mfe_pct": 4.0030,
            "loser_median_mfe_pct": 0.2537,
            "winner_median_mae_pct": -0.7491,
            "loser_median_mae_pct": -2.8140,
            "winner_no_positive_close_pct": 22.22,
            "loser_no_positive_close_pct": 41.94,
        },
        "interpretation": (
            "Losers show materially weaker early progress, but the distributions "
            "overlap enough that binary early exits still remove valuable future "
            "winners and trail activations."
        ),
    },
    "frozen_lack_of_progress_candidates": {
        "NO_POSITIVE_CLOSE_BY_6M": {
            "triggered": 24,
            "winner_untouched_pct": 82.76,
            "activated_untouched_pct": 85.29,
            "net_delta_vs_baseline_inr": -3965.89,
            "passed": False,
        },
        "NO_POSITIVE_CLOSE_BY_10M": {
            "triggered": 17,
            "winner_untouched_pct": 86.21,
            "activated_untouched_pct": 88.24,
            "net_delta_vs_baseline_inr": -5718.40,
            "passed": False,
        },
        "MFE_LT_2PCT_BY_10M": {
            "triggered": 29,
            "winner_untouched_pct": 75.86,
            "activated_untouched_pct": 79.41,
            "net_delta_vs_baseline_inr": -9385.72,
            "passed": False,
        },
    },
    "breadth_confirmed_secondary": {
        "baseline_trades": 64,
        "baseline_net_pnl_inr": 21077.72,
        "all_three_lack_of_progress_candidates_worsened_pnl": True,
    },
    "exploratory_late_entry_clue": {
        "status": "POSTHOC_DEVELOPMENT_CLUE_NOT_VALIDATION",
        "cutoff": "14:30",
        "derivation": (
            "Computed from the artifact target-trade rows after the frozen "
            "lack-of-progress candidates failed. This cutoff was not preregistered."
        ),
        "entries_at_or_after_cutoff": {
            "trades": 12,
            "wins": 1,
            "trail_activations": 1,
            "net_pnl_inr": -5472.69,
            "average_net_pnl_inr": -456.06,
            "monthly_net_pnl_inr": {
                "2026-07": -1945.62,
                "2026-08": -2191.12,
                "2026-09": -1335.95,
            },
        },
        "entries_before_cutoff": {
            "trades": 72,
            "wins": 28,
            "trail_activations": 33,
            "net_pnl_inr": 32714.90,
            "winner_preservation_if_late_entries_removed_pct": 96.55,
            "activation_preservation_if_late_entries_removed_pct": 97.06,
        },
        "breadth_confirmed": {
            "late_trades": 8,
            "late_net_pnl_inr": -3309.74,
            "late_wins": 1,
            "late_trail_activations": 1,
            "winner_preservation_if_late_entries_removed_pct": 95.24,
            "activation_preservation_if_late_entries_removed_pct": 96.00,
        },
    },
    "decision": "CLOSE_EARLY_EXIT_BRANCH_VALIDATE_SINGLE_LATE_ENTRY_RISK_CANDIDATE",
    "next_step": (
        "Freeze exactly one development-derived 14:30 late-entry risk candidate "
        "and validate it on an untouched June 2026 F5 holdout. Do not search "
        "additional time cutoffs on Jul-Sep or June."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_lack_of_progress_exit_promoted": True,
        "do_not_search_more_pretrail_exits_on_jul_sep": True,
        "late_1430_cutoff_is_posthoc_development_derived": True,
        "no_more_time_cutoff_search_on_jul_sep": True,
        "june_holdout_must_not_retune_cutoff": True,
        "keep_existing_f5_post_activation_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
