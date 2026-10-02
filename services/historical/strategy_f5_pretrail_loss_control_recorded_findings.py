"""Recorded Jul-Sep 2026 F5 pre-trail fixed-stop findings."""

STRATEGY_F5_PRETRAIL_LOSS_CONTROL_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_PRETRAIL_LOSS_CONTROL_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_PRETRAIL_LOSS_CONTROL_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "diagnostic_artifact_sha256": (
            "bb53ae8493786ec98a73ad438343b3357a1bae1b99bc3cac419a966fcdbb2264"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "matched_trades": 443,
    },
    "baseline": {
        "net_pnl_inr": -29092.87,
        "profit_factor": 0.8056,
        "max_drawdown_inr": 49133.99,
        "trail_activation_count": 129,
    },
    "mae_close_return_pct": {
        "trail_activated": {
            "p05": -15.6709,
            "p10": -10.2811,
            "p25": -3.6320,
            "p50": -0.2620,
        },
        "baseline_winners": {
            "p05": -13.1416,
            "p10": -9.8328,
            "p25": -3.5105,
            "p50": -0.6693,
        },
        "nonactivated": {
            "p05": -22.0380,
            "p10": -16.5509,
            "p25": -9.4325,
            "p50": -5.2002,
        },
    },
    "fixed_stop_candidates": {
        "5": {
            "stops_triggered": 194,
            "activated_trade_untouched_pct": 81.40,
            "baseline_winner_untouched_pct": 83.19,
            "net_delta_vs_baseline_inr": -7503.28,
        },
        "7.5": {
            "stops_triggered": 121,
            "activated_trade_untouched_pct": 86.82,
            "baseline_winner_untouched_pct": 88.24,
            "net_delta_vs_baseline_inr": -12965.33,
        },
        "10": {
            "stops_triggered": 84,
            "activated_trade_untouched_pct": 88.37,
            "baseline_winner_untouched_pct": 89.92,
            "net_delta_vs_baseline_inr": -17577.08,
        },
        "12.5": {
            "stops_triggered": 61,
            "activated_trade_untouched_pct": 91.47,
            "baseline_winner_untouched_pct": 93.28,
            "net_delta_vs_baseline_inr": -10161.36,
        },
        "15": {
            "stops_triggered": 45,
            "activated_trade_untouched_pct": 94.57,
            "baseline_winner_untouched_pct": 96.64,
            "net_delta_vs_baseline_inr": -7737.05,
        },
        "20": {
            "stops_triggered": 26,
            "activated_trade_untouched_pct": 95.35,
            "baseline_winner_untouched_pct": 96.64,
            "net_delta_vs_baseline_inr": -6529.75,
            "monthly_net_delta_zero_slippage_inr": {
                "2026-07": -2963.05,
                "2026-08": 77.89,
                "2026-09": -3644.59,
            },
        },
    },
    "interpretation": (
        "No fixed pre-activation percentage stop improves F5 while preserving "
        "the desired winners. Tight stops remove too many future +10% swing "
        "trades. Even the 20% stop meets the 95% preservation requirement but "
        "still worsens pooled PnL and worsens July and September. The overlap "
        "in adverse excursion between winners and losers means price drawdown "
        "alone does not cleanly distinguish failed setups."
    ),
    "decision": "CLOSE_FIXED_PERCENT_PRETRAIL_STOP_BRANCH",
    "next_question": (
        "Diagnose pre-activation signal failure and lack-of-progress features "
        "that may exit failed setups earlier without using price drawdown alone."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "keep_existing_post_activation_trail_frozen": True,
        "do_not_search_more_fixed_percentage_stops_on_jul_sep": True,
        "do_not_use_future_plus10_activation_as_an_entry_or_exit_signal": True,
        "strategy_d_remains_paused": True,
    },
}
