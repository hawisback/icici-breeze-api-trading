"""Strategy D: Support & Resistance Momentum Breakout.

This module is intentionally isolated from the production Strategy A/B/C
scheduler. It contains the deterministic Strategy D V1 signal contract and
underlying lifecycle used by the read-only Breeze backtest harness.

Execution contract:
- NIFTY spot completed 5m candles provide PDH/PDL/pivot breakouts, RSI and ATR.
- Active NIFTY futures completed 5m candles provide the volume-backed session
  VWAP confirmation because the cash index itself is not a traded instrument.
- Option lot sizing reuses the platform PositionManager and the selected
  contract's lot_size; no NIFTY lot size is hard-coded here.
- The research lifecycle is measured in underlying R. Executable option P&L
  remains a later validation layer and is never synthesized from the index.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import RiskConfig, SessionTimersConfig, TradeDirection
from services.strategy.position_manager import PositionManager


IST = ZoneInfo("Asia/Kolkata")
STRATEGY_D_V1_ID = "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V1"
STRATEGY_D_V2_ID = "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE"
# Backward-compatible public alias. Production wiring must use the explicit
# config strategy_id rather than assuming this alias is the active candidate.
STRATEGY_D_ID = STRATEGY_D_V1_ID
REAL_SOURCES = {"BREEZE", "KITE", "LIVE"}


@dataclass(frozen=True)
class StrategyDConfig:
    """Frozen Strategy D hypothesis.

    v1_control preserves the original signal contract. v2_candidate changes
    only the two entry-quality filters supported across both 2025 and 2026 in
    the first Breeze research sample. The lifecycle/risk hypothesis is
    intentionally unchanged so the next backtest isolates entry improvements.
    """

    variant: str = "V1_CONTROL"
    rsi_period: int = 14
    long_rsi_cross: float = 60.0
    short_rsi_cross: float = 40.0
    minimum_rsi_clearance_points: float = 0.0
    trap_rsi_low: float = 45.0
    trap_rsi_high: float = 55.0
    atr_period: int = 14
    max_previous_day_range_atr: float | None = None
    atr_stop_multiple: float = 1.50
    scale_out_r: float = 1.50
    scale_out_fraction: float = 0.50
    entry_start: str = "09:20"
    entry_end: str = "14:45"
    force_exit: str = "15:20"

    @classmethod
    def v1_control(cls) -> "StrategyDConfig":
        return cls()

    @classmethod
    def v2_candidate(cls) -> "StrategyDConfig":
        return cls(
            variant="V2_CANDIDATE",
            minimum_rsi_clearance_points=2.0,
            max_previous_day_range_atr=8.0,
        )

    @property
    def strategy_id(self) -> str:
        return (
            STRATEGY_D_V2_ID
            if self.variant == "V2_CANDIDATE"
            else STRATEGY_D_V1_ID
        )

    @property
    def ruleset_version(self) -> int:
        return 2 if self.variant == "V2_CANDIDATE" else 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PivotLevels:
    session_date: date
    source_session_date: date
    pdh: float
    pdl: float
    pdc: float
    pivot: float
    r1: float
    s1: float
    r2: float
    s2: float

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["session_date"] = self.session_date.isoformat()
        payload["source_session_date"] = self.source_session_date.isoformat()
        return payload


@dataclass(frozen=True)
class StrategyDSignal:
    strategy_id: str
    direction: TradeDirection
    option_type: str
    timestamp: datetime
    breakout_level_name: str
    breakout_level: float
    entry_price: float
    initial_stop: float
    risk_points: float
    atr_5m: float
    rsi_previous: float
    rsi_current: float
    rsi_clearance_points: float
    previous_day_range_atr: float
    vwap_reference_price: float
    vwap: float
    vwap_source: str
    next_pivot_name: Optional[str]
    next_pivot_price: Optional[float]
    levels: PivotLevels

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["timestamp"] = self.timestamp.isoformat()
        payload["levels"] = self.levels.to_dict()
        return payload


@dataclass(frozen=True)
class StrategyDLifecycleResult:
    entry_time: datetime
    exit_time: datetime
    direction: TradeDirection
    entry_price: float
    initial_stop: float
    scale_out_time: Optional[datetime]
    scale_out_price: Optional[float]
    scale_out_fraction: float
    runner_exit_price: float
    runner_exit_reason: str
    runner_r: float
    realized_r: float
    mfe_r: float
    mae_r: float
    final_stop: float

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["entry_time"] = self.entry_time.isoformat()
        payload["exit_time"] = self.exit_time.isoformat()
        payload["scale_out_time"] = (
            self.scale_out_time.isoformat() if self.scale_out_time else None
        )
        payload["direction"] = self.direction.value
        return payload


def classic_pivot_levels(
    *,
    session_date: date,
    source_session_date: date,
    high: float,
    low: float,
    close: float,
) -> PivotLevels:
    """Calculate classic P, R1/S1 and R2/S2 from the previous session."""
    if high <= 0 or low <= 0 or close <= 0 or high < low:
        raise ValueError("invalid previous-session OHLC for pivot calculation")
    pivot = (high + low + close) / 3.0
    r1 = (2.0 * pivot) - low
    s1 = (2.0 * pivot) - high
    span = high - low
    r2 = pivot + span
    s2 = pivot - span
    return PivotLevels(
        session_date=session_date,
        source_session_date=source_session_date,
        pdh=round(high, 6),
        pdl=round(low, 6),
        pdc=round(close, 6),
        pivot=round(pivot, 6),
        r1=round(r1, 6),
        s1=round(s1, 6),
        r2=round(r2, 6),
        s2=round(s2, 6),
    )


def previous_session_levels(
    spot_candles: Sequence[Candle],
    session_date: date,
) -> Optional[PivotLevels]:
    """Build Strategy D static levels using only the prior trading session."""
    grouped: dict[date, list[Candle]] = {}
    for candle in spot_candles:
        if candle.interval != "5m" or candle.source not in REAL_SOURCES:
            continue
        local_day = candle.start_time.astimezone(IST).date()
        if local_day >= session_date:
            continue
        grouped.setdefault(local_day, []).append(candle)
    if not grouped:
        return None
    previous_day = max(grouped)
    bars = sorted(grouped[previous_day], key=lambda item: item.start_time)
    if not bars:
        return None
    return classic_pivot_levels(
        session_date=session_date,
        source_session_date=previous_day,
        high=max(float(bar.high) for bar in bars),
        low=min(float(bar.low) for bar in bars),
        close=float(bars[-1].close),
    )


def _clock(value: str) -> time:
    hour, minute = map(int, value.split(":"))
    return time(hour, minute)


def _in_entry_window(timestamp: datetime, config: StrategyDConfig) -> bool:
    local = timestamp.astimezone(IST).time().replace(tzinfo=None)
    return _clock(config.entry_start) <= local <= _clock(config.entry_end)


def _completed_real_5m(candles: Iterable[Candle], *, through: datetime) -> list[Candle]:
    return sorted(
        {
            candle.start_time: candle
            for candle in candles
            if candle.interval == "5m"
            and candle.source in REAL_SOURCES
            and candle.end_time <= through
            and candle.low > 0
            and candle.low <= min(candle.open, candle.close)
            <= max(candle.open, candle.close) <= candle.high
        }.values(),
        key=lambda item: item.start_time,
    )


def _futures_vwap_confirmation(
    futures_candles: Sequence[Candle],
    *,
    through: datetime,
) -> tuple[float, float] | None:
    eligible = _completed_real_5m(futures_candles, through=through)
    session_day = through.astimezone(IST).date()
    eligible = [
        bar for bar in eligible
        if bar.start_time.astimezone(IST).date() == session_day
    ]
    if not eligible:
        return None
    vwap = FeatureEngine.calculate_futures_vwap(eligible)
    if vwap <= 0:
        return None
    return float(eligible[-1].close), float(vwap)


def _crossed_resistance(
    previous_close: float,
    current_close: float,
    levels: PivotLevels,
) -> tuple[str, float] | None:
    crossed = [
        ("PDH", levels.pdh) if previous_close <= levels.pdh < current_close else None,
        ("R1", levels.r1) if previous_close <= levels.r1 < current_close else None,
    ]
    valid = [item for item in crossed if item is not None]
    return max(valid, key=lambda item: item[1]) if valid else None


def _crossed_support(
    previous_close: float,
    current_close: float,
    levels: PivotLevels,
) -> tuple[str, float] | None:
    crossed = [
        ("PDL", levels.pdl) if previous_close >= levels.pdl > current_close else None,
        ("S1", levels.s1) if previous_close >= levels.s1 > current_close else None,
    ]
    valid = [item for item in crossed if item is not None]
    return min(valid, key=lambda item: item[1]) if valid else None


def evaluate_strategy_d_signal(
    spot_history: Sequence[Candle],
    futures_history: Sequence[Candle],
    levels: PivotLevels,
    config: StrategyDConfig | None = None,
) -> Optional[StrategyDSignal]:
    """Evaluate one completed 5m Strategy D breakout without look-ahead."""
    cfg = config or StrategyDConfig()
    if len(spot_history) < cfg.rsi_period + 2:
        return None
    current = spot_history[-1]
    previous = spot_history[-2]
    if current.interval != "5m" or previous.interval != "5m":
        return None
    if current.source not in REAL_SOURCES or previous.source not in REAL_SOURCES:
        return None
    session_day = current.end_time.astimezone(IST).date()
    if session_day != levels.session_date or previous.end_time >= current.end_time:
        return None
    if not _in_entry_window(current.end_time, cfg):
        return None

    closes = [float(bar.close) for bar in spot_history]
    previous_rsi = FeatureEngine.calculate_rsi(closes[:-1], cfg.rsi_period)
    current_rsi = FeatureEngine.calculate_rsi(closes, cfg.rsi_period)
    if cfg.trap_rsi_low <= current_rsi <= cfg.trap_rsi_high:
        return None

    atr = FeatureEngine.calculate_atr(list(spot_history), cfg.atr_period)
    if atr <= 0:
        return None
    previous_day_range_atr = (levels.pdh - levels.pdl) / float(atr)
    if (
        cfg.max_previous_day_range_atr is not None
        and previous_day_range_atr >= cfg.max_previous_day_range_atr
    ):
        return None
    confirmation = _futures_vwap_confirmation(
        futures_history,
        through=current.end_time,
    )
    if confirmation is None:
        return None
    futures_price, vwap = confirmation

    resistance = _crossed_resistance(
        float(previous.close),
        float(current.close),
        levels,
    )
    support = _crossed_support(
        float(previous.close),
        float(current.close),
        levels,
    )

    direction: TradeDirection | None = None
    option_type: str | None = None
    level: tuple[str, float] | None = None
    if (
        resistance is not None
        and futures_price > vwap
        and previous_rsi <= cfg.long_rsi_cross
        and current_rsi
        > cfg.long_rsi_cross + cfg.minimum_rsi_clearance_points
    ):
        direction = TradeDirection.BULLISH
        option_type = "CALL"
        level = resistance
    elif (
        support is not None
        and futures_price < vwap
        and previous_rsi >= cfg.short_rsi_cross
        and current_rsi
        < cfg.short_rsi_cross - cfg.minimum_rsi_clearance_points
    ):
        direction = TradeDirection.BEARISH
        option_type = "PUT"
        level = support
    if direction is None or option_type is None or level is None:
        return None

    entry = float(current.close)
    risk_points = cfg.atr_stop_multiple * float(atr)
    if risk_points <= 0:
        return None
    if direction == TradeDirection.BULLISH:
        stop = entry - risk_points
        next_name, next_price = "R2", levels.r2
        next_r = (next_price - entry) / risk_points
    else:
        stop = entry + risk_points
        next_name, next_price = "S2", levels.s2
        next_r = (entry - next_price) / risk_points

    if next_r <= cfg.scale_out_r:
        next_name, next_price = None, None

    rsi_clearance = (
        current_rsi - cfg.long_rsi_cross
        if direction == TradeDirection.BULLISH
        else cfg.short_rsi_cross - current_rsi
    )

    return StrategyDSignal(
        strategy_id=cfg.strategy_id,
        direction=direction,
        option_type=option_type,
        timestamp=current.end_time,
        breakout_level_name=level[0],
        breakout_level=round(level[1], 6),
        entry_price=round(entry, 6),
        initial_stop=round(stop, 6),
        risk_points=round(risk_points, 6),
        atr_5m=round(float(atr), 6),
        rsi_previous=round(float(previous_rsi), 6),
        rsi_current=round(float(current_rsi), 6),
        rsi_clearance_points=round(float(rsi_clearance), 6),
        previous_day_range_atr=round(float(previous_day_range_atr), 6),
        vwap_reference_price=round(futures_price, 6),
        vwap=round(vwap, 6),
        vwap_source="ACTIVE_NIFTY_FUTURES_5M",
        next_pivot_name=next_name,
        next_pivot_price=round(next_price, 6) if next_price is not None else None,
        levels=levels,
    )


class StrategyDPositionManager(PositionManager):
    """Strategy-D lifecycle layered on the existing risk/session manager."""

    def __init__(
        self,
        risk_config: RiskConfig | None = None,
        session_config: SessionTimersConfig | None = None,
        strategy_d_config: StrategyDConfig | None = None,
    ) -> None:
        super().__init__(
            risk_config=risk_config,
            session_config=session_config,
        )
        self.strategy_d_config = strategy_d_config or StrategyDConfig()

    def size_option_position(
        self,
        *,
        entry_premium: float,
        lot_size: int,
        account_equity: float | None = None,
    ) -> tuple[int, int]:
        """Reuse platform capital/risk sizing with the broker contract lot size."""
        return self.calculate_position_size(
            entry_premium=entry_premium,
            account_equity=account_equity or self.risk_config.account_equity,
            lot_size=lot_size,
        )

    @staticmethod
    def scale_out_lots(total_lots: int) -> int:
        """Exit about half the position while preserving whole exchange lots."""
        return total_lots // 2 if total_lots >= 2 else 0

    def is_strategy_d_force_exit_time(self, as_of: datetime) -> bool:
        local = as_of.astimezone(IST)
        exit_at = _clock(self.strategy_d_config.force_exit)
        return local.time().replace(tzinfo=None) >= exit_at

    @staticmethod
    def _r(signal: StrategyDSignal, price: float) -> float:
        if signal.direction == TradeDirection.BULLISH:
            return (price - signal.entry_price) / signal.risk_points
        return (signal.entry_price - price) / signal.risk_points

    @staticmethod
    def _stop_hit(
        direction: TradeDirection,
        bar: Candle,
        stop: float,
    ) -> bool:
        if direction == TradeDirection.BULLISH:
            return float(bar.low) <= stop
        return float(bar.high) >= stop

    @staticmethod
    def _target_hit(
        direction: TradeDirection,
        bar: Candle,
        target: float,
    ) -> bool:
        if direction == TradeDirection.BULLISH:
            return float(bar.high) >= target
        return float(bar.low) <= target

    @staticmethod
    def _stop_fill(
        direction: TradeDirection,
        bar: Candle,
        stop: float,
    ) -> float:
        """Use the bar open when a gap already crossed the protective stop."""
        opening = float(bar.open)
        if direction == TradeDirection.BULLISH and opening < stop:
            return opening
        if direction == TradeDirection.BEARISH and opening > stop:
            return opening
        return stop

    @staticmethod
    def _ema_exit(
        direction: TradeDirection,
        close: float,
        ema9: float,
    ) -> bool:
        return (
            close <= ema9
            if direction == TradeDirection.BULLISH
            else close >= ema9
        )

    def _result(
        self,
        signal: StrategyDSignal,
        *,
        bar: Candle,
        stop: float,
        scale_time: datetime | None,
        scale_fill: float | None,
        scale_fraction: float,
        exit_price: float,
        exit_reason: str,
        realized_component: float,
        remaining_fraction: float,
        mfe_r: float,
        mae_r: float,
    ) -> StrategyDLifecycleResult:
        runner_r = self._r(signal, exit_price)
        total_r = realized_component + remaining_fraction * runner_r
        return StrategyDLifecycleResult(
            entry_time=signal.timestamp,
            exit_time=bar.end_time,
            direction=signal.direction,
            entry_price=signal.entry_price,
            initial_stop=signal.initial_stop,
            scale_out_time=scale_time,
            scale_out_price=round(scale_fill, 6) if scale_fill is not None else None,
            scale_out_fraction=scale_fraction,
            runner_exit_price=round(exit_price, 6),
            runner_exit_reason=exit_reason,
            runner_r=round(runner_r, 6),
            realized_r=round(total_r, 6),
            mfe_r=round(mfe_r, 6),
            mae_r=round(mae_r, 6),
            final_stop=round(stop, 6),
        )

    @staticmethod
    def _complete_minutes(
        parent: Candle,
        one_minute_bars: Sequence[Candle],
    ) -> list[Candle]:
        """Return the five native 1m children only when coverage is complete."""
        minutes = sorted(
            [
                bar
                for bar in one_minute_bars
                if bar.interval == "1m"
                and bar.source in REAL_SOURCES
                and bar.start_time >= parent.start_time
                and bar.end_time <= parent.end_time
            ],
            key=lambda item: item.start_time,
        )
        if len(minutes) != 5:
            return []
        cursor = parent.start_time
        for minute in minutes:
            if minute.start_time != cursor:
                return []
            cursor = minute.end_time
        return minutes if cursor == parent.end_time else []

    def replay_underlying_lifecycle(
        self,
        signal: StrategyDSignal,
        *,
        history_through_entry: Sequence[Candle],
        future_bars: Sequence[Candle],
        one_minute_bars: Sequence[Candle] | None = None,
    ) -> StrategyDLifecycleResult:
        """Replay Strategy D using native 1m ordering whenever it is complete.

        The signal and EMA9 remain completed-5m decisions. Native 1m children
        are used only to order intrabar ATR stop, +1.5R scale-out, breakeven,
        and next-pivot events. If a 5m parent lacks a complete five-minute
        child set, the replay falls back conservatively to 5m OHLC. A newly
        activated breakeven stop is never applied retroactively to earlier
        prices in that same 5m fallback bar.
        """
        cfg = self.strategy_d_config
        if not future_bars:
            raise ValueError(
                "Strategy D lifecycle requires at least one post-entry bar"
            )

        stop = signal.initial_stop
        scale_price = (
            signal.entry_price + cfg.scale_out_r * signal.risk_points
            if signal.direction == TradeDirection.BULLISH
            else signal.entry_price - cfg.scale_out_r * signal.risk_points
        )
        running = list(history_through_entry)
        minute_source = list(one_minute_bars or [])
        scale_time: datetime | None = None
        scale_fill: float | None = None
        remaining_fraction = 1.0
        realized_component = 0.0
        mfe_r = 0.0
        mae_r = 0.0
        last_bar: Candle | None = None

        def update_excursions(price_bar: Candle) -> None:
            nonlocal mfe_r, mae_r
            high_r = self._r(signal, float(price_bar.high))
            low_r = self._r(signal, float(price_bar.low))
            mfe_r = max(mfe_r, high_r, low_r)
            mae_r = min(mae_r, high_r, low_r)

        for bar in future_bars:
            if bar.interval != "5m" or bar.source not in REAL_SOURCES:
                continue
            if (
                bar.start_time.astimezone(IST).date()
                != signal.timestamp.astimezone(IST).date()
            ):
                continue
            last_bar = bar
            minutes = self._complete_minutes(bar, minute_source)
            newly_scaled_without_minutes = False

            if minutes:
                for minute in minutes:
                    update_excursions(minute)
                    if scale_time is None:
                        stop_hit = self._stop_hit(
                            signal.direction,
                            minute,
                            stop,
                        )
                        scale_hit = self._target_hit(
                            signal.direction,
                            minute,
                            scale_price,
                        )
                        # Same-minute initial stop/target ambiguity remains
                        # conservative: the pre-existing protective stop wins.
                        if stop_hit:
                            stop_fill = self._stop_fill(
                                signal.direction,
                                minute,
                                stop,
                            )
                            return self._result(
                                signal,
                                bar=minute,
                                stop=stop,
                                scale_time=None,
                                scale_fill=None,
                                scale_fraction=0.0,
                                exit_price=stop_fill,
                                exit_reason="ATR_HARD_STOP",
                                realized_component=0.0,
                                remaining_fraction=1.0,
                                mfe_r=mfe_r,
                                mae_r=mae_r,
                            )
                        if scale_hit:
                            scale_time = minute.end_time
                            scale_fill = scale_price
                            realized_component = (
                                cfg.scale_out_fraction * cfg.scale_out_r
                            )
                            remaining_fraction = (
                                1.0 - cfg.scale_out_fraction
                            )
                            stop = signal.entry_price

                            # If the activation minute itself spans both +1.5R
                            # and the newly armed breakeven, 1m OHLC still
                            # cannot prove order. Conservatively realize the
                            # runner at breakeven.
                            if self._stop_hit(
                                signal.direction,
                                minute,
                                stop,
                            ):
                                return self._result(
                                    signal,
                                    bar=minute,
                                    stop=stop,
                                    scale_time=scale_time,
                                    scale_fill=scale_fill,
                                    scale_fraction=cfg.scale_out_fraction,
                                    exit_price=stop,
                                    exit_reason="BREAKEVEN_STOP_1M_AMBIGUOUS",
                                    realized_component=realized_component,
                                    remaining_fraction=remaining_fraction,
                                    mfe_r=mfe_r,
                                    mae_r=mae_r,
                                )
                            if (
                                signal.next_pivot_price is not None
                                and self._target_hit(
                                    signal.direction,
                                    minute,
                                    signal.next_pivot_price,
                                )
                            ):
                                return self._result(
                                    signal,
                                    bar=minute,
                                    stop=stop,
                                    scale_time=scale_time,
                                    scale_fill=scale_fill,
                                    scale_fraction=cfg.scale_out_fraction,
                                    exit_price=signal.next_pivot_price,
                                    exit_reason=(
                                        f"NEXT_PIVOT_{signal.next_pivot_name}"
                                    ),
                                    realized_component=realized_component,
                                    remaining_fraction=remaining_fraction,
                                    mfe_r=mfe_r,
                                    mae_r=mae_r,
                                )
                            continue
                    else:
                        if self._stop_hit(
                            signal.direction,
                            minute,
                            stop,
                        ):
                            stop_fill = self._stop_fill(
                                signal.direction,
                                minute,
                                stop,
                            )
                            return self._result(
                                signal,
                                bar=minute,
                                stop=stop,
                                scale_time=scale_time,
                                scale_fill=scale_fill,
                                scale_fraction=cfg.scale_out_fraction,
                                exit_price=stop_fill,
                                exit_reason="BREAKEVEN_STOP",
                                realized_component=realized_component,
                                remaining_fraction=remaining_fraction,
                                mfe_r=mfe_r,
                                mae_r=mae_r,
                            )
                        if (
                            signal.next_pivot_price is not None
                            and self._target_hit(
                                signal.direction,
                                minute,
                                signal.next_pivot_price,
                            )
                        ):
                            return self._result(
                                signal,
                                bar=minute,
                                stop=stop,
                                scale_time=scale_time,
                                scale_fill=scale_fill,
                                scale_fraction=cfg.scale_out_fraction,
                                exit_price=signal.next_pivot_price,
                                exit_reason=(
                                    f"NEXT_PIVOT_{signal.next_pivot_name}"
                                ),
                                realized_component=realized_component,
                                remaining_fraction=remaining_fraction,
                                mfe_r=mfe_r,
                                mae_r=mae_r,
                            )
            else:
                update_excursions(bar)
                if scale_time is None:
                    if self._stop_hit(signal.direction, bar, stop):
                        stop_fill = self._stop_fill(
                            signal.direction,
                            bar,
                            stop,
                        )
                        return self._result(
                            signal,
                            bar=bar,
                            stop=stop,
                            scale_time=None,
                            scale_fill=None,
                            scale_fraction=0.0,
                            exit_price=stop_fill,
                            exit_reason="ATR_HARD_STOP",
                            realized_component=0.0,
                            remaining_fraction=1.0,
                            mfe_r=mfe_r,
                            mae_r=mae_r,
                        )
                    if self._target_hit(
                        signal.direction,
                        bar,
                        scale_price,
                    ):
                        scale_time = bar.end_time
                        scale_fill = scale_price
                        realized_component = (
                            cfg.scale_out_fraction * cfg.scale_out_r
                        )
                        remaining_fraction = 1.0 - cfg.scale_out_fraction
                        stop = signal.entry_price
                        newly_scaled_without_minutes = True
                if scale_time is not None and newly_scaled_without_minutes:
                    if self._stop_hit(signal.direction, bar, stop):
                        return self._result(
                            signal,
                            bar=bar,
                            stop=stop,
                            scale_time=scale_time,
                            scale_fill=scale_fill,
                            scale_fraction=cfg.scale_out_fraction,
                            exit_price=stop,
                            exit_reason="BREAKEVEN_STOP_5M_AMBIGUOUS",
                            realized_component=realized_component,
                            remaining_fraction=remaining_fraction,
                            mfe_r=mfe_r,
                            mae_r=mae_r,
                        )
                    if (
                        signal.next_pivot_price is not None
                        and self._target_hit(
                            signal.direction,
                            bar,
                            signal.next_pivot_price,
                        )
                    ):
                        return self._result(
                            signal,
                            bar=bar,
                            stop=stop,
                            scale_time=scale_time,
                            scale_fill=scale_fill,
                            scale_fraction=cfg.scale_out_fraction,
                            exit_price=signal.next_pivot_price,
                            exit_reason=(
                                f"NEXT_PIVOT_{signal.next_pivot_name}"
                            ),
                            realized_component=realized_component,
                            remaining_fraction=remaining_fraction,
                            mfe_r=mfe_r,
                            mae_r=mae_r,
                        )
                elif scale_time is not None:
                    if self._stop_hit(signal.direction, bar, stop):
                        stop_fill = self._stop_fill(
                            signal.direction,
                            bar,
                            stop,
                        )
                        return self._result(
                            signal,
                            bar=bar,
                            stop=stop,
                            scale_time=scale_time,
                            scale_fill=scale_fill,
                            scale_fraction=cfg.scale_out_fraction,
                            exit_price=stop_fill,
                            exit_reason="BREAKEVEN_STOP",
                            realized_component=realized_component,
                            remaining_fraction=remaining_fraction,
                            mfe_r=mfe_r,
                            mae_r=mae_r,
                        )
                    if (
                        signal.next_pivot_price is not None
                        and self._target_hit(
                            signal.direction,
                            bar,
                            signal.next_pivot_price,
                        )
                    ):
                        return self._result(
                            signal,
                            bar=bar,
                            stop=stop,
                            scale_time=scale_time,
                            scale_fill=scale_fill,
                            scale_fraction=cfg.scale_out_fraction,
                            exit_price=signal.next_pivot_price,
                            exit_reason=(
                                f"NEXT_PIVOT_{signal.next_pivot_name}"
                            ),
                            realized_component=realized_component,
                            remaining_fraction=remaining_fraction,
                            mfe_r=mfe_r,
                            mae_r=mae_r,
                        )

            running.append(bar)
            closes = [float(item.close) for item in running]
            ema9 = FeatureEngine.calculate_ema(closes, 9)

            if (
                scale_time is not None
                and self._ema_exit(
                    signal.direction,
                    float(bar.close),
                    ema9,
                )
            ):
                return self._result(
                    signal,
                    bar=bar,
                    stop=stop,
                    scale_time=scale_time,
                    scale_fill=scale_fill,
                    scale_fraction=cfg.scale_out_fraction,
                    exit_price=float(bar.close),
                    exit_reason="EMA9_CLOSE_CROSS",
                    realized_component=realized_component,
                    remaining_fraction=remaining_fraction,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                )

            if self.is_strategy_d_force_exit_time(bar.end_time):
                return self._result(
                    signal,
                    bar=bar,
                    stop=stop,
                    scale_time=scale_time,
                    scale_fill=scale_fill,
                    scale_fraction=(
                        cfg.scale_out_fraction
                        if scale_time is not None
                        else 0.0
                    ),
                    exit_price=float(bar.close),
                    exit_reason="SESSION_FORCE_EXIT",
                    realized_component=realized_component,
                    remaining_fraction=remaining_fraction,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                )

        if last_bar is None:
            raise ValueError(
                "Strategy D lifecycle found no valid post-entry bars"
            )
        return self._result(
            signal,
            bar=last_bar,
            stop=stop,
            scale_time=scale_time,
            scale_fill=scale_fill,
            scale_fraction=(
                cfg.scale_out_fraction if scale_time is not None else 0.0
            ),
            exit_price=float(last_bar.close),
            exit_reason="DATA_END",
            realized_component=realized_component,
            remaining_fraction=remaining_fraction,
            mfe_r=mfe_r,
            mae_r=mae_r,
        )

