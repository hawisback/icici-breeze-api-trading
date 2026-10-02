"""Recorded corrected Jul-Sep 2026 NIFTY 50 breadth findings."""

STRATEGY_F5_NIFTY50_BREADTH_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_NIFTY50_BREADTH_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_NIFTY50_BREADTH_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "a164d7ec2ce3368dba452fd038d00837c6934b38eb9955b9af7ff840349ff18f"
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
        "spot_sessions": 65,
        "breadth_available_sessions": 60,
        "directional_regime_days": 41,
        "breadth_available_directional_days": 38,
        "breadth_unavailable_directional_days": 3,
        "breadth_confirmed_directional_days": 29,
        "matched_trades": 443,
    },
    "bearish_regime": {
        "PE_all": {
            "trades": 84,
            "win_rate_pct": 34.52,
            "trail_activation_rate_pct": 40.48,
            "net_pnl_inr": 27242.21,
            "average_net_pnl_inr": 324.31,
        },
        "PE_breadth_confirmed": {
            "trades": 64,
            "win_rate_pct": 32.81,
            "trail_activation_rate_pct": 39.06,
            "net_pnl_inr": 21077.72,
            "average_net_pnl_inr": 329.34,
        },
        "PE_breadth_available_not_confirmed": {
            "trades": 13,
            "win_rate_pct": 46.15,
            "trail_activation_rate_pct": 46.15,
            "net_pnl_inr": 159.19,
            "average_net_pnl_inr": 12.25,
        },
        "PE_breadth_unavailable": {
            "trades": 7,
            "win_rate_pct": 28.57,
            "trail_activation_rate_pct": 42.86,
            "net_pnl_inr": 6005.30,
            "average_net_pnl_inr": 857.90,
        },
        "CE_breadth_confirmed": {
            "trades": 34,
            "win_rate_pct": 23.53,
            "trail_activation_rate_pct": 26.47,
            "net_pnl_inr": -6238.01,
        },
        "CE_breadth_available_not_confirmed": {
            "trades": 7,
            "win_rate_pct": 0.0,
            "trail_activation_rate_pct": 0.0,
            "net_pnl_inr": -3422.34,
        },
    },
    "bearish_pe_breadth_confirmed_by_month": {
        "2026-07": {
            "trades": 16,
            "net_pnl_inr": 15963.22,
            "activation_rate_pct": 43.75,
        },
        "2026-08": {
            "trades": 27,
            "net_pnl_inr": -3071.24,
            "activation_rate_pct": 29.63,
        },
        "2026-09": {
            "trades": 21,
            "net_pnl_inr": 8185.74,
            "activation_rate_pct": 47.62,
        },
    },
    "bullish_regime": {
        "CE_all": {
            "trades": 82,
            "net_pnl_inr": -1711.49,
        },
        "CE_breadth_confirmed": {
            "trades": 60,
            "net_pnl_inr": 295.05,
        },
        "CE_breadth_available_not_confirmed": {
            "trades": 20,
            "net_pnl_inr": -1693.09,
        },
        "interpretation": (
            "Bullish breadth improves the aggregate CE subset, but the effect is "
            "not month-stable and remains too weak for rule promotion."
        ),
    },
    "interpretation": (
        "Equal-weight NIFTY 50 breadth is useful descriptive context. When breadth "
        "is available, almost all bearish-regime PE profit is concentrated in "
        "breadth-confirmed sessions, while available breadth disagreement is near "
        "flat overall. However, the breadth-confirmed bearish-PE subset is negative "
        "in August, so the effect is not month-stable. Bearish CE remains poor "
        "regardless of breadth agreement. Breadth therefore should not be promoted "
        "as a standalone gate."
    ),
    "decision": "NO_BREADTH_RULE_PROMOTED_ADVANCE_TO_LAGGED_INSTITUTIONAL_CONTEXT",
    "next_step": (
        "Test prior-session FII/FPI and DII cash net activity plus prior-session "
        "participant derivatives positioning as a lagged institutional context "
        "layer. Do not combine futures-state or breadth gates into this study."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_breadth_filter_promoted": True,
        "no_breadth_threshold_search": True,
        "missing_breadth_separate_from_disagreement": True,
        "keep_breadth_layer_standalone": True,
        "keep_existing_f5_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
