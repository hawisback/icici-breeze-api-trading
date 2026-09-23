from services.historical.strategy_d_candidate_manifest import (
    CANDIDATE_ID,
    candidate_spec,
    spec_fingerprint,
    validate_v2_config,
)
from services.strategy.strategies.sr_momentum_breakout import (
    STRATEGY_D_V2_ID,
    StrategyDConfig,
)


def test_v2_candidate_manifest_matches_frozen_config():
    spec = candidate_spec()
    cfg = StrategyDConfig.v2_candidate()

    assert CANDIDATE_ID == STRATEGY_D_V2_ID
    assert spec["candidate_id"] == STRATEGY_D_V2_ID
    assert spec["entry"]["minimum_rsi_clearance_points_strict"] == 2.0
    assert spec["entry"]["max_previous_day_range_atr_strict"] == 8.0
    assert spec["risk_lifecycle_unchanged_from_v1"][
        "atr_stop_multiple"
    ] == 1.5
    assert spec["risk_lifecycle_unchanged_from_v1"][
        "scale_out_r"
    ] == 1.5
    assert spec["not_enabled_as_hard_filters"]["r1_exclusion"] is False
    assert spec["not_enabled_as_hard_filters"][
        "12_00_ist_blackout"
    ] is False
    validate_v2_config(cfg)


def test_v2_candidate_fingerprint_is_deterministic():
    first = spec_fingerprint()
    second = spec_fingerprint()

    assert first == second
    assert len(first) == 64


def test_v2_candidate_rejects_parameter_drift():
    drifted = StrategyDConfig(
        variant="V2_CANDIDATE",
        minimum_rsi_clearance_points=1.5,
        max_previous_day_range_atr=8.0,
    )

    try:
        validate_v2_config(drifted)
    except ValueError as exc:
        assert "candidate config drift" in str(exc)
    else:
        raise AssertionError("drifted V2 config was not rejected")
