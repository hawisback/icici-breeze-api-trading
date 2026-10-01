from services.historical.strategy_f5_broad_indicator_atlas_recorded_findings import (
    STRATEGY_F5_BROAD_INDICATOR_ATLAS_RECORDED_FINDINGS_V1,
)


def test_atlas_findings_are_sha_bound():
    record = STRATEGY_F5_BROAD_INDICATOR_ATLAS_RECORDED_FINDINGS_V1
    assert record["source"]["atlas_artifact_sha256"] == (
        "1b7d3952ef4429250f3a2b8e4edad621f438a52426dec5227e325214c4ae9dfa"
    )
    assert record["source"]["trades"] == 443
    assert record["source"]["feature_coverage_pct"] == 100.0


def test_volatility_and_histogram_are_top_families():
    record = STRATEGY_F5_BROAD_INDICATOR_ATLAS_RECORDED_FINDINGS_V1
    hist = record["top_independent_families"]["crossover_strength"]
    vol = record["top_independent_families"]["volatility_regime"]
    assert hist["bad_trade_auc"] < 0.5
    assert hist["activation_auc"] > 0.5
    assert vol["bad_trade_auc"] < 0.5
    assert vol["activation_auc"] > 0.5


def test_no_side_filter_or_threshold_grid_is_promoted():
    record = STRATEGY_F5_BROAD_INDICATOR_ATLAS_RECORDED_FINDINGS_V1
    assert record["decision"] == (
        "TEST_SMALL_NATURAL_REGIME_COMBINATIONS_BEFORE_FRESH_HOLDOUT"
    )
    assert record["guardrails"]["do_not_search_indicator_threshold_grids_on_jul_sep"]
    assert record["guardrails"]["do_not_add_ce_pe_side_filter"]
