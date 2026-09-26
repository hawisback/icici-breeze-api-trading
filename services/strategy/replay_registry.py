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
from services.historical.strategy_c_candidate_manifest import (
    CANDIDATE_ID as STRATEGY_C_CANDIDATE_ID,
    _spec_fingerprint as strategy_c_spec_fingerprint,
)
from services.historical.strategy_c_forward_validation import (
    FREEZE_DATE as STRATEGY_C_FREEZE_DATE,
)
from services.historical.strategy_c_shadow_observer import (
    replay_strategy_c_to_as_of,
)
from services.historical.strategy_d_candidate_manifest import (
    CANDIDATE_ID as STRATEGY_D_CANDIDATE_ID,
    FREEZE_DATE as STRATEGY_D_FREEZE_DATE,
    spec_fingerprint as strategy_d_spec_fingerprint,
)
from services.strategy.strategies.candidate_runtime import (
    strategy_c_signal_from_status,
    strategy_d_signal_from_status,
    strategy_d_signal_id,
)
from services.strategy.strategies.pivot_vwap_scalp import (
    PivotVwapScalpStrategy,
)
from services.strategy.strategies.sr_momentum_breakout import (
    StrategyDConfig,
    evaluate_strategy_d_signal,
    previous_session_levels,
)
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
    active_futures_candles_5m: Sequence[Candle] = ()
    futures_candles_1m: Sequence[Candle] = ()


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

    def on_execution_rejected(
        self,
        signal: StrategySignal,
        reason: str,
    ) -> None: ...

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

    def on_execution_rejected(
        self,
        signal: StrategySignal,
        reason: str,
    ) -> None:
        self.strategy.on_execution_rejected(signal.timestamp, reason)

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

    def on_execution_rejected(
        self,
        signal: StrategySignal,
        reason: str,
    ) -> None:
        return None

    def reset(self, at: datetime) -> None:
        self.strategy.reset(at)


