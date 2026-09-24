"""Adapters that promote frozen Strategy C/D candidates into the common execution contract.

The research monitors remain the source of truth for signal discovery and
underlying lifecycle decisions.  This module only translates a fresh/open
candidate into the same StrategySignal type consumed by the main execution
engine; it does not relax candidate rules or synthesize signals.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.strategy.models import (
    OptionType,
    StrategyName,
    StrategySignal,
    TradeDirection,
)


def _aware_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    return None


MAX_EXECUTION_SIGNAL_LATENCY_SECONDS = 300.0


def _is_fresh(timestamp: datetime, as_of: datetime | None) -> bool:
    current = as_of or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return False
    age = (current - timestamp).total_seconds()
    return -5.0 <= age <= MAX_EXECUTION_SIGNAL_LATENCY_SECONDS


def strategy_c_signal_from_status(
    status: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> StrategySignal | None:
    """Translate a fresh/open frozen Strategy C candidate into an execution signal."""
    row = status.get("active_candidate_trade") or {}
    lifecycle = row.get("lifecycle") or {}
    if lifecycle.get("status") != "OPEN":
        return None

    signal_id = row.get("candidate_signal_id")
    timestamp = _aware_timestamp(row.get("entry_time"))
    direction_value = str(row.get("direction") or "").upper()
    if (
        not signal_id
        or timestamp is None
        or direction_value not in {"CALL", "PUT"}
        or not _is_fresh(timestamp, as_of)
    ):
        return None

    try:
        entry = float(row["entry_price"])
        stop = float(row["initial_stop"])
    except (KeyError, TypeError, ValueError):
        return None
    risk = abs(entry - stop)
    if entry <= 0 or stop <= 0 or risk <= 0:
        return None

    option_type = OptionType.CALL if direction_value == "CALL" else OptionType.PUT
    direction = TradeDirection.BULLISH if option_type == OptionType.CALL else TradeDirection.BEARISH
    return StrategySignal(
        signal_id=str(signal_id),
        strategy=StrategyName.DI_CONTINUATION,
        direction=direction,
        option_type=option_type,
        timestamp=timestamp,
        spot_reference_price=entry,
        underlying_entry_price=entry,
        structural_stop=stop,
        r_points=risk,
        derivatives_score=0.0,
        features_snapshot={
            "candidate_id": status.get("candidate_id"),
            "candidate_spec_fingerprint": status.get("candidate_spec_fingerprint"),
            "setup_end": row.get("setup_end"),
            "research_features": dict(row.get("research_features") or {}),
            "candidate_lifecycle": dict(lifecycle),
            "signal_source": "STRATEGY_C_FROZEN_RUNTIME",
        },
    )


def strategy_d_signal_from_status(
    status: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> StrategySignal | None:
    """Translate a fresh frozen Strategy D signal into the common execution contract."""
    payload = status.get("execution_signal") or {}
    signal_id = status.get("execution_signal_id")
    if not payload:
        tracked = (
            status.get("active_execution_trade")
            or status.get("active_paper_trade")
            or {}
        )
        payload = tracked.get("signal") or {}
        signal_id = tracked.get("signal_id")
        if not payload:
            return None
    timestamp = _aware_timestamp(payload.get("timestamp"))
    if not signal_id or timestamp is None or not _is_fresh(timestamp, as_of):
        return None
    try:
        direction = TradeDirection(str(payload["direction"]))
        option_type = OptionType(str(payload["option_type"]))
        entry = float(payload["entry_price"])
        stop = float(payload["initial_stop"])
        risk = float(payload.get("risk_points") or abs(entry - stop))
    except (KeyError, TypeError, ValueError):
        return None
    if entry <= 0 or stop <= 0 or risk <= 0:
        return None
    if (direction == TradeDirection.BULLISH) != (option_type == OptionType.CALL):
        return None

    return StrategySignal(
        signal_id=str(signal_id),
        strategy=StrategyName.SR_MOMENTUM_BREAKOUT,
        direction=direction,
        option_type=option_type,
        timestamp=timestamp,
        spot_reference_price=entry,
        underlying_entry_price=entry,
        structural_stop=stop,
        r_points=risk,
        derivatives_score=0.0,
        features_snapshot={
            "candidate_id": status.get("candidate_id"),
            "candidate_spec_fingerprint": status.get("candidate_spec_fingerprint"),
            "breakout_level_name": payload.get("breakout_level_name"),
            "breakout_level": payload.get("breakout_level"),
            "atr_5m": payload.get("atr_5m"),
            "rsi_previous": payload.get("rsi_previous"),
            "rsi_current": payload.get("rsi_current"),
            "previous_day_range_atr": payload.get("previous_day_range_atr"),
            "vwap_reference_price": payload.get("vwap_reference_price"),
            "vwap": payload.get("vwap"),
            "next_pivot_name": payload.get("next_pivot_name"),
            "next_pivot_price": payload.get("next_pivot_price"),
            "levels": dict(payload.get("levels") or {}),
            "signal_source": "STRATEGY_D_FROZEN_RUNTIME",
        },
    )
