"""Contract tests for the Strategy A configuration and lifecycle model."""

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

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
from services.strategy.repository import StrategyRepository
from services.strategy.service import StrategyService
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
    v2_strategy = TrendPullbackStrategy(
        adx_threshold=config.adx_threshold,
        breakout_buffer_atr=config.trigger_buffer_atr,
    )
    legacy_strategy = TrendPullbackStrategy(
        adx_threshold=config.legacy_strategy_a_adx_threshold,
        breakout_buffer_atr=config.legacy_trigger_buffer_atr,
    )

    assert auto_config.tunables.model_dump() == config.model_dump()
    assert v2_strategy.adx_threshold == config.adx_threshold == 22.0
    assert v2_strategy.breakout_buffer_atr == config.trigger_buffer_atr == 0.05
    assert legacy_strategy.adx_threshold == config.legacy_strategy_a_adx_threshold == 20.0
    assert legacy_strategy.breakout_buffer_atr == config.legacy_trigger_buffer_atr == 0.02
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


def test_service_keeps_legacy_strategy_a_evaluator_and_strategy_b_values():
    config = StrategyTunablesConfig()
    service = StrategyService(oms_service=Mock(), repository=Mock())

    assert service.config.tunables.adx_threshold == config.adx_threshold
    assert service.strategy_a.adx_threshold == config.legacy_strategy_a_adx_threshold
    assert service.strategy_a.breakout_buffer_atr == config.legacy_trigger_buffer_atr
    assert service.strategy_b.adx_threshold == config.strategy_b_adx_threshold


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


def test_legacy_triggered_state_never_recovers_as_an_open_position():
    assert StrategyState("SEARCHING") is StrategyState.FLAT
    assert StrategyState("TRIGGERED") is StrategyState.ARMED
    assert StrategyState("PAUSED") is StrategyState.FLAT

    with pytest.raises(ValidationError):
        StrategyStateSnapshot.model_validate({"state": "TRIGGERED"})

    recovered = StrategyStateSnapshot.model_validate({
        "state": "TRIGGERED",
        "direction": "CALL",
        "setup": make_setup().model_dump(),
    })
    assert recovered.state is StrategyState.ARMED
    assert recovered.entry_timestamp is None


def test_existing_strategy_public_imports_remain_available():
    # These imports/constructors are existing integration points for service,
    # replay, and callers that use Strategy B directly.
    assert TrendPullbackStrategy is not None
    assert VolatilityBreakoutStrategy is not None


@pytest.mark.parametrize("old_adx", [20.0, 25.0, 18.0])
@pytest.mark.asyncio
async def test_persisted_adx_migration_preserves_strategy_b_and_documents_v2_policy(tmp_path, old_adx):
    repo = StrategyRepository(tmp_path / f"strategy-{old_adx}.db")
    await repo.initialize()
    await repo.save_auto_config(AutoTradingConfig(strategy_a_revision=3))

    async with repo.engine.connect() as conn:
        row = await (await conn.execute(
            "SELECT config_json FROM auto_strategy_config WHERE id = 'active'"
        )).fetchone()
        persisted = json.loads(row["config_json"])
        persisted["strategy_a_revision"] = 3
        persisted["tunables"]["adx_threshold"] = old_adx
        persisted["tunables"].pop("strategy_b_adx_threshold", None)
        persisted["tunables"].pop("legacy_strategy_a_adx_threshold", None)
        await conn.execute(
            "UPDATE auto_strategy_config SET config_json = ? WHERE id = 'active'",
            (json.dumps(persisted),),
        )
        await conn.commit()

    migrated = await repo.get_auto_config()
    expected_v2_adx = 22.0 if old_adx == 20.0 else old_adx
    assert migrated.strategy_a_revision == 4
    assert migrated.tunables.strategy_b_adx_threshold == old_adx
    assert migrated.tunables.legacy_strategy_a_adx_threshold == old_adx
    assert migrated.tunables.adx_threshold == expected_v2_adx


@pytest.mark.parametrize("field_name", [
    "setup_timestamp",
    "confirmation_bar_timestamp",
    "setup_expiry_timestamp",
])
def test_setup_rejects_naive_timestamps(field_name):
    payload = make_setup().model_dump()
    payload[field_name] = datetime(2026, 9, 20, 9, 45)
    with pytest.raises(ValidationError, match="timezone-aware"):
        StrategySetup.model_validate(payload)


def test_state_rejects_naive_entry_and_cooldown_timestamps():
    setup = make_setup()
    with pytest.raises(ValidationError, match="timezone-aware"):
        StrategyStateSnapshot(
            state=StrategyState.ENTERED,
            direction=StrategyDirection.CALL,
            setup=setup,
            entry_timestamp=datetime(2026, 9, 20, 10, 0),
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        StrategyStateSnapshot(
            state=StrategyState.COOLDOWN,
            cooldown_until=datetime(2026, 9, 20, 10, 15),
        )
