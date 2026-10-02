"""Recorded Jul-Sep 2026 dominant-NIFTY-regime findings."""

STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "fa01c0b396fd5cf1b9ee93d33b56d3502a564e3ecd6f6f867765dfebeb9c89b2"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "sessions": 65,
        "trades": 443,
        "trades_missing_regime": 0,
    },
    "overall": {
        "regime_aligned": {
            "trades": 166,
            "win_rate_pct": 33.73,
            "trail_activation_rate_pct": 34.94,
            "net_pnl_inr": 25530.72,
        },
        "counter_regime": {
            "trades": 104,
            "win_rate_pct": 20.19,
            "trail_activation_rate_pct": 25.00,
            "net_pnl_inr": -24799.95,
        },
        "mixed_regime": {
            "trades": 173,
            "win_rate_pct": 24.28,
            "trail_activation_rate_pct": 26.01,
            "net_pnl_inr": -29823.64,
        },
    },
    "bearish_regime": {
        "days": 22,
        "CE": {
            "trades": 46,
            "win_rate_pct": 19.57,
            "trail_activation_rate_pct": 28.26,
            "net_pnl_inr": -9986.52,
        },
        "PE": {
            "trades": 84,
            "win_rate_pct": 34.52,
            "trail_activation_rate_pct": 40.48,
            "net_pnl_inr": 27242.21,
        },
        "days_pe_net_above_ce": 17,
        "days_ce_zero_trail_activations": 15,
        "days_pe_activation_rate_above_ce": 11,
    },
    "bearish_regime_by_month": {
        "2026-07": {
            "CE_net_pnl_inr": -924.93,
            "CE_activation_rate_pct": 33.33,
            "PE_net_pnl_inr": 15546.54,
            "PE_activation_rate_pct": 47.37,
        },
        "2026-08": {
            "CE_net_pnl_inr": -5477.94,
            "CE_activation_rate_pct": 30.77,
            "PE_net_pnl_inr": 3516.50,
            "PE_activation_rate_pct": 31.71,
        },
        "2026-09": {
            "CE_net_pnl_inr": -3583.65,
            "CE_activation_rate_pct": 18.18,
            "PE_net_pnl_inr": 8179.17,
            "PE_activation_rate_pct": 50.00,
        },
    },
    "bullish_regime": {
        "days": 19,
        "CE": {
            "trades": 82,
            "win_rate_pct": 32.93,
            "trail_activation_rate_pct": 29.27,
            "net_pnl_inr": -1711.49,
        },
        "PE": {
            "trades": 58,
            "win_rate_pct": 20.69,
            "trail_activation_rate_pct": 22.41,
            "net_pnl_inr": -14813.43,
        },
        "interpretation": (
            "Bullish CE is materially better than bullish PE, but remains slightly "
            "loss-making overall; do not infer symmetric bullish-CE validation."
        ),
    },
    "interpretation": (
        "The bearish dominant-regime effect generalizes across the full Jul-Sep "
        "development sample and all three months. Bearish-regime PE is positive "
        "while bearish-regime CE is negative in every month. Overall regime "
        "alignment is strongly better than counter-regime and mixed trades. This "
        "is still a hindsight whole-day classification, so it establishes market "
        "structure rather than a tradable filter."
    ),
    "decision": "ADVANCE_TO_NIFTY_FUTURES_CONFIRMATION",
    "next_step": (
        "Test front-month NIFTY futures price, open interest, volume and basis "
        "as an independent confirmation layer. Focus first on classic futures "
        "price/OI states, especially SHORT_BUILDUP on bearish NIFTY days. Do not "
        "search numeric magnitude thresholds."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "whole_day_regime_is_descriptive_only": True,
        "no_same_day_filter_from_whole_day_label": True,
        "no_regime_threshold_tuning": True,
        "no_side_filter_promoted_yet": True,
        "keep_existing_f5_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
