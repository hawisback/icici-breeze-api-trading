"""Replay-only adapter for the production :class:`PositionManager`.

This module owns no trading rules.  It supplies the manager with historical
spot/futures features and uses the existing replay-only OHLC/1-minute helpers
to decide when an intrabar event is chronologically usable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
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

IST = timezone(timedelta(hours=5, minutes=30))


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
    return ActiveTrade(
        trade_id=record.replay_signal_id,
        mode=AutoTradingMode.PAPER,
        strategy=StrategyName.TREND_PULLBACK,
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
        entry_spot_price=record.simulated_entry_price,
        initial_structural_stop=record.initial_structural_stop,
        initial_r_points=record.initial_risk_points,
        pullback_swing_low=record.pullback_swing_low,
        pullback_swing_high=record.pullback_swing_high,
        highest_close_since_entry=record.highest_favorable_price or record.simulated_entry_price,
        lowest_close_since_entry=record.lowest_favorable_price or record.simulated_entry_price,
        last_managed_bar=record.last_managed_completed_bar_timestamp,
        current_option_price=0.0,
        current_spot_price=record.simulated_entry_price,
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
    if reason == "SESSION_FORCE_SQUARE_OFF_1520":
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


class HistoricalPositionManagerReplayer:
    """Run one independent production PositionManager lifecycle per signal."""

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
    ) -> None:
        self.risk_config = risk_config
        self.session_config = session_config
        self.recorder = recorder
        self.instrument_id = instrument_id
        self.warmup = warmup_candles
        self.session = session_candles
        self.futures = futures_candles
        self.one_minute = one_minute_candles or []
        self.stats: dict[str, int] = defaultdict(int)

    def _minutes(self, start: datetime, end: datetime) -> list[Candle]:
        return [c for c in self.one_minute if c.start_time >= start and c.start_time < end]

    def _bars_after(self, record: ReplayManifestRecord) -> tuple[Candle | None, list[Candle]]:
        entry = None
        for bar in self.session:
            if bar.start_time == record.entry_5m_candle_timestamp:
                entry = bar
                break
        if entry is None:
            for bar in self.session:
                if bar.end_time == record.entry_5m_candle_timestamp:
                    entry = bar
                    break
        return entry, [bar for bar in self.session if entry is not None and bar.start_time > entry.start_time]

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
        decision = evaluate_replay_candle(
            record.direction, candle_open=entry_bar.open, candle_high=entry_bar.high,
            candle_low=entry_bar.low, active_stop=trade.current_trailing_stop, entry_candle=True,
        )
        if not decision.crossed:
            return True
        minutes = self._minutes(entry_bar.start_time, entry_bar.end_time)
        self.stats["entry_ambiguous_candidates"] += 1
        if not minutes:
            self.stats["entry_unavailable"] += 1
            self._record_event(record, event="AMBIGUOUS", timestamp=entry_bar.end_time, price=None,
                               trade=trade, source=entry_bar.start_time,
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
                               price=None, trade=trade, source=entry_bar.start_time)
            self._finish(record, trade, status="ADVERSE_BEFORE_ENTRY", reason="ADVERSE_BEFORE_ENTRY",
                         timestamp=resolution.event_time or entry_bar.end_time, ambiguous=False)
            return False
        if resolution.event == "STILL_AMBIGUOUS":
            self._record_event(record, event="AMBIGUOUS", timestamp=resolution.event_time or entry_bar.end_time,
                               price=None, trade=trade, source=entry_bar.start_time,
                               details={"reason": resolution.detail or "intrabar order unresolved"})
            self._finish(record, trade, status="AMBIGUOUS", reason="AMBIGUOUS_ENTRY_CANDLE",
                         timestamp=resolution.event_time or entry_bar.end_time, ambiguous=True)
            return False
        if resolution.event == "ENTRY_THEN_STOP":
            exit_price = resolution.exit_price or trade.current_trailing_stop
            event_time = resolution.event_time or entry_bar.end_time
            trade, reason = PositionManager(self.risk_config, self.session_config).update_position(
                trade, 0.0, _feature_at(MarketFeatures(spot_price=exit_price, timestamp=event_time), spot=exit_price, timestamp=event_time, completed=False), as_of=event_time
            )
            self._record_event(record, event="STRUCTURAL_STOP_CROSSED", timestamp=event_time, price=exit_price,
                               trade=trade, source=entry_bar.start_time, details={"entry_order": "ENTRY_THEN_STOP", "manager_reason": reason})
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

    def replay_record(self, record: ReplayManifestRecord) -> None:
        entry_bar, bars = self._bars_after(record)
        trade = _entry_trade(record, self.instrument_id)
        if entry_bar is None:
            self._finish(record, trade, status="UNRESOLVED", reason="ENTRY_CANDLE_NOT_FOUND")
            return
        if not self._entry_resolution(record, trade, entry_bar):
            return

        pm = PositionManager(self.risk_config, self.session_config)
        running = list(self.warmup)
        entry_seen = False
        for bar in self.session:
            running.append(bar)
            if bar.start_time <= entry_bar.start_time:
                continue
            if bar not in bars:
                continue
            entry_seen = True
            features = self._features(bar, running)
            before = _state_snapshot(trade, bar.start_time)
            active_stop = trade.current_trailing_stop
            stop_decision = evaluate_replay_candle(
                record.direction, candle_open=bar.open, candle_high=bar.high,
                candle_low=bar.low, active_stop=active_stop,
            )
            level, favorable_price = self._next_favorable_level(trade, bar)
            resolution = None
            if stop_decision.crossed and level is not None:
                minutes = self._minutes(bar.start_time, bar.end_time)
                if not minutes:
                    self.stats["trailing_unavailable"] += 1
                    self._record_event(record, event="AMBIGUOUS", timestamp=bar.end_time, price=None, trade=trade,
                                       source=bar.start_time, details={"reason": "1-minute data unavailable", "active_stop": active_stop, "favorable_level": favorable_price})
                    self._finish(record, trade, status="AMBIGUOUS", reason="AMBIGUOUS_INTRABAR_ORDER", timestamp=bar.end_time, ambiguous=True)
                    return
                resolution = resolve_stop_order(record.direction, active_stop=active_stop, minute_candles=minutes, favorable_level=favorable_price)
                if resolution.ambiguous:
                    self.stats["trailing_still_ambiguous"] += 1
                    self._record_event(record, event="AMBIGUOUS", timestamp=resolution.event_time or bar.end_time, price=None, trade=trade,
                                       source=bar.start_time, details={"reason": resolution.detail, "active_stop": active_stop, "favorable_level": favorable_price})
                    self._finish(record, trade, status="AMBIGUOUS", reason="AMBIGUOUS_INTRABAR_ORDER", timestamp=resolution.event_time or bar.end_time, ambiguous=True)
                    return

            if stop_decision.crossed and (resolution is None or resolution.event == "STRUCTURAL_STOP"):
                exit_price = stop_decision.exit_price or active_stop
                event_time = resolution.event_time if resolution else bar.start_time
                stop_features = _feature_at(features, spot=exit_price, timestamp=event_time, completed=False)
                trade, reason = pm.update_position(trade, 0.0, stop_features, as_of=event_time)
                after = _state_snapshot(trade, event_time)
                label = _exit_label(reason or "STRUCTURAL_SPOT_STOP_BREACHED", trade.state)
                event = ReplayEvent(event=label, timestamp=event_time, reference_price=exit_price,
                                    active_stop=active_stop, r_multiple=_r_for(trade.direction, trade.entry_spot_price, exit_price, trade.initial_r_points),
                                    source_candle=bar.start_time, details={"manager_reason": reason, "active_stop_at_bar_start": active_stop})
                self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after, exit_event=event)
                self._finish(record, trade, status="RESOLVED", reason=label, timestamp=event_time, price=exit_price)
                self.stats["structural_stop"] += 1
                return

            if resolution is not None and resolution.event == "FAVORABLE_THEN_STOP":
                fav_time = resolution.event_time or bar.start_time
                trade, _ = pm.update_position(trade, 0.0, _feature_at(features, spot=favorable_price or features.spot_price, timestamp=fav_time, completed=False), as_of=fav_time)
                self._record_event(record, event=_event_name_for_level(level or 0.0), timestamp=fav_time,
                                   price=favorable_price, trade=trade, source=bar.start_time)
                exit_price = resolution.exit_price or active_stop
                stop_time = fav_time
                trade, reason = pm.update_position(trade, 0.0, _feature_at(features, spot=exit_price, timestamp=stop_time, completed=False), as_of=stop_time)
                after = _state_snapshot(trade, stop_time)
                label = _exit_label(reason or "STRUCTURAL_SPOT_STOP_BREACHED", trade.state)
                event = ReplayEvent(event=label, timestamp=stop_time, reference_price=exit_price, active_stop=active_stop,
                                    r_multiple=_r_for(trade.direction, trade.entry_spot_price, exit_price, trade.initial_r_points), source_candle=bar.start_time,
                                    details={"manager_reason": reason, "favorable_before_stop": True, "active_stop_at_bar_start": active_stop})
                self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after, exit_event=event)
                self._finish(record, trade, status="RESOLVED", reason=label, timestamp=stop_time, price=exit_price)
                self.stats["trailing_then_stop"] += 1
                return

            if level is not None and favorable_price is not None:
                event_time = bar.start_time
                trade, _ = pm.update_position(trade, 0.0, _feature_at(features, spot=favorable_price, timestamp=event_time, completed=False), as_of=event_time)
                self._record_event(record, event=_event_name_for_level(level), timestamp=event_time, price=favorable_price, trade=trade, source=bar.start_time)

            trade, reason = pm.update_position(trade, 0.0, features, as_of=bar.end_time)
            after = _state_snapshot(trade, bar.end_time)
            if after.active_stop != before.active_stop:
                self._record_event(record, event="TRAILING_STOP_UPDATE", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=bar.start_time,
                                   details={"previous_stop": before.active_stop, "new_stop": after.active_stop})
            if after.protected_breakeven_active and not before.protected_breakeven_active:
                self._record_event(record, event="BREAKEVEN_PROTECTION", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=bar.start_time)
            if after.profit_lock_active and not before.profit_lock_active:
                self._record_event(record, event="PROFIT_LOCK", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=bar.start_time)
            if after.runner_mode_active and not before.runner_mode_active:
                self._record_event(record, event="RUNNER_MODE", timestamp=bar.end_time, price=after.active_stop, trade=trade, source=bar.start_time)
            if reason:
                label = _exit_label(reason, trade.state)
                price = features.spot_price
                exit_event = ReplayEvent(event=label, timestamp=bar.end_time, reference_price=price, active_stop=before.active_stop,
                                         r_multiple=trade.current_r, source_candle=bar.start_time, details={"manager_reason": reason})
                self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after, exit_event=exit_event)
                self._finish(record, trade, status="RESOLVED", reason=label, timestamp=bar.end_time, price=price)
                self.stats[label.lower()] += 1
                return
            self.recorder.record_state_timeline(record.replay_signal_id, before=before, after=after)

        if entry_seen:
            self.stats["unresolved_at_session_end"] += 1
            self._finish(record, trade, status="UNRESOLVED", reason="SESSION_END_WITHOUT_EXIT")
        else:
            self._finish(record, trade, status="UNRESOLVED", reason="NO_POST_ENTRY_CANDLES")

    def replay(self, records: Iterable[ReplayManifestRecord]) -> dict[str, Any]:
        for record in records:
            self.replay_record(record)
        return {"resolver": dict(sorted(self.stats.items())), "one_minute_candles": len(self.one_minute)}


def _trade_rows(records: Iterable[ReplayManifestRecord]) -> list[ReplayManifestRecord]:
    return [r for r in records if r.lifecycle_status == "RESOLVED" and r.realized_r is not None]


def _basic(rows: list[ReplayManifestRecord]) -> dict[str, Any]:
    rs = [float(r.realized_r) for r in rows]
    winners = [r for r in rs if r > 0]
    losers = [r for r in rs if r < 0]
    return {
        "trades": len(rows), "winners": len(winners), "losers": len(losers),
        "breakeven": sum(1 for r in rs if r == 0),
        "win_rate_pct": round(len(winners) / len(rs) * 100, 2) if rs else 0.0,
        "average_winner_r": round(sum(winners) / len(winners), 4) if winners else 0.0,
        "average_loser_r": round(sum(losers) / len(losers), 4) if losers else 0.0,
        "average_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "median_r": round(float(median(rs)), 4) if rs else 0.0,
        "profit_factor": round(sum(winners) / abs(sum(losers)), 4) if losers else 0.0,
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
