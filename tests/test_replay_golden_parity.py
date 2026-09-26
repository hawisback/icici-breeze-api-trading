import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.strategy.futures_signal import FuturesContractResolver
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
from services.strategy.replay_analytics import build_portfolio_metrics
from services.strategy.replay_execution_model import estimate_round_trip_execution
from services.strategy.replay_intrabar import resolve_stop_target_order
from services.strategy.replay_registry import ReplayStrategyRegistry
from services.strategy.replay_sizing import calculate_replay_sizing
from services.strategy.replay_contract_selection import ReplayPriceEvidence
from services.strategy.risk_gates import (
    check_daily_loss_limits,
    check_daily_trade_limit,
    check_loss_cooldown,
)
from services.strategy.simulation import _assert_replay_control_accounting


GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "replay_parity_golden.json").read_text()
)


def _signal(strategy: StrategyName) -> StrategySignal:
    structural = strategy in {
        StrategyName.TREND_PULLBACK,
        StrategyName.DI_CONTINUATION,
    }
    return StrategySignal(
        signal_id=f"GOLDEN-{strategy.value}",
        strategy=strategy,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=datetime(2026, 9, 24, 4, 30, tzinfo=UTC),
        spot_reference_price=100.0,
        underlying_entry_price=100.0 if structural else None,
        structural_stop=90.0,
        r_points=10.0,
        derivatives_score=0.0,
    )


def _contract():
    return SimpleNamespace(
        instrument_id="OPT-100-CE",
        stock_code="OPT100CE",
        expiry="2026-09-24",
        strike=100.0,
        lot_size=50,
    )


def test_golden_a_to_e_registry_binds_every_signal_family_to_one_replay_adapter():
    registry = ReplayStrategyRegistry.default(
        StrategyTunablesConfig(),
        SessionTimersConfig(),
        selected_strategies=list(StrategyName),
    )
    actual = [
        [adapter.strategy_metadata().strategy.value, type(adapter).__name__]
        for adapter in registry.adapters
    ]
    assert actual == GOLDEN["strategy_registry"]
    assert len({row[0] for row in actual}) == len(StrategyName)


@pytest.mark.parametrize(
    ("strategy", "expected_method"),
    [
        (StrategyName.TREND_PULLBACK, "UNDERLYING_R_WITH_POINT_IN_TIME_DELTA"),
        (StrategyName.VOLATILITY_BREAKOUT, "OPTION_HARD_STOP_PREMIUM_RISK"),
        (StrategyName.DI_CONTINUATION, "UNDERLYING_R_WITH_POINT_IN_TIME_DELTA"),
        (StrategyName.SR_MOMENTUM_BREAKOUT, "OPTION_HARD_STOP_PREMIUM_RISK"),
        (StrategyName.PIVOT_VWAP_SCALP, "OPTION_HARD_STOP_PREMIUM_RISK"),
    ],
)
def test_golden_a_to_e_sizing_dispatch(strategy, expected_method):
    risk = RiskConfig(
        account_equity=500000.0,
        risk_per_trade_pct_of_account=0.5,
        max_trade_capital=50000.0,
        max_lots_per_trade=100,
    )
    decision = calculate_replay_sizing(
        signal=_signal(strategy),
        contract=_contract(),
        entry_reference_price=100.0,
        risk_config=risk,
        option_selection=OptionSelectionConfig(),
        session_config=SessionTimersConfig(),
        strategy_config=StrategyTunablesConfig(strategy_e_lots=2),
        account_equity=risk.account_equity,
        option_delta=0.60 if strategy in {
            StrategyName.TREND_PULLBACK,
            StrategyName.DI_CONTINUATION,
        } else None,
        option_delta_source="BROKER",
        price_basis="POINT_IN_TIME_ASK",
    )
    assert decision.status == "APPLIED"
    assert decision.method == expected_method
    if strategy == StrategyName.PIVOT_VWAP_SCALP:
        assert decision.lots == GOLDEN["sizing"]["strategy_e_lot_cap"]


def test_golden_shared_risk_gates_cover_cooldown_daily_stop_and_trade_limit():
    risk = RiskConfig(
        cooldown_after_loss_min=10,
        max_daily_loss_r=2.0,
        max_trades_per_day=3,
    )
    now = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
    assert check_loss_cooldown(
        risk, at=now, last_loss_exit_time=now - timedelta(minutes=4)
    ).status == GOLDEN["risk"]["cooldown_status"]
    assert check_daily_loss_limits(
        risk, realized_r_total=-2.0, net_pnl_total=None
    ).status == GOLDEN["risk"]["daily_loss_status"]
    assert check_daily_trade_limit(
        risk, daily_count=3
    ).status == GOLDEN["risk"]["daily_trade_status"]


