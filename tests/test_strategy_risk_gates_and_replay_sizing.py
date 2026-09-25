from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.strategy.models import (
    OptionSelectionConfig,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
    TradeDirection,
)
from services.strategy.replay_sizing import calculate_replay_sizing
from services.strategy.risk_gates import (
    check_daily_loss_limits,
    check_daily_trade_limit,
    check_loss_cooldown,
    check_position_capacity,
    check_strategy_trade_limits,
)


def _signal(strategy: StrategyName) -> StrategySignal:
    structural = strategy == StrategyName.TREND_PULLBACK
    return StrategySignal(
        signal_id=f"SIG-{strategy.value}",
        strategy=strategy,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=datetime(2026, 9, 17, 4, 30, tzinfo=timezone.utc),
        spot_reference_price=100.0,
        underlying_entry_price=100.0 if structural else None,
        structural_stop=90.0,
        r_points=10.0,
        derivatives_score=0.0,
    )


def _contract(lot_size: int = 50):
    return SimpleNamespace(
        instrument_id="OPT-100-CE",
        stock_code="OPT100CE",
        expiry="2026-09-24",
        strike=100.0,
        lot_size=lot_size,
    )


def test_shared_numeric_risk_gates_preserve_production_statuses():
    risk = RiskConfig(
        max_concurrent_positions=1,
        cooldown_after_loss_min=10,
        max_daily_loss_r=2.0,
        max_trades_per_day=3,
        max_trades_per_strategy_per_day=2,
        max_failed_trades_per_strategy=1,
    )
    now = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)

    assert check_position_capacity(risk, active_count=1).status == (
        "MAX_CONCURRENT_POSITIONS_REACHED"
    )
    cooldown = check_loss_cooldown(
        risk,
        at=now,
        last_loss_exit_time=now - timedelta(minutes=4),
    )
    assert cooldown.status == "IN_LOSS_COOLDOWN"
    assert cooldown.details["cooldown_remaining_min"] == 6.0

    daily_loss = check_daily_loss_limits(
        risk,
        realized_r_total=-2.0,
        net_pnl_total=None,
    )
    assert daily_loss.status == "DAILY_LOSS_LIMIT_REACHED"
    assert daily_loss.details["reasons"] == ["MAX_DAILY_LOSS_R"]

    assert check_daily_trade_limit(risk, daily_count=3).status == (
        "DAILY_TRADE_LIMIT_REACHED"
    )
    assert check_strategy_trade_limits(
        risk,
        strategy_trade_count=2,
        strategy_failure_count=0,
    ).status == "STRATEGY_DAILY_TRADE_LIMIT_REACHED"
    assert check_strategy_trade_limits(
        risk,
        strategy_trade_count=1,
        strategy_failure_count=1,
    ).status == "STRATEGY_FAILURE_LIMIT_REACHED"


def test_strategy_b_replay_sizing_uses_production_premium_risk_math():
    risk = RiskConfig(
        account_equity=500000.0,
        risk_per_trade_pct_of_account=0.5,
        max_trade_capital=50000.0,
        option_hard_stop_pct=25.0,
        max_lots_per_trade=100,
    )
    decision = calculate_replay_sizing(
        signal=_signal(StrategyName.VOLATILITY_BREAKOUT),
        contract=_contract(),
        entry_mark=100.0,
        risk_config=risk,
        option_selection=OptionSelectionConfig(),
        session_config=SessionTimersConfig(),
        strategy_config=StrategyTunablesConfig(),
        account_equity=500000.0,
    )

    assert decision.status == "APPLIED"
    assert decision.method == "OPTION_HARD_STOP_PREMIUM_RISK"
    assert decision.risk_budget == 2500.0
    assert decision.option_loss_per_lot == 1250.0
    assert decision.lots == 2
    assert decision.quantity == 100

    rejected = calculate_replay_sizing(
        signal=_signal(StrategyName.VOLATILITY_BREAKOUT),
        contract=_contract(),
        entry_mark=100.0,
        risk_config=risk.model_copy(update={"account_equity": 100000.0}),
        option_selection=OptionSelectionConfig(),
        session_config=SessionTimersConfig(),
        strategy_config=StrategyTunablesConfig(),
        account_equity=100000.0,
    )
    assert rejected.status == "REJECTED"
    assert rejected.lots == 0
    assert rejected.quantity == 0
    assert rejected.rejection_reason == "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"


def test_strategy_a_replay_sizing_changes_with_capital_and_risk_budget():
    signal = _signal(StrategyName.TREND_PULLBACK)
    option_selection = OptionSelectionConfig(
        preferred_delta_min=0.60,
        preferred_delta_max=0.65,
    )
    high = calculate_replay_sizing(
        signal=signal,
        contract=_contract(),
        entry_mark=100.0,
        risk_config=RiskConfig(
            account_equity=500000.0,
            risk_per_trade_pct_of_account=0.5,
            max_trade_capital=50000.0,
        ),
        option_selection=option_selection,
        session_config=SessionTimersConfig(),
        strategy_config=StrategyTunablesConfig(),
        account_equity=500000.0,
    )
    low = calculate_replay_sizing(
        signal=signal,
        contract=_contract(),
        entry_mark=100.0,
        risk_config=RiskConfig(
            account_equity=100000.0,
            risk_per_trade_pct_of_account=0.5,
            max_trade_capital=50000.0,
        ),
        option_selection=option_selection,
        session_config=SessionTimersConfig(),
        strategy_config=StrategyTunablesConfig(),
        account_equity=100000.0,
    )

    assert high.status == "APPLIED"
    assert high.delta_proxy == 0.625
    assert high.delta_source == "CONFIGURED_PREFERRED_DELTA_MIDPOINT"
    assert high.lots == 8
    assert high.quantity == 400
    assert low.lots == 1
    assert low.quantity == 50
