from datetime import date, datetime, timedelta, timezone

import pytest

from libs.contracts.models import Candle
from services.historical.strategy_e_pivot_vwap_backtest import (
    EXPECTED_5M_BARS,
    EXPERIMENT_ARMS,
    _advance_trade,
    _complete_candidate_dates,
    _counterfactual_candidate,
    _is_complete_session,
    _research_ledger_summary,
    _select_requested_session_dates,
    _start_trade,
    build_experiment,
    run_backtest,
)
from services.strategy.strategies.pivot_vwap_scalp import StrategyEDecision
from services.strategy.models import (
    OptionType,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
    TradeDirection,
)


IST = timezone(timedelta(hours=5, minutes=30))


def _session(
    day: date,
    *,
    instrument_id: str = "INST-NIFTY-FUT-2026-09-29",
    interval: str = "5m",
) -> list[Candle]:
    minutes = 1 if interval == "1m" else 5
    count = 375 if interval == "1m" else EXPECTED_5M_BARS
    rows: list[Candle] = []
    start = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(
        hour=9,
        minute=15,
    )
    for index in range(count):
        bar_start = start + timedelta(minutes=minutes * index)
        rows.append(Candle(
            instrument_id=instrument_id,
            interval=interval,
            start_time=bar_start,
            end_time=bar_start + timedelta(minutes=minutes),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000,
            open_interest=100000,
            source="BREEZE",
        ))
    return rows


def _signal(
    *,
    timestamp: datetime,
    direction: TradeDirection = TradeDirection.BULLISH,
) -> StrategySignal:
    return StrategySignal(
        signal_id="STRATEGY-E-TEST",
        strategy=StrategyName.PIVOT_VWAP_SCALP,
        direction=direction,
        option_type=(
            OptionType.CALL
            if direction == TradeDirection.BULLISH
            else OptionType.PUT
        ),
        timestamp=timestamp,
        spot_reference_price=100.0,
        underlying_entry_price=100.0,
        structural_stop=95.0,
        r_points=5.0,
        derivatives_score=0.0,
        features_snapshot={
            "signal_type": "TREND_LONG",
            "target_price": 110.0,
            "futures_contract": "INST-NIFTY-FUT-2026-09-29",
        },
    )


def test_strategy_e_complete_session_requires_all_75_five_minute_bars():
    rows = _session(date(2026, 9, 24))
    assert _is_complete_session(rows) is True
    assert len(rows) == 75
    assert _is_complete_session(rows[:-1]) is False


def test_strategy_e_complete_candidate_requires_same_contract_previous_session():
    prior = _session(date(2026, 9, 23))
    current = _session(date(2026, 9, 24))
    assert _complete_candidate_dates([*prior, *current]) == [
        date(2026, 9, 24)
    ]

    different_contract_prior = _session(
        date(2026, 9, 23),
        instrument_id="INST-NIFTY-FUT-2026-09-22",
    )
    assert _complete_candidate_dates(
        [*different_contract_prior, *current]
    ) == []


def test_strategy_e_exact_session_selector_uses_latest_or_forward_dates():
    available = [
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
        date(2026, 9, 25),
    ]
    assert _select_requested_session_dates(
        available,
        sessions=2,
    ) == [date(2026, 9, 24), date(2026, 9, 25)]
    assert _select_requested_session_dates(
        available,
        sessions=2,
        start_date=date(2026, 9, 23),
    ) == [date(2026, 9, 23), date(2026, 9, 24)]

    with pytest.raises(ValueError, match="greater than zero"):
        _select_requested_session_dates(available, sessions=0)
    with pytest.raises(
        ValueError,
        match="only 4 complete Strategy E sessions",
    ):
        _select_requested_session_dates(available, sessions=5)


def test_strategy_e_ambiguous_stop_target_without_one_minute_is_not_scored():
    entry_end = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    state = _start_trade(_signal(timestamp=entry_end))
    bar = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval="5m",
        start_time=entry_end,
        end_time=entry_end + timedelta(minutes=5),
        open=100.0,
        high=111.0,
        low=94.0,
        close=105.0,
        volume=1000,
        open_interest=100000,
        source="BREEZE",
    )

    result = _advance_trade(
        state,
        bar,
        [],
        forced_exit_time="15:15",
    )

    assert result is not None
    assert result["lifecycle_status"] == "AMBIGUOUS"
    assert result["realized_r"] is None
    assert result["exit_reason"] == "AMBIGUOUS_INTRABAR_ORDER"


def test_strategy_e_backtest_reports_exact_complete_session_and_funnel():
    previous = _session(date(2026, 9, 23))
    current = _session(date(2026, 9, 24))

    report = run_backtest(
        futures_candles=[*previous, *current],
        session_dates=[date(2026, 9, 24)],
    )

    assert report["usable_sessions"] == 1
    assert report["session_summary"]["session_dates"] == ["2026-09-24"]
    assert report["signal_diagnostics"]["bars_evaluated"] == 75
    assert (
        sum(report["signal_diagnostics"]["decision_reason_counts"].values())
        == 75
    )
    assert report["skipped_sessions_or_events"] == {}



