from services.historical.strategy_c_candidate_manifest import (
    CANDIDATE_ID as STRATEGY_C_CANDIDATE_ID,
    _spec_fingerprint as strategy_c_spec_fingerprint,
)
from services.strategy.models import (
    HistoricalReplayMode,
    HistoricalReplaySource,
    SessionTimersConfig,
    SimulationRequest,
    StrategyName,
    StrategyTunablesConfig,
    ThresholdOverrides,
)
from services.strategy.replay_metadata import (
    build_configuration_snapshot,
    configuration_fingerprint,
)
from services.strategy.replay_registry import ReplayStrategyRegistry


def test_day_replay_can_select_strategy_c_without_mutating_frozen_contract():
    tunables = StrategyTunablesConfig()
    fingerprint_before = strategy_c_spec_fingerprint()

    registry = ReplayStrategyRegistry.default(
        tunables,
        SessionTimersConfig(),
        selected_strategies=[StrategyName.DI_CONTINUATION],
    )

    metadata = registry.strategy_metadata()
    assert [item.strategy for item in metadata] == [
        StrategyName.DI_CONTINUATION
    ]
    assert metadata[0].registry_key == "di_continuation"
    assert metadata[0].display_name == "Strategy C · DI Continuation"
    assert strategy_c_spec_fingerprint() == fingerprint_before
    assert STRATEGY_C_CANDIDATE_ID == "STRATEGY_C_DI_CONTINUATION_V1_CANDIDATE"


def test_strategy_c_selection_is_fingerprinted_but_does_not_change_tunables():
    tunables = StrategyTunablesConfig()
    original = tunables.model_dump(mode="json")
    common = {
        "start_date": "2026-09-24",
        "end_date": "2026-09-24",
        "instrument_id": "INST-NIFTY-INDEX",
        "historical_source": HistoricalReplaySource.BREEZE,
        "bypass_entry_window": False,
        "strategy_a_enabled": tunables.trend_pullback_enabled,
        "overrides": ThresholdOverrides(),
        "tunables": tunables,
        "session": SessionTimersConfig(),
    }

    all_strategies = build_configuration_snapshot(**common)
    strategy_c_only = build_configuration_snapshot(
        **common,
        selected_strategies=[StrategyName.DI_CONTINUATION.value],
    )

    assert strategy_c_only.selected_strategies == ["DI_CONTINUATION"]
    assert configuration_fingerprint(strategy_c_only) != configuration_fingerprint(
        all_strategies
    )
    assert tunables.model_dump(mode="json") == original
    assert strategy_c_spec_fingerprint() == strategy_c_spec_fingerprint()  # frozen pure manifest hash


def test_strategy_c_selection_preserves_research_and_execution_parity_modes():
    research = SimulationRequest(
        replay_mode=HistoricalReplayMode.RESEARCH,
        selected_strategies=[StrategyName.DI_CONTINUATION],
    )
    execution = SimulationRequest(
        replay_mode=HistoricalReplayMode.EXECUTION_PARITY,
        selected_strategies=[StrategyName.DI_CONTINUATION],
    )

    assert research.selected_strategies == [StrategyName.DI_CONTINUATION]
    assert execution.selected_strategies == [StrategyName.DI_CONTINUATION]
    assert research.replay_mode is HistoricalReplayMode.RESEARCH
    assert execution.replay_mode is HistoricalReplayMode.EXECUTION_PARITY
