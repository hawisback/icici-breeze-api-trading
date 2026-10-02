"""Recorded Jul-Sep 2026 lagged institutional cash-flow findings."""

STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "02b9862f59ac79d633a658080e222f75d305f31607e22d87cc9be898f924810e"
        ),
        "f5_market_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "f5_backtest_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "cash_market_sha256": (
            "1037bddb17029c21bc0578d5b566a9c3680a78c1b909984c5a0d7fa11e4b19cb"
        ),
        "sessions": 65,
        "sessions_with_lagged_context": 65,
        "matched_trades": 443,
        "source_precision": "DISPLAY_ROUNDED_SIGN_ONLY",
    },
    "bearish_regime": {
        "PE_all": {
            "trades": 84,
            "win_rate_pct": 34.52,
            "trail_activation_rate_pct": 40.48,
            "net_pnl_inr": 27242.21,
            "average_net_pnl_inr": 324.31,
        },
        "PE_prior_FII_selling": {
            "trades": 40,
            "win_rate_pct": 37.50,
            "trail_activation_rate_pct": 45.00,
            "net_pnl_inr": 3679.50,
            "average_net_pnl_inr": 91.99,
        },
        "PE_prior_FII_not_selling": {
            "trades": 44,
            "win_rate_pct": 31.82,
            "trail_activation_rate_pct": 36.36,
            "net_pnl_inr": 23562.71,
            "average_net_pnl_inr": 535.52,
        },
        "PE_prior_FII_sell_DII_buy": {
            "trades": 40,
            "win_rate_pct": 37.50,
            "trail_activation_rate_pct": 45.00,
            "net_pnl_inr": 3679.50,
            "average_net_pnl_inr": 91.99,
        },
        "CE_all": {
            "trades": 46,
            "win_rate_pct": 19.57,
            "trail_activation_rate_pct": 28.26,
            "net_pnl_inr": -9986.52,
            "average_net_pnl_inr": -217.10,
        },
        "CE_prior_FII_selling": {
            "trades": 18,
            "net_pnl_inr": -4638.58,
            "average_net_pnl_inr": -257.70,
        },
        "CE_prior_FII_not_selling": {
            "trades": 28,
            "net_pnl_inr": -5347.94,
            "average_net_pnl_inr": -191.00,
        },
        "interpretation": (
            "Prior-session FII cash selling does not improve bearish-PE economics. "
            "The not-selling subset carries most of the aggregate bearish-PE PnL, "
            "while the FII_SELL_DII_BUY subset is identical to FII selling for the "
            "bearish-PE sample and therefore adds no extra discrimination. Bearish "
            "CE remains poor on both sides of the cash-flow split."
        ),
    },
    "bearish_pe_by_month": {
        "2026-07": {
            "all_net_pnl_inr": 15546.54,
            "FII_selling": {
                "trades": 12,
                "net_pnl_inr": -1271.59,
                "average_net_pnl_inr": -105.97,
                "trail_activation_rate_pct": 50.00,
            },
            "FII_not_selling": {
                "trades": 7,
                "net_pnl_inr": 16818.13,
                "average_net_pnl_inr": 2402.59,
                "trail_activation_rate_pct": 42.86,
            },
        },
        "2026-08": {
            "all_net_pnl_inr": 3516.50,
            "FII_selling": {
                "trades": 12,
                "net_pnl_inr": -2170.44,
                "average_net_pnl_inr": -180.87,
                "trail_activation_rate_pct": 25.00,
            },
            "FII_not_selling": {
                "trades": 29,
                "net_pnl_inr": 5686.94,
                "average_net_pnl_inr": 196.10,
                "trail_activation_rate_pct": 34.48,
            },
        },
        "2026-09": {
            "all_net_pnl_inr": 8179.17,
            "FII_selling": {
                "trades": 16,
                "net_pnl_inr": 7121.53,
                "average_net_pnl_inr": 445.10,
                "trail_activation_rate_pct": 56.25,
            },
            "FII_not_selling": {
                "trades": 8,
                "net_pnl_inr": 1057.64,
                "average_net_pnl_inr": 132.21,
                "trail_activation_rate_pct": 37.50,
            },
        },
        "interpretation": (
            "The aggregate advantage for FII-not-selling is not month-stable: "
            "July and August favor not-selling, while September reverses strongly "
            "toward FII selling. No cash-flow sign gate is promoted."
        ),
    },
    "bullish_regime": {
        "CE_all": {
            "trades": 82,
            "win_rate_pct": 32.93,
            "trail_activation_rate_pct": 29.27,
            "net_pnl_inr": -1711.49,
            "average_net_pnl_inr": -20.87,
        },
        "CE_prior_FII_buying": {
            "trades": 36,
            "win_rate_pct": 30.56,
            "trail_activation_rate_pct": 27.78,
            "net_pnl_inr": -5595.13,
            "average_net_pnl_inr": -155.42,
        },
        "CE_prior_FII_not_buying": {
            "trades": 46,
            "win_rate_pct": 34.78,
            "trail_activation_rate_pct": 30.43,
            "net_pnl_inr": 3883.64,
            "average_net_pnl_inr": 84.43,
        },
        "interpretation": (
            "Prior FII cash buying does not rescue bullish CE performance and is "
            "worse in aggregate than the not-buying subset. Monthly sample sizes "
            "are uneven, so no bullish cash-flow rule is promoted."
        ),
    },
    "source_limitation": (
        "The archived cash input is a display-rounded mirror of provisional "
        "combined FII/FPI and DII activity. This pass intentionally uses sign only "
        "and does not support magnitude thresholds."
    ),
    "decision": (
        "NO_INSTITUTIONAL_CASH_RULE_PROMOTED_FREEZE_CONTEXT_DISCOVERY"
    ),
    "next_step": (
        "Freeze Jul-Sep context discovery rather than open another in-sample "
        "feature search. Preserve dominant-NIFTY regime as the primary context, "
        "retain breadth and DII-OI observations as descriptive research clues, "
        "and move future rule promotion to an out-of-sample holdout protocol."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_institutional_cash_filter_promoted": True,
        "no_cash_magnitude_threshold_search": True,
        "no_new_in_sample_composite_search": True,
        "keep_existing_f5_entry_exit_trail_unchanged": True,
        "strategy_d_remains_paused": True,
    },
}
