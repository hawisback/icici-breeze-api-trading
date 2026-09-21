"""Canonical, deterministic NIFTY futures signal data path for Strategy A.

This module deliberately has no spot, option-chain, wall-clock, or broker
dependencies.  It is shared by runtime, paper, and replay callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.models import Candle

IST = timezone(timedelta(hours=5, minutes=30))
_EXPIRY_RE = re.compile(r"FUT-(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def _aware(value: datetime, name: str = "timestamp") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _ordered(candles: Iterable[Candle]) -> list[Candle]:
    return sorted(candles, key=lambda c: (c.start_time, c.end_time, c.instrument_id, c.source))


def completed_futures_candles(
    candles: Iterable[Candle],
    *,
    as_of: datetime | None = None,
    interval: str = "15m",
    max_age_seconds: float | None = None,
) -> list[Candle]:
    """Return valid completed candles known at ``as_of`` in deterministic order."""
    if as_of is not None:
        _aware(as_of, "as_of")
    result: list[Candle] = []
    for candle in _ordered(candles):
        if candle.interval != interval or candle.end_time <= candle.start_time:
            continue
        if candle.end_time > (as_of or candle.end_time):
            continue
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
            continue
        if candle.low > candle.high or candle.volume < 0:
            continue
        if max_age_seconds is not None and as_of is not None:
            age = (as_of - candle.end_time).total_seconds()
            if age < 0 or age > max_age_seconds:
                continue
        result.append(candle)
    return result


def aggregate_completed_15m(
    candles_5m: Iterable[Candle], *, as_of: datetime | None = None
) -> list[Candle]:
    """Aggregate exactly three contiguous completed 5m futures bars per 15m bar."""
    source = completed_futures_candles(candles_5m, as_of=as_of, interval="5m")
    buckets: dict[tuple[str, datetime], list[Candle]] = {}
    for candle in source:
        local = candle.start_time.astimezone(IST)
        start_local = local.replace(minute=(local.minute // 15) * 15, second=0, microsecond=0)
        key = (candle.instrument_id, start_local.astimezone(timezone.utc))
        buckets.setdefault(key, []).append(candle)
    result: list[Candle] = []
    for (instrument_id, start), values in sorted(buckets.items(), key=lambda item: item[0]):
        values = _ordered(values)
        expected = [start + timedelta(minutes=5 * i) for i in range(3)]
        if len(values) != 3 or [c.start_time for c in values] != expected:
            continue
        result.append(Candle(
            instrument_id=instrument_id,
            interval="15m",
            start_time=start,
            end_time=start + timedelta(minutes=15),
            open=values[0].open,
            high=max(c.high for c in values),
            low=min(c.low for c in values),
            close=values[-1].close,
            volume=sum(c.volume for c in values),
            open_interest=values[-1].open_interest,
            source=values[0].source,
        ))
    return result


def contract_expiry(instrument_id: str) -> date | None:
    match = _EXPIRY_RE.search(instrument_id)
    return date.fromisoformat(match.group(1)) if match else None


class FuturesContractResolver:
    """Resolve one active NIFTY futures contract without splicing contracts."""

    @staticmethod
    def resolve_contracts(
        contracts: dict[str, date | None], *, as_of: datetime
    ) -> str | None:
        _aware(as_of, "as_of")
        local_day = as_of.astimezone(IST).date()
        ranked = sorted(
            contracts,
            key=lambda instrument_id: (
                contracts[instrument_id] is None,
                contracts[instrument_id] or date.max,
                instrument_id,
            ),
        )
        non_expired = [
            instrument_id for instrument_id in ranked
            if (contracts[instrument_id] or date.max) >= local_day
        ]
        return non_expired[0] if non_expired else None

    def resolve(self, candles: Sequence[Candle], *, as_of: datetime) -> str:
        _aware(as_of, "as_of")
        contracts: dict[str, date | None] = {}
        for candle in candles:
            if candle.instrument_id.upper().find("NIFTY-FUT-") < 0:
                continue
            contracts[candle.instrument_id] = contract_expiry(candle.instrument_id)
        if not contracts:
            raise ValueError("no NIFTY futures candles available")
        selected = self.resolve_contracts(contracts, as_of=as_of)
        if selected is None:
            raise ValueError("no non-expired NIFTY futures contract available")
        return selected

    def select(self, candles: Sequence[Candle], *, as_of: datetime) -> list[Candle]:
        instrument_id = self.resolve(candles, as_of=as_of)
        return [c for c in _ordered(candles) if c.instrument_id == instrument_id]


def canonical_active_futures_stream(
    candles: Iterable[Candle], *, as_of: datetime | None = None, interval: str = "15m"
) -> list[Candle]:
    """Return one active completed futures candle per timestamp.

    Contract selection is performed at each candle timestamp using the same
    nearest non-expired policy as runtime metadata resolution.  This prevents
    overlapping near/next-month inputs from alternating contract IDs during
    replay or feature construction.
    """
    result, _ = canonical_active_futures_stream_with_diagnostics(candles, as_of=as_of, interval=interval)
    return result


@dataclass(frozen=True)
class FuturesDataGap:
    timestamp: datetime
    expected_contract: str | None
    reason: str = "ACTIVE_FUTURES_CANDLE_MISSING"


def canonical_active_futures_stream_with_diagnostics(
    candles: Iterable[Candle], *, as_of: datetime | None = None, interval: str = "15m"
) -> tuple[list[Candle], list[FuturesDataGap]]:
    """Resolve expected contracts from the full universe before candle lookup.

    A missing near-contract candle is a data gap; a present next contract is
    never substituted until the expiry policy actually selects it.
    """
    completed = completed_futures_candles(candles, as_of=as_of, interval=interval)
    by_timestamp: dict[datetime, list[Candle]] = {}
    for candle in completed:
        by_timestamp.setdefault(candle.end_time, []).append(candle)
    resolver = FuturesContractResolver()
    contracts = {
        candle.instrument_id: contract_expiry(candle.instrument_id)
        for candle in completed
        if "NIFTY-FUT-" in candle.instrument_id.upper()
    }
    result: list[Candle] = []
    gaps: list[FuturesDataGap] = []
    for timestamp in sorted(by_timestamp):
        selected_id = resolver.resolve_contracts(contracts, as_of=timestamp) if contracts else None
        selected = [c for c in _ordered(by_timestamp[timestamp]) if c.instrument_id == selected_id]
        if selected:
            result.append(selected[0])
        else:
            gaps.append(FuturesDataGap(timestamp=timestamp, expected_contract=selected_id))
    return result, gaps


def resolve_active_futures_instrument(instruments: Iterable[object], *, as_of: datetime) -> str | None:
    """Resolve the same nearest non-expired contract policy from instrument metadata."""
    _aware(as_of, "as_of")
    candidates: dict[str, date | None] = {}
    for instrument in instruments:
        if getattr(instrument, "segment", None) != "FUTURES" or not getattr(instrument, "tradable", False):
            continue
        expiry_value = getattr(instrument, "expiry", None)
        instrument_id = getattr(instrument, "instrument_id", None)
        if not expiry_value or not instrument_id:
            continue
        try:
            expiry = date.fromisoformat(str(expiry_value)[:10])
        except ValueError:
            continue
        candidates[str(instrument_id)] = expiry
    return FuturesContractResolver.resolve_contracts(candidates, as_of=as_of) if candidates else None


def resolve_completed_futures_contract(
    candles: Iterable[Candle], *, as_of: datetime, interval: str = "15m"
) -> list[Candle]:
    """Canonical completed-bar contract selection shared by runtime and replay."""
    return canonical_active_futures_stream(candles, as_of=as_of, interval=interval)


class ConfirmedPivot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str
    price: float
    pivot_timestamp: datetime
    confirmed_at: datetime
    bar_index: int = Field(ge=0)


class FuturesFeatureSnapshot(BaseModel):
    """All Strategy A signal inputs at one completed futures bar."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    contract_id: str
    candle_timestamp: datetime
    candle_start: datetime
    open: float
    high: float
    low: float
    close: float
    ema20: float
    ema50: float
    adx14: float
    plus_di14: float
    minus_di14: float
    atr14: float
    session_vwap: float
    support: float | None = None
    resistance: float | None = None
    pivots: tuple[ConfirmedPivot, ...] = ()
    bar_index: int = Field(ge=0)

    @property
    def trend(self) -> str:
        if self.ema20 > self.ema50 and self.plus_di14 > self.minus_di14:
            return "BULLISH"
        if self.ema20 < self.ema50 and self.minus_di14 > self.plus_di14:
            return "BEARISH"
        return "NEUTRAL"


