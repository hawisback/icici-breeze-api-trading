from datetime import datetime, timedelta, timezone

from libs.contracts.models import Candle
from services.historical.strategy_c_candidate_manifest import CANDIDATE_ID
from services.historical.strategy_c_shadow_observer import (
    _run_lifecycle_to_as_of,
    candidate_id,
)
from services.historical.strategy_cd_multitimeframe_discovery import (
    DiscoveryConfig,
    Trigger,
)


UTC = timezone.utc


def _bar(start: datetime, *, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
        open_interest=1000,
        source="BREEZE",
    )


def _trigger(entry_end: datetime, direction: str = "CALL") -> Trigger:
    start = entry_end - timedelta(minutes=1)
    entry_bar = _bar(start, open_=100, high=101, low=99, close=100)
    return Trigger(
        family="di_continuation",
        direction=direction,
        setup_end=entry_end - timedelta(minutes=1),
        entry_bar=entry_bar,
        entry_price=100.0,
        initial_stop=90.0 if direction == "CALL" else 110.0,
        setup_atr=10.0,
        research_features={
            "trigger_delay_minutes": 1.0,
            "risk_atr": 1.0,
            "context_15m_ema_separation_atr": 1.0,
        },
    )


def test_shadow_observer_candidate_id_is_frozen():
    assert candidate_id() == CANDIDATE_ID


def test_lifecycle_remains_open_before_future_exit():
    entry_end = datetime(2026, 9, 23, 5, 0, tzinfo=UTC)
    trigger = _trigger(entry_end)
    bars = [
        _bar(entry_end, open_=100, high=105, low=98, close=104),
        _bar(entry_end + timedelta(minutes=1), open_=104, high=108, low=102, close=106),
    ]
    result = _run_lifecycle_to_as_of(
        trigger,
        bars,
        DiscoveryConfig(),
        as_of=entry_end + timedelta(minutes=2),
    )
    assert result.status == "OPEN"
    assert result.exit_time is None
    assert result.realized_r is None


def test_lifecycle_keeps_stop_first_same_minute_ambiguity():
    entry_end = datetime(2026, 9, 23, 5, 0, tzinfo=UTC)
    trigger = _trigger(entry_end)
    # With entry=100, stop=90 and target=120, both are touched in one minute.
    bar = _bar(entry_end, open_=100, high=121, low=89, close=110)
    result = _run_lifecycle_to_as_of(
        trigger,
        [bar],
        DiscoveryConfig(),
        as_of=bar.end_time,
    )
    assert result.status == "RESOLVED"
    assert result.exit_reason == "STOP_OR_TRAIL"
    assert result.realized_r == -1.0


def test_lifecycle_trailing_change_is_effective_next_minute():
    entry_end = datetime(2026, 9, 23, 5, 0, tzinfo=UTC)
    trigger = _trigger(entry_end)
    first = _bar(entry_end, open_=100, high=116, low=99, close=114)
    second = _bar(entry_end + timedelta(minutes=1), open_=114, high=115, low=107, close=109)
    result = _run_lifecycle_to_as_of(
        trigger,
        [first, second],
        DiscoveryConfig(),
        as_of=second.end_time,
    )
    assert result.status == "RESOLVED"
    assert result.exit_reason == "STOP_OR_TRAIL"
    # First bar arms 1.5R trail: 116 - 0.75R (7.5) = 108.5, active next bar.
    assert result.exit_price == 108.5
    assert result.realized_r == 0.85
