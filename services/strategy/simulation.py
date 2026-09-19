"""Simulation & Day-Replay Engine for NIFTY Intraday Options Auto-Trading.
Enables full-session backtesting and walk-forward replay against historical candles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any, Optional

from libs.contracts.models import Candle, utc_now
from services.strategy.contract_selector import ContractSelector
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    DecisionLogEntry,
    HistoricalReplaySource,
    MarketFeatures,
    OptionSelectionConfig,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    SimulatedTradeRecord,
    SimulationBarSnapshot,
    SimulationRequest,
    SimulationResult,
    StrategyName,
    ThresholdOverrides,
    TradeDirection,
    TradeLifecycleState,
)
from services.strategy.replay_metadata import (
    build_configuration_snapshot,
    build_data_fingerprint,
    configuration_fingerprint,
)
from services.strategy.position_manager import PositionManager
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


class SimulationEngine:
    """Replays historical 5m candles bar-by-bar to simulate intraday trading."""

    def __init__(
        self,
        historical_service: Optional[Any] = None,
        risk_config: Optional[RiskConfig] = None,
        session_config: Optional[SessionTimersConfig] = None,
        tunables=None,
        replay_manifest_recorder: Optional[ReplayManifestRecorder] = None,
    ) -> None:
        self.hist_svc = historical_service
        self.risk_config = risk_config or RiskConfig()
        self.session_config = session_config or SessionTimersConfig()
        from services.strategy.models import StrategyTunablesConfig
        self.tunables = tunables or StrategyTunablesConfig()
        self.replay_manifest_recorder = replay_manifest_recorder

    @staticmethod
    def resample_to_15m(candles_5m: list[Candle], instrument_id: str = "INST-NIFTY-INDEX") -> list[Candle]:
        """Resample a 5m candle sequence into 15m candles."""
        if not candles_5m:
            return []
        buckets: dict[datetime, list[Candle]] = {}
        for c in candles_5m:
            dt_ist = c.start_time.astimezone(IST)
            bucket_minute = (dt_ist.minute // 15) * 15
            bucket_ist = dt_ist.replace(minute=bucket_minute, second=0, microsecond=0)
            bucket_utc = bucket_ist.astimezone(timezone.utc)
            buckets.setdefault(bucket_utc, []).append(c)

        res: list[Candle] = []
        for b_start, b_candles in sorted(buckets.items()):
            b_candles.sort(key=lambda c: c.start_time)
            if len(b_candles) != 3 or any(c.start_time != b_start + timedelta(minutes=5*i) for i, c in enumerate(b_candles)):
                continue
            b_end = b_start + timedelta(minutes=15)
            res.append(
                Candle(
                    instrument_id=instrument_id,
                    interval="15m",
                    start_time=b_start,
                    end_time=b_end,
                    open=b_candles[0].open,
                    high=max(x.high for x in b_candles),
                    low=min(x.low for x in b_candles),
                    close=b_candles[-1].close,
                    volume=sum(x.volume for x in b_candles),
                    open_interest=b_candles[-1].open_interest,
                    source=b_candles[0].source,
                )
            )
        return res

    async def get_available_dates(
        self,
        historical_source: HistoricalReplaySource = HistoricalReplaySource.BREEZE,
    ) -> list[str]:
        """Discovers distinct trading session dates available in historical storage."""
        dates: set[str] = set()
        if self.hist_svc and hasattr(self.hist_svc, "repo"):
            try:
                async with self.hist_svc.repo.engine.connect() as conn:
                    if historical_source == HistoricalReplaySource.MIXED:
                        source_clause = "source IN ('BREEZE', 'KITE', 'LIVE')"
                        params: tuple[Any, ...] = ()
                    else:
                        source_clause = "source = ?"
                        params = (historical_source.value,)
                    cursor = await conn.execute(f"""
                        SELECT DISTINCT substr(start_time, 1, 10) as day
                        FROM historical_candles
                        WHERE instrument_id = 'INST-NIFTY-INDEX' AND interval = '5m'
                        AND {source_clause}
                        ORDER BY day DESC
                        LIMIT 30;
                    """, params)
                    rows = await cursor.fetchall()
                    for r in rows:
                        if r[0]:
                            dates.add(str(r[0]))
            except Exception as ex:
                logger.warning("Failed to query historical dates: %s", ex)

        return sorted(list(dates), reverse=True)

    async def _fetch_session_candles(
        self,
        date_str: str,
        instrument_id: str,
        *,
        historical_source: HistoricalReplaySource,
        source_diagnostics: dict[str, Any],
        role: str,
    ) -> tuple[list[Candle], list[Candle]]:
        """Retrieves warm-up candles and session candles for the given date.
        
        Returns:
            (warmup_candles, session_candles)
        """
        # Parse date in IST
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        session_start_ist = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST)
        session_end_ist = datetime(target_date.year, target_date.month, target_date.day, 15, 30, tzinfo=IST)

        session_start_utc = session_start_ist.astimezone(timezone.utc)
        session_end_utc = session_end_ist.astimezone(timezone.utc)
        warmup_start_utc = session_start_utc - timedelta(days=7)  # enough real 15m bars to initialize EMA50

        all_candles: list[Candle] = []

        # 1. Try fetching from HistoricalService / Breeze
        if self.hist_svc:
            try:
                # If breeze is available, proactively fetch to refresh cache
                if (
                    getattr(self.hist_svc, "broker_gateway", None)
                    and historical_source in (HistoricalReplaySource.BREEZE, HistoricalReplaySource.MIXED)
                ):
                    await self.hist_svc.fetch_candles_from_breeze(instrument_id, interval="5m", days_back=7)

                candles = await self.hist_svc.get_candles(
                    instrument_id=instrument_id,
                    interval="5m",
                    start_time=warmup_start_utc,
                    end_time=session_end_utc,
                    limit=1000,
                    requested_source=historical_source.value,
                    allow_provider_fallback=False,
                    allow_synthetic_fallback=False,
                )
                if candles:
                    all_candles = sorted(
                        (c for c in candles if c.source in ("BREEZE", "KITE", "LIVE")
                         and c.end_time <= min(utc_now(), session_end_utc)),
                        key=lambda c: c.start_time,
                    )
            except Exception as ex:
                logger.warning("Historical service query error: %s", ex)

        available_counts: dict[str, int] = {}
        for candle in all_candles:
            available_counts[candle.source] = available_counts.get(candle.source, 0) + 1
        allowed_sources = (
            {"BREEZE", "KITE", "LIVE"}
            if historical_source == HistoricalReplaySource.MIXED
            else {historical_source.value}
        )
        selected_candles = [c for c in all_candles if c.source in allowed_sources]
        selected_counts: dict[str, int] = {}
        for candle in selected_candles:
            selected_counts[candle.source] = selected_counts.get(candle.source, 0) + 1
        source_diagnostics[role] = {
            "requested_source": historical_source.value,
            "available_before_filter": dict(sorted(available_counts.items())),
            "selected_after_filter": dict(sorted(selected_counts.items())),
            "selected_count": len(selected_candles),
            "missing_selected_source": len(selected_candles) == 0,
        }
        all_candles = selected_candles

        # 2. Separate into warm-up vs session candles
        warmup_candles: list[Candle] = []
        session_candles: list[Candle] = []

        for c in all_candles:
            c_ist = c.start_time.astimezone(IST)
            if c_ist.date() < target_date or (c_ist.date() == target_date and c_ist.time() < session_start_ist.time()):
                warmup_candles.append(c)
            elif c_ist.date() == target_date and session_start_ist.time() <= c_ist.time() <= session_end_ist.time():
                session_candles.append(c)

        return warmup_candles, session_candles

    async def run_day_simulation(self, request: SimulationRequest) -> SimulationResult:
        """Replay actual bars without inventing historical option fills or PnL."""
        date_str = request.date or datetime.now(IST).strftime("%Y-%m-%d")
        historical_source = request.historical_source
        bypass_entry_window = (
            request.bypass_entry_window
            if request.bypass_entry_window is not None
            else request.bypass_window
        )
        source_diagnostics: dict[str, Any] = {}
        warmup, session = await self._fetch_session_candles(
            date_str,
            request.instrument_id,
            historical_source=historical_source,
            source_diagnostics=source_diagnostics,
            role="spot",
        )
        futures_history = []
        selected_contracts: list[dict[str, str | None]] = []
        inst_svc = getattr(self.hist_svc, "instrument_service", None)
        if inst_svc:
            instruments = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
            contracts = sorted((i for i in instruments if i.segment == "FUTURES" and i.expiry
                                and i.expiry >= date_str), key=lambda i: i.expiry)
            if contracts:
                selected_contracts = [{
                    "instrument_id": contracts[0].instrument_id,
                    "expiry": contracts[0].expiry,
                }]
                warm_fut, day_fut = await self._fetch_session_candles(
                    date_str,
                    contracts[0].instrument_id,
                    historical_source=historical_source,
                    source_diagnostics=source_diagnostics,
                    role="futures",
                )
                futures_history = warm_fut + day_fut
        if "futures" not in source_diagnostics:
            source_diagnostics["futures"] = {
                "requested_source": historical_source.value,
                "available_before_filter": {},
                "selected_after_filter": {},
                "selected_count": 0,
                "missing_selected_source": True,
            }
        overrides = request.overrides or ThresholdOverrides()
        if bypass_entry_window:
            overrides = overrides.model_copy(update={"bypass_entry_window": True})
        cfg = self.tunables
        strat_a = TrendPullbackStrategy(
            adx_threshold=cfg.adx_threshold, rvol_threshold=cfg.rvol_threshold,
            ema_slope_threshold=cfg.ema_slope_threshold, min_confirmation_score=cfg.min_confirmation_score)
        strat_b = VolatilityBreakoutStrategy(rvol_threshold=cfg.rvol_threshold, adx_threshold=cfg.adx_threshold,
                                             min_confirmation_score=cfg.strat_b_min_confirmation,
                                             box_max_height_atr=cfg.box_max_height_atr,
                                             bb_width_percentile_threshold=cfg.bb_width_percentile_threshold,
                                             lookback_bars=cfg.compression_lookback_bars,
                                             max_age_bars=cfg.box_max_age_bars,
                                             breakout_buffer_atr=cfg.breakout_buffer_atr,
                                             max_extension_atr=cfg.breakout_max_extension_atr,
                                             entry_start=self.session_config.strategy_b_no_new_trade_before,
                                             entry_end=self.session_config.no_new_trade_after)
        missing_data: list[str] = []
        if source_diagnostics["spot"]["missing_selected_source"] or not session:
            missing_data.append("spot")
        if source_diagnostics["futures"]["missing_selected_source"] or not futures_history:
            missing_data.append("futures")
        config_snapshot = build_configuration_snapshot(
            start_date=date_str,
            end_date=date_str,
            instrument_id=request.instrument_id,
            historical_source=historical_source,
            bypass_entry_window=bypass_entry_window,
            strategy_a_enabled=cfg.trend_pullback_enabled,
            overrides=overrides,
            tunables=cfg,
            session=self.session_config,
        )
        config_hash = configuration_fingerprint(config_snapshot)
        data_snapshot = build_data_fingerprint(
            source=historical_source,
            start_date=date_str,
            end_date=date_str,
            spot_candles=warmup + session,
            futures_candles=futures_history,
            source_diagnostics=source_diagnostics,
            futures_contracts=selected_contracts,
            missing_data=missing_data,
        )
        replay_metadata = {
            "configuration_snapshot": config_snapshot.model_dump(mode="json"),
            "configuration_fingerprint": config_hash,
            "data_fingerprint": data_snapshot.model_dump(mode="json"),
            "historical_source": historical_source.value,
            "bypass_entry_window": bypass_entry_window,
            "missing_data": sorted(set(missing_data)),
        }
        timeline, logs = [], []
        replay_trigger_states: dict[str, dict[str, Any]] = {}
        replay_trigger_diagnostics: list[dict[str, Any]] = []
        replay_manifest_recorder = self.replay_manifest_recorder or ReplayManifestRecorder()
        replay_manifest_recorder.set_replay_metadata(replay_metadata)
        running = list(warmup)
        start_h, start_m = map(int, self.session_config.no_new_trade_before.split(":"))
        end_h, end_m = map(int, self.session_config.no_new_trade_after.split(":"))
        for idx, bar in enumerate(session):
            running.append(bar)
            macro = self.resample_to_15m(running, request.instrument_id)
            futures = [c for c in futures_history if c.end_time <= bar.end_time]
            features = FeatureEngine.compute_all_features(running, macro, futures,
                                                          spot_price=bar.close, as_of=bar.end_time)
            diags_a = strat_a.diagnose(features, running, macro, overrides=overrides, futures_candles=futures)
            diags_b = strat_b.diagnose(features, running, overrides=overrides)
            clock = bar.end_time.astimezone(IST)
            minutes = clock.hour*60+clock.minute
            in_window = bypass_entry_window or start_h*60+start_m <= minutes <= end_h*60+end_m
            event, details = None, None
            effective_diags_a = diags_a
            if (features.data_ready or features.breakout_data_ready) and in_window:
                sig_a = None
                replay_trigger_handled = False
                if cfg.trend_pullback_enabled and features.data_ready:
                    for diag in diags_a:
                        direction = diag.direction.value
                        direction_label = "CALL" if direction == "BULLISH" else "PUT"
                        phase_summary = diag.phase_summary or {}
                        impulse = phase_summary.get("impulse") or {}
                        trigger = phase_summary.get("trigger") or {}
                        stored = replay_trigger_states.get(direction)

                        if diag.phase_state != "WAIT_FOR_TRIGGER" or not impulse.get("found") or not trigger:
                            if stored:
                                replay_trigger_states.pop(direction, None)
                            continue

                        setup_id = (
                            f"{direction_label}:{impulse.get('impulse_start')}"
                            f"->{impulse.get('impulse_end')}"
                        )
                        if stored is None or stored["setup_id"] != setup_id:
                            replay_trigger_states[direction] = stored = {
                                "setup_id": setup_id,
                                "direction": direction_label,
                                "wait_timestamp": bar.end_time.isoformat(),
                                "trigger_price": float(trigger["breakout_trigger_price"]),
                                "buffer_atr": trigger.get("atr_buffer_multiple"),
                                "buffer_points": trigger.get("atr_buffer_points"),
                                "source_candle_timestamp": bar.end_time.isoformat(),
                                "attempt_number": 0,
                            }
                            # The candle that establishes the trigger is not itself
                            # evaluated as a later crossing candle.
                            continue

                        # A replay crossing is a one-time event for this locked
                        # setup.  Keep the setup record after the crossing so a
                        # later candle cannot recreate the trigger or require a
                        # second crossing.  The state is cleared only when the
                        # underlying strategy no longer reports this setup as
                        # WAIT_FOR_TRIGGER (for example, on invalidation).
                        if stored.get("processed"):
                            replay_trigger_handled = True
                            continue

                        stored["attempt_number"] += 1
                        trigger_price = stored["trigger_price"]
                        crossed = (
                            bar.high >= trigger_price
                            if direction_label == "CALL"
                            else bar.low <= trigger_price
                        )
                        crossing_amount = (
                            bar.high - trigger_price
                            if direction_label == "CALL"
                            else trigger_price - bar.low
                        ) if crossed else 0.0

                        replay_record = {
                            "direction": direction_label,
                            "setup_id": stored["setup_id"],
                            "wait_for_trigger_timestamp": stored["wait_timestamp"],
                            "stored_trigger_level": trigger_price,
                            "atr_buffer_multiple": stored["buffer_atr"],
                            "atr_buffer_points": stored["buffer_points"],
                            "trigger_source_candle_timestamp": stored["source_candle_timestamp"],
                            "historical_candle_timestamp": bar.end_time.isoformat(),
                            "historical_candle_ohlc": {
                                "open": bar.open,
                                "high": bar.high,
                                "low": bar.low,
                                "close": bar.close,
                            },
                            "intrabar_crossing": "YES" if crossed else "NO",
                            "crossing_amount": round(crossing_amount, 2) if crossed else 0.0,
                            "simulated_trigger_timestamp": bar.end_time.isoformat() if crossed else None,
                            "simulated_trigger_price": trigger_price if crossed else None,
                            "replay_price_used": "candle_high" if direction_label == "CALL" else "candle_low",
                        }

                        if crossed:
                            sig_a, replay_diag = strat_a.evaluate_replay_trigger(
                                diag.direction,
                                features,
                                running,
                                macro,
                                futures,
                                overrides,
                                trigger_price=trigger_price,
                                trigger_timestamp=bar.end_time.isoformat(),
                                source_candle_timestamp=stored["source_candle_timestamp"],
                                historical_candle_timestamp=bar.end_time.isoformat(),
                            )
                            replay_trigger_handled = True
                            stored["processed"] = True
                            stored["processed_timestamp"] = bar.end_time.isoformat()
                            if replay_diag:
                                effective_diags_a = [
                                    replay_diag if item.direction == diag.direction else item
                                    for item in diags_a
                                ]
                                replay_summary = replay_diag.phase_summary or {}
                                replay_confirmation = replay_summary.get("confirmation") or {}
                                replay_risk = replay_summary.get("risk") or {}
                                risk_condition = next(
                                    (item for item in replay_diag.conditions if item.id == "risk_r_band"),
                                    None,
                                )
                                replay_record.update(
                                    {
                                        "confirmation_available": replay_confirmation.get("available_confirmation_count"),
                                        "confirmation_passed": replay_confirmation.get("passed_confirmation_count"),
                                        "confirmation_result": replay_confirmation.get("reason"),
                                        "structural_r_atr": replay_risk.get("initial_risk_atr"),
                                        "structural_r_result": (
                                            "PASS"
                                            if risk_condition and risk_condition.status in ("PASSED", "PASS")
                                            else "FAIL"
                                        ),
                                        "signal_generated": bool(sig_a),
                                        "entry_ready": bool(sig_a),
                                        "final_state": replay_diag.phase_state,
                                        "final_blocker": replay_diag.key_blocker,
                                    }
                                )
                            if sig_a is not None:
                                snapshot = sig_a.features_snapshot
                                replay_manifest_recorder.record_entry(
                                    signal=sig_a,
                                    trading_date=date_str,
                                    trigger_source_candle_timestamp=stored["source_candle_timestamp"],
                                    trigger_level=trigger_price,
                                    simulated_entry_timestamp=stored["processed_timestamp"],
                                    simulated_entry_price=float(sig_a.spot_reference_price),
                                    entry_5m_candle_timestamp=bar.start_time,
                                    entry_occurred_intrabar=True,
                                    entry_features={
                                        **snapshot,
                                        "adx": features.adx_15m,
                                        "rvol": features.rvol_5m,
                                        "ema_slope": features.ema20_slope_norm_15m,
                                        "entry_bar_timestamp": bar.start_time.isoformat(),
                                    },
                                    setup_id=stored["setup_id"],
                                    pullback_swing_low=snapshot.get("pullback_low"),
                                    pullback_swing_high=snapshot.get("pullback_high"),
                                    impulse_low=snapshot.get("impulse_low"),
                                    impulse_high=snapshot.get("impulse_high"),
                                    atr_at_entry=float(snapshot.get("atr", 0.0)),
                                    initial_structural_stop=float(sig_a.structural_stop),
                                    initial_risk_points=float(sig_a.r_points),
                                    initial_risk_atr=float(replay_record.get("structural_r_atr") or 0.0),
                                    current_trailing_stop=float(sig_a.structural_stop),
                                    current_r=0.0,
                                    highest_favorable_price=float(sig_a.spot_reference_price),
                                    lowest_favorable_price=float(sig_a.spot_reference_price),
                                    peak_r=0.0,
                                    protected_breakeven_active=False,
                                    profit_lock_active=False,
                                    runner_mode_active=False,
                                    current_ladder_stage="OPEN_INITIAL_RISK",
                                    reversal_score=0,
                                    adverse_health_counters={},
                                    entry_bar_timestamp=bar.start_time,
                                    last_managed_completed_bar_timestamp=None,
                                )
                            replay_trigger_diagnostics.append(replay_record)
                            break

                        replay_record.update(
                            {
                                "confirmation_available": None,
                                "confirmation_passed": None,
                                "confirmation_result": None,
                                "structural_r_atr": None,
                                "structural_r_result": None,
                                "final_state": "WAIT_FOR_TRIGGER",
                                "final_blocker": diag.key_blocker,
                            }
                        )
                        replay_trigger_diagnostics.append(replay_record)

                if not replay_trigger_handled and cfg.trend_pullback_enabled:
                    sig_a = strat_a.evaluate(features, running, macro, futures, overrides)
                sig_b = strat_b.evaluate(features, running, overrides=overrides) if cfg.volatility_breakout_enabled else None
                signal = sig_a or sig_b
                if signal:
                    event, details = "SIGNAL_ONLY", "Qualified signal; historical executable option quotes unavailable"
                    logs.append(DecisionLogEntry(id=f"SIM-{idx}", timestamp=bar.end_time, category="SETUP",
                                                 strategy=signal.strategy.value, message=details,
                                                 details=signal.model_dump(mode="json")))
                elif replay_trigger_handled:
                    replay_event = replay_trigger_diagnostics[-1]
                    event = "TRIGGER_CROSSED"
                    details = replay_event.get("final_blocker") or "Intrabar trigger crossed"
                    logs.append(DecisionLogEntry(
                        id=f"SIM-TRIGGER-{idx}",
                        timestamp=bar.end_time,
                        category="TRIGGER",
                        strategy=StrategyName.TREND_PULLBACK.value,
                        message=details,
                        details=replay_event,
                    ))
            else:
                strat_a.reset(bar.end_time)
                strat_b.reset(bar.end_time)
                replay_trigger_states.clear()
                details = features.data_reason if not features.data_ready else "Outside entry window"
            timeline.append(SimulationBarSnapshot(
                bar_index=idx, timestamp=bar.end_time.isoformat(), ist_time=clock.strftime("%H:%M"),
                open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume, spot=bar.close,
                ema9_5m=features.ema9_5m, ema20_5m=features.ema20_5m,
                supertrend=features.supertrend_direction, adx_15m=features.adx_15m,
                rvol_5m=features.rvol_5m, bb_width_percentile=features.bb_width_percentile,
                strategy_a_phase=max(effective_diags_a, key=lambda d: d.passed_count).phase_state,
                strategy_b_phase=max(diags_b, key=lambda d: d.passed_count).phase_state,
                event=event, event_details=details))

        # Replay-only lifecycle pass.  It consumes the frozen signal manifests
        # after signal generation has completed, so PositionManager state can
        # never suppress or alter Strategy A signal discovery.
        one_minute_candles: list[Candle] = []
        if self.hist_svc and hasattr(self.hist_svc, "repo"):
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            one_minute_start = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST).astimezone(timezone.utc)
            one_minute_end = datetime(target_date.year, target_date.month, target_date.day, 15, 30, tzinfo=IST).astimezone(timezone.utc)
            try:
                one_minute_candles = await self.hist_svc.repo.get_candles(
                    request.instrument_id, "1m", start_time=one_minute_start,
                    end_time=one_minute_end, limit=1000,
                )
                allowed = {"BREEZE", "KITE", "LIVE"} if historical_source == HistoricalReplaySource.MIXED else {historical_source.value}
                one_minute_candles = [c for c in one_minute_candles if c.source in allowed]
            except Exception as ex:
                logger.warning("Historical 1m replay query error: %s", ex)

        from services.strategy.replay_lifecycle import (
            HistoricalPositionManagerReplayer,
            build_lifecycle_report,
        )
        lifecycle_replayer = HistoricalPositionManagerReplayer(
            risk_config=self.risk_config,
            session_config=self.session_config,
            recorder=replay_manifest_recorder,
            instrument_id=request.instrument_id,
            warmup_candles=warmup,
            session_candles=session,
            futures_candles=futures_history,
            one_minute_candles=one_minute_candles,
        )
        lifecycle_resolver = lifecycle_replayer.replay(replay_manifest_recorder.records())
        lifecycle_report = build_lifecycle_report(replay_manifest_recorder.records(), lifecycle_resolver)
        lifecycle_report["manifest_validation"] = replay_manifest_recorder.validate_complete(expected_count=len(replay_manifest_recorder.records()))
        replay_lifecycle = lifecycle_report
        return SimulationResult(
            replay_mode="POSITION_MANAGER_REPLAY",
            session_date=date_str, total_bars_evaluated=len(session), total_trades=lifecycle_report["resolved"],
            winning_trades=lifecycle_report["winners"], losing_trades=lifecycle_report["losers"],
            win_rate_pct=lifecycle_report["win_rate_pct"], total_pnl=0, net_pnl=0,
            total_realized_r=lifecycle_report["average_r"] * lifecycle_report["resolved"],
            max_drawdown_pnl=0, profit_factor=0, timeline=timeline, decision_logs=logs,
            replay_trigger_diagnostics=replay_trigger_diagnostics,
            replay_manifests=[record.model_dump(mode="json") for record in replay_manifest_recorder.records()],
            replay_metadata=replay_metadata,
            replay_lifecycle=replay_lifecycle,
        )
