"""Recorded Jul-Sep 2026 conventional regime-combination findings."""

STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "bffe04030bcba47093fffc22c13734885ed0892842f40edd3409d63be264b2e6"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "selected_sessions": 65,
        "baseline_trades": 443,
    },
    "baseline": {
        "trades": 443,
        "win_rate_pct": 26.86,
        "net_pnl_inr": -29092.87,
        "profit_factor": 0.8056,
        "max_drawdown_inr": 49133.99,
        "trail_activation_rate_pct": 29.12,
    },
    "candidates": {
        "ATR_ACTIVE": {
            "trades": 151,
            "net_pnl_inr": -14661.22,
            "profit_factor": 0.7550,
            "max_drawdown_inr": 20466.22,
            "trail_activation_rate_pct": 35.76,
            "baseline_activated_signal_capture_pct": 38.76,
            "baseline_winner_signal_capture_pct": 32.77,
            "net_delta_vs_baseline_inr": 14431.65,
            "passed": False,
        },
        "BB_ACTIVE": {
            "trades": 237,
            "net_pnl_inr": -11844.90,
            "profit_factor": 0.8583,
            "max_drawdown_inr": 22310.41,
            "trail_activation_rate_pct": 36.71,
            "baseline_activated_signal_capture_pct": 62.79,
            "baseline_winner_signal_capture_pct": 56.30,
            "net_delta_vs_baseline_inr": 17247.97,
            "passed": False,
        },
        "ATR_BB_ACTIVE": {
            "trades": 117,
            "net_pnl_inr": -16277.50,
            "profit_factor": 0.6769,
            "max_drawdown_inr": 18668.68,
            "trail_activation_rate_pct": 39.32,
            "baseline_activated_signal_capture_pct": 34.11,
            "baseline_winner_signal_capture_pct": 26.89,
            "net_delta_vs_baseline_inr": 12815.37,
            "passed": False,
        },
        "ATR_BB_STOCH80": {
            "trades": 114,
            "net_pnl_inr": -14324.67,
            "profit_factor": 0.7042,
            "max_drawdown_inr": 17725.08,
            "trail_activation_rate_pct": 40.35,
            "baseline_activated_signal_capture_pct": 34.11,
            "baseline_winner_signal_capture_pct": 26.89,
            "net_delta_vs_baseline_inr": 14768.20,
            "passed": False,
        },
        "ATR_BB_HIST015": {
            "trades": 80,
            "net_pnl_inr": -2976.60,
            "profit_factor": 0.9191,
            "max_drawdown_inr": 13363.70,
            "trail_activation_rate_pct": 43.75,
            "baseline_activated_signal_capture_pct": 25.58,
            "baseline_winner_signal_capture_pct": 19.33,
            "net_delta_vs_baseline_inr": 26116.27,
            "passed": False,
        },
        "ATR_BB_STOCH80_HIST015": {
            "trades": 78,
            "net_pnl_inr": -1460.05,
            "profit_factor": 0.9586,
            "max_drawdown_inr": 11847.15,
            "trail_activation_rate_pct": 44.87,
            "baseline_activated_signal_capture_pct": 25.58,
            "baseline_winner_signal_capture_pct": 19.33,
            "net_delta_vs_baseline_inr": 27632.82,
            "passed": False,
        },
    },
    "interpretation": (
        "All six predeclared conventional-regime filters improved pooled economics "
        "and trail activation rate relative to baseline, and all reduced drawdown. "
        "However, every candidate failed the frozen requirement to preserve at least "
        "65% of the baseline activated signals and 65% of baseline winner signals. "
        "The strictest four-indicator combination approached breakeven but retained "
        "only about one quarter of original activated signals and one fifth of "
        "original winners. This indicates that hard entry gating improves average "
        "setup quality by changing the trade path substantially rather than simply "
        "removing only bad baseline entries."
    ),
    "decision": "NO_CONVENTIONAL_REGIME_COMBINATION_PASSED_DEVELOPMENT_SCREEN",
    "next_step": (
        "Do not retune ATR, Bollinger, Stochastic, or histogram thresholds on "
        "Jul-Sep. If continuing F5 research, evaluate whether a continuous "
        "quality score/ranking using the established indicator families can "
        "separate bad setups under grouped out-of-sample validation without "
        "hard-filtering away most baseline winners."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "no_threshold_retuning_on_jul_sep": True,
        "no_candidate_selected_posthoc": True,
        "keep_existing_post_activation_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
