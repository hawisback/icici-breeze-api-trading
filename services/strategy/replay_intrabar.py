"""Replay-only ordering helpers for targeted Breeze 1-minute candles.

The 5-minute replay remains authoritative for all strategy calculations.  The
functions here consume only a previously identified ambiguous 5-minute
window and answer the narrower question of which event happened first inside
that window.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from services.strategy.models import TradeDirection


@dataclass(frozen=True, slots=True)
class IntrabarResolution:
    event: str
    ambiguous: bool
    event_time: datetime | None = None
    exit_price: float | None = None
    detail: str | None = None


def _is_call(direction: TradeDirection | str) -> bool:
    value = direction.value if isinstance(direction, TradeDirection) else str(direction)
    if value in {TradeDirection.BULLISH.value, "CALL"}:
        return True
    if value in {TradeDirection.BEARISH.value, "PUT"}:
        return False
    raise ValueError(f"Unsupported direction: {direction}")


def _bar_event(
    *,
    call: bool,
    open_price: float,
    high: float,
    low: float,
    trigger: float,
    stop: float,
) -> str | None:
    trigger_hit = high >= trigger if call else low <= trigger
    stop_hit = low <= stop if call else high >= stop
    if not trigger_hit and not stop_hit:
        return None
    if trigger_hit and not stop_hit:
        return "TRIGGER"
    if stop_hit and not trigger_hit:
        return "STOP"

    # If the opening print is already beyond one level, that level is known
    # to have occurred first.  Otherwise OHLC cannot order both touches.
    if call:
        if open_price <= stop:
            return "STOP_BEFORE_TRIGGER"
        if open_price >= trigger:
            return "TRIGGER_BEFORE_STOP"
    else:
        if open_price >= stop:
            return "STOP_BEFORE_TRIGGER"
        if open_price <= trigger:
            return "TRIGGER_BEFORE_STOP"
    return "SAME_MINUTE_AMBIGUOUS"


def resolve_entry_candle(
    direction: TradeDirection | str,
    *,
    trigger_price: float,
    active_stop: float,
    minute_candles: Iterable[object],
) -> IntrabarResolution:
    """Resolve trigger/stop ordering inside one already-identified entry bar."""
    if trigger_price <= 0 or active_stop <= 0:
        raise ValueError("trigger_price and active_stop must be positive")
    call = _is_call(direction)
    saw_trigger = False
    for candle in sorted(minute_candles, key=lambda item: item.start_time):
        event = _bar_event(
            call=call,
            open_price=candle.open,
            high=candle.high,
            low=candle.low,
            trigger=trigger_price,
            stop=active_stop,
        )
        if event == "SAME_MINUTE_AMBIGUOUS":
            return IntrabarResolution(
                event="STILL_AMBIGUOUS",
                ambiguous=True,
                event_time=candle.start_time,
                detail="trigger and stop both crossed inside one 1-minute candle",
            )
        if event == "STOP_BEFORE_TRIGGER":
            return IntrabarResolution(
                event="ADVERSE_BEFORE_ENTRY",
                ambiguous=False,
                event_time=candle.start_time,
                detail="stop level was reached before the trigger",
            )
        if event == "TRIGGER_BEFORE_STOP":
            gap = candle.open <= active_stop if call else candle.open >= active_stop
            return IntrabarResolution(
                event="ENTRY_THEN_STOP",
                ambiguous=False,
                event_time=candle.start_time,
                exit_price=candle.open if gap else active_stop,
                detail="trigger and stop were both crossed in one minute with trigger first",
            )
        if event == "TRIGGER":
            saw_trigger = True
        elif event == "STOP" and saw_trigger:
            gap = candle.open <= active_stop if call else candle.open >= active_stop
            return IntrabarResolution(
                event="ENTRY_THEN_STOP",
                ambiguous=False,
                event_time=candle.start_time,
                exit_price=candle.open if gap else active_stop,
                detail="stop was crossed after the trigger",
            )
        elif event == "STOP" and not saw_trigger:
            return IntrabarResolution(
                event="ADVERSE_BEFORE_ENTRY",
                ambiguous=False,
                event_time=candle.start_time,
                detail="adverse stop touch preceded the trigger",
            )

    if saw_trigger:
        return IntrabarResolution(event="ENTRY_AND_SURVIVE", ambiguous=False)
    return IntrabarResolution(
        event="STILL_AMBIGUOUS",
        ambiguous=True,
        detail="the supplied 1-minute window did not establish a trigger",
    )


def resolve_stop_order(
    direction: TradeDirection | str,
    *,
    active_stop: float,
    minute_candles: Iterable[object],
    favorable_level: float | None = None,
) -> IntrabarResolution:
    """Resolve a known active-stop crossing without activating future levels.

    ``favorable_level`` is optional and is used only when the caller has
    already identified a same-5-minute ladder/stop ambiguity.  A favorable
    threshold and stop in the same minute remain ambiguous unless the opening
    print establishes their order.
    """
    if active_stop <= 0:
        raise ValueError("active_stop must be positive")
    call = _is_call(direction)
    for candle in sorted(minute_candles, key=lambda item: item.start_time):
        stop_hit = candle.low <= active_stop if call else candle.high >= active_stop
        favorable_hit = False
        if favorable_level is not None:
            favorable_hit = candle.high >= favorable_level if call else candle.low <= favorable_level
        if favorable_hit and stop_hit:
            if call and candle.open <= active_stop:
                event = "STRUCTURAL_STOP"
            elif call and candle.open >= favorable_level:
                event = "FAVORABLE_THEN_STOP"
            elif not call and candle.open >= active_stop:
                event = "STRUCTURAL_STOP"
            elif not call and candle.open <= favorable_level:
                event = "FAVORABLE_THEN_STOP"
            else:
                return IntrabarResolution(
                    event="STILL_AMBIGUOUS",
                    ambiguous=True,
                    event_time=candle.start_time,
                    detail="favorable threshold and active stop both crossed in one 1-minute candle",
                )
        elif stop_hit:
            event = "STRUCTURAL_STOP"
        else:
            continue

        gap = candle.open <= active_stop if call else candle.open >= active_stop
        return IntrabarResolution(
            event=event,
            ambiguous=False,
            event_time=candle.start_time,
            exit_price=candle.open if gap else active_stop,
        )
    return IntrabarResolution(event="NO_STOP", ambiguous=False)
