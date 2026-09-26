"""Production-path Strategy A replay and event-by-event comparison."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.models import Candle
from services.strategy.futures_signal import canonical_active_futures_stream_with_diagnostics, completed_futures_candles
from services.strategy.models import ActiveTrade, AutoTradingMode, OptionType, STRATEGY_A_VERSION_ID, StrategyName, StrategySignal, StrategyTunablesConfig, TradeDirection, TradeLifecycleState
from services.strategy.position_manager import PositionManager, calculate_realized_trade_r
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy


class ReplayDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timestamp: datetime
    futures_contract: str
    state: str
    event: str | None = None
    reason: str | None = None
    signal_id: str | None = None
    direction: str | None = None
    trigger: float | None = None
    stop: float | None = None
    r_points: float | None = None
    underlying_entry_price: float | None = None


class StrategyAReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_version: str = STRATEGY_A_VERSION_ID
    config_fingerprint: str
    data_range: dict[str, str | None]
    futures_contracts: list[str]
    setup_count: int
    entry_count: int
    rejection_reasons: dict[str, int]
    direction_distribution: dict[str, int]
    r_results: list[float] = Field(default_factory=list)
    expectancy_r: float | None = None
    profit_factor: float | None = None
    max_drawdown_r: float = 0.0
    exit_reasons: dict[str, int] = Field(default_factory=dict)
    unresolved_trades: int = 0
    data_quality_counts: dict[str, int] = Field(default_factory=dict)
    data_quality_limitations: list[str] = Field(default_factory=list)
    decisions: list[ReplayDecision] = Field(default_factory=list)


def _fingerprint(config: StrategyTunablesConfig) -> str:
    import hashlib, json
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class StrategyAReplayEngine:
    """Replay the same TrendPullbackStrategy used by service.py."""

    def __init__(self, config: StrategyTunablesConfig | None = None) -> None:
        self.config = config or StrategyTunablesConfig()

    @staticmethod
    def _trade(signal: StrategySignal) -> ActiveTrade:
        entry = signal.underlying_entry_price or signal.spot_reference_price
        return ActiveTrade(
            trade_id=signal.signal_id, mode=AutoTradingMode.PAPER,
            strategy=StrategyName.TREND_PULLBACK, direction=signal.direction,
            option_type=signal.option_type, contract_symbol="UNDERLYING-REPLAY",
            contract_instrument_id=signal.features_snapshot.get("futures_contract", "UNKNOWN"),
            expiry="REPLAY", strike=0.0, quantity=2, lot_size=1, lots=2,
            entry_time=signal.timestamp, entry_option_price=100.0,
            entry_spot_price=entry, initial_structural_stop=signal.structural_stop,
            initial_r_points=signal.r_points, current_option_price=100.0,
            current_spot_price=entry, current_trailing_stop=signal.structural_stop,
            option_hard_stop_price=0.0, state=TradeLifecycleState.OPEN_INITIAL_RISK,
            futures_contract_id=signal.features_snapshot.get("futures_contract"),
            underlying_entry_price=entry, underlying_current_price=entry,
            underlying_structural_stop=signal.structural_stop, underlying_r=signal.r_points,
            initial_quantity=2, remaining_quantity=2,
        )

    @staticmethod
    def _drawdown(values: list[float]) -> float:
        equity = peak = drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return round(drawdown, 4)

    @staticmethod
    def _realized_r(trade: ActiveTrade, final_price: float) -> float:
        entry = trade.underlying_entry_price or trade.entry_spot_price
        risk = trade.underlying_r or trade.initial_r_points
        exits: list[tuple[int, float]] = []
        if trade.partial_exit_filled_quantity and trade.t1_decision_underlying_price:
            exits.append((trade.partial_exit_filled_quantity, trade.t1_decision_underlying_price))
        if trade.quantity > 0:
            exits.append((trade.quantity, final_price))
        return calculate_realized_trade_r(
            trade.direction, entry, risk, trade.initial_quantity or trade.quantity, exits
        )

    def replay(self, futures_candles: Sequence[Candle]) -> StrategyAReplayReport:
        bars, data_gaps = canonical_active_futures_stream_with_diagnostics(futures_candles, interval="15m")
        if not bars:
            bars = completed_futures_candles(futures_candles, interval="5m")
        strategy = TrendPullbackStrategy(config=self.config)
        history: list[Candle] = []
        decisions: list[ReplayDecision] = []
        contracts: set[str] = set()
        setups = entries = 0
        rejections: Counter[str] = Counter()
        directions: Counter[str] = Counter()
        r_results: list[float] = []
        exit_reasons: Counter[str] = Counter()
        unresolved = 0
        active_trade: ActiveTrade | None = None
        manager = PositionManager(strategy_config=self.config)
        last_contract: str | None = None
        for bar in bars:
            contracts.add(bar.instrument_id)
            if last_contract and bar.instrument_id != last_contract:
                if active_trade is not None:
                    unresolved += 1
                    exit_reasons["FUTURES_ROLLOVER_RESET"] += 1
                    active_trade = None
                rejections["FUTURES_ROLLOVER_RESET"] += 1
            last_contract = bar.instrument_id
            history.append(bar)
            class EventClock:
                timestamp = bar.end_time
            signal = strategy.evaluate(EventClock(), [], [], futures_candles=history)
            event = strategy.last_event
            if event and event.event == "SETUP_CREATED":
                setups += 1
            if signal:
                entries += 1
                directions[signal.option_type.value] += 1
                if active_trade is None:
                    active_trade = self._trade(signal)
                    strategy.confirm_entry(signal.timestamp)
            if event and event.reason and event.event in {"REJECTED", "INVALIDATED", "EXPIRED"}:
                rejections[event.reason] += 1
            if active_trade is not None and active_trade.entry_time < bar.end_time:
                stop = active_trade.current_trailing_stop
                favorable = bar.high if active_trade.direction is TradeDirection.BULLISH else bar.low
                stop_hit = (bar.low <= stop) if active_trade.direction is TradeDirection.BULLISH else (bar.high >= stop)
                favorable_hit = (
                    favorable >= active_trade.underlying_entry_price + active_trade.initial_r_points
                    if active_trade.direction is TradeDirection.BULLISH
                    else favorable <= active_trade.underlying_entry_price - active_trade.initial_r_points
                )
                # Conservative OHLC ordering: when both a protective stop and
                # a favorable excursion are present, resolve the stop first.
                if stop_hit:
                    exit_price = bar.open if ((bar.open <= stop) if active_trade.direction is TradeDirection.BULLISH else (bar.open >= stop)) else stop
                    r_results.append(self._realized_r(active_trade, exit_price))
                    exit_reasons["UNDERLYING_TRAILING_STOP" if active_trade.peak_r >= self.config.trailing_activation_r else "UNDERLYING_STRUCTURAL_STOP"] += 1
                    strategy.on_exit(active_trade.direction, bar.end_time)
                    active_trade = None
                else:
                    if favorable_hit:
                        _, favorable_reason = manager.update_strategy_a_position(active_trade, favorable, 100.0, as_of=bar.end_time)
                        if favorable_reason == "T1_PARTIAL_EXIT":
                            manager.apply_t1_partial_fill(active_trade, raw_bid=100.0, executable_price=100.0, slippage_points=0.0, filled_at=bar.end_time)
                    _, reason = manager.update_strategy_a_position(active_trade, bar.close, 100.0, as_of=bar.end_time)
                    if reason == "T1_PARTIAL_EXIT":
                        manager.apply_t1_partial_fill(active_trade, raw_bid=100.0, executable_price=100.0, slippage_points=0.0, filled_at=bar.end_time)
                        _, reason = manager.update_strategy_a_position(active_trade, bar.close, 100.0, as_of=bar.end_time)
                    if reason and reason.startswith("SESSION_FORCE_SQUARE_OFF"):
                        exit_price = bar.close
                        r_results.append(self._realized_r(active_trade, exit_price)); exit_reasons[reason] += 1
                        strategy.on_exit(active_trade.direction, bar.end_time); active_trade = None
                    elif reason in {"UNDERLYING_STRUCTURAL_STOP", "UNDERLYING_TRAILING_STOP"}:
                        r_results.append(self._realized_r(active_trade, bar.close)); exit_reasons[reason] += 1
                        strategy.on_exit(active_trade.direction, bar.end_time); active_trade = None
            setup = strategy.snapshot.setup
            decisions.append(ReplayDecision(
                timestamp=bar.end_time, futures_contract=bar.instrument_id,
                state=strategy.snapshot.state.value, event=event.event if event else None,
                reason=event.reason if event else None, signal_id=signal.signal_id if signal else None,
                direction=signal.option_type.value if signal else (setup.direction.value if setup else None),
                trigger=setup.trigger_price if setup else None,
                stop=setup.structural_stop if setup else None,
                r_points=signal.r_points if signal else (setup.initial_underlying_r if setup else None),
                underlying_entry_price=signal.underlying_entry_price if signal else None,
            ))
        if active_trade is not None:
            unresolved += 1
            exit_reasons["SESSION_END_WITHOUT_EXIT"] += 1
            active_trade = None
        limitation = [
            "15m OHLC cannot prove intrabar order; trigger assumptions are gap-at-open otherwise trigger-price fills.",
            "No option quote stream is used; report is underlying-signal evidence, not option profitability.",
            "When a 15m bar touches both a favorable level and a protective stop, the stop is resolved first.",
            "Unresolved trades are retained at session end rather than marked profitable or losing.",
        ]
        if data_gaps:
            limitation.append("Active futures candle gaps are skipped and reported as data quality, never treated as contract rollovers.")
        timestamps = [bar.end_time.astimezone(timezone.utc).isoformat() for bar in bars]
        expectancy = round(sum(r_results) / len(r_results), 4) if r_results else None
        gains = sum(r for r in r_results if r > 0)
        losses = abs(sum(r for r in r_results if r < 0))
        return StrategyAReplayReport(
            config_fingerprint=_fingerprint(self.config),
            data_range={"start": min(timestamps) if timestamps else None, "end": max(timestamps) if timestamps else None},
            futures_contracts=sorted(contracts), setup_count=setups, entry_count=entries,
            rejection_reasons=dict(sorted(rejections.items())), direction_distribution=dict(sorted(directions.items())),
            r_results=r_results, expectancy_r=expectancy,
            profit_factor=round(gains / losses, 4) if losses else (None if not gains else None),
            max_drawdown_r=self._drawdown(r_results), exit_reasons=dict(sorted(exit_reasons.items())),
            unresolved_trades=unresolved,
            data_quality_counts={"ACTIVE_FUTURES_CANDLE_MISSING": len(data_gaps)} if data_gaps else {},
            data_quality_limitations=limitation, decisions=decisions,
        )


def compare_replay_decisions(runtime: Iterable[dict[str, Any] | ReplayDecision], replay: Iterable[ReplayDecision]) -> list[dict[str, Any]]:
    """Return meaningful event-level mismatches, not only aggregate totals."""
    def key(row: Any) -> tuple[str, str]:
        if isinstance(row, ReplayDecision):
            return row.timestamp.isoformat(), row.futures_contract
        return str(row.get("timestamp")), str(row.get("futures_contract"))
    left = {key(row): row for row in runtime}
    right = {key(row): row for row in replay}
    mismatches: list[dict[str, Any]] = []
    for identity in sorted(set(left) | set(right)):
        a, b = left.get(identity), right.get(identity)
        def values(row: Any) -> dict[str, Any] | None:
            if row is None: return None
            payload = row.model_dump(mode="json") if isinstance(row, ReplayDecision) else dict(row)
            # Pydantic serializes UTC as ``Z`` while runtime adapters often
            # emit ``+00:00``.  Compare the event identity semantically while
            # retaining every decision field in the parity check.
            if isinstance(payload.get("timestamp"), str):
                try:
                    payload["timestamp"] = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00")).isoformat()
                except ValueError:
                    pass
            return payload
        if values(a) != values(b):
            mismatches.append({"identity": identity, "runtime": values(a), "replay": values(b)})
    return mismatches