def test_strategy_e_experiment_arms_change_exactly_one_control_tunable():
    control = StrategyTunablesConfig()
    expected = {
        "MAX_STOP_40": ("strategy_e_max_stop_points", 40.0),
        "MIN_RR_0_75": ("strategy_e_min_reward_risk", 0.75),
        "MIN_ROOM_3": ("strategy_e_min_room_to_level_points", 3.0),
        "CHOP_CROSS_3": ("strategy_e_chop_cross_threshold", 3),
    }

    assert EXPERIMENT_ARMS["CONTROL"] == {}
    for name, (field, value) in expected.items():
        assert EXPERIMENT_ARMS[name] == {field: value}
        candidate = control.model_copy(update=EXPERIMENT_ARMS[name])
        assert getattr(candidate, field) == value


def test_strategy_e_experiment_replays_identical_complete_sessions():
    previous = _session(date(2026, 9, 23))
    current = _session(date(2026, 9, 24))

    experiment = build_experiment(
        futures_candles=[*previous, *current],
        session_dates=[date(2026, 9, 24)],
    )

    assert experiment["research_only"] is True
    assert experiment["production_defaults_changed"] is False
    assert experiment["session_dates"] == ["2026-09-24"]
    assert set(experiment["arms"]) == set(EXPERIMENT_ARMS)
    for arm in experiment["arms"].values():
        assert arm["session_summary"]["session_dates"] == ["2026-09-24"]
        assert arm["signal_diagnostics"]["bars_evaluated"] == 75
        assert arm["incremental_vs_control"]["retained_control_trades"] == 0
        assert arm["incremental_vs_control"]["displaced_control_trades"] == 0



def test_strategy_e_counterfactual_rejected_candidate_replays_lifecycle():
    entry_end = datetime(2026, 9, 24, 10, 0, tzinfo=IST)
    entry_bar = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval="5m",
        start_time=entry_end - timedelta(minutes=5),
        end_time=entry_end,
        open=99.0,
        high=101.0,
        low=98.0,
        close=100.0,
        volume=1000,
        open_interest=100000,
        source="BREEZE",
    )
    target_bar = Candle(
        instrument_id=entry_bar.instrument_id,
        interval="5m",
        start_time=entry_end,
        end_time=entry_end + timedelta(minutes=5),
        open=100.0,
        high=111.0,
        low=99.0,
        close=110.0,
        volume=1000,
        open_interest=100000,
        source="BREEZE",
    )
    decision = StrategyEDecision(
        "NO_TRADE",
        "INSUFFICIENT_REWARD_TO_RISK",
        {
            "candidate_signal_type": "TREND_LONG",
            "candidate_direction": "BULLISH",
            "entry_price": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "risk_points": 5.0,
            "reward_points": 10.0,
            "reward_risk": 2.0,
            "pivot": 99.0,
            "vwap": 99.5,
        },
    )

    row = _counterfactual_candidate(
        day=date(2026, 9, 24),
        bar=entry_bar,
        decision=decision,
        future_bars=[target_bar],
        minute_bars=[],
        forced_exit_time="15:15",
    )

    assert row is not None
    assert row["independent_counterfactual"] is True
    assert row["rejection_reason"] == "INSUFFICIENT_REWARD_TO_RISK"
    assert row["lifecycle_status"] == "RESOLVED"
    assert row["exit_reason"] == "STRATEGY_E_TARGET"
    assert row["realized_r"] == 2.0


def test_strategy_e_research_ledger_summarizes_gate_and_rr_band():
    rows = [
        {
            "rejection_reason": "INSUFFICIENT_REWARD_TO_RISK",
            "signal_type": "COUNTER_LONG",
            "direction": "BULLISH",
            "reward_risk": 0.7,
            "lifecycle_status": "RESOLVED",
            "realized_r": 1.0,
            "mfe_r": 1.4,
            "mae_r": -0.2,
        },
        {
            "rejection_reason": "INSUFFICIENT_REWARD_TO_RISK",
            "signal_type": "COUNTER_LONG",
            "direction": "BULLISH",
            "reward_risk": 0.6,
            "lifecycle_status": "RESOLVED",
            "realized_r": -1.0,
            "mfe_r": 0.3,
            "mae_r": -1.0,
        },
    ]

    summary = _research_ledger_summary(rows)

    gate = summary["by_rejection_reason"]["INSUFFICIENT_REWARD_TO_RISK"]
    assert gate["candidates"] == 2
    assert gate["wins"] == 1
    assert gate["losses"] == 1
    assert gate["total_r"] == 0.0
    assert summary["by_reward_risk_band"]["0_50_TO_0_74"]["candidates"] == 2