class FuturesFeatureEngine:
    """Pure feature calculations over a single contract's completed bars."""

    @staticmethod
    def ema(values: Sequence[float], period: int) -> float:
        if not values:
            return 0.0
        alpha = 2.0 / (period + 1.0)
        value = float(values[0])
        for item in values[1:]:
            value = alpha * float(item) + (1.0 - alpha) * value
        return value

    @staticmethod
    def _true_ranges(candles: Sequence[Candle]) -> list[float]:
        result: list[float] = []
        previous_close: float | None = None
        for candle in candles:
            result.append(max(
                candle.high - candle.low,
                abs(candle.high - previous_close) if previous_close is not None else 0.0,
                abs(candle.low - previous_close) if previous_close is not None else 0.0,
            ))
            previous_close = candle.close
        return result

    @classmethod
    def atr(cls, candles: Sequence[Candle], period: int = 14) -> float:
        values = cls._true_ranges(candles)
        if not values:
            return 0.0
        if len(values) < period:
            return sum(values) / len(values)
        smoothed = sum(values[:period]) / period
        for value in values[period:]:
            smoothed = ((smoothed * (period - 1)) + value) / period
        return smoothed

    @classmethod
    def adx_di(cls, candles: Sequence[Candle], period: int = 14) -> tuple[float, float, float]:
        """Return canonical Wilder ADX, +DI and -DI for completed candles.

        ADX is seeded from the first full period of DX observations and then
        Wilder-smoothed. Until that seed exists, ADX is reported as 0.0 while
        the latest DI values remain available.
        """
        if len(candles) < 2:
            return 0.0, 0.0, 0.0
        tr: list[float] = []
        plus: list[float] = []
        minus: list[float] = []
        for previous, current in zip(candles, candles[1:]):
            up = current.high - previous.high
            down = previous.low - current.low
            tr.append(max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            ))
            plus.append(up if up > down and up > 0 else 0.0)
            minus.append(down if down > up and down > 0 else 0.0)
        if len(tr) < period:
            return 0.0, 0.0, 0.0

        tr_s = sum(tr[:period])
        plus_s = sum(plus[:period])
        minus_s = sum(minus[:period])
        latest_plus = latest_minus = 0.0
        dx_values: list[float] = []

        def append_dx() -> None:
            nonlocal latest_plus, latest_minus
            latest_plus = 100.0 * plus_s / tr_s if tr_s else 0.0
            latest_minus = 100.0 * minus_s / tr_s if tr_s else 0.0
            denom = latest_plus + latest_minus
            dx_values.append(
                100.0 * abs(latest_plus - latest_minus) / denom if denom else 0.0
            )

        append_dx()
        for index in range(period, len(tr)):
            tr_s = tr_s - tr_s / period + tr[index]
            plus_s = plus_s - plus_s / period + plus[index]
            minus_s = minus_s - minus_s / period + minus[index]
            append_dx()

        if len(dx_values) < period:
            return 0.0, latest_plus, latest_minus

        adx = sum(dx_values[:period]) / period
        for dx in dx_values[period:]:
            adx = ((adx * (period - 1)) + dx) / period
        return adx, latest_plus, latest_minus

    @staticmethod
    def session_vwap(candles: Sequence[Candle]) -> float:
        if not candles:
            return 0.0
        day = candles[-1].start_time.astimezone(IST).date()
        session = [c for c in candles if c.start_time.astimezone(IST).date() == day]
        value = sum(((c.high + c.low + c.close) / 3.0) * max(c.volume, 0) for c in session)
        volume = sum(max(c.volume, 0) for c in session)
        return value / volume if volume else session[-1].close

    @staticmethod
    def confirmed_pivots(candles: Sequence[Candle], left_right: int = 2) -> list[ConfirmedPivot]:
        pivots: list[ConfirmedPivot] = []
        width = left_right
        for index in range(width, len(candles) - width):
            current = candles[index]
            left = candles[index - width:index]
            right = candles[index + 1:index + width + 1]
            if current.low < min(c.low for c in left) and current.low <= min(c.low for c in right):
                pivots.append(ConfirmedPivot(
                    kind="SUPPORT", price=current.low,
                    pivot_timestamp=current.end_time,
                    confirmed_at=candles[index + width].end_time,
                    bar_index=index,
                ))
            if current.high > max(c.high for c in left) and current.high >= max(c.high for c in right):
                pivots.append(ConfirmedPivot(
                    kind="RESISTANCE", price=current.high,
                    pivot_timestamp=current.end_time,
                    confirmed_at=candles[index + width].end_time,
                    bar_index=index,
                ))
        return pivots

    @classmethod
    def build(cls, candles: Sequence[Candle], *, as_of: datetime | None = None) -> FuturesFeatureSnapshot:
        bars = canonical_active_futures_stream(candles, as_of=as_of, interval="15m")
        if not bars:
            raise ValueError("at least one completed 15m futures candle is required")
        last = bars[-1]
        pivots = [p for p in cls.confirmed_pivots(bars) if p.confirmed_at <= last.end_time]
        supports = [p.price for p in pivots if p.kind == "SUPPORT" and p.price <= last.close]
        resistances = [p.price for p in pivots if p.kind == "RESISTANCE" and p.price >= last.close]
        return FuturesFeatureSnapshot(
            contract_id=last.instrument_id,
            candle_timestamp=last.end_time,
            candle_start=last.start_time,
            open=last.open, high=last.high, low=last.low, close=last.close,
            ema20=cls.ema([c.close for c in bars], 20),
            ema50=cls.ema([c.close for c in bars], 50),
            adx14=cls.adx_di(bars, 14)[0],
            plus_di14=cls.adx_di(bars, 14)[1],
            minus_di14=cls.adx_di(bars, 14)[2],
            atr14=cls.atr(bars, 14),
            session_vwap=cls.session_vwap(bars),
            support=max(supports) if supports else None,
            resistance=min(resistances) if resistances else None,
            pivots=tuple(pivots),
            bar_index=len(bars) - 1,
        )
