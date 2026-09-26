"""Replay-only adapter for the production :class:`PositionManager`.

This module owns no trading rules.  It supplies the manager with historical
spot/futures features and uses the existing replay-only OHLC/1-minute helpers
to decide when an intrabar event is chronologically usable.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from libs.contracts.models import Candle
from libs.market_time import IST
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyTunablesConfig,
    SimulatedTradeRecord,
    StrategyName,
    TradeDirection,
    TradeLifecycleState,
)
from services.strategy.position_manager import PositionManager
from services.strategy.replay_intrabar import resolve_entry_candle, resolve_stop_order
from services.strategy.replay_manifest import (
    ReplayEvent,
    ReplayManifestRecord,
    ReplayManifestRecorder,
    ReplayStateSnapshot,
)
from services.strategy.replay_stops import evaluate_replay_candle


def _direction(value: str) -> TradeDirection:
    return TradeDirection.BULLISH if value == "CALL" else TradeDirection.BEARISH


def _r_for(direction: TradeDirection, entry: float, price: float, risk: float) -> float:
    if risk <= 0:
        return 0.0
    return round(((price - entry) if direction == TradeDirection.BULLISH else (entry - price)) / risk, 4)


def _state_snapshot(trade: ActiveTrade, timestamp: datetime) -> ReplayStateSnapshot:
    return ReplayStateSnapshot(
        timestamp=timestamp,
        active_stop=trade.current_trailing_stop,
        ladder_stage=trade.state.value,
        current_r=trade.current_r,
        peak_r=trade.peak_r,
        protected_breakeven_active=trade.state in (TradeLifecycleState.PROTECTED_BREAKEVEN, TradeLifecycleState.PROFIT_LOCKED, TradeLifecycleState.RUNNER_MODE),
        profit_lock_active=trade.state in (TradeLifecycleState.PROFIT_LOCKED, TradeLifecycleState.RUNNER_MODE),
        runner_mode_active=trade.state == TradeLifecycleState.RUNNER_MODE,
        highest_favorable_price=trade.highest_close_since_entry,
        lowest_favorable_price=trade.lowest_close_since_entry,
        reversal_score=trade.reversal_score,
        adverse_health_counters={"reversal_score": trade.reversal_score},
    )


def _entry_trade(record: ReplayManifestRecord, instrument_id: str) -> ActiveTrade:
    try:
        strategy = StrategyName(record.strategy_id)
    except ValueError as exc:
        raise ValueError(f"Unsupported replay strategy_id: {record.strategy_id}") from exc

    strategy_b_state = {}
    if strategy == StrategyName.VOLATILITY_BREAKOUT:
        missing = [
            name for name, value in {
                "box_high": record.box_high,
                "box_low": record.box_low,
                "atr_at_lock": record.atr_at_lock,
            }.items() if value is None
        ]
        if missing:
            raise ValueError(
                f"Strategy B replay record {record.replay_signal_id} is missing structural state: "
                f"{', '.join(missing)}"
            )
        if record.atr_at_lock <= 0 or record.box_high <= record.box_low:
            raise ValueError(f"Invalid Strategy B structural state for {record.replay_signal_id}")
        strategy_b_state = {
            "box_high": record.box_high,
            "box_low": record.box_low,
            "atr_at_lock": record.atr_at_lock,
            "consecutive_inside_box_closes": record.consecutive_inside_box_closes or 0,
        }

    strategy_a_entry = (
        float(record.entry_features.get("underlying_entry_price"))
        if strategy == StrategyName.TREND_PULLBACK
        and record.entry_features.get("underlying_entry_price") is not None
        else record.simulated_entry_price
    )
    strategy_a_stop = record.initial_structural_stop
    strategy_a_r = record.initial_risk_points
    return ActiveTrade(
        trade_id=record.replay_signal_id,
        mode=AutoTradingMode.PAPER,
        strategy=strategy,
        direction=_direction(record.direction),
        option_type=OptionType.CALL if record.direction == "CALL" else OptionType.PUT,
        contract_symbol="HISTORICAL-SPOT",
        contract_instrument_id=instrument_id,
        expiry=record.trading_date,
        strike=0.0,
        quantity=1,
        lot_size=1,
        lots=1,
        entry_time=record.simulated_entry_timestamp,
        entry_option_price=0.0,
        entry_spot_price=strategy_a_entry,
        initial_structural_stop=strategy_a_stop,
        initial_r_points=strategy_a_r,
        pullback_swing_low=record.pullback_swing_low,
        pullback_swing_high=record.pullback_swing_high,
        **strategy_b_state,
        highest_close_since_entry=record.highest_favorable_price or record.simulated_entry_price,
        lowest_close_since_entry=record.lowest_favorable_price or record.simulated_entry_price,
        last_managed_bar=record.last_managed_completed_bar_timestamp,
        current_option_price=0.0,
        current_spot_price=strategy_a_entry,
        futures_contract_id=(
            record.entry_features.get("futures_contract")
            or record.entry_features.get("futures_contract_id")
            if strategy == StrategyName.TREND_PULLBACK
            else None
        ),
        underlying_entry_price=(strategy_a_entry if strategy == StrategyName.TREND_PULLBACK else None),
        underlying_current_price=(strategy_a_entry if strategy == StrategyName.TREND_PULLBACK else None),
        underlying_structural_stop=(strategy_a_stop if strategy == StrategyName.TREND_PULLBACK else None),
        underlying_r=(strategy_a_r if strategy == StrategyName.TREND_PULLBACK else None),
        initial_quantity=1,
        remaining_quantity=1,
        current_trailing_stop=record.current_trailing_stop,
        option_hard_stop_price=0.0,
        current_r=record.current_r,
        peak_r=record.peak_r,
        reversal_score=record.reversal_score,
        state=TradeLifecycleState.OPEN_INITIAL_RISK,
    )


def _feature_at(features: MarketFeatures, *, spot: float, timestamp: datetime, completed: bool) -> MarketFeatures:
    """Make a spot-only poll without inventing a completed candle."""
    return features.model_copy(update={
        "spot_price": spot,
        "futures_price": spot,
        "timestamp": timestamp,
        "closed_5m_price": features.closed_5m_price if completed else None,
        "closed_5m_time": features.closed_5m_time if completed else None,
    })


def _exit_label(reason: str | None, state: TradeLifecycleState) -> str:
    if not reason:
        return "UNKNOWN"
    if reason.startswith("IMMEDIATE_THESIS_INVALIDATION"):
        return "THESIS_INVALIDATION"
    if reason.startswith("ADVERSE_HEALTH"):
        return "ADVERSE_HEALTH_EXIT"
    if reason in ("SESSION_FORCE_SQUARE_OFF_1515", "SESSION_FORCE_SQUARE_OFF_1520"):
        return "SESSION_EXIT"
    if reason.startswith("STRUCTURAL_SPOT_STOP"):
        return {
            TradeLifecycleState.OPEN_INITIAL_RISK: "STRUCTURAL_STOP",
            TradeLifecycleState.PROTECTED_BREAKEVEN: "PROTECTED_BREAKEVEN",
            TradeLifecycleState.PROFIT_LOCKED: "TRAILING_STOP_EXIT",
            TradeLifecycleState.RUNNER_MODE: "RUNNER_STOP_EXIT",
        }.get(state, "STRUCTURAL_STOP")
    return reason.split(" (")[0]


def _event_name_for_level(level: float) -> str:
    return {1.0: "+1R", 1.5: "+1.5R", 2.0: "+2R"}.get(level, "FAVORABLE_THRESHOLD")


@dataclass
class HistoricalManagedReplayPosition:
    """One active lifecycle cursor advanced by completed bars."""

    record: ReplayManifestRecord
    trade: ActiveTrade
    entry_bar: Candle
    manager: PositionManager
    managed_bars: int = 0


class HistoricalPositionManagerReplayer:
    """Run production PositionManager lifecycles independently or incrementally."""

    def __init__(
        self,
        *,
        risk_config: RiskConfig,
        session_config: SessionTimersConfig,
        recorder: ReplayManifestRecorder,
        instrument_id: str,
        warmup_candles: list[Candle],
        session_candles: list[Candle],
        futures_candles: list[Candle],
        one_minute_candles: list[Candle] | None = None,
        strategy_config: StrategyTunablesConfig | None = None,
    ) -> None:
        self.risk_config = risk_config
        self.session_config = session_config
        self.recorder = recorder
        self.instrument_id = instrument_id
        self.warmup = warmup_candles
        self.session = session_candles
        self.futures = futures_candles
        self.one_minute = one_minute_candles or []
        self.strategy_config = strategy_config or StrategyTunablesConfig()
        self.stats: dict[str, int] = defaultdict(int)

    def _minutes(
        self,
        start: datetime,
        end: datetime,
        *,
        instrument_id: str | None = None,
    ) -> list[Candle]:
        return [
            c for c in self.one_minute
            if c.start_time >= start
            and c.start_time < end
            and (instrument_id is None or c.instrument_id == instrument_id)
        ]

    @staticmethod
    def _strategy_a_contract(record: ReplayManifestRecord) -> str | None:
        return (
            record.entry_features.get("futures_contract")
            or record.entry_features.get("futures_contract_id")
        )

    def _underlying_bar(
        self,
        record: ReplayManifestRecord,
        session_bar: Candle,
    ) -> Candle | None:
        if record.strategy_id != StrategyName.TREND_PULLBACK.value:
            return session_bar
        contract = self._strategy_a_contract(record)
        if not contract:
            return None
        exact = [
            candle for candle in self.futures
            if candle.instrument_id == contract
            and candle.start_time == session_bar.start_time
            and candle.end_time == session_bar.end_time
        ]
        return exact[0] if exact else None

    def _bars_after(self, record: ReplayManifestRecord) -> tuple[Candle | None, list[Candle]]:
        entry = None
        # Simulation records the completed trigger bar's END timestamp. Match
        # that first so the immediately following 5m candle is managed.
        for bar in self.session:
            if bar.end_time == record.entry_5m_candle_timestamp:
                entry = bar
                break
        # Backward compatibility for older manifests that stored bar start.
        if entry is None:
            for bar in self.session:
                if bar.start_time == record.entry_5m_candle_timestamp:
                    entry = bar
                    break
        return entry, [bar for bar in self.session if entry is not None and bar.start_time >= entry.end_time]

    def _features(self, bar: Candle, running: list[Candle]) -> MarketFeatures:
        from services.strategy.simulation import SimulationEngine
        macro = SimulationEngine.resample_to_15m(running, self.instrument_id)
        futures = [c for c in self.futures if c.end_time <= bar.end_time]
        return FeatureEngine.compute_all_features(
            running, macro, futures, spot_price=bar.close, as_of=bar.end_time
        )

    def _finish(
        self,
        record: ReplayManifestRecord,
        trade: ActiveTrade,
        *,
        status: str,
        reason: str | None = None,
        timestamp: datetime | None = None,
        price: float | None = None,
        ambiguous: bool = False,
    ) -> None:
        realized = None if price is None else _r_for(trade.direction, trade.entry_spot_price, price, trade.initial_r_points)
        mfe = trade.mfe_points / trade.initial_r_points if trade.initial_r_points > 0 else None
        mae = trade.mae_points / trade.initial_r_points if trade.initial_r_points > 0 else None
        self.recorder.set_lifecycle_result(
            record.replay_signal_id, status=status, exit_timestamp=timestamp,
            exit_price=price, exit_reason=reason, realized_r=realized,
            mfe_r=mfe, mae_r=mae, ambiguous=ambiguous,
        )

    def _record_event(self, record: ReplayManifestRecord, *, event: str, timestamp: datetime,
                      price: float | None, trade: ActiveTrade, source: datetime | None,
                      details: dict[str, Any] | None = None) -> None:
        self.recorder.record_event(record.replay_signal_id, ReplayEvent(
            event=event, timestamp=timestamp, reference_price=price,
            active_stop=trade.current_trailing_stop,
            r_multiple=_r_for(trade.direction, trade.entry_spot_price, price, trade.initial_r_points) if price is not None else trade.current_r,
            source_candle=source, details=details or {},
        ))

    def _entry_resolution(self, record: ReplayManifestRecord, trade: ActiveTrade, entry_bar: Candle) -> bool:
        price_bar = self._underlying_bar(record, entry_bar)
        if price_bar is None:
            self._finish(
                record,
                trade,
                status="UNRESOLVED",
                reason="FUTURES_ENTRY_CANDLE_NOT_FOUND",
            )
            return False
        decision = evaluate_replay_candle(
            record.direction, candle_open=price_bar.open, candle_high=price_bar.high,
            candle_low=price_bar.low, active_stop=trade.current_trailing_stop, entry_candle=True,
        )
        if not decision.crossed:
            return True
        minutes = self._minutes(
            price_bar.start_time,
            price_bar.end_time,
            instrument_id=price_bar.instrument_id,
        )
        self.stats["entry_ambiguous_candidates"] += 1
        if not minutes:
            self.stats["entry_unavailable"] += 1
            self._record_event(record, event="AMBIGUOUS", timestamp=entry_bar.end_time, price=None,
                               trade=trade, source=price_bar.start_time,
                               details={"reason": "1-minute data unavailable", "active_stop": decision.stop_level})
            self._finish(record, trade, status="AMBIGUOUS", reason="AMBIGUOUS_ENTRY_CANDLE",
                         timestamp=entry_bar.end_time, ambiguous=True)
            return False
        resolution = resolve_entry_candle(
            record.direction, trigger_price=record.trigger_level,
            active_stop=trade.current_trailing_stop, minute_candles=minutes,
        )
        self.stats[f"entry_{resolution.event.lower()}"] += 1
        if resolution.event == "ADVERSE_BEFORE_ENTRY":
            self._record_event(record, event="ADVERSE_BEFORE_ENTRY", timestamp=resolution.event_time or entry_bar.end_time,
                               price=None, trade=trade, source=price_bar.start_time)
            self._finish(record, trade, status="ADVERSE_BEFORE_ENTRY", reason="ADVERSE_BEFORE_ENTRY",
                         timestamp=resolution.event_time or entry_bar.end_time, ambiguous=False)
            return False
        if resolution.event == "STILL_AMBIGUOUS":
            self._record_event(record, event="AMBIGUOUS", timestamp=resolution.event_time or entry_bar.end_time,
                               price=None, trade=trade, source=price_bar.start_time,
                               details={"reason": resolution.detail or "intrabar order unresolved"})
            self._finish(record, trade, status="AMBIGUOUS", reason="AMBIGUOUS_ENTRY_CANDLE",
                         timestamp=resolution.event_time or entry_bar.end_time, ambiguous=True)
            return False
        if resolution.event == "ENTRY_THEN_STOP":
            exit_price = resolution.exit_price or trade.current_trailing_stop
            event_time = resolution.event_time or entry_bar.end_time
            trade, reason = PositionManager(self.risk_config, self.session_config, strategy_config=self.strategy_config).update_position(
                trade, 0.0, _feature_at(MarketFeatures(spot_price=exit_price, timestamp=event_time), spot=exit_price, timestamp=event_time, completed=False), as_of=event_time
            )
            self._record_event(record, event="STRUCTURAL_STOP_CROSSED", timestamp=event_time, price=exit_price,
                               trade=trade, source=price_bar.start_time, details={"entry_order": "ENTRY_THEN_STOP", "manager_reason": reason})
            self._finish(record, trade, status="RESOLVED", reason="STRUCTURAL_STOP", timestamp=event_time, price=exit_price)
            self.stats["entry_then_stop"] += 1
            return False
        self.stats["entry_survive"] += 1
        return True

    def _next_favorable_level(self, trade: ActiveTrade, bar: Candle) -> tuple[float | None, float | None]:
        candidates = [level for level in (1.0, 1.5, 2.0) if trade.peak_r < level]
        if not candidates or trade.initial_r_points <= 0:
            return None, None
        level = min(candidates)
        price = trade.entry_spot_price + level * trade.initial_r_points if trade.direction == TradeDirection.BULLISH else trade.entry_spot_price - level * trade.initial_r_points
        hit = bar.high >= price if trade.direction == TradeDirection.BULLISH else bar.low <= price
        return (level, price) if hit else (None, None)

    def start_record(
        self,
        record: ReplayManifestRecord,
    ) -> HistoricalManagedReplayPosition | None:
        """Create a lifecycle cursor without scanning future bars."""
        entry_bar, _ = self._bars_after(record)
        trade = _entry_trade(record, self.instrument_id)
        if entry_bar is None:
            self._finish(
                record,
                trade,
                status="UNRESOLVED",
                reason="ENTRY_CANDLE_NOT_FOUND",
            )
            return None
        if (
            record.entry_occurred_intrabar
            and not self._entry_resolution(record, trade, entry_bar)
        ):
            return None
        return HistoricalManagedReplayPosition(
            record=record,
            trade=trade,
            entry_bar=entry_bar,
            manager=PositionManager(
                self.risk_config,
                self.session_config,
                strategy_config=self.strategy_config,
            ),
        )

    def advance_record(
        self,
        position: HistoricalManagedReplayPosition,
        bar: Candle,
        running: list[Candle],
    ) -> bool:
        """Advance one active lifecycle using only this completed bar."""
        record = position.record
        trade = position.trade
        pm = position.manager
        if bar.start_time < position.entry_bar.end_time:
            return True

        price_bar = self._underlying_bar(record, bar)
        if price_bar is None:
            self._finish(
                record,
                trade,
                status="UNRESOLVED",
                reason=(
                    "FUTURES_CANDLE_NOT_FOUND"
                    if record.strategy_id == StrategyName.TREND_PULLBACK.value
                    else "SESSION_CANDLE_NOT_FOUND"
                ),
                timestamp=bar.end_time,
            )
            return False
        features = self._features(bar, running)
        before = _state_snapshot(trade, bar.start_time)

        # Replay excursions on the same authoritative price series used by
        # the strategy.  Strategy A is futures-authoritative; Strategy B
        # remains spot-authoritative.
        if trade.direction == TradeDirection.BULLISH:
            trade.mfe_points = max(
                trade.mfe_points,
                price_bar.high - trade.entry_spot_price,
            )
            trade.mae_points = min(
                trade.mae_points,
                price_bar.low - trade.entry_spot_price,
            )
        else:
            trade.mfe_points = max(
                trade.mfe_points,
                trade.entry_spot_price - price_bar.low,
            )
            trade.mae_points = min(
                trade.mae_points,
                trade.entry_spot_price - price_bar.high,
            )

        active_stop = trade.current_trailing_stop
        stop_decision = evaluate_replay_candle(
            record.direction, candle_open=price_bar.open, candle_high=price_bar.high,
            candle_low=price_bar.low, active_stop=active_stop,
        )
        level, favorable_price = self._next_favorable_level(trade, price_bar)
        resolution = None
        if stop_decision.crossed and level is not None:
            minutes = self._minutes(
                price_bar.start_time,
                price_bar.end_time,
                instrument_id=price_bar.instrument_id,
            )
            if not minutes:
                self.stats["trailing_unavailable"] += 1
                self._record_event(record, event="AMBIGUOUS", timestamp=bar.end_time, price=None, trade=trade,
                                   source=price_bar.start_time, details={"reason": "1-minute data unavailable", "active_stop": active_stop, "favorable_level": favorable_price})
                self._finish(record, trade, status="AMBIGUOUS", reason="AMBIGUOUS_INTRABAR_ORDER", timestamp=bar.end_time, ambiguous=True)
                return False
            resolution = resolve_stop_order(record.direction, active_stop=active_stop, minute_candles=minutes, favorable_level=favorable_price)
            if resolution.ambiguous:
                self.stats["trailing_still_ambiguous"] += 1
                self._record_event(record, event="AMBIGUOUS", timestamp=resolution.event_time or bar.end_time, price=None, trade=trade,
                                   source=price_bar.start_time, details={"reason": resolution.detail, "active_stop": active_stop, "favorable_level": favorable_price})
                self._finish(record, trade, status="AMBIGUOUS", reason="AMBIGUOUS_INTRABAR_ORDER", timestamp=resolution.event_time or bar.end_time, ambiguous=True)
                return False

        if stop_decision.crossed and (resolution is None or resolution.event == "STRUCTURAL_STOP"):
            exit_price = stop_decision.exit_price or active_stop
            event_time = resolution.event_time if resolution else price_bar.start_time
            stop_features = _feature_at(features, spot=exit_price, timestamp=event_time, completed=False)
            trade, reason = pm.update_position(trade, 0.0, stop_features, as_of=event_time)
            after = _state_snapshot(trade, event_time)
            label = _exit_label(reason or "STRUCTURAL_SPOT_STOP_BREACHED", trade.state)
            event = ReplayEvent(event=label, timestamp=event_time, reference_price=exit_price,
                                active_stop=active_stop, r_multiple=_r_for(trade.direction, trade.entry_spot_price, exit_price, trade.initial_r_points),
                                source_candle=price_bar.start_time, details={"manager_reason": reason, "active_stop_at_bar_start": active_stop})
            self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after, exit_event=event)
            self._finish(record, trade, status="RESOLVED", reason=label, timestamp=event_time, price=exit_price)
            self.stats["structural_stop"] += 1
            return False

        if resolution is not None and resolution.event == "FAVORABLE_THEN_STOP":
            fav_time = resolution.event_time or price_bar.start_time
            trade, _ = pm.update_position(trade, 0.0, _feature_at(features, spot=favorable_price or features.spot_price, timestamp=fav_time, completed=False), as_of=fav_time)
            self._record_event(record, event=_event_name_for_level(level or 0.0), timestamp=fav_time,
                               price=favorable_price, trade=trade, source=price_bar.start_time)
            exit_price = resolution.exit_price or active_stop
            stop_time = fav_time
            trade, reason = pm.update_position(trade, 0.0, _feature_at(features, spot=exit_price, timestamp=stop_time, completed=False), as_of=stop_time)
            after = _state_snapshot(trade, stop_time)
            label = _exit_label(reason or "STRUCTURAL_SPOT_STOP_BREACHED", trade.state)
            event = ReplayEvent(event=label, timestamp=stop_time, reference_price=exit_price, active_stop=active_stop,
                                r_multiple=_r_for(trade.direction, trade.entry_spot_price, exit_price, trade.initial_r_points), source_candle=price_bar.start_time,
                                details={"manager_reason": reason, "favorable_before_stop": True, "active_stop_at_bar_start": active_stop})
            self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after, exit_event=event)
            self._finish(record, trade, status="RESOLVED", reason=label, timestamp=stop_time, price=exit_price)
            self.stats["trailing_then_stop"] += 1
            return False

        if level is not None and favorable_price is not None:
            # The bar high/low is only known once this completed candle
            # ends.  Without minute-level ordering, do not timestamp a
            # favorable event at the start of a candle whose future range
            # was used to detect it.
            event_time = bar.end_time
            trade, _ = pm.update_position(trade, 0.0, _feature_at(features, spot=favorable_price, timestamp=event_time, completed=False), as_of=event_time)
            self._record_event(record, event=_event_name_for_level(level), timestamp=event_time, price=favorable_price, trade=trade, source=price_bar.start_time)

        trade, reason = pm.update_position(trade, 0.0, features, as_of=bar.end_time)
        after = _state_snapshot(trade, bar.end_time)
        if after.active_stop != before.active_stop:
            self._record_event(record, event="TRAILING_STOP_UPDATE", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=price_bar.start_time,
                               details={"previous_stop": before.active_stop, "new_stop": after.active_stop})
        if after.protected_breakeven_active and not before.protected_breakeven_active:
            self._record_event(record, event="BREAKEVEN_PROTECTION", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=price_bar.start_time)
        if after.profit_lock_active and not before.profit_lock_active:
            self._record_event(record, event="PROFIT_LOCK", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=price_bar.start_time)
        if after.runner_mode_active and not before.runner_mode_active:
            self._record_event(record, event="RUNNER_MODE", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=price_bar.start_time)
        if reason in ("T1_PARTIAL_EXIT", "T1_REACHED_NO_PARTIAL_ONE_LOT"):
            self._record_event(
                record,
                event=reason,
                timestamp=bar.end_time,
                price=features.futures_price,
                trade=trade,
                source=price_bar.start_time,
                details={"remaining_quantity": trade.remaining_quantity, "t1_exit_quantity": trade.t1_exit_quantity},
            )
            self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after)
            position.trade = trade
            position.managed_bars += 1
            return True
        if reason:
            label = _exit_label(reason, trade.state)
            price = features.futures_price if record.strategy_id == StrategyName.TREND_PULLBACK.value else features.spot_price
            exit_event = ReplayEvent(event=label, timestamp=bar.end_time, reference_price=price, active_stop=before.active_stop,
                                     r_multiple=trade.current_r, source_candle=price_bar.start_time, details={"manager_reason": reason})
            self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after, exit_event=exit_event)
            self._finish(record, trade, status="RESOLVED", reason=label, timestamp=bar.end_time, price=price)
            self.stats[label.lower()] += 1
            return False
        self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after)

        position.trade = trade
        position.managed_bars += 1
        return True

    def finalize_record(
        self,
        position: HistoricalManagedReplayPosition,
    ) -> None:
        """Close an unresolved cursor at the end of available history."""
        if position.record.lifecycle_status != "PENDING":
            return
        if position.managed_bars:
            self.stats["unresolved_at_session_end"] += 1
            reason = "SESSION_END_WITHOUT_EXIT"
        else:
            reason = "NO_POST_ENTRY_CANDLES"
        self._finish(
            position.record,
            position.trade,
            status="UNRESOLVED",
            reason=reason,
        )

    def replay_record(self, record: ReplayManifestRecord) -> None:
        """Compatibility path: replay one signal independently to completion."""
        position = self.start_record(record)
        if position is None:
            return
        running = list(self.warmup)
        for bar in self.session:
            running.append(bar)
            if bar.start_time < position.entry_bar.end_time:
                continue
            if not self.advance_record(position, bar, running):
                return
        self.finalize_record(position)

    def replay(self, records: Iterable[ReplayManifestRecord]) -> dict[str, Any]:
        for record in records:
            self.replay_record(record)
        return {"resolver": dict(sorted(self.stats.items())), "one_minute_candles": len(self.one_minute)}


def _trade_rows(records: Iterable[ReplayManifestRecord]) -> list[ReplayManifestRecord]:
    return [r for r in records if r.lifecycle_status == "RESOLVED" and r.realized_r is not None]


def _utc_timestamp(value: datetime | None) -> datetime | None:
    """Return an aware UTC timestamp, rejecting naive values."""
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _historical_candle_at(candles: list[Candle], timestamp: datetime | None) -> Candle | None:
    """Return the latest valid historical candle completed by ``timestamp``.

    Historical OHLC closes are only known after their candle ends.  In
    particular, a candle beginning at the event timestamp is still incomplete
    and must not be used as a historical mark.
    """
    event_timestamp = _utc_timestamp(timestamp)
    if event_timestamp is None:
        return None

    eligible: list[tuple[datetime, datetime, Candle]] = []
    for candle in candles:
        candle_start = _utc_timestamp(candle.start_time)
        candle_end = _utc_timestamp(candle.end_time)
        close = float(candle.close)
        if (
            candle_start is None
            or candle_end is None
            or candle_start > candle_end
            or candle_end > event_timestamp
            or close <= 0
            or not math.isfinite(close)
        ):
            continue
        eligible.append((candle_end, candle_start, candle))

    if not eligible:
        return None
    return max(eligible, key=lambda item: (item[0], item[1]))[2]


def _historical_close_at(candles: list[Candle], timestamp: datetime | None) -> float | None:
    """Return the latest valid completed candle close at an event timestamp."""
    candle = _historical_candle_at(candles, timestamp)
    return float(candle.close) if candle is not None else None


def _historical_mark_provenance(candle: Candle | None, timestamp: datetime | None) -> dict[str, Any]:
    event_timestamp = _utc_timestamp(timestamp)
    candle_start = _utc_timestamp(candle.start_time) if candle is not None else None
    candle_end = _utc_timestamp(candle.end_time) if candle is not None else None
    return {
        "event_timestamp": event_timestamp.isoformat() if event_timestamp is not None else None,
        "candle_start": candle_start.isoformat() if candle_start is not None else None,
        "candle_end": candle_end.isoformat() if candle_end is not None else None,
        "mark_age_seconds": (
            (event_timestamp - candle_end).total_seconds()
            if event_timestamp is not None and candle_end is not None
            else None
        ),
        "available": candle is not None,
    }


def attach_historical_option_prices(
    records: Iterable[ReplayManifestRecord],
    contracts: Iterable[Any],
    candles_by_instrument: dict[str, list[Candle]],
    risk_config: RiskConfig,
) -> None:
    """Attach completed-candle Breeze option marks to resolved replay records.

    Breeze historical data is OHLCV, not historical bid/ask.  Therefore this
    deliberately uses the most recent completed candle close at each replay
    event and labels the result as a historical mark; it never derives an
    option price from the underlying or uses an incomplete/future candle.
    Contract selection is deterministic (nearest strike in the first expiry
    available on the replay date) because Breeze has no historical chain
    snapshot endpoint for replay-time selection.
    """
    normalized: list[dict[str, Any]] = []
    for contract in contracts:
        expiry = getattr(contract, "expiry", None)
        strike = getattr(contract, "strike", None)
        option_right = getattr(getattr(contract, "option_right", None), "value", getattr(contract, "option_right", None))
        instrument_id = getattr(contract, "instrument_id", None)
        if not instrument_id or not expiry or strike is None or not option_right:
            continue
        normalized.append({
            "instrument_id": instrument_id,
            "symbol": getattr(contract, "stock_code", instrument_id),
            "expiry": str(expiry),
            "strike": float(strike),
            "right": str(option_right).upper(),
            "lot_size": int(getattr(contract, "lot_size", 0) or 0),
        })

    for record in records:
        record.option_data_status = "UNAVAILABLE"
        record.historical_option_provenance = {
            "historical_price_source": "BREEZE_HISTORICAL_OHLC",
            "pricing_field": "completed_candle_close",
            "mark_policy": "latest_completed_candle_close_at_event",
            "contract_selection_method": "nearest_strike_first_expiry_on_or_after_replay_date",
            "sizing_status": record.sizing_status,
            "sizing_method": record.sizing_method,
            "sizing_price_basis": record.sizing_price_basis,
            "bid_ask_available": False,
            "executable_fill_equivalent": False,
            "entry": _historical_mark_provenance(None, record.simulated_entry_timestamp),
            "exit": _historical_mark_provenance(None, record.exit_timestamp),
        }
        sizing_contract_id = record.sizing_contract_instrument_id
        if sizing_contract_id:
            selected = next(
                (
                    item
                    for item in normalized
                    if item["instrument_id"] == sizing_contract_id
                ),
                None,
            )
            record.historical_option_provenance["sizing_contract_locked"] = True
            if selected is None:
                record.option_data_quality_reason = (
                    "Historical sizing contract metadata unavailable"
                )
                continue
        else:
            record.historical_option_provenance["sizing_contract_locked"] = False
            direction = "CALL" if record.direction == "CALL" else "PUT"
            candidates = [
                item for item in normalized
                if item["right"] in {
                    direction,
                    "CE" if direction == "CALL" else "PE",
                }
                and item["expiry"] >= record.trading_date
            ]
            if not candidates:
                record.option_data_quality_reason = (
                    "No historical contract metadata for replay date"
                )
                continue
            expiry = min(item["expiry"] for item in candidates)
            candidates = [
                item for item in candidates if item["expiry"] == expiry
            ]
            selected = min(
                candidates,
                key=lambda item: abs(
                    item["strike"] - record.simulated_entry_price
                ),
            )
        record.option_contract_instrument_id = selected["instrument_id"]
        record.option_contract_symbol = selected["symbol"]
        record.option_expiry = selected["expiry"]
        record.option_strike = selected["strike"]
        record.option_lot_size = selected["lot_size"]

        candles = candles_by_instrument.get(selected["instrument_id"], [])
        entry_candle = _historical_candle_at(candles, record.simulated_entry_timestamp)
        exit_candle = _historical_candle_at(candles, record.exit_timestamp)
        record.historical_option_provenance["entry"] = _historical_mark_provenance(
            entry_candle, record.simulated_entry_timestamp
        )
        record.historical_option_provenance["exit"] = _historical_mark_provenance(
            exit_candle, record.exit_timestamp
        )
        record.historical_option_provenance["candle_sources"] = sorted({
            candle.source for candle in (entry_candle, exit_candle) if candle is not None
        })
        entry_price = float(entry_candle.close) if entry_candle is not None else None
        exit_price = float(exit_candle.close) if exit_candle is not None else None
        if entry_price is None or exit_price is None or selected["lot_size"] <= 0:
            record.option_data_quality_reason = "Breeze historical option candle unavailable at entry or exit"
            continue

        quantity = int(record.replay_quantity or selected["lot_size"])
        gross = round((exit_price - entry_price) * quantity, 2)
        turnover = (entry_price + exit_price) * quantity
        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        brokerage = 2 * risk_config.paper_brokerage_per_order
        exchange_charges = turnover * risk_config.paper_exchange_charge_rate
        stt = sell_turnover * risk_config.paper_stt_sell_rate
        sebi = turnover * risk_config.paper_sebi_charge_rate
        stamp = buy_turnover * risk_config.paper_stamp_buy_rate
        gst = (brokerage + exchange_charges + sebi) * risk_config.paper_gst_rate
        costs = round(brokerage + exchange_charges + stt + gst + sebi + stamp, 2)

        record.option_entry_price = round(entry_price, 2)
        record.option_exit_price = round(exit_price, 2)
        record.option_gross_pnl = gross
        record.option_transaction_costs = costs
        record.option_net_pnl = round(gross - costs, 2)
        record.option_price_source = "BREEZE_HISTORICAL_OHLC_CLOSE"
        record.option_data_status = "AVAILABLE"
        record.option_data_quality_reason = None


def build_simulated_trade_records(records: Iterable[ReplayManifestRecord]) -> list[SimulatedTradeRecord]:
    """Expose resolved lifecycle records in the simulation response shape.

    The replay manifest is the authoritative source for these values.  This
    adapter only serializes the lifecycle result for the existing API model;
    it does not recalculate entries, exits, sizing, stops, or P&L.
    """
    rows: list[SimulatedTradeRecord] = []
    for record in _trade_rows(records):
        hold_duration_mins = 0.0
        if record.exit_timestamp is not None:
            hold_duration_mins = max(
                0.0,
                (record.exit_timestamp - record.simulated_entry_timestamp).total_seconds() / 60.0,
            )
        rows.append(
            SimulatedTradeRecord(
                trade_id=record.replay_signal_id,
                strategy=record.strategy_id,
                direction="BULLISH" if record.direction == "CALL" else "BEARISH",
                option_type=record.direction,
                strike=float(record.option_strike or 0.0),
                contract_symbol=record.option_contract_symbol or "HISTORICAL-SPOT",
                entry_time=record.simulated_entry_timestamp.isoformat(),
                entry_spot=record.simulated_entry_price,
                entry_premium=record.option_entry_price,
                exit_time=record.exit_timestamp.isoformat() if record.exit_timestamp else None,
                exit_spot=record.exit_price,
                exit_premium=record.option_exit_price,
                exit_reason=record.exit_reason,
                initial_stop=record.initial_structural_stop,
                initial_r_points=record.initial_risk_points,
                peak_r=record.mfe_r if record.mfe_r is not None else record.peak_r,
                realized_r=float(record.realized_r),
                quantity=int(
                    record.replay_quantity
                    or record.option_lot_size
                    or 1
                ),
                lots=int(record.replay_lots or 1),
                gross_pnl=record.option_gross_pnl,
                net_pnl=record.option_net_pnl,
                entry_mark=record.option_entry_price,
                exit_mark=record.option_exit_price,
                simulated_entry_fill=record.simulated_entry_fill_price,
                simulated_exit_fill=record.simulated_exit_fill_price,
                simulated_entry_fill_method=(
                    record.simulated_entry_fill_method
                ),
                simulated_exit_fill_method=(
                    record.simulated_exit_fill_method
                ),
                estimated_executable_gross_pnl=(
                    record.simulated_gross_pnl
                ),
                estimated_slippage_cost=record.simulated_slippage_cost,
                estimated_transaction_costs=(
                    record.simulated_transaction_costs
                ),
                estimated_executable_net_pnl=(
                    record.simulated_net_pnl
                ),
                contract_selection_evidence_status=(
                    record.contract_selection_evidence_status
                ),
                contract_selection_method=(
                    record.contract_selection_actual_method
                ),
                hold_duration_mins=hold_duration_mins,
            )
        )
    return rows


def summarize_simulated_pnl(
    trades: Iterable[SimulatedTradeRecord],
) -> tuple[float | None, float | None]:
    """Aggregate P&L only when every canonical trade has P&L values."""
    rows = list(trades)
    if not rows:
        return 0.0, 0.0
    if any(row.gross_pnl is None or row.net_pnl is None for row in rows):
        return None, None
    return (
        round(sum(row.gross_pnl for row in rows if row.gross_pnl is not None), 2),
        round(sum(row.net_pnl for row in rows if row.net_pnl is not None), 2),
    )


def summarize_historical_option_marks(
    records: Iterable[ReplayManifestRecord],
) -> dict[str, Any]:
    """Summarize historical option marks without implying fill availability."""
    rows = _trade_rows(records)
    available = [
        row for row in rows
        if row.option_data_status == "AVAILABLE"
        and row.option_gross_pnl is not None
        and row.option_net_pnl is not None
        and row.option_transaction_costs is not None
    ]
    unavailable = [row for row in rows if row not in available]
    reasons = Counter(
        row.option_data_quality_reason or row.option_data_status or "UNKNOWN"
        for row in unavailable
    )
    complete = bool(rows) and len(available) == len(rows)
    if not rows:
        gross_mark_pnl: float | None = 0.0
        transaction_costs: float | None = 0.0
        net_mark_pnl: float | None = 0.0
    elif complete:
        gross_mark_pnl = round(sum(float(row.option_gross_pnl or 0.0) for row in available), 2)
        transaction_costs = round(
            sum(float(row.option_transaction_costs or 0.0) for row in available),
            2,
        )
        net_mark_pnl = round(sum(float(row.option_net_pnl or 0.0) for row in available), 2)
    else:
        gross_mark_pnl = None
        transaction_costs = None
        net_mark_pnl = None
    execution_available = [
        row
        for row in rows
        if row.simulated_gross_pnl is not None
        and row.simulated_transaction_costs is not None
        and row.simulated_net_pnl is not None
    ]
    execution_unavailable = [
        row for row in rows if row not in execution_available
    ]
    execution_complete = (
        bool(rows) and len(execution_available) == len(rows)
    )
    if not rows:
        gross_execution_pnl: float | None = 0.0
        execution_transaction_costs: float | None = 0.0
        slippage_costs: float | None = 0.0
        net_execution_pnl: float | None = 0.0
    elif execution_complete:
        gross_execution_pnl = round(
            sum(
                float(row.simulated_gross_pnl or 0.0)
                for row in execution_available
            ),
            2,
        )
        execution_transaction_costs = round(
            sum(
                float(row.simulated_transaction_costs or 0.0)
                for row in execution_available
            ),
            2,
        )
        slippage_costs = round(
            sum(
                float(row.simulated_slippage_cost or 0.0)
                for row in execution_available
            ),
            2,
        )
        net_execution_pnl = round(
            sum(
                float(row.simulated_net_pnl or 0.0)
                for row in execution_available
            ),
            2,
        )
    else:
        gross_execution_pnl = None
        execution_transaction_costs = None
        slippage_costs = None
        net_execution_pnl = None

    bid_ask_supported = sum(
        1
        for row in execution_available
        if row.simulated_fill_quote_equivalent
    )
    mark_fallback = sum(
        1
        for row in execution_available
        if not row.simulated_fill_quote_equivalent
    )

    return {
        "priced_trades": len(available),
        "unpriced_trades": len(unavailable),
        "all_resolved_trades_priced": complete,
        "gross_mark_pnl": gross_mark_pnl,
        "estimated_transaction_costs": transaction_costs,
        "net_mark_pnl": net_mark_pnl,
        "execution_estimated_trades": len(execution_available),
        "execution_unavailable_trades": len(execution_unavailable),
        "bid_ask_supported_trades": bid_ask_supported,
        "mark_fallback_fill_trades": mark_fallback,
        "gross_estimated_executable_pnl": gross_execution_pnl,
        "estimated_slippage_costs": slippage_costs,
        "estimated_execution_transaction_costs": (
            execution_transaction_costs
        ),
        "net_estimated_executable_pnl": net_execution_pnl,
        "quality_reasons": dict(reasons.most_common()),
    }


def _max_drawdown_r(rs: list[float]) -> float:
    """Return peak-to-trough drawdown for a realized-R sequence."""
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return round(max_drawdown, 4)


def _basic(rows: list[ReplayManifestRecord]) -> dict[str, Any]:
    ordered = sorted(
        rows,
        key=lambda row: (
            row.exit_timestamp or row.simulated_entry_timestamp,
            row.simulated_entry_timestamp,
            row.replay_signal_id,
        ),
    )
    rs = [float(r.realized_r) for r in ordered]
    winners = [r for r in rs if r > 0]
    losers = [r for r in rs if r < 0]
    raw_total_r = sum(rs)
    total_r = round(raw_total_r, 4)
    return {
        "trades": len(ordered), "winners": len(winners), "losers": len(losers),
        "breakeven": sum(1 for r in rs if r == 0),
        "win_rate_pct": round(len(winners) / len(rs) * 100, 2) if rs else 0.0,
        "average_winner_r": round(sum(winners) / len(winners), 4) if winners else 0.0,
        "average_loser_r": round(sum(losers) / len(losers), 4) if losers else 0.0,
        "average_r": round(raw_total_r / len(rs), 4) if rs else 0.0,
        "median_r": round(float(median(rs)), 4) if rs else 0.0,
        "total_r": total_r,
        "profit_factor": round(sum(winners) / abs(sum(losers)), 4) if losers else None,
        "max_drawdown_r": _max_drawdown_r(rs),
        "max_consecutive_losses": _max_consecutive_losses(rs),
    }


def _max_consecutive_losses(rs: list[float]) -> int:
    best = cur = 0
    for value in rs:
        if value < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _segments(rows: list[ReplayManifestRecord], key_name: str, key_fn) -> dict[str, Any]:
    grouped: dict[str, list[ReplayManifestRecord]] = defaultdict(list)
    for row in rows:
        key = key_fn(row)
        grouped[str(key if key is not None else "UNKNOWN")].append(row)
    return {key: _basic(value) for key, value in sorted(grouped.items())}


def build_lifecycle_report(records: list[ReplayManifestRecord], resolver: dict[str, Any]) -> dict[str, Any]:
    resolved = _trade_rows(records)
    losses = [r for r in resolved if (r.realized_r or 0) < 0]
    exit_groups: dict[str, list[ReplayManifestRecord]] = defaultdict(list)
    for row in resolved:
        exit_groups[row.exit_reason or "UNKNOWN"].append(row)
    exit_breakdown = {}
    for key, group in sorted(exit_groups.items()):
        exit_breakdown[key] = {
            "count": len(group),
            "average_r": round(sum(float(r.realized_r or 0) for r in group) / len(group), 4),
            "average_mfe_r": round(sum(float(r.mfe_r or 0) for r in group) / len(group), 4),
            "average_mae_r": round(sum(float(r.mae_r or 0) for r in group) / len(group), 4),
        }
    mfe = {}
    for threshold in (0.25, 0.5, 0.75, 1.0, 1.5):
        mfe[str(threshold)] = round(sum(1 for row in losses if (row.mfe_r or 0) >= threshold) / len(losses) * 100, 2) if losses else 0.0
    def bucket(value: Any, ranges: list[tuple[float, float | None, str]]) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "UNKNOWN"
        for low, high, label in ranges:
            if number >= low and (high is None or number < high):
                return label
        return "UNKNOWN"

    def time_bucket(row: ReplayManifestRecord) -> str:
        hour = row.simulated_entry_timestamp.astimezone(IST).hour
        return {9: "09:20-09:59", 10: "10:00-10:59", 11: "11:00-11:59",
                12: "12:00-12:59", 13: "13:00-13:59"}.get(hour, "14:00-14:45")

    segment_rows = {
        "confirmation_count": _segments(resolved, "confirmation_count", lambda r: r.confirmation_passed),
        "pullback_depth": _segments(resolved, "pullback_depth", lambda r: bucket(r.entry_features.get("pullback_depth"), [(0, .2, "<0.20"), (.2, .4, "0.20-0.39"), (.4, .6, "0.40-0.59"), (.6, None, ">=0.60")])),
        "impulse_atr": _segments(resolved, "impulse_atr", lambda r: bucket(
            r.entry_features.get("impulse_atr") or r.entry_features.get("impulse_size_atr") or
            ((r.entry_features.get("impulse_size") or 0) / (r.entry_features.get("atr") or 1)),
            [(0, 1, "<1.00"), (1, 1.5, "1.00-1.49"), (1.5, 2, "1.50-1.99"), (2, None, ">=2.00")])),
        "structural_r": _segments(resolved, "structural_r", lambda r: bucket(r.initial_risk_atr, [(0, .5, "<0.50"), (.5, 1, "0.50-0.99"), (1, 1.6, "1.00-1.59"), (1.6, None, ">=1.60")])),
        "adx": _segments(resolved, "adx", lambda r: bucket(r.entry_features.get("adx"), [(0, 20, "<20"), (20, 25, "20-24.99"), (25, 30, "25-29.99"), (30, None, ">=30")])),
        "time_of_day": _segments(resolved, "time_of_day", time_bucket),
        "direction": _segments(resolved, "direction", lambda r: r.direction),
    }
    ambiguous = [r for r in records if r.lifecycle_status == "AMBIGUOUS"]
    return {
        "total_signals": len(records),
        "resolved": len(resolved),
        "ambiguous": len(ambiguous),
        "unresolved": sum(1 for r in records if r.lifecycle_status == "UNRESOLVED"),
        **_basic(resolved),
        "call": _basic([r for r in resolved if r.direction == "CALL"]),
        "put": _basic([r for r in resolved if r.direction == "PUT"]),
        "exit_breakdown": exit_breakdown,
        "mfe_before_loss_pct": mfe,
        "segments": segment_rows,
        "resolver": resolver,
    }
