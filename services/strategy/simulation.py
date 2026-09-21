"""Simulation & Day-Replay Engine for NIFTY Intraday Options Auto-Trading.
Enables full-session backtesting and walk-forward replay against historical candles.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any, Optional

from libs.contracts.models import Candle, utc_now
from services.strategy.contract_selector import ContractSelector
from services.strategy.features import FeatureEngine
from services.strategy.futures_signal import resolve_active_futures_instrument
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


def _record_strategy_b_manifest(
    recorder: ReplayManifestRecorder,
    signal: Any,
    *,
    trading_date: str,
    breakout_candle: Candle,
) -> None:
    """Record one completed-candle Strategy B signal for lifecycle replay."""
    if signal.strategy != StrategyName.VOLATILITY_BREAKOUT:
        raise ValueError("Strategy B manifest helper received a non-Strategy-B signal")
    if signal.timestamp != breakout_candle.end_time:
        raise ValueError("Strategy B replay entry must use the completed breakout candle end time")

    snapshot = signal.features_snapshot
    required = ("box_high", "box_low", "atr_at_lock", "breakout_trigger_price")
    missing = [key for key in required if snapshot.get(key) is None]
    if missing:
        raise ValueError(
            f"Strategy B signal {signal.signal_id} is missing replay state: {', '.join(missing)}"
        )

    box_high = float(snapshot["box_high"])
    box_low = float(snapshot["box_low"])
    atr_at_lock = float(snapshot["atr_at_lock"])
    if atr_at_lock <= 0 or box_high <= box_low:
        raise ValueError(f"Invalid Strategy B replay state for {signal.signal_id}")

    recorder.record_entry(
        signal=signal,
        trading_date=trading_date,
        trigger_source_candle_timestamp=signal.timestamp,
        trigger_level=float(snapshot["breakout_trigger_price"]),
        simulated_entry_timestamp=signal.timestamp,
        simulated_entry_price=float(signal.spot_reference_price),
        entry_5m_candle_timestamp=breakout_candle.start_time,
        entry_occurred_intrabar=False,
        entry_features={
            **snapshot,
            "entry_reference_spot": float(signal.spot_reference_price),
            "entry_bar_timestamp": breakout_candle.start_time.isoformat(),
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
        entry_bar_timestamp=breakout_candle.start_time,
        last_managed_completed_bar_timestamp=None,
    )


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

    async def _fetch_replay_option_candles(
        self,
        records: list[Any],
        date_str: str,
        session_start: datetime,
        session_end: datetime,
        historical_source: HistoricalReplaySource,
    ) -> tuple[list[Any], dict[str, list[Candle]], dict[str, Any]]:
        """Resolve replay contracts and load their real 1-minute OHLC candles."""
        inst_svc = getattr(self.hist_svc, "instrument_service", None)
        if not inst_svc or not records:
            return [], {}, {"status": "UNAVAILABLE", "reason": "instrument service unavailable", "contracts": 0}

        try:
            instruments = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
        except Exception as exc:
            logger.warning("Historical option contract lookup failed: %s", exc)
            return [], {}, {"status": "UNAVAILABLE", "reason": "contract lookup failed", "contracts": 0}

        option_instruments = [
            instrument for instrument in instruments
            if str(getattr(instrument, "segment", "")).upper() == "OPTIONS"
            and getattr(instrument, "expiry", None)
            and str(instrument.expiry) >= date_str
            and getattr(instrument, "strike", None) is not None
            and getattr(instrument, "option_right", None)
        ]
        selected: dict[str, Any] = {}
        for record in records:
            direction = "CALL" if record.direction == "CALL" else "PUT"
            candidates = [
                instrument for instrument in option_instruments
                if str(getattr(getattr(instrument, "option_right", None), "value", "")).upper() in {direction, "CE" if direction == "CALL" else "PE"}
            ]
            if not candidates:
                continue
            expiry = min(str(instrument.expiry) for instrument in candidates)
            same_expiry = [instrument for instrument in candidates if str(instrument.expiry) == expiry]
            contract = min(same_expiry, key=lambda instrument: abs(float(instrument.strike) - record.simulated_entry_price))
            selected[contract.instrument_id] = contract

        candles_by_instrument: dict[str, list[Candle]] = {}
        allowed_sources = {"BREEZE", "KITE", "LIVE"} if historical_source == HistoricalReplaySource.MIXED else {historical_source.value}
        fetched_count = 0
        for instrument_id, contract in selected.items():
            candles: list[Candle] = []
            if hasattr(self.hist_svc, "repo"):
                try:
                    candles = await self.hist_svc.repo.get_candles(
                        instrument_id, "1m", start_time=session_start, end_time=session_end, limit=1000,
                    )
                    candles = [c for c in candles if c.source in allowed_sources]
                except Exception as exc:
                    logger.warning("Historical option cache query failed for %s: %s", instrument_id, exc)

            if not candles and hasattr(self.hist_svc, "fetch_candles_from_breeze_window") and historical_source in (HistoricalReplaySource.BREEZE, HistoricalReplaySource.MIXED):
                try:
                    candles = await self.hist_svc.fetch_candles_from_breeze_window(
                        instrument_id,
                        interval="1m",
                        start_time=session_start,
                        end_time=session_end,
                    )
                    fetched_count += len(candles)
                except Exception as exc:
                    logger.warning("Historical option fetch failed for %s: %s", instrument_id, exc)
            candles_by_instrument[instrument_id] = sorted(
                [c for c in candles if c.source in allowed_sources],
                key=lambda candle: candle.start_time,
            )

        available = sum(bool(candles) for candles in candles_by_instrument.values())
        return list(selected.values()), candles_by_instrument, {
            "status": "AVAILABLE" if available else "UNAVAILABLE",
            "contracts": len(selected),
            "contracts_with_candles": available,
            "candle_count": sum(len(candles) for candles in candles_by_instrument.values()),
            "fetched_candle_count": fetched_count,
            "price_basis": "BREEZE_HISTORICAL_OHLC_CLOSE" if available else None,
            "mark_policy": "latest_completed_candle_close_at_event",
            "pricing_field": "completed_candle_close",
            "bid_ask_available": False,
            "executable_fill_equivalent": False,
            "selection": "nearest_strike_first_expiry_on_or_after_replay_date",
        }

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
                            raw_date = str(r[0])
                            try:
                                parsed_date = datetime.strptime(raw_date, "%Y-%m-%d")
                            except ValueError:
                                logger.warning("Skipping malformed historical session date: %r", raw_date)
                                continue
                            dates.add(parsed_date.strftime("%Y-%m-%d"))
            except Exception:
                logger.exception("Failed to query historical dates")
                raise

        return sorted(dates, reverse=True)

    async def _fetch_session_candles(
        self,
        date_str: str,
        instrument_id: str,
        *,
        historical_source: HistoricalReplaySource,
        source_diagnostics: dict[str, Any],
        role: str,
    ) -> tuple[list[Candle], list[Candle]]:
        """Load a deterministic replay window for one instrument.

        Cache is read directly first, then the broker is asked for the exact
        requested historical window.  This avoids the previous bug where a
        replay for an old date refreshed only "the last 7 days from now".
        """
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        session_start_ist = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST)
        session_end_ist = datetime(target_date.year, target_date.month, target_date.day, 15, 30, tzinfo=IST)
        session_start_utc = session_start_ist.astimezone(timezone.utc)
        session_end_utc = session_end_ist.astimezone(timezone.utc)
        warmup_start_utc = session_start_utc - timedelta(days=7)
        fetch_end_utc = min(utc_now(), session_end_utc)

        all_candles: list[Candle] = []
        fetched_count = 0
        if self.hist_svc and hasattr(self.hist_svc, "repo"):
            try:
                cached = await self.hist_svc.repo.get_candles(
                    instrument_id,
                    "5m",
                    start_time=warmup_start_utc,
                    end_time=session_end_utc,
                    limit=2000,
                )
                all_candles.extend(cached)
            except Exception as exc:
                logger.warning("Replay cache query failed for %s: %s", instrument_id, exc)

            targeted_fetch = getattr(self.hist_svc, "fetch_candles_from_provider_window", None)
            if callable(targeted_fetch) and fetch_end_utc >= warmup_start_utc:
                try:
                    fetched = await targeted_fetch(
                        instrument_id,
                        interval="5m",
                        start_time=warmup_start_utc,
                        end_time=fetch_end_utc,
                        requested_source=historical_source.value,
                    )
                    fetched_count = len(fetched)
                    all_candles.extend(fetched)
                except Exception as exc:
                    logger.warning(
                        "Targeted replay historical fetch failed for %s on %s: %s",
                        instrument_id, date_str, exc,
                    )
        elif self.hist_svc:
            # Compatibility path for isolated test doubles without a repository.
            try:
                candles = await self.hist_svc.get_candles(
                    instrument_id=instrument_id,
                    interval="5m",
                    start_time=warmup_start_utc,
                    end_time=session_end_utc,
                    limit=2000,
                    requested_source=historical_source.value,
                    allow_provider_fallback=False,
                    allow_synthetic_fallback=False,
                )
                all_candles.extend(candles)
            except Exception as exc:
                logger.warning("Historical service query error: %s", exc)

        # De-duplicate cached/fetched copies by source and start time.
        deduped = {
            (c.source, c.start_time): c
            for c in all_candles
            if c.source in {"BREEZE", "KITE", "LIVE"} and c.end_time <= fetch_end_utc
        }
        all_candles = sorted(deduped.values(), key=lambda c: c.start_time)

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
            "window_start": warmup_start_utc.isoformat(),
            "window_end": fetch_end_utc.isoformat(),
            "targeted_fetch_count": fetched_count,
            "available_before_filter": dict(sorted(available_counts.items())),
            "selected_after_filter": dict(sorted(selected_counts.items())),
            "selected_count": len(selected_candles),
            "missing_selected_source": len(selected_candles) == 0,
        }

        warmup_candles: list[Candle] = []
        session_candles: list[Candle] = []
        for candle in selected_candles:
            candle_ist = candle.start_time.astimezone(IST)
            if candle_ist.date() < target_date or (
                candle_ist.date() == target_date and candle_ist.time() < session_start_ist.time()
            ):
                warmup_candles.append(candle)
            elif candle_ist.date() == target_date and session_start_ist.time() <= candle_ist.time() < session_end_ist.time():
                session_candles.append(candle)
        return warmup_candles, session_candles

    async def _resolve_replay_futures_instrument(
        self,
        date_str: str,
        *,
        source_diagnostics: dict[str, Any],
    ) -> tuple[str | None, list[dict[str, str | None]]]:
        """Resolve the nearest futures contract as it existed on replay date."""
        inst_svc = getattr(self.hist_svc, "instrument_service", None)
        if not inst_svc:
            source_diagnostics["futures_contract"] = {"status": "UNAVAILABLE", "reason": "instrument service unavailable"}
            return None, []

        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        ensure = getattr(inst_svc, "ensure_current_nifty_futures", None)
        if callable(ensure):
            # Seed metadata for the replay date, not today's date. This is
            # essential when replaying a prior monthly contract with Breeze.
            await ensure(today=target_date)
        instruments = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
        as_of = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST)
        active_instrument = resolve_active_futures_instrument(instruments, as_of=as_of)
        contract = next(
            (item for item in instruments if getattr(item, "instrument_id", None) == active_instrument),
            None,
        )
        source_diagnostics["futures_contract"] = {
            "status": "RESOLVED" if active_instrument else "UNAVAILABLE",
            "instrument_id": active_instrument,
            "expiry": getattr(contract, "expiry", None) if contract else None,
            "as_of": as_of.isoformat(),
        }
        selected = [{
            "instrument_id": active_instrument,
            "expiry": getattr(contract, "expiry", None),
        }] if active_instrument and contract else []
        return active_instrument, selected

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
        futures_history: list[Candle] = []
        active_instrument, selected_contracts = await self._resolve_replay_futures_instrument(
            date_str,
            source_diagnostics=source_diagnostics,
        )
        if active_instrument:
            warm_fut, day_fut = await self._fetch_session_candles(
                date_str,
                active_instrument,
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
        strat_a = TrendPullbackStrategy(config=cfg, allow_session_bypass=True)
        strat_b = VolatilityBreakoutStrategy(rvol_threshold=cfg.rvol_threshold, adx_threshold=cfg.strategy_b_adx_threshold,
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
        replay_trigger_diagnostics: list[dict[str, Any]] = []
        replay_diagnostic_keys: set[tuple[str, str]] = set()
        replay_manifest_recorder = self.replay_manifest_recorder or ReplayManifestRecorder()
        replay_manifest_recorder.set_replay_metadata(replay_metadata)
        running = list(warmup)
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
            a_start_h, a_start_m = map(int, self.tunables.entry_session_start.split(":"))
            a_end_h, a_end_m = map(int, self.tunables.entry_session_end.split(":"))
            b_start_h, b_start_m = map(int, self.session_config.no_new_trade_before.split(":"))
            b_end_h, b_end_m = map(int, self.session_config.no_new_trade_after.split(":"))
            a_window = a_start_h * 60 + a_start_m <= minutes <= a_end_h * 60 + a_end_m
            b_window = b_start_h * 60 + b_start_m <= minutes <= b_end_h * 60 + b_end_m
            in_window = bypass_entry_window or (
                (self.tunables.trend_pullback_enabled and a_window)
                or (self.tunables.volatility_breakout_enabled and b_window)
            )
            event, details = None, None
            effective_diags_a = diags_a
            sig_a = None
            if (futures and in_window) or (features.data_ready or features.breakout_data_ready) and in_window:
                if cfg.trend_pullback_enabled and futures:
                    # Strategy A replay calls the exact production state
                    # machine.  There is no replay-only trigger evaluator.
                    sig_a = strat_a.evaluate(features, running, macro, futures, overrides)
                    effective_diags_a = strat_a.diagnose(features, running, macro, overrides=overrides, futures_candles=futures)
                    if sig_a is not None:
                        snapshot = sig_a.features_snapshot
                        replay_manifest_recorder.record_entry(
                            signal=sig_a, trading_date=date_str,
                            trigger_source_candle_timestamp=bar.end_time,
                            trigger_level=float(snapshot.get("trigger", sig_a.underlying_entry_price or sig_a.spot_reference_price)),
                            simulated_entry_timestamp=bar.end_time,
                            simulated_entry_price=float(snapshot.get("entry_price", sig_a.underlying_entry_price or sig_a.spot_reference_price)),
                            entry_5m_candle_timestamp=bar.end_time,
                            entry_occurred_intrabar=False, entry_features=snapshot,
                            setup_id=sig_a.signal_id,
                            pullback_swing_low=None, pullback_swing_high=None,
                            impulse_low=None, impulse_high=None,
                            atr_at_entry=float(snapshot.get("atr14", 0.0)),
                            initial_structural_stop=float(sig_a.structural_stop),
                            initial_risk_points=float(sig_a.r_points),
                            initial_risk_atr=(float(sig_a.r_points) / float(snapshot.get("atr14", 1.0))) if snapshot.get("atr14") else 0.0,
                            current_trailing_stop=float(sig_a.structural_stop), current_r=0.0,
                            highest_favorable_price=float(sig_a.underlying_entry_price or sig_a.spot_reference_price),
                            lowest_favorable_price=float(sig_a.underlying_entry_price or sig_a.spot_reference_price),
                            peak_r=0.0, protected_breakeven_active=False, profit_lock_active=False,
                            runner_mode_active=False, current_ladder_stage="OPEN_INITIAL_RISK", reversal_score=0,
                            adverse_health_counters={}, entry_bar_timestamp=bar.end_time,
                            last_managed_completed_bar_timestamp=None,
                        )
                        strat_a.confirm_entry(sig_a.timestamp)
                        effective_diags_a = strat_a.diagnose(
                            features,
                            running,
                            macro,
                            overrides=overrides,
                            futures_candles=futures,
                        )
                sig_b = strat_b.evaluate(features, running, overrides=overrides) if cfg.volatility_breakout_enabled else None
                if sig_b is not None:
                    _record_strategy_b_manifest(
                        replay_manifest_recorder,
                        sig_b,
                        trading_date=date_str,
                        breakout_candle=bar,
                    )
                signal = sig_a or sig_b
                if signal:
                    event, details = "SIGNAL_ONLY", "Qualified signal; historical executable option quotes unavailable"
                    logs.append(DecisionLogEntry(id=f"SIM-{idx}", timestamp=bar.end_time, category="SETUP",
                                                 strategy=signal.strategy.value, message=details,
                                                 details=signal.model_dump(mode="json")))
            else:
                strat_a.reset(bar.end_time)
                strat_b.reset(bar.end_time)
                details = features.data_reason if not features.data_ready else "Outside entry window"
            for diag in effective_diags_a:
                completed_ts = str((diag.phase_summary or {}).get("completed_candle_timestamp") or bar.end_time.isoformat())
                key = (diag.direction.value, completed_ts)
                if key in replay_diagnostic_keys:
                    continue
                replay_diagnostic_keys.add(key)
                replay_trigger_diagnostics.append({
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
                    "conditions": [item.model_dump(mode="json") for item in diag.conditions],
                })
            timeline.append(SimulationBarSnapshot(
                bar_index=idx, timestamp=bar.end_time.isoformat(), ist_time=clock.strftime("%H:%M"),
                open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume, spot=bar.close,
                ema9_5m=features.ema9_5m, ema20_5m=features.ema20_5m,
                supertrend=features.supertrend_direction, adx_15m=features.adx_15m,
                rvol_5m=features.rvol_5m, bb_width_percentile=features.bb_width_percentile,
                strategy_a_phase=(max(effective_diags_a, key=lambda d: d.passed_count).phase_state if effective_diags_a else strat_a.snapshot.state.value),
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
            attach_historical_option_prices,
            build_lifecycle_report,
            build_simulated_trade_records,
            summarize_simulated_pnl,
        )
        lifecycle_replayer = HistoricalPositionManagerReplayer(
            risk_config=self.risk_config,
            session_config=self.session_config,
            strategy_config=self.tunables,
            recorder=replay_manifest_recorder,
            instrument_id=request.instrument_id,
            warmup_candles=warmup,
            session_candles=session,
            futures_candles=futures_history,
            one_minute_candles=one_minute_candles,
        )
        lifecycle_resolver = lifecycle_replayer.replay(replay_manifest_recorder.records())
        lifecycle_report = build_lifecycle_report(replay_manifest_recorder.records(), lifecycle_resolver)
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        option_session_start = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST).astimezone(timezone.utc)
        option_session_end = datetime(target_date.year, target_date.month, target_date.day, 15, 30, tzinfo=IST).astimezone(timezone.utc)
        option_contracts, option_candles, option_data = await self._fetch_replay_option_candles(
            replay_manifest_recorder.records(),
            date_str,
            option_session_start,
            option_session_end,
            historical_source,
        )
        attach_historical_option_prices(
            replay_manifest_recorder.records(),
            option_contracts,
            option_candles,
            self.risk_config,
        )
        replay_metadata["historical_option_data"] = option_data
        trades = build_simulated_trade_records(replay_manifest_recorder.records())
        total_pnl, net_pnl = summarize_simulated_pnl(trades)
        resolved_records = [
            record for record in replay_manifest_recorder.records()
            if record.lifecycle_status == "RESOLVED" and record.realized_r is not None
        ]
        option_complete = bool(resolved_records) and all(
            record.option_data_status == "AVAILABLE" for record in resolved_records
        )
        blocker_counts = Counter(
            item["key_blocker"] for item in replay_trigger_diagnostics if item.get("key_blocker")
        )
        replay_metadata["strategy_a_replay_diagnostics"] = {
            "evaluations": len(replay_trigger_diagnostics),
            "blocker_counts": dict(blocker_counts.most_common()),
            "signal_count": len(replay_manifest_recorder.records()),
            "resolved_trade_count": lifecycle_report["resolved"],
            "unresolved_trade_count": lifecycle_report["unresolved"],
            "ambiguous_trade_count": lifecycle_report["ambiguous"],
        }
        if not replay_manifest_recorder.records():
            top = ", ".join(f"{name}={count}" for name, count in blocker_counts.most_common(5))
            limitation = (
                "No strategy signals qualified on this replay session. "
                + (f"Top Strategy A blockers: {top}." if top else "See replay data diagnostics for missing market inputs.")
            )
        elif lifecycle_report["resolved"] == 0:
            limitation = (
                f"{len(replay_manifest_recorder.records())} signal(s) were identified, but no lifecycle "
                "could be resolved from the available post-entry historical bars."
            )
        elif option_complete:
            limitation = (
                "Real historical option OHLC completed-candle close marks used for entry/exit PNL; "
                "these are not executable fills, historical bid/ask and point-in-time option-chain "
                "selection are unavailable."
            )
        else:
            limitation = (
                "Real completed spot/futures candles used. Historical completed option candles were "
                "unavailable for one or more resolved trades."
            )
        lifecycle_report["manifest_validation"] = replay_manifest_recorder.validate_complete(expected_count=len(replay_manifest_recorder.records()))
        replay_lifecycle = lifecycle_report
        return SimulationResult(
            replay_mode="POSITION_MANAGER_REPLAY",
            limitation=limitation,
            session_date=date_str, total_bars_evaluated=len(session), total_trades=lifecycle_report["resolved"],
            winning_trades=lifecycle_report["winners"], losing_trades=lifecycle_report["losers"],
            win_rate_pct=lifecycle_report["win_rate_pct"], total_pnl=total_pnl, net_pnl=net_pnl,
            total_realized_r=lifecycle_report["average_r"] * lifecycle_report["resolved"],
            max_drawdown_pnl=None, profit_factor=0, trades=trades, timeline=timeline, decision_logs=logs,
            replay_trigger_diagnostics=replay_trigger_diagnostics,
            replay_manifests=[record.model_dump(mode="json") for record in replay_manifest_recorder.records()],
            replay_metadata=replay_metadata,
            replay_lifecycle=replay_lifecycle,
        )
