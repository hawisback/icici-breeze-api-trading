"""Recorded Jul-Sep 2026 NIFTY futures-confirmation findings."""

STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "302bd4e05107e392bfb8d143d8ac945d87e2f1f08be526cd6aa6d51730865387"
        ),
        "f5_market_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "f5_backtest_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "futures_market_sha256": (
            "1395e1d565b0f74af9cfb1195027be30f8d70ad39826dce314333030a07d803b"
        ),
        "sessions": 65,
        "sessions_with_oi": 65,
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
        "PE_short_buildup": {
            "trades": 72,
            "win_rate_pct": 34.72,
            "trail_activation_rate_pct": 38.89,
            "net_pnl_inr": 22350.13,
            "average_net_pnl_inr": 310.42,
        },
        "PE_non_short_buildup": {
            "trades": 12,
            "win_rate_pct": 33.33,
            "trail_activation_rate_pct": 50.00,
            "net_pnl_inr": 4892.08,
            "average_net_pnl_inr": 407.67,
        },
        "CE_short_buildup": {
            "trades": 40,
            "win_rate_pct": 17.50,
            "trail_activation_rate_pct": 27.50,
            "net_pnl_inr": -9241.50,
        },
        "interpretation": (
            "SHORT_BUILDUP does not add a clean independent confirmation edge to "
            "the already-established bearish NIFTY regime. Bearish PE remains "
            "profitable both with and without short buildup; the short-buildup "
            "subset has slightly lower activation and average PnL than the "
            "non-short-buildup subset. Futures short buildup largely overlaps with "
            "the same bearish session structure rather than sharpening it."
        ),
    },
    "bearish_pe_short_buildup_by_month": {
        "2026-07": {
            "trades": 14,
            "activation_rate_pct": 50.00,
            "net_pnl_inr": 15492.87,
        },
        "2026-08": {
            "trades": 41,
            "activation_rate_pct": 31.71,
            "net_pnl_inr": 3516.50,
        },
        "2026-09": {
            "trades": 17,
            "activation_rate_pct": 47.06,
            "net_pnl_inr": 3340.76,
        },
    },
    "bullish_regime": {
        "CE_all": {
            "trades": 82,
            "win_rate_pct": 32.93,
            "trail_activation_rate_pct": 29.27,
            "net_pnl_inr": -1711.49,
        },
        "CE_long_buildup": {
            "trades": 18,
            "win_rate_pct": 38.89,
            "trail_activation_rate_pct": 33.33,
            "net_pnl_inr": 1571.63,
        },
        "CE_non_long_buildup": {
            "trades": 64,
            "win_rate_pct": 31.25,
            "trail_activation_rate_pct": 28.12,
            "net_pnl_inr": -3283.12,
        },
        "warning": (
            "The bullish LONG_BUILDUP clue is not month-stable: July long-buildup "
            "CE was negative, August had no long-buildup CE sample, and September "
            "had only four such CE trades. Do not promote it."
        ),
    },
    "interpretation": (
        "Front-month NIFTY futures price/OI state is useful context but did not "
        "demonstrate incremental confirmation value beyond the dominant NIFTY "
        "regime for bearish PE. Continue to the next independent context layer "
        "rather than stacking a redundant futures-state gate."
    ),
    "decision": "NO_FUTURES_STATE_RULE_PROMOTED_ADVANCE_TO_BREADTH",
    "next_step": (
        "Measure NIFTY breadth and leadership as an independent context layer. "
        "Test whether bearish regimes with broad constituent participation are "
        "cleaner PE environments and whether countertrend CE is especially weak. "
        "Do not combine futures state into the breadth test."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_futures_state_filter_promoted": True,
        "no_oi_threshold_search": True,
        "no_basis_threshold_search": True,
        "no_volume_threshold_search": True,
        "keep_futures_layer_standalone": True,
        "keep_existing_f5_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