class DiContinuationReplayAdapter:
    """Replay the frozen Strategy C observer used by production promotion."""

    def __init__(self, tunables: StrategyTunablesConfig) -> None:
        self.tunables = tunables
        self._consumed_signal_ids: set[str] = set()

    def strategy_metadata(self) -> ReplayStrategyMetadata:
        return ReplayStrategyMetadata(
            registry_key="di_continuation",
            strategy=StrategyName.DI_CONTINUATION,
            display_name="Strategy C · DI Continuation",
            priority=30,
            enabled=self.tunables.di_continuation_enabled,
            evaluation_start="09:45",
            evaluation_end="14:45",
            entry_start="09:45",
            entry_end="14:45",
            timeline_phase_key="strategy_c_phase",
        )

    def prepare_session(self, context: ReplaySessionContext) -> None:
        self._consumed_signal_ids.clear()

    def evaluate_completed_bar(
        self,
        context: ReplayBarContext,
        *,
        allow_evaluation: bool,
    ) -> ReplayStrategyEvaluation:
        meta = self.strategy_metadata()
        if not meta.enabled:
            return ReplayStrategyEvaluation(meta, phase="DISABLED")
        local_day = context.bar.end_time.astimezone(IST).date()
        if local_day <= STRATEGY_C_FREEZE_DATE:
            return ReplayStrategyEvaluation(
                meta,
                phase="WAITING_FOR_POST_FREEZE_SESSION",
            )
        if (
            not context.active_futures_candles_5m
            or not context.futures_candles_1m
        ):
            return ReplayStrategyEvaluation(
                meta,
                phase="NATIVE_FUTURES_HISTORY_UNAVAILABLE",
            )

        report = replay_strategy_c_to_as_of(
            context.active_futures_candles_5m,
            context.futures_candles_1m,
            as_of=context.bar.end_time,
        )
        eligible_candidates: list[tuple[datetime, dict[str, Any]]] = []
        for row in report.get("candidate_entries") or []:
            signal_id = str(row.get("candidate_signal_id") or "")
            if not signal_id or signal_id in self._consumed_signal_ids:
                continue
            try:
                entry_time = datetime.fromisoformat(
                    str(row["entry_time"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError):
                continue
            age = (context.bar.end_time - entry_time).total_seconds()
            if -5.0 <= age <= 300.0:
                eligible_candidates.append((entry_time, row))

        signal = None
        if allow_evaluation and eligible_candidates:
            entry_time, candidate = min(
                eligible_candidates,
                key=lambda item: item[0],
            )
            actual_lifecycle = dict(candidate.get("lifecycle") or {})
            entry_view = {
                **candidate,
                "lifecycle": {
                    **actual_lifecycle,
                    "status": "OPEN",
                },
            }
            status = {
                "status": report.get("status"),
                "candidate_id": STRATEGY_C_CANDIDATE_ID,
                "candidate_spec_fingerprint": (
                    strategy_c_spec_fingerprint()
                ),
                "active_candidate_trade": entry_view,
            }
            signal = strategy_c_signal_from_status(
                status,
                as_of=context.bar.end_time,
            )
            if signal is not None:
                signal = signal.model_copy(update={
                    "features_snapshot": {
                        **signal.features_snapshot,
                        "candidate_lifecycle": actual_lifecycle,
                        "replay_observed_at": (
                            context.bar.end_time.isoformat()
                        ),
                        "replay_observation_latency_seconds": round(
                            (context.bar.end_time - entry_time).total_seconds(),
                            3,
                        ),
                    }
                })
        return ReplayStrategyEvaluation(
            meta,
            signal=signal,
            phase=str(report.get("status") or "WAITING"),
            audit_records=[{
                "timestamp": context.bar.end_time.isoformat(),
                "strategy": StrategyName.DI_CONTINUATION.value,
                "phase_state": str(report.get("status") or "WAITING"),
                "raw_entries_today": len(report.get("raw_entries") or []),
                "candidate_entries_today": len(
                    report.get("candidate_entries") or []
                ),
                "native_futures_5m": len(
                    context.active_futures_candles_5m
                ),
                "native_futures_1m": len(context.futures_candles_1m),
            }],
        )

    def on_entry_confirmed(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        self._consumed_signal_ids.add(signal.signal_id)
        snapshot = dict(signal.features_snapshot)
        research = dict(snapshot.get("research_features") or {})
        atr = float(research.get("setup_atr") or 0.0)
        entry = float(
            signal.underlying_entry_price or signal.spot_reference_price
        )
        setup_end = snapshot.get("setup_end") or signal.timestamp.isoformat()
        return context.session.recorder.record_entry(
            signal=signal,
            trading_date=context.session.trading_date,
            trigger_source_candle_timestamp=setup_end,
            trigger_level=entry,
            simulated_entry_timestamp=signal.timestamp,
            simulated_entry_price=entry,
            entry_5m_candle_timestamp=context.bar.end_time,
            entry_occurred_intrabar=False,
            entry_features={
                **snapshot,
                "underlying_entry_price": entry,
                "futures_contract": (
                    context.active_futures_candles_5m[-1].instrument_id
                    if context.active_futures_candles_5m
                    else None
                ),
            },
            setup_id=str(setup_end),
            pullback_swing_low=None,
            pullback_swing_high=None,
            impulse_low=None,
            impulse_high=None,
            atr_at_entry=atr,
            initial_structural_stop=float(signal.structural_stop),
            initial_risk_points=float(signal.r_points),
            initial_risk_atr=(
                float(signal.r_points) / atr if atr > 0 else 0.0
            ),
            current_trailing_stop=float(signal.structural_stop),
            current_r=0.0,
            highest_favorable_price=entry,
            lowest_favorable_price=entry,
            peak_r=0.0,
            protected_breakeven_active=False,
            profit_lock_active=False,
            runner_mode_active=False,
            current_ladder_stage="C_FROZEN_LIFECYCLE",
            reversal_score=0,
            adverse_health_counters={},
            entry_bar_timestamp=context.bar.end_time,
            last_managed_completed_bar_timestamp=None,
        )

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        return None

    def on_execution_rejected(
        self,
        signal: StrategySignal,
        reason: str,
    ) -> None:
        self._consumed_signal_ids.add(signal.signal_id)

    def reset(self, at: datetime) -> None:
        return None


class SRMomentumBreakoutReplayAdapter:
    """Replay the frozen Strategy D V2 signal contract."""

    def __init__(self, tunables: StrategyTunablesConfig) -> None:
        self.tunables = tunables
        self.config = StrategyDConfig.v2_candidate()
        self._consumed_signal_ids: set[str] = set()
        self._used_level_keys: set[str] = set()
        self._pending_signal: StrategySignal | None = None

    def strategy_metadata(self) -> ReplayStrategyMetadata:
        return ReplayStrategyMetadata(
            registry_key="sr_momentum_breakout",
            strategy=StrategyName.SR_MOMENTUM_BREAKOUT,
            display_name="Strategy D · S&R Momentum V2 (Frozen Candidate)",
            priority=40,
            enabled=self.tunables.sr_momentum_breakout_enabled,
            evaluation_start=self.config.entry_start,
            evaluation_end=self.config.entry_end,
            entry_start=self.config.entry_start,
            entry_end=self.config.entry_end,
            timeline_phase_key="strategy_d_phase",
        )

    def prepare_session(self, context: ReplaySessionContext) -> None:
        self._consumed_signal_ids.clear()
        self._used_level_keys.clear()
        self._pending_signal = None

    def evaluate_completed_bar(
        self,
        context: ReplayBarContext,
        *,
        allow_evaluation: bool,
    ) -> ReplayStrategyEvaluation:
        meta = self.strategy_metadata()
        if not meta.enabled:
            return ReplayStrategyEvaluation(meta, phase="DISABLED")
        local_day = context.bar.end_time.astimezone(IST).date()
        if local_day < STRATEGY_D_FREEZE_DATE:
            return ReplayStrategyEvaluation(
                meta,
                phase="WAITING_FOR_FREEZE_DATE",
            )
        levels = previous_session_levels(
            context.spot_candles_5m,
            local_day,
        )
        if levels is None or not context.active_futures_candles_5m:
            return ReplayStrategyEvaluation(
                meta,
                phase="MARKET_DATA_UNAVAILABLE",
            )
        raw_signal = evaluate_strategy_d_signal(
            context.spot_candles_5m,
            context.active_futures_candles_5m,
            levels,
            self.config,
        )
        if raw_signal is not None:
            signal_id = strategy_d_signal_id(raw_signal)
            level_key = (
                f"{local_day.isoformat()}|{raw_signal.option_type}|"
                f"{raw_signal.breakout_level_name}"
            )
            if (
                level_key not in self._used_level_keys
                and signal_id not in self._consumed_signal_ids
            ):
                status = {
                    "candidate_id": STRATEGY_D_CANDIDATE_ID,
                    "candidate_spec_fingerprint": (
                        strategy_d_spec_fingerprint()
                    ),
                    "execution_signal": raw_signal.to_dict(),
                    "execution_signal_id": signal_id,
                }
                pending = strategy_d_signal_from_status(
                    status,
                    as_of=context.bar.end_time,
                )
                if pending is not None:
                    self._pending_signal = pending.model_copy(update={
                        "features_snapshot": {
                            **pending.features_snapshot,
                            "strategy_d_signal": raw_signal.to_dict(),
                        }
                    })
                    # The paper monitor freezes the level as soon as the
                    # candidate is captured, before StrategyService gates.
                    self._used_level_keys.add(level_key)

        if self._pending_signal is not None:
            age = (
                context.bar.end_time - self._pending_signal.timestamp
            ).total_seconds()
            if (
                age < -5.0
                or age > 300.0
                or self._pending_signal.signal_id
                in self._consumed_signal_ids
            ):
                self._pending_signal = None

        signal = self._pending_signal if allow_evaluation else None
        phase = (
            "SIGNAL_READY"
            if signal is not None
            else (
                "SIGNAL_HELD_BY_HIGHER_PRIORITY_OR_RISK_GATE"
                if self._pending_signal is not None
                else "MONITORING"
            )
        )
        return ReplayStrategyEvaluation(
            meta,
            signal=signal,
            phase=phase,
            audit_records=[{
                "timestamp": context.bar.end_time.isoformat(),
                "strategy": StrategyName.SR_MOMENTUM_BREAKOUT.value,
                "phase_state": phase,
                "variant": self.config.variant,
                "strategy_id": self.config.strategy_id,
                "candidate_id": STRATEGY_D_CANDIDATE_ID,
                "candidate_spec_fingerprint": strategy_d_spec_fingerprint(),
                "control_variant": "V1_CONTROL",
                "control_strategy_id": StrategyDConfig.v1_control().strategy_id,
                "levels": levels.to_dict(),
                "used_level_keys": sorted(self._used_level_keys),
                "pending_signal_id": (
                    self._pending_signal.signal_id
                    if self._pending_signal is not None
                    else None
                ),
            }],
        )

    def on_entry_confirmed(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        self._consumed_signal_ids.add(signal.signal_id)
        if (
            self._pending_signal is not None
            and self._pending_signal.signal_id == signal.signal_id
        ):
            self._pending_signal = None
        snapshot = dict(signal.features_snapshot)
        raw = dict(snapshot.get("strategy_d_signal") or {})
        entry = float(signal.spot_reference_price)
        atr = float(raw.get("atr_5m") or snapshot.get("atr_5m") or 0.0)
        return context.session.recorder.record_entry(
            signal=signal,
            trading_date=context.session.trading_date,
            trigger_source_candle_timestamp=signal.timestamp,
            trigger_level=float(
                raw.get("breakout_level")
                or snapshot.get("breakout_level")
                or entry
            ),
            simulated_entry_timestamp=signal.timestamp,
            simulated_entry_price=entry,
            entry_5m_candle_timestamp=context.bar.end_time,
            entry_occurred_intrabar=False,
            entry_features={
                **snapshot,
                "underlying_entry_price": entry,
            },
            setup_id=(
                f"{raw.get('breakout_level_name', 'LEVEL')}:"
                f"{signal.timestamp.isoformat()}"
            ),
            pullback_swing_low=None,
            pullback_swing_high=None,
            impulse_low=None,
            impulse_high=None,
            atr_at_entry=atr,
            initial_structural_stop=float(signal.structural_stop),
            initial_risk_points=float(signal.r_points),
            initial_risk_atr=(
                float(signal.r_points) / atr if atr > 0 else 0.0
            ),
            current_trailing_stop=float(signal.structural_stop),
            current_r=0.0,
            highest_favorable_price=entry,
            lowest_favorable_price=entry,
            peak_r=0.0,
            protected_breakeven_active=False,
            profit_lock_active=False,
            runner_mode_active=False,
            current_ladder_stage="D_PRE_SCALE",
            reversal_score=0,
            adverse_health_counters={},
            entry_bar_timestamp=context.bar.end_time,
            last_managed_completed_bar_timestamp=None,
        )

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        return None

    def on_execution_rejected(
        self,
        signal: StrategySignal,
        reason: str,
    ) -> None:
        self._consumed_signal_ids.add(signal.signal_id)
        if (
            self._pending_signal is not None
            and self._pending_signal.signal_id == signal.signal_id
        ):
            self._pending_signal = None

    def reset(self, at: datetime) -> None:
        # Production's frozen D paper monitor is observed before main entry
        # gates and retains used-level/pending state across blocked cycles.
        return None


class PivotVwapScalpReplayAdapter:
    """Replay adapter for the production Strategy E signal engine."""

    def __init__(
        self,
        tunables: StrategyTunablesConfig,
        *,
        replay_selected: bool = False,
    ) -> None:
        self.tunables = tunables
        self.replay_selected = replay_selected
        self.strategy = PivotVwapScalpStrategy(tunables)

    def strategy_metadata(self) -> ReplayStrategyMetadata:
        return ReplayStrategyMetadata(
            registry_key="pivot_vwap_scalp",
            strategy=StrategyName.PIVOT_VWAP_SCALP,
            display_name="Strategy E · Pivot/VWAP Scalp",
            priority=50,
            enabled=(
                self.tunables.pivot_vwap_scalp_enabled
                or self.replay_selected
            ),
            evaluation_start=self.tunables.strategy_e_entry_start,
            evaluation_end=self.tunables.strategy_e_entry_end,
            entry_start=self.tunables.strategy_e_entry_start,
            entry_end=self.tunables.strategy_e_entry_end,
            timeline_phase_key="strategy_e_phase",
        )

    def prepare_session(self, context: ReplaySessionContext) -> None:
        self.strategy.reset()

    def evaluate_completed_bar(
        self,
        context: ReplayBarContext,
        *,
        allow_evaluation: bool,
    ) -> ReplayStrategyEvaluation:
        meta = self.strategy_metadata()
        if not meta.enabled:
            return ReplayStrategyEvaluation(meta, phase="DISABLED")
        if not context.active_futures_candles_5m:
            return ReplayStrategyEvaluation(
                meta,
                phase="FUTURES_5M_UNAVAILABLE",
            )
        if allow_evaluation:
            decision = self.strategy.evaluate(
                context.active_futures_candles_5m,
                as_of=context.bar.end_time,
                expected_completed_end=context.bar.end_time,
            )
        else:
            decision = self.strategy.analyze_snapshot(
                context.active_futures_candles_5m,
                as_of=context.bar.end_time,
                expected_completed_end=context.bar.end_time,
            )
        return ReplayStrategyEvaluation(
            meta,
            signal=decision.signal if allow_evaluation else None,
            phase=decision.reason,
            audit_records=[{
                "timestamp": context.bar.end_time.isoformat(),
                "strategy": StrategyName.PIVOT_VWAP_SCALP.value,
                "phase_state": decision.reason,
                "result": decision.result,
                "signal_type": (
                    decision.signal.features_snapshot.get("signal_type")
                    if decision.signal is not None
                    else None
                ),
                "setup_family": (
                    "COUNTERTREND"
                    if decision.result.startswith("COUNTER_")
                    else (
                        "TREND"
                        if decision.result.startswith("TREND_")
                        else None
                    )
                ),
                "completed_5m_candle_timestamp": (
                    context.active_futures_candles_5m[-1]
                    .end_time.isoformat()
                ),
                "dedupe_state": self.strategy.export_state(),
                "production_enabled": (
                    self.tunables.pivot_vwap_scalp_enabled
                ),
                "replay_selected": self.replay_selected,
                "metrics": dict(decision.metrics),
            }],
        )

    def on_entry_confirmed(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        snapshot = dict(signal.features_snapshot)
        entry = float(
            signal.underlying_entry_price or signal.spot_reference_price
        )
        target = snapshot.get("target_price")
        return context.session.recorder.record_entry(
            signal=signal,
            trading_date=context.session.trading_date,
            trigger_source_candle_timestamp=signal.timestamp,
            trigger_level=entry,
            simulated_entry_timestamp=signal.timestamp,
            simulated_entry_price=entry,
            entry_5m_candle_timestamp=context.bar.end_time,
            entry_occurred_intrabar=False,
            entry_features={
                **snapshot,
                "underlying_entry_price": entry,
                "strategy_target_price": target,
            },
            setup_id=(
                f"{snapshot.get('signal_type', 'STRATEGY_E')}:"
                f"{signal.timestamp.isoformat()}"
            ),
            pullback_swing_low=None,
            pullback_swing_high=None,
            impulse_low=None,
            impulse_high=None,
            atr_at_entry=0.0,
            initial_structural_stop=float(signal.structural_stop),
            initial_risk_points=float(signal.r_points),
            initial_risk_atr=0.0,
            current_trailing_stop=float(signal.structural_stop),
            current_r=0.0,
            highest_favorable_price=entry,
            lowest_favorable_price=entry,
            peak_r=0.0,
            protected_breakeven_active=False,
            profit_lock_active=False,
            runner_mode_active=False,
            current_ladder_stage="E_FIXED_STOP_TARGET",
            reversal_score=0,
            adverse_health_counters={},
            entry_bar_timestamp=context.bar.end_time,
            last_managed_completed_bar_timestamp=None,
        )

    def on_exit(self, direction: TradeDirection, at: datetime) -> None:
        return None

    def on_execution_rejected(
        self,
        signal: StrategySignal,
        reason: str,
    ) -> None:
        return None

    def reset(self, at: datetime) -> None:
        self.strategy.reset()


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
        selected_strategies: Sequence[StrategyName] | None = None,
    ) -> "ReplayStrategyRegistry":
        adapters: tuple[ReplayStrategyAdapter, ...] = (
            TrendPullbackReplayAdapter(tunables),
            VolatilityBreakoutReplayAdapter(tunables, session),
            DiContinuationReplayAdapter(tunables),
            SRMomentumBreakoutReplayAdapter(tunables),
            PivotVwapScalpReplayAdapter(
                tunables,
                replay_selected=(
                    selected_strategies is not None
                    and StrategyName.PIVOT_VWAP_SCALP
                    in selected_strategies
                ),
            ),
        )
        if selected_strategies is not None:
            selected = set(selected_strategies)
            adapters = tuple(
                adapter
                for adapter in adapters
                if adapter.strategy_metadata().strategy in selected
            )
        return cls(adapters)

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
        winner_selected = False
        for adapter in self.adapters:
            result = adapter.evaluate_completed_bar(
                context,
                allow_evaluation=(
                    allow_evaluation and not winner_selected
                ),
            )
            results.append(result)
            if (
                stop_after_signal
                and result.signal is not None
                and not winner_selected
            ):
                winner_selected = True
        return results

    def refresh_diagnostics(
        self,
        evaluations: Sequence[ReplayStrategyEvaluation],
        context: ReplayBarContext,
    ) -> list[ReplayStrategyEvaluation]:
        """Refresh read-only diagnostics after entry/state transitions."""
        refreshed: list[ReplayStrategyEvaluation] = []
        originals = {
            item.metadata.strategy: item
            for item in evaluations
        }
        for adapter in self.adapters:
            metadata = adapter.strategy_metadata()
            original = originals.get(metadata.strategy)
            if original is None:
                continue
            current = adapter.evaluate_completed_bar(
                context,
                allow_evaluation=False,
            )
            current.signal = original.signal
            current.event = original.event
            refreshed.append(current)
        return refreshed

    def confirm_entry(
        self,
        signal: StrategySignal,
        context: ReplayBarContext,
    ) -> ReplayManifestRecord:
        for adapter in self.adapters:
            if adapter.strategy_metadata().strategy == signal.strategy:
                return adapter.on_entry_confirmed(signal, context)
        raise ValueError(f"Replay registry has no adapter for {signal.strategy.value}")

    def notify_execution_rejected(
        self,
        signal: StrategySignal,
        reason: str,
    ) -> None:
        for adapter in self.adapters:
            if adapter.strategy_metadata().strategy == signal.strategy:
                adapter.on_execution_rejected(signal, reason)
                return

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
