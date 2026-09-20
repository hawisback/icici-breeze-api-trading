"""Contract tests for the Strategy A configuration and lifecycle model."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from services.strategy.models import (
    AutoTradingConfig,
    SetupInvalidationState,
    StrategyDirection,
    StrategySetup,
    StrategyState,
    StrategyStateSnapshot,
    StrategyTunablesConfig,
)
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy


UTC = timezone.utc


def make_setup(direction: StrategyDirection = StrategyDirection.CALL) -> StrategySetup:
    base = datetime(2026, 9, 20, 9, 45, tzinfo=UTC)
    if direction == StrategyDirection.CALL:
        trigger, stop = 100.0, 90.0
    else:
        trigger, stop = 100.0, 110.0
    return StrategySetup(
        direction=direction,
        setup_timestamp=base,
        confirmation_bar_timestamp=base - timedelta(minutes=15),
        confirmation_high=101.0,
        confirmation_low=99.0,
        trigger_price=trigger,
        structural_stop=stop,
        initial_underlying_r=10.0,
        relevant_support_resistance_level=95.0,
        confluence_references=["EMA20", "prior_day_level"],
        setup_expiry_timestamp=base + timedelta(minutes=30),
        setup_expiry_bar_index=12,
    )


def test_strategy_a_defaults_are_single_and_consistent_with_runtime_constructor():
    config = StrategyTunablesConfig()
    auto_config = AutoTradingConfig()
    strategy = TrendPullbackStrategy(
        adx_threshold=config.adx_threshold,
        breakout_buffer_atr=config.trigger_buffer_atr,
    )

    assert auto_config.tunables.model_dump() == config.model_dump()
    assert strategy.adx_threshold == config.adx_threshold == 22.0
    assert config.ema_fast_period == 20
    assert config.ema_slow_period == 50
    assert config.adx_period == config.atr_period == 14
    assert config.ema_separation_min_atr == 0.10
    assert config.confluence_distance_atr == 0.25
    assert config.sr_zone_atr == 0.10
    assert config.confirmation_min_body_ratio == 0.40
    assert config.confirmation_close_location_pct == 0.30
    assert config.confirmation_max_range_atr == 1.50
    assert config.trigger_buffer_atr == 0.05
    assert config.trigger_validity_bars == 2
    assert config.maximum_chase_atr == 0.25
    assert config.structural_stop_buffer_atr == 0.10
    assert config.minimum_stop_distance_atr == 0.80
    assert config.maximum_stop_distance_atr == 1.50
    assert config.minimum_room_to_opposing_sr_r == 1.50
    assert config.t1_r == 1.50
    assert config.runner_target_reference_r == 2.50
    assert config.trailing_activation_r == 1.0
    assert (config.entry_session_start, config.entry_session_end, config.forced_exit_time) == (
        "09:45",
        "14:45",
        "15:15",
    )
    # Strategy B keeps its pre-refactor hypothesis explicitly.
    assert config.strategy_b_adx_threshold == 20.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"ema_fast_period": 50, "ema_slow_period": 20},
        {"minimum_stop_distance_atr": 1.51, "maximum_stop_distance_atr": 1.50},
        {"entry_session_start": "14:45", "entry_session_end": "14:00"},
    ],
)
def test_invalid_strategy_a_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        StrategyTunablesConfig(**overrides)


def test_setup_serializes_and_round_trips_without_losing_direction_or_invalidation_state():
    setup = make_setup(StrategyDirection.PUT)
    restored = StrategySetup.model_validate(setup.model_dump())

    assert restored == setup
    assert restored.direction is StrategyDirection.PUT
    assert restored.invalidation_state is SetupInvalidationState.ACTIVE


def test_setup_rejects_inconsistent_risk_and_invalidation_fields():
    with pytest.raises(ValidationError, match="initial_underlying_r"):
        StrategySetup.model_validate({**make_setup().model_dump(), "initial_underlying_r": 9.0})

    with pytest.raises(ValidationError, match="invalidation_reason"):
        StrategySetup.model_validate({
            **make_setup().model_dump(),
            "invalidation_state": SetupInvalidationState.EXPIRED,
        })


def test_state_model_rejects_impossible_combinations_and_allows_legal_transitions():
    setup = make_setup()
    flat = StrategyStateSnapshot()
    in_setup = flat.transition(StrategyState.SETUP, setup=setup)
    armed = in_setup.transition(StrategyState.ARMED)
    entered = armed.transition(
        StrategyState.ENTERED,
        entry_timestamp=setup.setup_timestamp + timedelta(minutes=1),
    )
    cooldown = entered.transition(
        StrategyState.COOLDOWN,
        cooldown_until=setup.setup_timestamp + timedelta(minutes=15),
    )

    assert cooldown.state is StrategyState.COOLDOWN
    assert cooldown.transition(StrategyState.FLAT) == flat

    with pytest.raises(ValidationError):
        StrategyStateSnapshot(state=StrategyState.SETUP)
    with pytest.raises(ValidationError):
        StrategyStateSnapshot(state=StrategyState.FLAT, direction=StrategyDirection.CALL)
    with pytest.raises(ValueError, match="invalid strategy state transition"):
        flat.transition(StrategyState.ENTERED, setup=setup, entry_timestamp=setup.setup_timestamp)


def test_existing_strategy_public_imports_remain_available():
    # These imports/constructors are existing integration points for service,
    # replay, and callers that use Strategy B directly.
    assert TrendPullbackStrategy is not None
    assert VolatilityBreakoutStrategy is not None
