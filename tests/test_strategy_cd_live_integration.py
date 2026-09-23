from datetime import datetime, timedelta, timezone

from services.strategy.models import (
    AutoTradingConfig,
    AutoTradingMode,
    OptionType,
    StrategyName,
    TradeDirection,
)
from services.strategy.service import StrategyService
from services.strategy.strategies.candidate_runtime import (
    strategy_c_signal_from_status,
    strategy_d_signal_from_status,
)


UTC = timezone.utc


def _strategy_c_status(entry_time: datetime) -> dict:
    return {
        "candidate_id": "STRATEGY_C_DI_CONTINUATION_V1_CANDIDATE",
        "candidate_spec_fingerprint": "c" * 64,
        "active_candidate_trade": {
            "candidate_signal_id": "C-SIGNAL-1",
            "direction": "CALL",
            "entry_time": entry_time.isoformat(),
            "entry_price": 25000.0,
            "initial_stop": 24900.0,
            "setup_end": entry_time.isoformat(),
            "research_features": {"ema_separation_atr": 0.5},
            "lifecycle": {
                "status": "OPEN",
                "current_stop": 24900.0,
                "current_r": 0.25,
            },
        },
    }


def _strategy_d_status(entry_time: datetime) -> dict:
    return {
        "candidate_id": "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE",
        "candidate_spec_fingerprint": "d" * 64,
        # This is intentionally independent of active_paper_trade. The main
        # execution path must not require a simulated option fill.
        "execution_signal_id": "D-SIGNAL-1",
        "execution_signal": {
            "timestamp": entry_time.isoformat(),
            "direction": "BEARISH",
            "option_type": "PUT",
            "entry_price": 24950.0,
            "initial_stop": 25025.0,
            "risk_points": 75.0,
            "breakout_level_name": "PDL",
            "breakout_level": 24960.0,
            "atr_5m": 50.0,
            "rsi_previous": 41.0,
            "rsi_current": 37.0,
            "previous_day_range_atr": 5.0,
            "vwap_reference_price": 24940.0,
            "vwap": 24955.0,
            "levels": {"PDL": 24960.0},
        },
        "active_paper_trade": None,
    }


def test_strategy_c_promotes_fresh_open_candidate_to_common_signal():
    now = datetime(2026, 9, 24, 4, 31, tzinfo=UTC)
    signal = strategy_c_signal_from_status(
        _strategy_c_status(now - timedelta(seconds=2)),
        as_of=now,
    )

    assert signal is not None
    assert signal.strategy == StrategyName.DI_CONTINUATION
    assert signal.direction == TradeDirection.BULLISH
    assert signal.option_type == OptionType.CALL
    assert signal.underlying_entry_price == 25000.0
    assert signal.structural_stop == 24900.0
    assert signal.r_points == 100.0


def test_strategy_c_rejects_late_restart_entry():
    entry = datetime(2026, 9, 24, 4, 31, tzinfo=UTC)
    signal = strategy_c_signal_from_status(
        _strategy_c_status(entry),
        as_of=entry + timedelta(seconds=301),
    )
    assert signal is None


def test_strategy_d_execution_signal_does_not_depend_on_paper_fill():
    now = datetime(2026, 9, 24, 5, 0, tzinfo=UTC)
    signal = strategy_d_signal_from_status(
        _strategy_d_status(now - timedelta(seconds=1)),
        as_of=now,
    )

    assert signal is not None
    assert signal.signal_id == "D-SIGNAL-1"
    assert signal.strategy == StrategyName.SR_MOMENTUM_BREAKOUT
    assert signal.direction == TradeDirection.BEARISH
    assert signal.option_type == OptionType.PUT
    assert signal.underlying_entry_price == 24950.0
    assert signal.structural_stop == 25025.0
    assert signal.r_points == 75.0


def test_c_and_d_follow_configured_execution_mode():
    service = StrategyService.__new__(StrategyService)
    service.config = AutoTradingConfig(mode=AutoTradingMode.LIVE)

    assert service._execution_mode_for_strategy(
        StrategyName.DI_CONTINUATION,
        OptionType.CALL,
    ) == AutoTradingMode.LIVE
    assert service._execution_mode_for_strategy(
        StrategyName.SR_MOMENTUM_BREAKOUT,
        OptionType.PUT,
    ) == AutoTradingMode.LIVE

    service.config.mode = AutoTradingMode.PAPER
    assert service._execution_mode_for_strategy(
        StrategyName.DI_CONTINUATION,
        OptionType.CALL,
    ) == AutoTradingMode.PAPER
    assert service._execution_mode_for_strategy(
        StrategyName.SR_MOMENTUM_BREAKOUT,
        OptionType.PUT,
    ) == AutoTradingMode.PAPER


def test_c_uses_structural_delta_risk_while_d_uses_premium_risk():
    assert StrategyService._uses_delta_aware_selector(
        StrategyName.DI_CONTINUATION
    )
    assert StrategyService._uses_underlying_risk_sizing(
        StrategyName.DI_CONTINUATION
    )
    assert not StrategyService._uses_delta_aware_selector(
        StrategyName.SR_MOMENTUM_BREAKOUT
    )
    assert not StrategyService._uses_underlying_risk_sizing(
        StrategyName.SR_MOMENTUM_BREAKOUT
    )


def test_c_and_d_have_first_class_enable_flags():
    config = AutoTradingConfig()
    assert config.tunables.di_continuation_enabled is True
    assert config.tunables.sr_momentum_breakout_enabled is True
