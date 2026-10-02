"""Recorded Jul-Sep 2026 lagged institutional OI findings."""

STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "7449ffd06ad28f41b919f8411c000dce868a40df8bd2b94e67ecf6db5321a53e"
        ),
        "f5_market_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "f5_backtest_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "institutional_market_sha256": (
            "ce8f035bd95e189ce63d5262a84d3af519f9cd6d9b3f037ae7af136738e4bef4"
        ),
        "sessions": 65,
        "sessions_with_lagged_context": 65,
        "matched_trades": 443,
    },
    "position_state_coverage": {
        "FII": {
            "all_sessions_state": "NET_SHORT",
            "interpretation": (
                "Prior-session FII index-futures net position was NET_SHORT for "
                "the entire Jul-Sep sample, so the absolute state has no "
                "discriminating power in this development window."
            ),
        },
        "DII": {
            "all_sessions_state": "NET_LONG",
            "interpretation": (
                "Prior-session DII index-futures net position was NET_LONG for "
                "the entire Jul-Sep sample, so the absolute state also has no "
                "discriminating power in this development window."
            ),
        },
    },
    "bearish_regime": {
        "PE_all": {
            "trades": 84,
            "win_rate_pct": 34.52,
            "trail_activation_rate_pct": 40.48,
            "net_pnl_inr": 27242.21,
            "average_net_pnl_inr": 324.31,
        },
        "PE_prior_FII_more_short": {
            "trades": 64,
            "win_rate_pct": 34.38,
            "trail_activation_rate_pct": 39.06,
            "net_pnl_inr": 5614.46,
            "average_net_pnl_inr": 87.73,
        },
        "PE_prior_FII_not_more_short": {
            "trades": 20,
            "win_rate_pct": 35.00,
            "trail_activation_rate_pct": 45.00,
            "net_pnl_inr": 21627.75,
            "average_net_pnl_inr": 1081.39,
        },
        "CE_prior_FII_more_short": {
            "trades": 34,
            "win_rate_pct": 14.71,
            "trail_activation_rate_pct": 23.53,
            "net_pnl_inr": -8176.96,
            "average_net_pnl_inr": -240.50,
        },
        "CE_prior_FII_not_more_short": {
            "trades": 12,
            "win_rate_pct": 33.33,
            "trail_activation_rate_pct": 41.67,
            "net_pnl_inr": -1809.56,
            "average_net_pnl_inr": -150.80,
        },
        "interpretation": (
            "Increasing prior-session FII index-futures shorts does not improve "
            "bearish PE outcomes. The opposite subset produced much more PnL and "
            "higher activation. FII MORE_SHORT does coincide with especially "
            "poor bearish-regime CE performance, but CE is already structurally "
            "weak in the bearish regime and this is not promoted as an extra "
            "filter."
        ),
    },
    "bearish_pe_fii_more_short_by_month": {
        "2026-07": {
            "trades": 11,
            "net_pnl_inr": -1351.83,
            "trail_activation_rate_pct": 45.45,
        },
        "2026-08": {
            "trades": 34,
            "net_pnl_inr": 1791.72,
            "trail_activation_rate_pct": 32.35,
        },
        "2026-09": {
            "trades": 19,
            "net_pnl_inr": 5174.57,
            "trail_activation_rate_pct": 47.37,
        },
    },
    "dii_change_discovery": {
        "bearish_PE_prior_DII_more_long": {
            "trades": 39,
            "net_pnl_inr": 19758.64,
            "average_net_pnl_inr": 506.63,
            "trail_activation_rate_pct": 41.03,
        },
        "bearish_PE_prior_DII_more_short": {
            "trades": 45,
            "net_pnl_inr": 7483.57,
            "average_net_pnl_inr": 166.30,
            "trail_activation_rate_pct": 40.00,
        },
        "more_long_monthly_net_pnl_inr": {
            "2026-07": 16871.80,
            "2026-08": 1348.94,
            "2026-09": 1537.90,
        },
        "interpretation": (
            "Prior DII net-futures change is the only institutional-OI clue worth "
            "retaining for later holdout/composite research. Bearish PE remained "
            "positive when DII became more net-long in each month, but the PnL is "
            "highly concentrated in July and the relationship is not used as a "
            "standalone rule."
        ),
    },
    "bullish_regime": {
        "CE_all": {
            "trades": 82,
            "win_rate_pct": 32.93,
            "trail_activation_rate_pct": 29.27,
            "net_pnl_inr": -1711.49,
        },
        "CE_prior_FII_more_long": {
            "trades": 50,
            "win_rate_pct": 32.00,
            "trail_activation_rate_pct": 28.00,
            "net_pnl_inr": -1147.07,
        },
        "interpretation": (
            "Prior FII movement toward more-long did not rescue bullish CE "
            "economics. There were no prior FII NET_LONG sessions in this window."
        ),
    },
    "option_position_proxy": {
        "interpretation": (
            "The participant option directional-balance field remains descriptive "
            "only. Its sign did not vary in this sample and it is not delta/gamma."
        ),
    },
    "decision": (
        "NO_INSTITUTIONAL_OI_RULE_PROMOTED_CONTINUE_TO_LAGGED_CASH_FLOW"
    ),
    "next_step": (
        "Complete the institutional layer with prior-session FII/FPI and DII "
        "cash-market net activity. Keep it lagged one session. Test cash-flow sign "
        "and FII-vs-DII offsetting without magnitude thresholds or combining with "
        "futures state/breadth yet."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_institutional_oi_filter_promoted": True,
        "no_position_magnitude_threshold_search": True,
        "no_composite_score": True,
        "cash_flow_must_be_lagged_one_session": True,
        "no_same_day_report_backfill": True,
        "keep_existing_f5_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
