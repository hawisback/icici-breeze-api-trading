"""Production-path Strategy A replay and event-by-event comparison."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.models import Candle
from services.strategy.futures_signal import completed_futures_candles
from services.strategy.models import StrategyTunablesConfig
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


class StrategyAReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_version: str = "trend_pullback_confluence_v1"
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

    def replay(self, futures_candles: Sequence[Candle]) -> StrategyAReplayReport:
        bars = completed_futures_candles(futures_candles, interval="15m")
        if not bars:
            bars = completed_futures_candles(futures_candles, interval="5m")
        strategy = TrendPullbackStrategy(config=self.config)
        history: list[Candle] = []
        decisions: list[ReplayDecision] = []
        contracts: set[str] = set()
        setups = entries = 0
        rejections: Counter[str] = Counter()
        directions: Counter[str] = Counter()
        last_contract: str | None = None
        for bar in bars:
            contracts.add(bar.instrument_id)
            if last_contract and bar.instrument_id != last_contract:
                strategy.reset(bar.end_time)
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
            if event and event.reason and event.event in {"REJECTED", "INVALIDATED", "EXPIRED"}:
                rejections[event.reason] += 1
            setup = strategy.snapshot.setup
            decisions.append(ReplayDecision(
                timestamp=bar.end_time, futures_contract=bar.instrument_id,
                state=strategy.snapshot.state.value, event=event.event if event else None,
                reason=event.reason if event else None, signal_id=signal.signal_id if signal else None,
                direction=signal.option_type.value if signal else (setup.direction.value if setup else None),
                trigger=setup.trigger_price if setup else None,
                stop=setup.structural_stop if setup else None,
                r_points=setup.initial_underlying_r if setup else None,
            ))
        limitation = [
            "15m OHLC cannot prove intrabar order; trigger assumptions are gap-at-open otherwise trigger-price fills.",
            "No option quote stream is used; report is underlying-signal evidence, not option profitability.",
        ]
        timestamps = [bar.end_time.astimezone(timezone.utc).isoformat() for bar in bars]
        return StrategyAReplayReport(
            config_fingerprint=_fingerprint(self.config),
            data_range={"start": min(timestamps) if timestamps else None, "end": max(timestamps) if timestamps else None},
            futures_contracts=sorted(contracts), setup_count=setups, entry_count=entries,
            rejection_reasons=dict(sorted(rejections.items())), direction_distribution=dict(sorted(directions.items())),
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
            return row.model_dump(mode="json") if isinstance(row, ReplayDecision) else row
        if values(a) != values(b):
            mismatches.append({"identity": identity, "runtime": values(a), "replay": values(b)})
    return mismatches
