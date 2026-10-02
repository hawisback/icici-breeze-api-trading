"""Recorded Sep 21-25 2026 NIFTY-direction diagnostic findings."""

STRATEGY_F5_NIFTY_DIRECTION_LAST_WEEK_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_NIFTY_DIRECTION_LAST_WEEK_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_NIFTY_DIRECTION_LAST_WEEK_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "2f434bfd279ffbf3e07f8e11ff19c5aaa93a613a9c7bcbf94a924865789454d7"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "window": ["2026-09-21", "2026-09-25"],
        "trades": 36,
        "entry_time_direction_available_trades": 35,
    },
    "full_day_alignment": {
        "aligned": {
            "trades": 24,
            "win_rate_pct": 41.67,
            "trail_activation_rate_pct": 37.50,
            "net_pnl_inr": 626.61,
        },
        "counter_direction": {
            "trades": 12,
            "win_rate_pct": 16.67,
            "trail_activation_rate_pct": 16.67,
            "net_pnl_inr": -3207.90,
        },
        "warning": "DESCRIPTIVE_ONLY_USES_END_OF_DAY_INFORMATION",
    },
    "entry_time_alignment": {
        "aligned": {
            "trades": 26,
            "win_rate_pct": 38.46,
            "trail_activation_rate_pct": 30.77,
            "net_pnl_inr": -647.84,
        },
        "counter_direction": {
            "trades": 9,
            "win_rate_pct": 22.22,
            "trail_activation_rate_pct": 22.22,
            "net_pnl_inr": -1820.95,
        },
        "unclassified": {
            "trades": 1,
            "net_pnl_inr": -112.50,
        },
    },
    "entry_time_direction_by_side": {
        "BULLISH_CE": {
            "trades": 17,
            "win_rate_pct": 29.41,
            "trail_activation_rate_pct": 17.65,
            "net_pnl_inr": -3375.74,
        },
        "BULLISH_PE": {
            "trades": 8,
            "win_rate_pct": 25.00,
            "trail_activation_rate_pct": 25.00,
            "net_pnl_inr": -1303.64,
        },
        "BEARISH_CE": {
            "trades": 1,
            "win_rate_pct": 0.00,
            "trail_activation_rate_pct": 0.00,
            "net_pnl_inr": -517.31,
        },
        "BEARISH_PE": {
            "trades": 9,
            "win_rate_pct": 55.56,
            "trail_activation_rate_pct": 55.56,
            "net_pnl_inr": 2727.90,
        },
    },
    "full_day_direction_by_side": {
        "BULLISH_CE": {
            "trades": 15,
            "win_rate_pct": 33.33,
            "trail_activation_rate_pct": 20.00,
            "net_pnl_inr": -2320.07,
        },
        "BULLISH_PE": {
            "trades": 9,
            "win_rate_pct": 22.22,
            "trail_activation_rate_pct": 22.22,
            "net_pnl_inr": -1634.92,
        },
        "BEARISH_CE": {
            "trades": 3,
            "win_rate_pct": 0.00,
            "trail_activation_rate_pct": 0.00,
            "net_pnl_inr": -1572.98,
        },
        "BEARISH_PE": {
            "trades": 9,
            "win_rate_pct": 55.56,
            "trail_activation_rate_pct": 66.67,
            "net_pnl_inr": 2946.68,
        },
    },
    "interpretation": (
        "Direction alignment materially improved trade quality over this five-session "
        "sample. The strongest effect was bearish NIFTY plus PE: both full-day and "
        "entry-time direction produced positive PE PnL and high activation. Bullish "
        "NIFTY plus CE was not profitable, so the data does not support a symmetric "
        "rule that simply maps bullish to CE and bearish to PE. Counter-direction "
        "trades were poor overall. Because this is only one week, no directional "
        "entry filter is promoted."
    ),
    "decision": "EXPAND_ENTRY_TIME_DIRECTION_DIAGNOSTIC_BEFORE_ANY_FILTER",
    "next_step": (
        "Apply the same no-lookahead entry-time NIFTY direction labeling to the "
        "full Jul-Sep development sample, without changing any F5 trades. Evaluate "
        "CE/PE separately by month and direction. Do not introduce direction "
        "magnitude thresholds yet."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "do_not_use_full_day_direction_as_entry_filter": True,
        "no_direction_threshold_search": True,
        "no_side_filter_from_one_week": True,
        "keep_existing_post_activation_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
