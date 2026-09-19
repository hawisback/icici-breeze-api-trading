"""Replay-only structural stop decisions.

This module deliberately has no live-position or strategy logic.  A replay
caller supplies the stop that was active at the start of the candle; the
caller may advance that stop only after the candle has been processed.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.strategy.models import TradeDirection


@dataclass(frozen=True)
class ReplayStopDecision:
    """Result of applying one historical candle to an already-active stop."""

    event: str
    crossed: bool
    ambiguous: bool
    stop_level: float
    exit_price: float | None = None


def evaluate_replay_candle(
    direction: TradeDirection | str,
    *,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    active_stop: float,
    entry_candle: bool = False,
    thesis_invalidated_at_close: bool = False,
) -> ReplayStopDecision:
    """Evaluate an OHLC candle against the stop active at candle start.

    Structural crossing is checked from the candle range before the completed
    close thesis rule.  A normal-side open fills at the active stop; an open
    already beyond the stop preserves gap risk by filling at the open.

    The entry candle is special: OHLC cannot establish whether entry happened
    before or after a stop touch, so a crossing is reported as ambiguous.
    ``active_stop`` is intentionally an input and is never recalculated here,
    enforcing the replay no-lookahead boundary.
    """
    if active_stop <= 0:
        raise ValueError("active_stop must be positive")
    if candle_low > candle_high:
        raise ValueError("candle_low must not exceed candle_high")

    direction_value = direction.value if isinstance(direction, TradeDirection) else str(direction)
    is_call = direction_value in {TradeDirection.BULLISH.value, "CALL"}
    is_put = direction_value in {TradeDirection.BEARISH.value, "PUT"}
    if not (is_call or is_put):
        raise ValueError(f"Unsupported direction: {direction}")

    crossed = candle_low <= active_stop if is_call else candle_high >= active_stop
    if crossed:
        if entry_candle:
            return ReplayStopDecision(
                event="AMBIGUOUS_ENTRY_CANDLE",
                crossed=True,
                ambiguous=True,
                stop_level=active_stop,
            )

        opened_beyond_stop = candle_open <= active_stop if is_call else candle_open >= active_stop
        return ReplayStopDecision(
            event="STRUCTURAL_STOP",
            crossed=True,
            ambiguous=False,
            stop_level=active_stop,
            exit_price=candle_open if opened_beyond_stop else active_stop,
        )

    if thesis_invalidated_at_close:
        return ReplayStopDecision(
            event="THESIS_INVALIDATION",
            crossed=False,
            ambiguous=False,
            stop_level=active_stop,
        )

    return ReplayStopDecision(
        event="NO_EXIT",
        crossed=False,
        ambiguous=False,
        stop_level=active_stop,
    )
