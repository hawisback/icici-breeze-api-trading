"""Recorded findings for the six-session dominant-NIFTY-regime diagnostic."""

STRATEGY_F5_DOMINANT_NIFTY_REGIME_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_DOMINANT_NIFTY_REGIME_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_DOMINANT_NIFTY_REGIME_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "8477a212259ce2855a5d1871f9de1364323c9095914cb94c62c16a12599074d8"
        ),
        "market_artifact_sha256": (
            "aa5f62dedbf433d595c45c53645832b53140717dff00fb79676d5b9dc46c6646"
        ),
        "sessions": 6,
        "trades": 44,
    },
    "overall_regime_comparison": {
        "aligned": {
            "trades": 29,
            "win_rate_pct": 44.83,
            "trail_activation_rate_pct": 41.38,
            "net_pnl_inr": 11054.07,
            "average_net_pnl_inr": 381.17,
        },
        "counter_regime": {
            "trades": 15,
            "win_rate_pct": 13.33,
            "trail_activation_rate_pct": 13.33,
            "net_pnl_inr": -4893.02,
            "average_net_pnl_inr": -326.20,
        },
    },
    "bearish_regime": {
        "CE": {
            "trades": 6,
            "wins": 0,
            "win_rate_pct": 0.0,
            "trail_activation_rate_pct": 0.0,
            "net_pnl_inr": -3258.10,
        },
        "PE": {
            "trades": 14,
            "wins": 8,
            "win_rate_pct": 57.14,
            "trail_activation_rate_pct": 64.29,
            "net_pnl_inr": 13374.14,
        },
    },
    "bullish_regime": {
        "CE": {
            "trades": 15,
            "wins": 5,
            "win_rate_pct": 33.33,
            "trail_activation_rate_pct": 20.0,
            "net_pnl_inr": -2320.07,
        },
        "PE": {
            "trades": 9,
            "wins": 2,
            "win_rate_pct": 22.22,
            "trail_activation_rate_pct": 22.22,
            "net_pnl_inr": -1634.92,
        },
    },
    "october_1": {
        "regime": "BEARISH",
        "nifty_0915_to_1520_return_pct": -0.4334,
        "pct_5m_closes_below_0915_open": 61.64,
        "CE": {
            "trades": 3,
            "wins": 0,
            "trail_activation_rate_pct": 0.0,
            "net_pnl_inr": -1685.12,
        },
        "PE": {
            "trades": 5,
            "wins": 3,
            "trail_activation_rate_pct": 60.0,
            "net_pnl_inr": 10427.46,
        },
    },
    "interpretation": (
        "The six-session matched-trade diagnostic strongly supports the user's "
        "specific hypothesis on bearish dominant-regime days: countertrend CE "
        "signals were uniformly poor, while PE signals captured the meaningful "
        "moves. Across all three bearish sessions, CE was 0-for-6 with zero trail "
        "activations and -₹3,258.10, whereas PE produced +₹13,374.14 with a 64.29% "
        "activation rate. The relationship is not symmetric on bullish days: CE "
        "still lost money overall. Therefore the strongest current evidence is "
        "specifically to suppress CE countertrend setups when a bearish NIFTY "
        "regime can be established, not yet to assert a general bullish-CE / "
        "bearish-PE rule."
    ),
    "decision": "BUILD_NO_LOOKAHEAD_INTRADAY_DOMINANT_REGIME_DETECTOR",
    "next_step": (
        "Keep this whole-day classifier descriptive only. Next, measure when the "
        "same dominant NIFTY regime becomes identifiable during the session using "
        "only completed 5-minute bars, and whether the classification remains "
        "stable thereafter. Do not use future end-of-day information in the "
        "tradable version."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "whole_day_regime_is_lookahead_only": True,
        "no_same_day_filter_from_whole_day_label": True,
        "no_bullish_ce_rule_promoted": True,
        "no_side_filter_promoted_from_six_sessions": True,
        "keep_existing_f5_entry_exit_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
