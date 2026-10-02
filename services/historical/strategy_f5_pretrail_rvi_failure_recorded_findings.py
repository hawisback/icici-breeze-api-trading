"""Recorded Jul-Sep 2026 F5 pre-trail RVI-failure findings."""

STRATEGY_F5_PRETRAIL_RVI_FAILURE_RECORDED_FINDINGS_V1 = {
    "research_type": "STRATEGY_F5_PRETRAIL_RVI_FAILURE_RECORDED_FINDINGS_V1",
    "protocol_version": "STRATEGY_F5_PRETRAIL_RVI_FAILURE_EXIT_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "diagnostic_artifact_sha256": (
            "ddda6af2b8285978ac1ac964af5c8c423c531c0a83c958d9183533d363547398"
        ),
        "market_artifact_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_artifact_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "matched_trades": 443,
    },
    "rule": {
        "rvi_length": 10,
        "centerline": 50.0,
        "consecutive_completed_2m_bars_below_centerline": 2,
        "active_only_before_trail_activation": True,
    },
    "result": {
        "early_exits": 194,
        "early_exited_baseline_winners": 37,
        "early_exited_activated_trades": 33,
        "activated_trade_untouched_pct": 74.42,
        "baseline_winner_untouched_pct": 68.91,
        "net_pnl_inr": -43135.75,
        "profit_factor": 0.6934,
        "max_drawdown_inr": 56508.94,
        "net_delta_vs_baseline_inr": -14042.88,
        "monthly_net_delta_zero_slippage_inr": {
            "2026-07": -2748.88,
            "2026-08": 1333.88,
            "2026-09": -12627.88,
        },
    },
    "interpretation": (
        "Two consecutive RVI(10)<50 bars are not a clean failure signal for F5. "
        "The rule exits too many eventual winners and trail-activated trades and "
        "worsens pooled PnL materially. Combined with the failed fixed-percentage "
        "stop study, pre-activation exit tinkering on Jul-Sep is now exhausted. "
        "Further loss reduction should focus on avoiding weak entries or on fresh "
        "out-of-sample risk structure rather than adding more same-sample exits."
    ),
    "decision": "CLOSE_PRETRAIL_RVI_FAILURE_EXIT_BRANCH",
    "next_step": (
        "Keep the existing post-activation trail frozen. Do not search more "
        "pre-activation exits on Jul-Sep. Use fresh historical months to test "
        "entry-selectivity hypotheses or risk-allocation methods."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "keep_existing_post_activation_trail_frozen": True,
        "do_not_search_more_pretrail_exit_rules_on_jul_sep": True,
        "do_not_search_rvi_failure_thresholds_on_jul_sep": True,
        "do_not_search_consecutive_bar_count_on_jul_sep": True,
        "strategy_d_remains_paused": True,
    },
}
