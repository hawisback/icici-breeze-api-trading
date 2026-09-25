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
from libs.market_time import IST
from services.strategy.contract_selector import ContractSelector
from services.strategy.features import FeatureEngine
from services.strategy.futures_signal import (
    FuturesContractResolver,
    aggregate_completed_15m,
    canonical_active_futures_stream,
    contract_expiry,
    resolve_active_futures_instrument,
)
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
        """Discover replay dates with both spot and usable Strategy A futures data."""
        dates: set[str] = set()
        if self.hist_svc and hasattr(self.hist_svc, "repo"):
            try:
                async with self.hist_svc.repo.engine.connect() as conn:
                    if historical_source == HistoricalReplaySource.MIXED:
                        spot_source_clause = "spot.source IN ('BREEZE', 'KITE', 'LIVE')"
                        futures_source_clause = "fut.source IN ('BREEZE', 'KITE', 'LIVE')"
                        params: tuple[Any, ...] = ()
                    else:
                        spot_source_clause = "spot.source = ?"
                        futures_source_clause = "fut.source = ?"
                        params = (historical_source.value, historical_source.value)
                    cursor = await conn.execute(f"""
                        SELECT DISTINCT substr(spot.start_time, 1, 10) AS day
                        FROM historical_candles AS spot
                        WHERE spot.instrument_id = 'INST-NIFTY-INDEX'
                          AND spot.interval = '5m'
                          AND {spot_source_clause}
                          AND (
                              SELECT COUNT(*)
                              FROM historical_candles AS fut
                              WHERE fut.interval = '5m'
                                AND fut.instrument_id LIKE 'INST-NIFTY-FUT-%'
                                AND substr(fut.start_time, 1, 10) = substr(spot.start_time, 1, 10)
                                AND {futures_source_clause}
                          ) >= 3
                        ORDER BY day DESC
                        LIMIT 120;
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

    async def _fetch_cached_futures_universe(
        self,
        date_str: str,
        *,
        historical_source: HistoricalReplaySource,
        source_diagnostics: dict[str, Any],
    ) -> list[Candle]:
        """Load cached NIFTY futures across contract rollovers for Strategy A.

        Strategy A's feature engine resolves the active contract independently
        at each completed timestamp.  Replaying only the contract active on the
        target date discards valid pre-roll warmup bars and can change EMA/ADX,
        pivots, confluence, and ultimately signal discovery immediately after
        expiry.  This helper is cache-only: the normal active-contract fetch
        remains responsible for targeted provider refreshes.
        """
        repo = getattr(self.hist_svc, "repo", None)
        engine = getattr(repo, "engine", None)
        if engine is None:
            return []

        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        session_start_ist = datetime(
            target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST
        )
        session_end_ist = datetime(
            target_date.year, target_date.month, target_date.day, 15, 30, tzinfo=IST
        )
        warmup_start_utc = session_start_ist.astimezone(timezone.utc) - timedelta(days=7)
        fetch_end_utc = min(utc_now(), session_end_ist.astimezone(timezone.utc))

        if historical_source == HistoricalReplaySource.MIXED:
            source_clause = "source IN ('BREEZE', 'KITE', 'LIVE')"
            params: tuple[Any, ...] = (
                warmup_start_utc.isoformat(),
                fetch_end_utc.isoformat(),
            )
        else:
            source_clause = "source = ?"
            params = (
                warmup_start_utc.isoformat(),
                fetch_end_utc.isoformat(),
                historical_source.value,
            )

        async with engine.connect() as conn:
            cursor = await conn.execute(
                f"""
                SELECT instrument_id, interval, start_time, end_time,
                       open, high, low, close, volume, open_interest, source
                FROM historical_candles
                WHERE interval = '5m'
                  AND instrument_id LIKE 'INST-NIFTY-FUT-%'
                  AND start_time >= ?
                  AND start_time <= ?
                  AND {source_clause}
                ORDER BY start_time ASC, instrument_id ASC
                """,
                params,
            )
            rows = await cursor.fetchall()

        candles = [
            Candle(
                instrument_id=row["instrument_id"],
                interval=row["interval"],
                start_time=datetime.fromisoformat(row["start_time"]),
                end_time=datetime.fromisoformat(row["end_time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                open_interest=int(row["open_interest"]),
                source=row["source"],
            )
            for row in rows
        ]
        contracts = sorted({c.instrument_id for c in candles})
        source_diagnostics.setdefault("futures", {})["strategy_a_canonical_history"] = {
            "selected_count": len(candles),
            "contracts": contracts,
            "contract_count": len(contracts),
            "window_start": warmup_start_utc.isoformat(),
            "window_end": fetch_end_utc.isoformat(),
            "cache_only": True,
        }
        return candles

    async def _resolve_replay_futures_instrument(
        self,
        date_str: str,
        *,
        source_diagnostics: dict[str, Any],
        historical_source: HistoricalReplaySource = HistoricalReplaySource.BREEZE,
    ) -> tuple[str | None, list[dict[str, str | None]]]:
        """Resolve the actual nearest futures contract for the replay session.

        Historical storage is authoritative when it already contains futures
        for the requested source/date. The instrument master is the fallback;
        deterministic seeding is used only when neither source can resolve a
        contract.
        """
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        as_of = datetime(target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST)

        historical_repo = getattr(self.hist_svc, "repo", None)
        historical_engine = getattr(historical_repo, "engine", None)
        if historical_engine is not None:
            try:
                async with historical_engine.connect() as conn:
                    if historical_source == HistoricalReplaySource.MIXED:
                        source_clause = "source IN ('BREEZE', 'KITE', 'LIVE')"
                        params: tuple[Any, ...] = (date_str,)
                    else:
                        source_clause = "source = ?"
                        params = (date_str, historical_source.value)
                    cursor = await conn.execute(f"""
                        SELECT DISTINCT instrument_id
                        FROM historical_candles
                        WHERE interval = '5m'
                          AND instrument_id LIKE 'INST-NIFTY-FUT-%'
                          AND substr(start_time, 1, 10) = ?
                          AND {source_clause}
                        ORDER BY instrument_id;
                    """, params)
                    rows = await cursor.fetchall()
                stored_contracts: dict[str, Any] = {}
                for row in rows:
                    instrument_id = str(row[0])
                    expiry = contract_expiry(instrument_id)
                    if expiry is not None:
                        stored_contracts[instrument_id] = expiry
                active_instrument = (
                    FuturesContractResolver.resolve_contracts(stored_contracts, as_of=as_of)
                    if stored_contracts else None
                )
                if active_instrument:
                    expiry = stored_contracts[active_instrument].isoformat()
                    source_diagnostics["futures_contract"] = {
                        "status": "RESOLVED",
                        "instrument_id": active_instrument,
                        "expiry": expiry,
                        "as_of": as_of.isoformat(),
                        "resolution_source": "HISTORICAL_STORAGE",
                    }
                    return active_instrument, [{
                        "instrument_id": active_instrument,
                        "expiry": expiry,
                    }]
            except Exception as exc:
                logger.warning(
                    "Replay futures contract lookup from historical storage failed for %s: %s",
                    date_str,
                    exc,
                )

        inst_svc = getattr(self.hist_svc, "instrument_service", None)
        if not inst_svc:
            source_diagnostics["futures_contract"] = {
                "status": "UNAVAILABLE",
                "reason": "no historical futures contract and instrument service unavailable",
                "as_of": as_of.isoformat(),
            }
            return None, []

        instruments = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
        active_instrument = resolve_active_futures_instrument(instruments, as_of=as_of)
        resolution_source = "INSTRUMENT_MASTER"
        if active_instrument is None:
            ensure = getattr(inst_svc, "ensure_current_nifty_futures", None)
            if callable(ensure):
                await ensure(today=target_date)
                instruments = await inst_svc.repo.search(query="NIFTY", underlying="NIFTY", limit=10000)
                active_instrument = resolve_active_futures_instrument(instruments, as_of=as_of)
                resolution_source = "DETERMINISTIC_FALLBACK"

        contract = next(
            (item for item in instruments if getattr(item, "instrument_id", None) == active_instrument),
            None,
        )
        expiry = getattr(contract, "expiry", None) if contract else None
        source_diagnostics["futures_contract"] = {
            "status": "RESOLVED" if active_instrument else "UNAVAILABLE",
            "instrument_id": active_instrument,
            "expiry": expiry,
            "as_of": as_of.isoformat(),
            "resolution_source": resolution_source if active_instrument else None,
        }
        selected = [{
            "instrument_id": active_instrument,
            "expiry": expiry,
        }] if active_instrument and contract else []
        return active_instrument, selected

    def _strategy_a_config_for_replay(self, _overrides: ThresholdOverrides) -> Any:
        """Return the canonical Strategy A config used by Day Replay.

        The current Strategy A evaluator has no replay-time signal threshold
        override. In particular, adx_threshold is a retained compatibility
        field and is not a hard entry gate.
        """
        return self.tunables

    def _strategy_a_futures_coverage(
        self,
        date_str: str,
        futures_history: list[Candle],
        instrument_id: str | None,
    ) -> dict[str, Any]:
        """Measure completed 15m futures coverage during Strategy A's entry window."""
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_h, start_m = map(int, self.tunables.entry_session_start.split(":"))
        end_h, end_m = map(int, self.tunables.entry_session_end.split(":"))
        first_end = datetime(target.year, target.month, target.day, start_h, start_m, tzinfo=IST)
        last_end = datetime(target.year, target.month, target.day, end_h, end_m, tzinfo=IST)
        expected: list[datetime] = []
        cursor = first_end
        while cursor <= last_end:
            expected.append(cursor)
            cursor += timedelta(minutes=15)

        coverage_as_of = datetime(
            target.year, target.month, target.day, 15, 30, tzinfo=IST
        )
        aggregated_all = aggregate_completed_15m(
            futures_history,
            as_of=coverage_as_of,
        )
        aggregated = canonical_active_futures_stream(
            aggregated_all,
            as_of=coverage_as_of,
            interval="15m",
        )
        available = {
            candle.end_time.astimezone(IST).replace(second=0, microsecond=0)
            for candle in aggregated
            if candle.end_time.astimezone(IST).date() == target
        }
        missing = [value for value in expected if value not in available]
        return {
            "expected_15m_bars": len(expected),
            "available_15m_bars": len(expected) - len(missing),
            "coverage_pct": round((len(expected) - len(missing)) / len(expected) * 100, 2) if expected else 100.0,
            "missing_15m_bar_ends_ist": [value.isoformat() for value in missing],
        }

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
            historical_source=historical_source,
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
        strategy_a_futures_history = list(futures_history)
        cached_futures_universe = await self._fetch_cached_futures_universe(
            date_str,
            historical_source=historical_source,
            source_diagnostics=source_diagnostics,
        )
        if cached_futures_universe:
            merged = {
                (c.instrument_id, c.start_time): c
                for c in cached_futures_universe + futures_history
            }
            strategy_a_futures_history = sorted(
                merged.values(),
                key=lambda candle: (candle.start_time, candle.instrument_id),
            )
            contracts = {
                candle.instrument_id: contract_expiry(candle.instrument_id)
                for candle in strategy_a_futures_history
                if "NIFTY-FUT-" in candle.instrument_id.upper()
            }
            selected_contracts = [
                {
                    "instrument_id": instrument_id,
                    "expiry": expiry.isoformat() if expiry else None,
                }
                for instrument_id, expiry in sorted(
                    contracts.items(),
                    key=lambda item: (
                        item[1] is None,
                        item[1] or datetime.max.date(),
                        item[0],
                    ),
                )
            ]
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

        override_values = overrides.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )
        strategy_b_replay_overrides = {
            "rvol_threshold",
            "strat_b_min_confirmation",
            "strat_b_min_available_confirmations",
            "box_max_height_atr",
            "bb_width_percentile",
            "strat_b_box_max_age_bars",
            "strat_b_breakout_buffer_atr",
            "breakout_buffer_atr",
            "strat_b_max_extension_atr",
        }
        applied_overrides = {
            name: value
            for name, value in override_values.items()
            if name in strategy_b_replay_overrides
        }
        if bypass_entry_window:
            applied_overrides["bypass_entry_window"] = True

        def replay_override_reason(name: str) -> str:
            if name == "adx_threshold":
                return (
                    "Strategy A uses momentum-health gates (ADX change and EMA20 slope), "
                    "not a hard ADX floor."
                )
            if "premium" in name:
                return (
                    "Historical option reconstruction does not apply production "
                    "premium-based contract selection."
                )
            return "Current A/B Day Replay does not consume this override."

        not_applied_overrides = {
            name: {
                "value": value,
                "reason": replay_override_reason(name),
            }
            for name, value in override_values.items()
            if name not in strategy_b_replay_overrides
            and name != "bypass_entry_window"
        }
        cfg = self.tunables
        strategy_a_cfg = self._strategy_a_config_for_replay(overrides)
        strat_a = TrendPullbackStrategy(config=strategy_a_cfg, allow_session_bypass=True)
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
        if source_diagnostics["futures"]["missing_selected_source"] or not strategy_a_futures_history:
            missing_data.append("futures")
        futures_coverage = self._strategy_a_futures_coverage(
            date_str,
            strategy_a_futures_history,
            active_instrument,
        )
        source_diagnostics["futures"]["strategy_a_entry_window_coverage"] = futures_coverage
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
            futures_candles=strategy_a_futures_history,
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
            "control_application": {
                "applied_overrides": applied_overrides,
                "not_applied_overrides": not_applied_overrides,
                "not_applied_request_controls": {
                    "capital": {
                        "value": request.capital,
                        "reason": (
                            "Historical position-sizing parity is not implemented; "
                            "resolved option mark P&L currently uses one reconstructed lot."
                        ),
                    },
                    "max_trades_per_day": {
                        "value": request.max_trades_per_day,
                        "reason": (
                            "Signal-first Day Replay does not enforce chronological "
                            "portfolio/day trade gates yet."
                        ),
                    },
                },
            },
        }
        timeline, logs = [], []
        replay_trigger_diagnostics: list[dict[str, Any]] = []
        replay_diagnostic_keys: set[tuple[str, str]] = set()
        strategy_a_event_keys: set[tuple[str, str, str | None]] = set()
        strategy_a_event_counts: Counter[str] = Counter()
        replay_manifest_recorder = self.replay_manifest_recorder or ReplayManifestRecorder()
        replay_manifest_recorder.set_replay_metadata(replay_metadata)
        running = list(warmup)
        for idx, bar in enumerate(session):
            running.append(bar)
            macro = self.resample_to_15m(running, request.instrument_id)
            futures = [c for c in futures_history if c.end_time <= bar.end_time]
            strategy_a_futures = [
                c for c in strategy_a_futures_history if c.end_time <= bar.end_time
            ]
            features = FeatureEngine.compute_all_features(running, macro, futures,
                                                          spot_price=bar.close, as_of=bar.end_time)
            diags_a = strat_a.diagnose(
                features,
                running,
                macro,
                overrides=overrides,
                futures_candles=strategy_a_futures,
            )
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
            strategy_a_event = None
            if (strategy_a_futures and in_window) or (features.data_ready or features.breakout_data_ready) and in_window:
                if cfg.trend_pullback_enabled and strategy_a_futures:
                    # Strategy A replay calls the exact production state
                    # machine over the canonical multi-contract futures stream.
                    # There is no replay-only trigger evaluator.
                    sig_a = strat_a.evaluate(
                        features,
                        running,
                        macro,
                        strategy_a_futures,
                        overrides,
                    )
                    strategy_a_event = strat_a.last_event
                    effective_diags_a = strat_a.diagnose(
                        features,
                        running,
                        macro,
                        overrides=overrides,
                        futures_candles=strategy_a_futures,
                    )
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
                            futures_candles=strategy_a_futures,
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
            strategy_a_summary_window = bypass_entry_window or a_window
            if strategy_a_summary_window:
                if strategy_a_event is not None and strategy_a_event.event != "DUPLICATE_IGNORED":
                    event_key = (
                        strategy_a_event.event,
                        strategy_a_event.timestamp.isoformat(),
                        strategy_a_event.reason,
                    )
                    if event_key not in strategy_a_event_keys:
                        strategy_a_event_keys.add(event_key)
                        strategy_a_event_counts[strategy_a_event.event] += 1
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
                        "strategy_a_contract": (diag.phase_summary or {}).get("strategy_a_contract", {}),
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
        data_quality_reasons = {"STALE_FUTURES_DATA", "FUTURES_DATA_UNAVAILABLE", "INCOMPLETE_FUTURES_DATA"}
        blocker_counts = Counter(
            item["key_blocker"]
            for item in replay_trigger_diagnostics
            if item.get("key_blocker")
            and item["key_blocker"] not in data_quality_reasons
            and item["key_blocker"] != "READY"
        )
        data_quality_counts = Counter(
            item["key_blocker"]
            for item in replay_trigger_diagnostics
            if item.get("key_blocker") in data_quality_reasons
        )
        ready_count = sum(item.get("key_blocker") == "READY" for item in replay_trigger_diagnostics)

        gate_funnel: dict[str, dict[str, float | int]] = {}
        for gate_id in ("trend", "confirmation", "confluence", "risk"):
            evaluated = 0
            passed_gate = 0
            for item in replay_trigger_diagnostics:
                condition = next(
                    (condition for condition in item.get("conditions", []) if condition.get("id") == gate_id),
                    None,
                )
                if condition is None:
                    continue
                if gate_id == "risk" and condition.get("gap_description") == "WAITING_FOR_SETUP_PREREQUISITES":
                    continue
                evaluated += 1
                if condition.get("status") == "PASSED":
                    passed_gate += 1
            gate_funnel[gate_id] = {
                "evaluated": evaluated,
                "passed": passed_gate,
                "pass_pct": round(passed_gate / evaluated * 100, 2) if evaluated else 0.0,
            }

        component_funnel: dict[str, dict[str, float | int]] = {}
        for item in replay_trigger_diagnostics:
            payload = item.get("strategy_a_contract") or {}
            for section in ("trend", "confirmation", "confluence"):
                for name, value in (payload.get(section, {}).get("components") or {}).items():
                    key = f"{section}.{name}"
                    row = component_funnel.setdefault(
                        key, {"evaluated": 0, "passed": 0, "pass_pct": 0.0}
                    )
                    row["evaluated"] = int(row["evaluated"]) + 1
                    if bool(value):
                        row["passed"] = int(row["passed"]) + 1
        for row in component_funnel.values():
            evaluated = int(row["evaluated"])
            passed_component = int(row["passed"])
            row["pass_pct"] = round(passed_component / evaluated * 100, 2) if evaluated else 0.0

        completed_bar_checks = len({
            item["completed_futures_candle"]
            for item in replay_trigger_diagnostics
            if item.get("completed_futures_candle")
        })
        strategy_a_records = [
            record for record in replay_manifest_recorder.records()
            if record.strategy_id == StrategyName.TREND_PULLBACK.value
        ]
        strategy_a_resolved = sum(
            record.lifecycle_status == "RESOLVED" and record.realized_r is not None
            for record in strategy_a_records
        )
        strategy_a_unresolved = sum(record.lifecycle_status == "UNRESOLVED" for record in strategy_a_records)
        strategy_a_ambiguous = sum(record.lifecycle_status == "AMBIGUOUS" for record in strategy_a_records)
        replay_metadata["strategy_a_replay_diagnostics"] = {
            "directional_evaluations": len(replay_trigger_diagnostics),
            "completed_bar_checks": completed_bar_checks,
            "ready_direction_checks": ready_count,
            "blocker_counts": dict(blocker_counts.most_common()),
            "data_quality_counts": dict(data_quality_counts.most_common()),
            "event_counts": dict(strategy_a_event_counts),
            "setup_count": int(strategy_a_event_counts.get("SETUP_CREATED", 0)),
            "signal_count": len(strategy_a_records),
            "resolved_trade_count": strategy_a_resolved,
            "unresolved_trade_count": strategy_a_unresolved,
            "ambiguous_trade_count": strategy_a_ambiguous,
            "futures_entry_window_coverage": futures_coverage,
            "gate_funnel": gate_funnel,
            "component_funnel": component_funnel,
        }
        if not replay_manifest_recorder.records():
            top = ", ".join(f"{name}={count}" for name, count in blocker_counts.most_common(5))
            quality = ", ".join(f"{name}={count}" for name, count in data_quality_counts.most_common())
            detail_parts = []
            if top:
                detail_parts.append(f"Top Strategy A market-condition blockers: {top}.")
            if quality:
                detail_parts.append(f"Historical futures data-quality issues: {quality}.")
            if strategy_a_event_counts.get("SETUP_CREATED", 0):
                detail_parts.append(
                    f"Strategy A created {strategy_a_event_counts['SETUP_CREATED']} setup(s), but no trigger signal qualified."
                )
            limitation = (
                "No strategy signals qualified on this replay session. "
                + (" ".join(detail_parts) if detail_parts else "See replay diagnostics for the evaluated conditions.")
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
            total_realized_r=lifecycle_report["total_r"],
            max_drawdown_pnl=None,
            profit_factor=lifecycle_report["profit_factor"],
            max_drawdown_r=lifecycle_report["max_drawdown_r"],
            trades=trades, timeline=timeline, decision_logs=logs,
            replay_trigger_diagnostics=replay_trigger_diagnostics,
            replay_manifests=[record.model_dump(mode="json") for record in replay_manifest_recorder.records()],
            replay_metadata=replay_metadata,
            replay_lifecycle=replay_lifecycle,
        )
