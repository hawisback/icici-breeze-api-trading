from services.historical.strategy_f5_dominant_nifty_regime_recorded_findings import (
    STRATEGY_F5_DOMINANT_NIFTY_REGIME_RECORDED_FINDINGS_V1,
)


def test_dominant_regime_findings_are_sha_bound():
    record = STRATEGY_F5_DOMINANT_NIFTY_REGIME_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "8477a212259ce2855a5d1871f9de1364323c9095914cb94c62c16a12599074d8"
    )


def test_bearish_regime_strongly_separates_ce_and_pe():
    record = STRATEGY_F5_DOMINANT_NIFTY_REGIME_RECORDED_FINDINGS_V1
    ce = record["bearish_regime"]["CE"]
    pe = record["bearish_regime"]["PE"]
    assert ce["wins"] == 0
    assert ce["trail_activation_rate_pct"] == 0.0
    assert ce["net_pnl_inr"] < 0
    assert pe["trail_activation_rate_pct"] > 50.0
    assert pe["net_pnl_inr"] > 0


def test_whole_day_label_not_promoted_as_live_filter():
    record = STRATEGY_F5_DOMINANT_NIFTY_REGIME_RECORDED_FINDINGS_V1
    assert record["decision"] == (
        "BUILD_NO_LOOKAHEAD_INTRADAY_DOMINANT_REGIME_DETECTOR"
    )
    assert record["guardrails"]["whole_day_regime_is_lookahead_only"] is True
    assert record["guardrails"]["no_same_day_filter_from_whole_day_label"] is True
