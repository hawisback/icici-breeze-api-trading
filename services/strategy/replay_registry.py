"""Replay strategy adapters and registry.

The registry deliberately wraps production strategy implementations rather than
reimplementing their signal rules.  It gives Day Replay one orchestration
contract while preserving each strategy's existing state machine, diagnostics,
and manifest semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Sequence

from libs.contracts.models import Candle
from libs.market_time import IST
from services.strategy.models import (
    MarketFeatures,
    SessionTimersConfig,
    STRATEGY_A_DISPLAY_LABEL,
    StrategyName,
    StrategySignal,
    StrategyTriggerDiagnostics,
    StrategyTunablesConfig,
    ThresholdOverrides,
    TradeDirection,
)
from services.strategy.replay_manifest import ReplayManifestRecord, ReplayManifestRecorder
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy


@dataclass(frozen=True)
class ReplayStrategyMetadata:
    registry_key: str
    strategy: StrategyName
    display_name: str
    priority: int
    enabled: bool
    evaluation_start: str
    evaluation_end: str
    entry_start: str
    entry_end: str
    supported_override_fields: frozenset[str] = frozenset()
    audit_diagnostics: bool = False
    audit_events: bool = False
    timeline_phase_key: str | None = None


@dataclass(frozen=True)
class ReplaySessionContext:
    trading_date: str
    instrument_id: str
    overrides: ThresholdOverrides
    recorder: ReplayManifestRecorder


@dataclass(frozen=True)
class ReplayBarContext:
    session: ReplaySessionContext
    bar: Candle
    features: MarketFeatures
    spot_candles_5m: Sequence[Candle]
    spot_candles_15m: Sequence[Candle]
    futures_candles: Sequence[Candle]


@dataclass
class ReplayStrategyEvaluation:
    metadata: ReplayStrategyMetadata
    diagnostics: list[StrategyTriggerDiagnostics] = field(default_factory=list)
    signal: StrategySignal | None = None
    phase: str = "WAITING"
    event: Any | None = None
    audit_records: list[dict[str, Any]] = field(default_factory=list)


class ReplayStrategyAdapter(Protocol):
    def strategy_metadata(self) -> ReplayStrategyMetadata: ...

    def prepare_session(self, context: ReplaySessionContext) -> None: ...

    def evaluate_completed_bar(
        self,
        context: ReplayBarContext,
        *,
        allow_evaluation: bool,
    ) -> ReplayStrategyEvaluation: ...

    def on_entry_confirmed(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord: ...

    def on_exit(self, direction: TradeDirection, at: datetime) -> None: ...

    def reset(self, at: datetime) -> None: ...


class TrendPullbackReplayAdapter:
    """Replay adapter for the production Strategy A state machine."""

    def __init__(self, tunables: StrategyTunablesConfig) -> None:
        self.tunables = tunables
        self.strategy = TrendPullbackStrategy(
            config=tunables,
            allow_session_bypass=True,
        )

    def strategy_metadata(self) -> ReplayStrategyMetadata:
        return ReplayStrategyMetadata(
            registry_key="trend_pullback",
            strategy=StrategyName.TREND_PULLBACK,
            display_name=STRATEGY_A_DISPLAY_LABEL,
            priority=10,
            enabled=self.tunables.trend_pullback_enabled,
            evaluation_start=self.tunables.entry_session_start,
            evaluation_end=self.tunables.entry_session_end,
            entry_start=self.tunables.entry_session_start,
            entry_end=self.tunables.entry_session_end,
            supported_override_fields=frozenset(),
            audit_diagnostics=True,
            audit_events=True,
            timeline_phase_key="strategy_a_phase",
        )

    def prepare_session(self, context: ReplaySessionContext) -> None:
        self.strategy.reset()

    @staticmethod
    def _audit_records(
        diagnostics: Sequence[StrategyTriggerDiagnostics],
        bar: Candle,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for diag in diagnostics:
            completed_ts = str(
                (diag.phase_summary or {}).get("completed_candle_timestamp")
                or bar.end_time.isoformat()
            )
            rows.append({
                "timestamp": bar.end_time.isoformat(),
                "completed_futures_candle": completed_ts,
                "strategy": diag.strategy.value,
                "direction": diag.direction.value,
                "option_type": diag.option_type.value,
                "phase_state": diag.phase_state,
                "key_blocker": diag.key_blocker,
                "passed_count": diag.passed_count,
                "total_count": diag.total_count,
                "ready_pct": diag.ready_pct,
                "conditions": [
                    item.model_dump(mode="json") for item in diag.conditions
                ],
                "strategy_a_contract": (
                    (diag.phase_summary or {}).get("strategy_a_contract", {})
                ),
            })
        return rows

    def evaluate_completed_bar(
        self,
        context: ReplayBarContext,
        *,
        allow_evaluation: bool,
    ) -> ReplayStrategyEvaluation:
        diagnostics = self.strategy.diagnose(
            context.features,
            context.spot_candles_5m,
            context.spot_candles_15m,
            overrides=context.session.overrides,
            futures_candles=context.futures_candles,
        )
        signal: StrategySignal | None = None
        event = None
        if (
            allow_evaluation
            and self.strategy_metadata().enabled
            and bool(context.futures_candles)
        ):
            signal = self.strategy.evaluate(
                context.features,
                context.spot_candles_5m,
                context.spot_candles_15m,
                context.futures_candles,
                context.session.overrides,
            )
            event = self.strategy.last_event
            diagnostics = self.strategy.diagnose(
                context.features,
                context.spot_candles_5m,
                context.spot_candles_15m,
                overrides=context.session.overrides,
                futures_candles=context.futures_candles,
            )

        phase = (
            max(diagnostics, key=lambda item: item.passed_count).phase_state
            if diagnostics
            else self.strategy.snapshot.state.value
        )
        return ReplayStrategyEvaluation(
            metadata=self.strategy_metadata(),
            diagnostics=diagnostics,
            signal=signal,
            phase=phase,
            event=event,
            audit_records=self._audit_records(diagnostics, context.bar),
        )

    def on_entry_confirmed(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        snapshot = signal.features_snapshot
        atr = float(snapshot.get("atr14", 0.0))
        entry_price = float(
            snapshot.get(
                "entry_price",
                signal.underlying_entry_price or signal.spot_reference_price,
            )
        )
        record = context.session.recorder.record_entry(
            signal=signal,
            trading_date=context.session.trading_date,
            trigger_source_candle_timestamp=context.bar.end_time,
            trigger_level=float(
                snapshot.get(
                    "trigger",
                    signal.underlying_entry_price or signal.spot_reference_price,
                )
            ),
            simulated_entry_timestamp=context.bar.end_time,
            simulated_entry_price=entry_price,
            entry_5m_candle_timestamp=context.bar.end_time,
            entry_occurred_intrabar=False,
            entry_features=snapshot,
            setup_id=signal.signal_id,
            pullback_swing_low=None,
            pullback_swing_high=None,
            impulse_low=None,
            impulse_high=None,
            atr_at_entry=atr,
            initial_structural_stop=float(signal.structural_stop),
            initial_risk_points=float(signal.r_points),
            initial_risk_atr=(float(signal.r_points) / atr) if atr else 0.0,
            current_trailing_stop=float(signal.structural_stop),
            current_r=0.0,
            highest_favorable_price=float(
                signal.underlying_entry_price or signal.spot_reference_price
            ),
            lowest_favorable_price=float(
                signal.underlying_entry_price or signal.spot_reference_price
            ),
            peak_r=0.0,
            protected_breakeven_active=False,
            profit_lock_active=False,
            runner_mode_active=False,
            current_ladder_stage="OPEN_INITIAL_RISK",
            reversal_score=0,
            adverse_health_counters={},
            entry_bar_timestamp=context.bar.end_time,
            last_managed_completed_bar_timestamp=None,
        )
        self.strategy.confirm_entry(signal.timestamp)
        return record

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        self.strategy.on_exit(direction, at)

    def reset(self, at: datetime) -> None:
        self.strategy.reset(at)


class VolatilityBreakoutReplayAdapter:
    """Replay adapter for the production Strategy B evaluator."""

    SUPPORTED_OVERRIDES = frozenset({
        "rvol_threshold",
        "strat_b_min_confirmation",
        "strat_b_min_available_confirmations",
        "box_max_height_atr",
        "bb_width_percentile",
        "strat_b_box_max_age_bars",
        "strat_b_breakout_buffer_atr",
        "breakout_buffer_atr",
        "strat_b_max_extension_atr",
    })

    def __init__(
        self,
        tunables: StrategyTunablesConfig,
        session: SessionTimersConfig,
    ) -> None:
        self.tunables = tunables
        self.session = session
        self.strategy = VolatilityBreakoutStrategy(
            rvol_threshold=tunables.rvol_threshold,
            adx_threshold=tunables.strategy_b_adx_threshold,
            min_confirmation_score=tunables.strat_b_min_confirmation,
            box_max_height_atr=tunables.box_max_height_atr,
            bb_width_percentile_threshold=tunables.bb_width_percentile_threshold,
            lookback_bars=tunables.compression_lookback_bars,
            max_age_bars=tunables.box_max_age_bars,
            breakout_buffer_atr=tunables.breakout_buffer_atr,
            max_extension_atr=tunables.breakout_max_extension_atr,
            entry_start=session.strategy_b_no_new_trade_before,
            entry_end=session.no_new_trade_after,
        )

    def strategy_metadata(self) -> ReplayStrategyMetadata:
        return ReplayStrategyMetadata(
            registry_key="volatility_breakout",
            strategy=StrategyName.VOLATILITY_BREAKOUT,
            display_name="Strategy B · Volatility Breakout",
            priority=20,
            enabled=self.tunables.volatility_breakout_enabled,
            evaluation_start=self.session.no_new_trade_before,
            evaluation_end=self.session.no_new_trade_after,
            entry_start=self.session.strategy_b_no_new_trade_before,
            entry_end=self.session.no_new_trade_after,
            supported_override_fields=self.SUPPORTED_OVERRIDES,
            timeline_phase_key="strategy_b_phase",
        )

    def prepare_session(self, context: ReplaySessionContext) -> None:
        self.strategy.reset()

    def evaluate_completed_bar(
        self,
        context: ReplayBarContext,
        *,
        allow_evaluation: bool,
    ) -> ReplayStrategyEvaluation:
        diagnostics = self.strategy.diagnose(
            context.features,
            context.spot_candles_5m,
            overrides=context.session.overrides,
        )
        signal = None
        if allow_evaluation and self.strategy_metadata().enabled:
            signal = self.strategy.evaluate(
                context.features,
                context.spot_candles_5m,
                overrides=context.session.overrides,
            )
        phase = (
            max(diagnostics, key=lambda item: item.passed_count).phase_state
            if diagnostics
            else "RESET"
        )
        return ReplayStrategyEvaluation(
            metadata=self.strategy_metadata(),
            diagnostics=diagnostics,
            signal=signal,
            phase=phase,
        )

    def on_entry_confirmed(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        if signal.strategy != StrategyName.VOLATILITY_BREAKOUT:
            raise ValueError(
                "Strategy B replay adapter received a non-Strategy-B signal"
            )
        if signal.timestamp != context.bar.end_time:
            raise ValueError(
                "Strategy B replay entry must use the completed breakout candle end time"
            )
        snapshot = signal.features_snapshot
        required = ("box_high", "box_low", "atr_at_lock", "breakout_trigger_price")
        missing = [key for key in required if snapshot.get(key) is None]
        if missing:
            raise ValueError(
                f"Strategy B signal {signal.signal_id} is missing replay state: "
                + ", ".join(missing)
            )
        box_high = float(snapshot["box_high"])
        box_low = float(snapshot["box_low"])
        atr_at_lock = float(snapshot["atr_at_lock"])
        if atr_at_lock <= 0 or box_high <= box_low:
            raise ValueError(f"Invalid Strategy B replay state for {signal.signal_id}")

        record = context.session.recorder.record_entry(
            signal=signal,
            trading_date=context.session.trading_date,
            trigger_source_candle_timestamp=signal.timestamp,
            trigger_level=float(snapshot["breakout_trigger_price"]),
            simulated_entry_timestamp=signal.timestamp,
            simulated_entry_price=float(signal.spot_reference_price),
            entry_5m_candle_timestamp=context.bar.start_time,
            entry_occurred_intrabar=False,
            entry_features={
                **snapshot,
                "entry_reference_spot": float(signal.spot_reference_price),
                "entry_bar_timestamp": context.bar.start_time.isoformat(),
            },
            setup_id=(
                f"VOLATILITY_BREAKOUT:{snapshot.get('box_created_time', 'UNKNOWN')}"
                f"->{signal.timestamp.isoformat()}"
            ),
            pullback_swing_low=None,
            pullback_swing_high=None,
            impulse_low=None,
            impulse_high=None,
            atr_at_entry=atr_at_lock,
            initial_structural_stop=float(signal.structural_stop),
            initial_risk_points=float(signal.r_points),
            initial_risk_atr=round(float(signal.r_points) / atr_at_lock, 2),
            box_high=box_high,
            box_low=box_low,
            atr_at_lock=atr_at_lock,
            consecutive_inside_box_closes=0,
            current_trailing_stop=float(signal.structural_stop),
            current_r=0.0,
            highest_favorable_price=float(signal.spot_reference_price),
            lowest_favorable_price=float(signal.spot_reference_price),
            peak_r=0.0,
            protected_breakeven_active=False,
            profit_lock_active=False,
            runner_mode_active=False,
            current_ladder_stage="OPEN_INITIAL_RISK",
            reversal_score=0,
            adverse_health_counters={},
            entry_bar_timestamp=context.bar.start_time,
            last_managed_completed_bar_timestamp=None,
        )
        return record

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        return None

    def reset(self, at: datetime) -> None:
        self.strategy.reset(at)


class ReplayStrategyRegistry:
    """Ordered strategy adapter registry used by Day Replay orchestration."""

    def __init__(self, adapters: Sequence[ReplayStrategyAdapter]) -> None:
        self.adapters = tuple(sorted(
            adapters,
            key=lambda adapter: adapter.strategy_metadata().priority,
        ))
        keys = [adapter.strategy_metadata().registry_key for adapter in self.adapters]
        if len(keys) != len(set(keys)):
            raise ValueError("Replay strategy registry contains duplicate keys")

    @classmethod
    def default(
        cls,
        tunables: StrategyTunablesConfig,
        session: SessionTimersConfig,
    ) -> "ReplayStrategyRegistry":
        return cls((
            TrendPullbackReplayAdapter(tunables),
            VolatilityBreakoutReplayAdapter(tunables, session),
        ))

    def strategy_metadata(self) -> list[ReplayStrategyMetadata]:
        return [adapter.strategy_metadata() for adapter in self.adapters]

    def metadata_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "registry_key": meta.registry_key,
                "strategy": meta.strategy.value,
                "display_name": meta.display_name,
                "priority": meta.priority,
                "enabled": meta.enabled,
                "evaluation_window": {
                    "start": meta.evaluation_start,
                    "end": meta.evaluation_end,
                },
                "entry_window": {
                    "start": meta.entry_start,
                    "end": meta.entry_end,
                },
                "supported_override_fields": sorted(meta.supported_override_fields),
                "audit_diagnostics": meta.audit_diagnostics,
                "audit_events": meta.audit_events,
                "timeline_phase_key": meta.timeline_phase_key,
            }
            for meta in self.strategy_metadata()
        ]

    def supported_override_fields(self) -> frozenset[str]:
        fields: set[str] = set()
        for meta in self.strategy_metadata():
            if meta.enabled:
                fields.update(meta.supported_override_fields)
        return frozenset(fields)

    def prepare_session(self, context: ReplaySessionContext) -> None:
        for adapter in self.adapters:
            adapter.prepare_session(context)

    @staticmethod
    def evaluation_window_active(
        metadata: ReplayStrategyMetadata,
        at: datetime,
        *,
        bypass_entry_window: bool,
    ) -> bool:
        if not metadata.enabled:
            return False
        if bypass_entry_window:
            return True
        clock = at.astimezone(IST)
        minutes = clock.hour * 60 + clock.minute
        start_h, start_m = map(int, metadata.evaluation_start.split(":"))
        end_h, end_m = map(int, metadata.evaluation_end.split(":"))
        return start_h * 60 + start_m <= minutes <= end_h * 60 + end_m

    def any_evaluation_window_active(
        self,
        at: datetime,
        *,
        bypass_entry_window: bool,
    ) -> bool:
        return any(
            self.evaluation_window_active(
                meta,
                at,
                bypass_entry_window=bypass_entry_window,
            )
            for meta in self.strategy_metadata()
        )

    def evaluate_completed_bar(
        self,
        context: ReplayBarContext,
        *,
        allow_evaluation: bool,
        stop_after_signal: bool = False,
    ) -> list[ReplayStrategyEvaluation]:
        results: list[ReplayStrategyEvaluation] = []
        for adapter in self.adapters:
            result = adapter.evaluate_completed_bar(
                context,
                allow_evaluation=allow_evaluation,
            )
            results.append(result)
            if stop_after_signal and result.signal is not None:
                break
        return results

    def confirm_entry(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        for adapter in self.adapters:
            if adapter.strategy_metadata().strategy == signal.strategy:
                return adapter.on_entry_confirmed(signal, context)
        raise ValueError(f"Replay registry has no adapter for {signal.strategy.value}")

    def notify_exit(
        self,
        strategy: StrategyName,
        direction: TradeDirection,
        at: datetime,
    ) -> None:
        for adapter in self.adapters:
            if adapter.strategy_metadata().strategy == strategy:
                adapter.on_exit(direction, at)
                return

    def reset_all(self, at: datetime) -> None:
        for adapter in self.adapters:
            adapter.reset(at)

    @staticmethod
    def first_signal(
        evaluations: Sequence[ReplayStrategyEvaluation],
    ) -> StrategySignal | None:
        return next(
            (result.signal for result in evaluations if result.signal is not None),
            None,
        )

    @staticmethod
    def timeline_phases(
        evaluations: Sequence[ReplayStrategyEvaluation],
    ) -> dict[str, str]:
        return {
            result.metadata.timeline_phase_key: result.phase
            for result in evaluations
            if result.metadata.timeline_phase_key
        }