def test_golden_expiry_rollover_uses_same_nearest_nonexpired_policy():
    contracts = {
        "NIFTY-FUT-2026-09-24": datetime(2026, 9, 24).date(),
        "NIFTY-FUT-2026-10-29": datetime(2026, 10, 29).date(),
    }
    before = FuturesContractResolver.resolve_contracts(
        contracts, as_of=datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
    )
    after = FuturesContractResolver.resolve_contracts(
        contracts, as_of=datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
    )
    assert before == GOLDEN["expiry_rollover"]["before_expiry"]
    assert after == GOLDEN["expiry_rollover"]["after_expiry"]


def test_golden_one_minute_same_bar_ambiguity_fails_closed():
    minute = SimpleNamespace(
        start_time=datetime(2026, 9, 24, 5, 0, tzinfo=UTC),
        open=100.0,
        high=106.0,
        low=94.0,
        close=101.0,
    )
    result = resolve_stop_target_order(
        "CALL",
        active_stop=95.0,
        target=105.0,
        minute_candles=[minute],
    )
    assert result.event == GOLDEN["intrabar"]["same_minute_stop_target"]
    assert result.ambiguous is True


def test_golden_transaction_cost_model_remains_exact():
    risk = RiskConfig(paper_slippage_points=1.0)
    event = datetime(2026, 9, 24, 5, 0, tzinfo=UTC)
    execution = estimate_round_trip_execution(
        entry_evidence=ReplayPriceEvidence(
            status="AVAILABLE",
            basis="POINT_IN_TIME_BID_ASK",
            source="KITE",
            event_timestamp=event,
            evidence_timestamp=event,
            bid=99.0,
            ask=100.0,
        ),
        exit_evidence=ReplayPriceEvidence(
            status="AVAILABLE",
            basis="POINT_IN_TIME_BID_ASK",
            source="KITE",
            event_timestamp=event + timedelta(minutes=30),
            evidence_timestamp=event + timedelta(minutes=30),
            bid=110.0,
            ask=111.0,
        ),
        quantity=50,
        risk_config=risk,
    )
    assert execution.gross_execution_pnl == GOLDEN["execution"]["gross_pnl"]
    assert execution.transaction_costs == GOLDEN["execution"]["transaction_costs"]
    assert execution.net_execution_pnl == GOLDEN["execution"]["net_pnl"]


def test_unsupported_control_cannot_become_metadata_only_without_classification():
    with pytest.raises(RuntimeError, match="accounting invariant"):
        _assert_replay_control_accounting(
            supplied_overrides={"future_knob": 123},
            applied_overrides={},
            conditional_overrides={},
            not_applied_overrides={},
        )

    _assert_replay_control_accounting(
        supplied_overrides={"future_knob": 123},
        applied_overrides={},
        conditional_overrides={},
        not_applied_overrides={
            "future_knob": {"value": 123, "reason": "unsupported"}
        },
    )

    with pytest.raises(RuntimeError, match="overlaps"):
        _assert_replay_control_accounting(
            supplied_overrides={"future_knob": 123},
            applied_overrides={"future_knob": 123},
            conditional_overrides={},
            not_applied_overrides={
                "future_knob": {"value": 123, "reason": "unsupported"}
            },
        )


def test_golden_portfolio_metrics_reject_missing_execution_pnl():
    record = SimpleNamespace(
        lifecycle_status="RESOLVED",
        replay_signal_id="GOLDEN-PORTFOLIO",
        strategy_id=StrategyName.TREND_PULLBACK.value,
        simulated_entry_timestamp=datetime(2026, 9, 24, 4, 30, tzinfo=UTC),
        exit_timestamp=datetime(2026, 9, 24, 5, 0, tzinfo=UTC),
        realized_r=1.0,
        simulated_net_pnl=None,
        sizing_status="APPLIED",
    )
    metrics = build_portfolio_metrics(
        [record],
        starting_equity=100000.0,
        session_start=datetime(2026, 9, 24, 3, 45, tzinfo=UTC),
        session_end=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        execution_metadata={},
    )
    assert metrics.resolved_trades == 1
    assert metrics.net_executable_pnl is None
    assert metrics.expectancy_pnl is None
