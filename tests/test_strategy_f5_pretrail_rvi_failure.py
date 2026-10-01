from services.historical.strategy_f5_pretrail_rvi_failure_protocol import (
    GUARDRAILS,
    PRESERVATION_SCREEN,
    PROTOCOL_VERSION,
    RVI_FAILURE,
)


def test_rvi_failure_rule_is_frozen_and_nonexecution():
    assert PROTOCOL_VERSION == "STRATEGY_F5_PRETRAIL_RVI_FAILURE_EXIT_V1"
    assert RVI_FAILURE["centerline"] == 50.0
    assert RVI_FAILURE["consecutive_completed_2m_bars_below_centerline"] == 2
    assert RVI_FAILURE["active_only_before_trail_activation"] is True
    assert RVI_FAILURE["activation_precedence"] is True
    assert RVI_FAILURE["after_activation_baseline_f5_management_unchanged"] is True
    assert GUARDRAILS["live_execution"] is False
    assert GUARDRAILS["paper_execution"] is False
    assert GUARDRAILS["broker_orders"] is False


def test_rvi_failure_screen_preserves_winners():
    assert PRESERVATION_SCREEN["minimum_activated_trade_untouched_pct"] == 95.0
    assert PRESERVATION_SCREEN["minimum_baseline_winner_untouched_pct"] == 95.0
    assert PRESERVATION_SCREEN[
        "require_net_pnl_improvement_each_month_zero_slippage"
    ] is True
    assert PRESERVATION_SCREEN[
        "require_pooled_net_pnl_improvement_at_0_5_slippage"
    ] is True
    assert PRESERVATION_SCREEN[
        "require_pooled_net_pnl_improvement_at_1_0_slippage"
    ] is True
