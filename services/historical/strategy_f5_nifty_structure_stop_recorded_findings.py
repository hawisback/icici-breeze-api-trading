"""Recorded Jul-Sep 2026 F5 NIFTY structure-stop findings."""

STRATEGY_F5_NIFTY_STRUCTURE_STOP_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_NIFTY_STRUCTURE_STOP_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_NIFTY_STRUCTURE_STOP_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "453b241d47952e646f1b8aea9c55e7599e470ab53bb02fb072cb8203c06ec96a"
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
        "CONFIRMED_SWING_HIGH_2X2": {
            "level_available_trades": 75,
            "breaches": 5,
            "breached_baseline_winners": 2,
            "breached_baseline_activated": 3,
            "winner_untouched_pct": 93.10,
            "activated_untouched_pct": 91.18,
            "net_delta_vs_baseline_zero_slippage_inr": -1509.12,
            "monthly_delta_zero_slippage_inr": {
                "2026-07": 87.63,
                "2026-08": -1596.75,
                "2026-09": 0.0,
            },
            "passed": False,
        },
        "RECENT_30M_HIGH": {
            "level_available_trades": 82,
            "breaches": 1,
            "breached_baseline_winners": 1,
            "breached_baseline_activated": 1,
            "winner_untouched_pct": 96.55,
            "activated_untouched_pct": 97.06,
            "net_delta_vs_baseline_zero_slippage_inr": -1554.55,
            "passed": False,
        },
        "SESSION_HIGH_TO_ENTRY": {
            "level_available_trades": 82,
            "breaches": 0,
            "winner_untouched_pct": 100.0,
            "activated_untouched_pct": 100.0,
            "net_delta_vs_baseline_zero_slippage_inr": 0.0,
            "passed": False,
        },
    },
    "interpretation": (
        "Pre-entry NIFTY resistance was either too rarely breached before the "
        "baseline trade resolved or cut recovery trades that later won/activated. "
        "The latest confirmed swing-high rule was the only candidate with enough "
        "breaches, but it reduced pooled P&L and failed winner/activation "
        "preservation. NIFTY resistance is therefore not promoted as the F5 "
        "stop-loss boundary."
    ),
    "decision": "NO_NIFTY_RESISTANCE_STOP_PROMOTED_MOVE_TO_OPTION_SUPPORT_STRUCTURE",
    "next_step": (
        "Test pre-entry support on the traded PE premium itself using a small "
        "frozen family: signal-bar low, recent 10-minute low, and latest "
        "confirmed 2-minute swing low. Do not tune NIFTY resistance buffers, "
        "pivot widths, or confirmation counts."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_nifty_resistance_stop_promoted": True,
        "no_nifty_resistance_retuning": True,
        "move_to_option_support_structure": True,
        "strategy_d_remains_paused": True,
    },
}
