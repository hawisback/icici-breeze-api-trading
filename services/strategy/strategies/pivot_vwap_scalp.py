"""Strategy E: lightweight 5-minute Pivot/VWAP intraday scalp.

The signal engine is intentionally broker-agnostic. It consumes completed,
real NIFTY futures candles and emits a canonical StrategySignal. Option
selection, sizing, PAPER/LIVE execution, broker protection, reconciliation,
and kill-switch handling remain owned by StrategyService/OMS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Sequence

from libs.contracts.models import Candle
from services.strategy.models import (
    OptionType,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
    TradeDirection,
)


IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class StrategyEDecision:
    result: str
    reason: str
    metrics: dict[str, Any]
    signal: StrategySignal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "reason": self.reason,
            **self.metrics,
            "signal_id": self.signal.signal_id if self.signal else None,
        }


class PivotVwapScalpStrategy:
    """Deterministic, completed-5m Strategy E signal engine."""

    def __init__(self, config: StrategyTunablesConfig) -> None:
        self.config = config
        self.last_processed_candle: datetime | None = None
        self.last_decision: StrategyEDecision = StrategyEDecision(
            "NO_TRADE",
            "NOT_EVALUATED",
            {},
        )

    def export_state(self) -> dict[str, Any]:
        return {
            "last_processed_candle": (
                self.last_processed_candle.isoformat()
                if self.last_processed_candle
                else None
            ),
            "last_decision": self.last_decision.to_dict(),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        raw = state.get("last_processed_candle")
        try:
            self.last_processed_candle = datetime.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            self.last_processed_candle = None

    def reset(self) -> None:
        self.last_processed_candle = None
        self.last_decision = StrategyEDecision(
            "NO_TRADE",
            "RESET",
            {},
        )

    @staticmethod
    def _valid_completed(
        candles: Sequence[Candle],
        as_of: datetime,
    ) -> list[Candle]:
        return sorted(
            {
                c.start_time: c
                for c in candles
                if c.interval == "5m"
                and c.source in ("BREEZE", "KITE", "LIVE")
                and c.end_time <= as_of
                and c.volume >= 0
                and c.low > 0
                and c.low
                <= min(c.open, c.close)
                <= max(c.open, c.close)
                <= c.high
            }.values(),
            key=lambda c: c.start_time,
        )

    @staticmethod
    def _session_date(candle: Candle):
        return candle.start_time.astimezone(IST).date()

    @staticmethod
    def _vwap_series(candles: Sequence[Candle]) -> list[float]:
        total_pv = 0.0
        total_volume = 0
        values: list[float] = []
        fallback_total = 0.0
        for index, candle in enumerate(candles, start=1):
            typical = (candle.high + candle.low + candle.close) / 3.0
            volume = max(0, int(candle.volume))
            total_pv += typical * volume
            total_volume += volume
            fallback_total += typical
            values.append(
                total_pv / total_volume
                if total_volume > 0
                else fallback_total / index
            )
        return values

    @staticmethod
    def _cross_count(values: Sequence[float]) -> int:
        signs: list[int] = []
        for value in values:
            if value > 0:
                signs.append(1)
            elif value < 0:
                signs.append(-1)
            else:
                signs.append(0)
        nonzero = [value for value in signs if value]
        return sum(
            1
            for left, right in zip(nonzero, nonzero[1:])
            if left != right
        )

    @staticmethod
    def _confirmed_swings(
        candles: Sequence[Candle],
        wing: int,
    ) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
        highs: list[tuple[int, float]] = []
        lows: list[tuple[int, float]] = []
        if len(candles) < (2 * wing) + 1:
            return highs, lows

        for index in range(wing, len(candles) - wing):
            candle = candles[index]
            left = candles[index - wing : index]
            right = candles[index + 1 : index + wing + 1]
            if (
                all(candle.high > item.high for item in left)
                and all(candle.high >= item.high for item in right)
            ):
                highs.append((index, float(candle.high)))
            if (
                all(candle.low < item.low for item in left)
                and all(candle.low <= item.low for item in right)
            ):
                lows.append((index, float(candle.low)))
        return highs, lows

    @staticmethod
    def _nearest_below(
        price: float,
        levels: Sequence[float],
    ) -> float | None:
        valid = [float(level) for level in levels if 0 < float(level) < price]
        return max(valid) if valid else None

    @staticmethod
    def _nearest_above(
        price: float,
        levels: Sequence[float],
    ) -> float | None:
        valid = [float(level) for level in levels if float(level) > price]
        return min(valid) if valid else None

    def _no_trade(
        self,
        reason: str,
        metrics: dict[str, Any],
    ) -> StrategyEDecision:
        decision = StrategyEDecision("NO_TRADE", reason, metrics)
        self.last_decision = decision
        return decision

    def _build_signal(
        self,
        *,
        signal_type: str,
        latest: Candle,
        entry: float,
        stop: float,
        target: float,
        metrics: dict[str, Any],
    ) -> StrategyEDecision:
        bullish = signal_type in {"TREND_LONG", "COUNTER_LONG"}
        direction = (
            TradeDirection.BULLISH
            if bullish
            else TradeDirection.BEARISH
        )
        option_type = OptionType.CALL if bullish else OptionType.PUT
        risk = abs(entry - stop)
        signal = StrategySignal(
            signal_id=(
                f"STRATEGY-E-{signal_type}-"
                f"{latest.instrument_id}-{int(latest.end_time.timestamp())}"
            ),
            strategy=StrategyName.PIVOT_VWAP_SCALP,
            direction=direction,
            option_type=option_type,
            timestamp=latest.end_time,
            spot_reference_price=entry,
            underlying_entry_price=entry,
            structural_stop=stop,
            r_points=risk,
            derivatives_score=0.0,
            features_snapshot={
                **metrics,
                "signal_type": signal_type,
                "entry_price": entry,
                "target_price": target,
                "structural_stop": stop,
                "futures_contract": latest.instrument_id,
                "completed_candle_timestamp": latest.end_time.isoformat(),
            },
        )
        decision = StrategyEDecision(
            signal_type,
            "SIGNAL_READY",
            {
                **metrics,
                "entry_price": entry,
                "stop": stop,
                "target": target,
                "risk_points": risk,
                "reward_points": abs(target - entry),
            },
            signal,
        )
        self.last_decision = decision
        return decision

    def evaluate(
        self,
        futures_5m: Sequence[Candle],
        *,
        as_of: datetime,
    ) -> StrategyEDecision:
        candles = self._valid_completed(futures_5m, as_of)
        cfg = self.config
        minimum = max(
            cfg.strategy_e_volume_lookback + 1,
            (2 * cfg.strategy_e_swing_lookback) + 5,
        )
        if len(candles) < minimum:
            return self._no_trade(
                "INSUFFICIENT_5M_HISTORY",
                {"completed_5m_bars": len(candles), "required_bars": minimum},
            )

        latest = candles[-1]
        if self.last_processed_candle == latest.end_time:
            return StrategyEDecision(
                "NO_TRADE",
                "NO_NEW_COMPLETED_5M_BAR",
                self.last_decision.metrics,
            )
        self.last_processed_candle = latest.end_time

        current_date = self._session_date(latest)
        session = [c for c in candles if self._session_date(c) == current_date]
        previous_dates = sorted(
            {self._session_date(c) for c in candles if self._session_date(c) < current_date}
        )
        if not previous_dates:
            return self._no_trade("PREVIOUS_TRADING_SESSION_UNAVAILABLE", {})
        previous_date = previous_dates[-1]
        previous_session = [
            c for c in candles if self._session_date(c) == previous_date
        ]
        if not previous_session:
            return self._no_trade("PREVIOUS_TRADING_SESSION_UNAVAILABLE", {})

        previous_high = max(c.high for c in previous_session)
        previous_low = min(c.low for c in previous_session)
        previous_close = previous_session[-1].close
        pivot = (previous_high + previous_low + previous_close) / 3.0

        vwap_series = self._vwap_series(session)
        vwap = vwap_series[-1]
        price = float(latest.close)

        volume_history = candles[-cfg.strategy_e_volume_lookback - 1 : -1]
        average_volume = mean(float(c.volume) for c in volume_history)
        relative_volume = (
            float(latest.volume) / average_volume
            if average_volume > 0
            else 0.0
        )

        wing = cfg.strategy_e_swing_lookback
        swing_highs, swing_lows = self._confirmed_swings(session, wing)

        lookback = min(cfg.strategy_e_chop_lookback_bars, len(session))
        recent = session[-lookback:]
        recent_vwap = vwap_series[-lookback:]
        pivot_crosses = self._cross_count(
            [c.close - pivot for c in recent]
        )
        vwap_crosses = self._cross_count(
            [
                candle.close - candle_vwap
                for candle, candle_vwap in zip(recent, recent_vwap)
            ]
        )
        flat_lookback = min(
            cfg.strategy_e_flat_vwap_lookback_bars,
            max(0, len(vwap_series) - 1),
        )
        vwap_delta = (
            abs(vwap_series[-1] - vwap_series[-1 - flat_lookback])
            if flat_lookback > 0
            else 0.0
        )
        flat_vwap = (
            flat_lookback > 0
            and vwap_delta <= cfg.strategy_e_flat_vwap_threshold_points
        )
        choppy = (
            (
                pivot_crosses >= cfg.strategy_e_chop_cross_threshold
                and vwap_crosses >= cfg.strategy_e_chop_cross_threshold
            )
            or (
                flat_vwap
                and vwap_crosses >= cfg.strategy_e_chop_cross_threshold
            )
        )

        sr_start = max(0, len(session) - cfg.strategy_e_sr_lookback_bars)
        established_highs = [
            value for index, value in swing_highs if index >= sr_start
        ]
        established_lows = [
            value for index, value in swing_lows if index >= sr_start
        ]
        if len(session) > 2:
            established_highs.append(max(c.high for c in session[:-2]))
            established_lows.append(min(c.low for c in session[:-2]))
        established_highs.append(float(previous_high))
        established_lows.append(float(previous_low))

        nearest_support = self._nearest_below(price, established_lows)
        nearest_resistance = self._nearest_above(price, established_highs)

        base_metrics: dict[str, Any] = {
            "price": round(price, 2),
            "pivot": round(pivot, 2),
            "vwap": round(vwap, 2),
            "previous_day_high": round(float(previous_high), 2),
            "previous_day_low": round(float(previous_low), 2),
            "previous_day_close": round(float(previous_close), 2),
            "volume": int(latest.volume),
            "average_volume_20": round(average_volume, 2),
            "relative_volume": round(relative_volume, 3),
            "volume_confirmed": (
                relative_volume >= cfg.strategy_e_rvol_confirmation
            ),
            "nearest_support": (
                round(nearest_support, 2)
                if nearest_support is not None
                else None
            ),
            "nearest_resistance": (
                round(nearest_resistance, 2)
                if nearest_resistance is not None
                else None
            ),
            "pivot_crosses": pivot_crosses,
            "vwap_crosses": vwap_crosses,
            "vwap_delta": round(vwap_delta, 3),
            "flat_vwap": flat_vwap,
            "choppy": choppy,
        }

        local_time = latest.end_time.astimezone(IST).strftime("%H:%M")
        if not (
            cfg.strategy_e_entry_start
            <= local_time
            <= cfg.strategy_e_entry_end
        ):
            return self._no_trade("OUTSIDE_STRATEGY_E_ENTRY_WINDOW", base_metrics)
        if choppy:
            return self._no_trade("CHOPPY_AROUND_PIVOT_VWAP", base_metrics)
        if len(session) < (2 * wing) + 3:
            return self._no_trade("INSUFFICIENT_SESSION_SWING_HISTORY", base_metrics)

        previous_bar = session[-2]
        previous_vwap = vwap_series[-2]

        # Trend long: break the swing high that preceded the latest confirmed
        # pullback low. The current bar must close through that level.
        if price > pivot and price > vwap and swing_lows:
            pullback_index, pullback_low = swing_lows[-1]
            prior_highs = [
                (index, value)
                for index, value in swing_highs
                if index < pullback_index
            ]
            if prior_highs:
                _, breakout_high = prior_highs[-1]
                if (
                    previous_bar.close <= breakout_high
                    and price > breakout_high
                    and latest.close > latest.open
                ):
                    stop = round(
                        pullback_low - cfg.strategy_e_stop_buffer_points,
                        2,
                    )
                    risk = price - stop
                    resistance_target = (
                        nearest_resistance - cfg.strategy_e_sr_buffer_points
                        if nearest_resistance is not None
                        else float("inf")
                    )
                    target = round(
                        min(
                            price + cfg.strategy_e_trend_target_points,
                            resistance_target,
                        ),
                        2,
                    )
                    reward = target - price
                    metrics = {
                        **base_metrics,
                        "swing_low": round(pullback_low, 2),
                        "swing_high": round(breakout_high, 2),
                    }
                    if risk <= 0:
                        return self._no_trade("INVALID_TREND_LONG_STOP", metrics)
                    if risk > cfg.strategy_e_max_stop_points:
                        return self._no_trade("STOP_TOO_WIDE", metrics)
                    if reward < cfg.strategy_e_min_room_to_level_points:
                        return self._no_trade(
                            "INSUFFICIENT_ROOM_TO_RESISTANCE",
                            metrics,
                        )
                    if reward / risk < cfg.strategy_e_min_reward_risk:
                        return self._no_trade(
                            "INSUFFICIENT_REWARD_TO_RISK",
                            metrics,
                        )
                    return self._build_signal(
                        signal_type="TREND_LONG",
                        latest=latest,
                        entry=round(price, 2),
                        stop=stop,
                        target=target,
                        metrics=metrics,
                    )

        # Trend short: mirror image of trend long.
        if price < pivot and price < vwap and swing_highs:
            pullback_index, pullback_high = swing_highs[-1]
            prior_lows = [
                (index, value)
                for index, value in swing_lows
                if index < pullback_index
            ]
            if prior_lows:
                _, breakout_low = prior_lows[-1]
                if (
                    previous_bar.close >= breakout_low
                    and price < breakout_low
                    and latest.close < latest.open
                ):
                    stop = round(
                        pullback_high + cfg.strategy_e_stop_buffer_points,
                        2,
                    )
                    risk = stop - price
                    support_target = (
                        nearest_support + cfg.strategy_e_sr_buffer_points
                        if nearest_support is not None
                        else float("-inf")
                    )
                    target = round(
                        max(
                            price - cfg.strategy_e_trend_target_points,
                            support_target,
                        ),
                        2,
                    )
                    reward = price - target
                    metrics = {
                        **base_metrics,
                        "swing_low": round(breakout_low, 2),
                        "swing_high": round(pullback_high, 2),
                    }
                    if risk <= 0:
                        return self._no_trade("INVALID_TREND_SHORT_STOP", metrics)
                    if risk > cfg.strategy_e_max_stop_points:
                        return self._no_trade("STOP_TOO_WIDE", metrics)
                    if reward < cfg.strategy_e_min_room_to_level_points:
                        return self._no_trade(
                            "INSUFFICIENT_ROOM_TO_SUPPORT",
                            metrics,
                        )
                    if reward / risk < cfg.strategy_e_min_reward_risk:
                        return self._no_trade(
                            "INSUFFICIENT_REWARD_TO_RISK",
                            metrics,
                        )
                    return self._build_signal(
                        signal_type="TREND_SHORT",
                        latest=latest,
                        entry=round(price, 2),
                        stop=stop,
                        target=target,
                        metrics=metrics,
                    )

        if not cfg.strategy_e_countertrend_enabled:
            return self._no_trade(
                "NO_TREND_SIGNAL_COUNTERTREND_DISABLED",
                base_metrics,
            )

        # Countertrend long: below pivot, established support is touched, then
        # either VWAP is reclaimed or a one-bar bullish micro swing confirms.
        if price < pivot and nearest_support is not None:
            support_touched = (
                latest.low
                <= nearest_support + cfg.strategy_e_counter_zone_points
                or previous_bar.low
                <= nearest_support + cfg.strategy_e_counter_zone_points
            )
            reclaim_vwap = (
                previous_bar.close <= previous_vwap
                and price > vwap
            )
            bullish_micro = (
                price > previous_bar.high
                and latest.low >= previous_bar.low
                and latest.close > latest.open
            )
            if support_touched and (reclaim_vwap or bullish_micro):
                stop = round(
                    min(
                        float(latest.low),
                        float(previous_bar.low),
                        nearest_support,
                    )
                    - cfg.strategy_e_stop_buffer_points,
                    2,
                )
                risk = price - stop
                counter_resistance_levels = [
                    *established_highs,
                    pivot,
                ]
                if vwap > price:
                    counter_resistance_levels.append(vwap)
                counter_resistance = self._nearest_above(
                    price,
                    counter_resistance_levels,
                )
                resistance_target = (
                    counter_resistance - cfg.strategy_e_sr_buffer_points
                    if counter_resistance is not None
                    else float("inf")
                )
                target = round(
                    min(
                        price + cfg.strategy_e_counter_target_points,
                        resistance_target,
                    ),
                    2,
                )
                reward = target - price
                metrics = {
                    **base_metrics,
                    "swing_low": round(nearest_support, 2),
                    "swing_high": None,
                    "counter_confirmation": (
                        "VWAP_RECLAIM" if reclaim_vwap else "BULLISH_MICRO_SWING"
                    ),
                }
                if risk <= 0:
                    return self._no_trade("INVALID_COUNTER_LONG_STOP", metrics)
                if risk > cfg.strategy_e_max_stop_points:
                    return self._no_trade("STOP_TOO_WIDE", metrics)
                if reward < cfg.strategy_e_min_room_to_level_points:
                    return self._no_trade(
                        "INSUFFICIENT_COUNTERTREND_ROOM",
                        metrics,
                    )
                if reward / risk < cfg.strategy_e_min_reward_risk:
                    return self._no_trade(
                        "INSUFFICIENT_REWARD_TO_RISK",
                        metrics,
                    )
                return self._build_signal(
                    signal_type="COUNTER_LONG",
                    latest=latest,
                    entry=round(price, 2),
                    stop=stop,
                    target=target,
                    metrics=metrics,
                )

        # Countertrend short: symmetrical resistance rejection.
        if price > pivot and nearest_resistance is not None:
            resistance_touched = (
                latest.high
                >= nearest_resistance - cfg.strategy_e_counter_zone_points
                or previous_bar.high
                >= nearest_resistance - cfg.strategy_e_counter_zone_points
            )
            lose_vwap = (
                previous_bar.close >= previous_vwap
                and price < vwap
            )
            bearish_micro = (
                price < previous_bar.low
                and latest.high <= previous_bar.high
                and latest.close < latest.open
            )
            if resistance_touched and (lose_vwap or bearish_micro):
                stop = round(
                    max(
                        float(latest.high),
                        float(previous_bar.high),
                        nearest_resistance,
                    )
                    + cfg.strategy_e_stop_buffer_points,
                    2,
                )
                risk = stop - price
                counter_support_levels = [
                    *established_lows,
                    pivot,
                ]
                if vwap < price:
                    counter_support_levels.append(vwap)
                counter_support = self._nearest_below(
                    price,
                    counter_support_levels,
                )
                support_target = (
                    counter_support + cfg.strategy_e_sr_buffer_points
                    if counter_support is not None
                    else float("-inf")
                )
                target = round(
                    max(
                        price - cfg.strategy_e_counter_target_points,
                        support_target,
                    ),
                    2,
                )
                reward = price - target
                metrics = {
                    **base_metrics,
                    "swing_low": None,
                    "swing_high": round(nearest_resistance, 2),
                    "counter_confirmation": (
                        "VWAP_LOSS" if lose_vwap else "BEARISH_MICRO_SWING"
                    ),
                }
                if risk <= 0:
                    return self._no_trade("INVALID_COUNTER_SHORT_STOP", metrics)
                if risk > cfg.strategy_e_max_stop_points:
                    return self._no_trade("STOP_TOO_WIDE", metrics)
                if reward < cfg.strategy_e_min_room_to_level_points:
                    return self._no_trade(
                        "INSUFFICIENT_COUNTERTREND_ROOM",
                        metrics,
                    )
                if reward / risk < cfg.strategy_e_min_reward_risk:
                    return self._no_trade(
                        "INSUFFICIENT_REWARD_TO_RISK",
                        metrics,
                    )
                return self._build_signal(
                    signal_type="COUNTER_SHORT",
                    latest=latest,
                    entry=round(price, 2),
                    stop=stop,
                    target=target,
                    metrics=metrics,
                )

        return self._no_trade("NO_SETUP", base_metrics)
