from datetime import datetime, timedelta, timezone

from libs.contracts.models import Candle
from services.historical.strategy_a_research import (
    ResearchVariant,
    _row_passes,
    _selected_rows,
    _trigger_label,
    _walk_forward_folds,
)
from services.strategy.futures_signal import FuturesFeatureSnapshot
from services.strategy.models import StrategyTunablesConfig


UTC = timezone.utc


def _research_row() -> dict:
    return {
        "date": "2026-09-21",
        "timestamp": "2026-09-21T06:30:00+00:00",
        "direction": "CALL",
        "ema_order_directional": True,
        "di_directional": True,
        "adx14": 22.0,
        "ema_separation_atr": 0.10,
        "confirmation_directional": True,
        "confirmation_body_ratio": 0.40,
        "confirmation_close_location_pct": 0.30,
        "confirmation_range_atr": 1.50,
        "sr_present": True,
        "sr_touch_distance_atr": 0.10,
        "ema_distance_atr": 0.25,
        "vwap_distance_atr": 0.50,
        "risk_atr": 0.80,
        "opposing_room_r": 1.50,
        "label": {
            "trigger_status": "TRIGGERED",
            "t1_first_hit_r": 1.5,
            "max_favorable_r": 1.8,
            "max_adverse_r": 0.4,
            "hit_t1_before_stop": True,
            "hit_runner_before_stop": False,
        },
    }


def _bar(start: datetime, *, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval="15m",
        start_time=start,
        end_time=start + timedelta(minutes=15),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
        open_interest=1000,
        source="BREEZE",
    )


def _feature(bar: Candle, atr: float = 2.0) -> FuturesFeatureSnapshot:
    return FuturesFeatureSnapshot(
        contract_id=bar.instrument_id,
        candle_timestamp=bar.end_time,
        candle_start=bar.start_time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        ema20=100.0,
        ema50=99.0,
        adx14=25.0,
        plus_di14=30.0,
        minus_di14=10.0,
        atr14=atr,
        session_vwap=100.0,
        support=99.0,
        resistance=110.0,
        bar_index=100,
    )


def test_research_baseline_matches_boundary_thresholds_and_ablation_is_one_rule_only():
    config = StrategyTunablesConfig()
    row = _research_row()

    assert _row_passes(row, config=config) is True

    row["confirmation_body_ratio"] = 0.39
    assert _row_passes(row, config=config) is False

    variant = ResearchVariant(
        name="without_confirmation_body",
        parameter_overrides={},
        ignored_rules=frozenset({"confirmation_body"}),
    )
    assert _selected_rows([row], variant, config) == [row]


def test_research_label_uses_conservative_stop_first_for_same_bar_ambiguity():
    config = StrategyTunablesConfig()
    start = datetime(2026, 9, 21, 4, 15, tzinfo=UTC)
    trigger_bar = _bar(start, open_=100.5, high=102.0, low=100.0, close=101.5)
    ambiguous = _bar(
        start + timedelta(minutes=15),
        open_=101.5,
        high=104.5,
        low=98.5,
        close=103.0,
    )
    row = {
        "direction": "CALL",
        "trigger": 101.0,
        "stop": 99.0,
        "risk_points": 2.0,
        "atr14": 2.0,
    }

    label = _trigger_label(
        row=row,
        future_bars=[trigger_bar, ambiguous],
        future_features={trigger_bar.end_time: _feature(trigger_bar)},
        config=config,
    )

    assert label["trigger_status"] == "TRIGGERED"
    assert label["hit_t1_before_stop"] is False
    assert label["t1_first_hit_r"] == -1.0


def test_research_label_tracks_runner_after_t1_without_changing_t1_score():
    config = StrategyTunablesConfig()
    start = datetime(2026, 9, 21, 4, 15, tzinfo=UTC)
    trigger_bar = _bar(start, open_=100.5, high=102.0, low=100.0, close=101.5)
    t1_bar = _bar(
        start + timedelta(minutes=15),
        open_=101.5,
        high=104.1,
        low=100.5,
        close=103.5,
    )
    runner_bar = _bar(
        start + timedelta(minutes=30),
        open_=103.5,
        high=106.1,
        low=103.0,
        close=105.5,
    )
    row = {
        "direction": "CALL",
        "trigger": 101.0,
        "stop": 99.0,
        "risk_points": 2.0,
        "atr14": 2.0,
    }

    label = _trigger_label(
        row=row,
        future_bars=[trigger_bar, t1_bar, runner_bar],
        future_features={trigger_bar.end_time: _feature(trigger_bar)},
        config=config,
    )

    assert label["hit_t1_before_stop"] is True
    assert label["hit_runner_before_stop"] is True
    assert label["t1_first_hit_r"] == config.t1_r


def test_walk_forward_folds_are_strictly_chronological_and_non_overlapping_within_fold():
    dates = [f"2026-01-{day:02d}" for day in range(1, 13)]
    folds = _walk_forward_folds(dates, train_sessions=6, test_sessions=3)

    assert len(folds) == 2
    first_train, first_test = folds[0]
    second_train, second_test = folds[1]

    assert first_train == dates[:6]
    assert first_test == dates[6:9]
    assert set(first_train).isdisjoint(first_test)
    assert second_train == dates[3:9]
    assert second_test == dates[9:12]
    assert set(second_train).isdisjoint(second_test)


def test_research_label_rejects_trigger_after_entry_window():
    config = StrategyTunablesConfig()
    # 14:45 IST is 09:15 UTC. A setup formed at 14:45 can only be tested by
    # later bars; a 15:00 trigger must not be credited as a Strategy A entry.
    start = datetime(2026, 9, 21, 9, 15, tzinfo=UTC)
    after_close = _bar(start, open_=100.5, high=102.0, low=100.0, close=101.5)
    row = {
        "direction": "CALL",
        "trigger": 101.0,
        "stop": 99.0,
        "risk_points": 2.0,
        "atr14": 2.0,
    }

    label = _trigger_label(
        row=row,
        future_bars=[after_close],
        future_features={after_close.end_time: _feature(after_close)},
        config=config,
    )

    assert after_close.end_time.hour == 9
    assert after_close.end_time.minute == 30
    assert label["trigger_status"] == "ENTRY_SESSION_CLOSED"
    assert label["entry_price"] is None
