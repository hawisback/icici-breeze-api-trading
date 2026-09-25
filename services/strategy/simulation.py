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
    HistoricalReplayMode,
    HistoricalReplaySource,
    MarketFeatures,
    OptionSelectionConfig,
    OptionType,
    RiskConfig,
    ReplayDataQuality,
    ReplayOptionMarkMetrics,
    ReplayPortfolioMetrics,
    ReplaySignalMetrics,
    ReplayUnderlyingLifecycleMetrics,
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
from services.strategy.replay_contract_selection import (
    HistoricalContractSelectionProvider,
    ReplayContractSelectionDecision,
)
from services.strategy.replay_execution import ChronologicalReplayExecutor
from services.strategy.replay_execution_model import (
    estimate_round_trip_execution,
)
from services.strategy.replay_lifecycle import (
    HistoricalPositionManagerReplayer,
    attach_historical_option_prices,
    build_lifecycle_report,
    _historical_close_at,
    build_simulated_trade_records,
    summarize_historical_option_marks,
)
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.replay_registry import (
    ReplayBarContext,
    ReplaySessionContext,
    ReplayStrategyRegistry,
)
from services.strategy.replay_sizing import (
    ReplaySizingDecision,
    calculate_replay_sizing,
)

logger = logging.getLogger(__name__)

class SimulationEngine:
    """Replays historical 5m candles bar-by-bar to simulate intraday trading."""

    def __init__(
        self,
        historical_service: Optional[Any] = None,
        risk_config: Optional[RiskConfig] = None,
        session_config: Optional[SessionTimersConfig] = None,
        tunables=None,
        option_selection_config: Optional[OptionSelectionConfig] = None,
        strategy_repository: Optional[Any] = None,
        replay_manifest_recorder: Optional[ReplayManifestRecorder] = None,
    ) -> None:
        self.hist_svc = historical_service
        self.strategy_repo = strategy_repository
        self.risk_config = risk_config or RiskConfig()
        self.session_config = session_config or SessionTimersConfig()
        self.option_selection_config = (
            option_selection_config or OptionSelectionConfig()
        )
        from services.strategy.models import StrategyTunablesConfig
        self.tunables = tunables or StrategyTunablesConfig()
        self.replay_manifest_recorder = replay_manifest_recorder

    async def _load_replay_option_universe(
        self,
        date_str: str,
    ) -> list[Any]:
        """Load option contract metadata available for replay approximation."""
        inst_svc = getattr(self.hist_svc, "instrument_service", None)
        if not inst_svc:
            return []
        try:
            instruments = await inst_svc.repo.search(
                query="NIFTY",
                underlying="NIFTY",
                limit=10000,
            )
        except Exception as exc:
            logger.warning("Historical option universe lookup failed: %s", exc)
            return []
        return [
            instrument
            for instrument in instruments
            if str(getattr(instrument, "segment", "")).upper() == "OPTIONS"
            and getattr(instrument, "expiry", None)
            and str(instrument.expiry) >= date_str
            and getattr(instrument, "strike", None) is not None
            and getattr(instrument, "option_right", None)
            and int(getattr(instrument, "lot_size", 0) or 0) > 0
        ]

    @staticmethod
    def _select_replay_sizing_contract(
        signal: Any,
        *,
        date_str: str,
        option_universe: list[Any],
    ) -> Any | None:
        direction = "CALL" if signal.direction == TradeDirection.BULLISH else "PUT"
        rights = {direction, "CE" if direction == "CALL" else "PE"}
        candidates = [
            instrument
            for instrument in option_universe
            if str(
                getattr(
                    getattr(instrument, "option_right", None),
                    "value",
                    getattr(instrument, "option_right", None),
                )
            ).upper()
            in rights
            and str(getattr(instrument, "expiry", "")) >= date_str
        ]
        if not candidates:
            return None
        expiry = min(str(instrument.expiry) for instrument in candidates)
        candidates = [
            instrument
            for instrument in candidates
            if str(instrument.expiry) == expiry
        ]
        underlying_entry = float(
            signal.underlying_entry_price or signal.spot_reference_price
        )
        return min(
            candidates,
            key=lambda instrument: abs(
                float(instrument.strike) - underlying_entry
            ),
        )

    async def _resolve_replay_sizing(
        self,
        *,
        signal: Any,
        date_str: str,
        historical_source: HistoricalReplaySource,
        option_universe: list[Any],
        option_candle_cache: dict[str, list[Candle]],
        effective_risk_config: RiskConfig,
    ) -> tuple[ReplaySizingDecision | None, str | None]:
        """Resolve sizing from current replay approximation and completed marks."""
        contract = self._select_replay_sizing_contract(
            signal,
            date_str=date_str,
            option_universe=option_universe,
        )
        if contract is None:
            return None, "NO_HISTORICAL_OPTION_CONTRACT_METADATA"

        instrument_id = str(contract.instrument_id)
        candles = option_candle_cache.get(instrument_id)
        if candles is None:
            candles = []
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            start = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    9,
                    15,
                    tzinfo=IST,
                ).astimezone(timezone.utc)
            end = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    15,
                    30,
                    tzinfo=IST,
                ).astimezone(timezone.utc)
            if self.hist_svc and hasattr(self.hist_svc, "repo"):
                try:
                    candles = await self.hist_svc.repo.get_candles(
                        instrument_id,
                        "1m",
                        start_time=start,
                        end_time=end,
                        limit=1000,
                    )
                except Exception as exc:
                    logger.warning(
                        "Historical sizing option query failed for %s: %s",
                        instrument_id,
                        exc,
                    )
            if (
                not candles
                and hasattr(self.hist_svc, "fetch_candles_from_breeze_window")
                and historical_source
                in (
                    HistoricalReplaySource.BREEZE,
                    HistoricalReplaySource.MIXED,
                )
            ):
                try:
                    candles = await self.hist_svc.fetch_candles_from_breeze_window(
                        instrument_id,
                        interval="1m",
                        start_time=start,
                        end_time=end,
                    )
                except Exception as exc:
                    logger.warning(
                        "Historical sizing option fetch failed for %s: %s",
                        instrument_id,
                        exc,
                    )
            allowed = (
                {"BREEZE", "KITE", "LIVE"}
                if historical_source == HistoricalReplaySource.MIXED
                else {historical_source.value}
            )
            candles = sorted(
                [candle for candle in candles if candle.source in allowed],
                key=lambda candle: candle.start_time,
            )
            option_candle_cache[instrument_id] = candles

        entry_mark = _historical_close_at(candles, signal.timestamp)
        if entry_mark is None:
            return None, "HISTORICAL_OPTION_ENTRY_MARK_UNAVAILABLE"

        return (
            calculate_replay_sizing(
                signal=signal,
                contract=contract,
                entry_mark=float(entry_mark),
                risk_config=effective_risk_config,
                option_selection=self.option_selection_config,
                session_config=self.session_config,
                strategy_config=self.tunables,
                account_equity=effective_risk_config.account_equity,
            ),
            None,
        )

    async def _attach_execution_parity_economics(
        self,
        *,
        record: Any,
        provider: HistoricalContractSelectionProvider,
        risk_config: RiskConfig,
        selection: ReplayContractSelectionDecision | None,
        recorder: ReplayManifestRecorder,
    ) -> None:
        if (
            record.lifecycle_status != "RESOLVED"
            or record.exit_timestamp is None
        ):
            return
        instrument_id = str(
            record.sizing_contract_instrument_id
            or record.option_contract_instrument_id
            or ""
        )
        quantity = int(record.replay_quantity or 0)
        if not instrument_id or quantity <= 0:
            record.option_data_quality_reason = (
                "Historical selected contract or replay quantity unavailable"
            )
            return

        record.option_contract_instrument_id = instrument_id
        record.option_contract_symbol = record.sizing_contract_symbol
        record.option_expiry = record.sizing_contract_expiry
        record.option_strike = record.sizing_contract_strike
        record.option_lot_size = record.sizing_contract_lot_size

        mark_entry = await provider.completed_mark_evidence(
            instrument_id=instrument_id,
            event_time=record.simulated_entry_timestamp,
        )
        mark_exit = await provider.completed_mark_evidence(
            instrument_id=instrument_id,
            event_time=record.exit_timestamp,
        )
        mark_risk = risk_config.model_copy(
            update={"paper_slippage_points": 0.0}
        )
        mark_round_trip = estimate_round_trip_execution(
            entry_evidence=mark_entry,
            exit_evidence=mark_exit,
            quantity=quantity,
            risk_config=mark_risk,
        )
        if (
            mark_entry.mark_price is not None
            and mark_exit.mark_price is not None
            and mark_round_trip.gross_execution_pnl is not None
        ):
            record.option_entry_price = round(
                float(mark_entry.mark_price),
                2,
            )
            record.option_exit_price = round(
                float(mark_exit.mark_price),
                2,
            )
            record.option_gross_pnl = mark_round_trip.gross_execution_pnl
            record.option_transaction_costs = (
                mark_round_trip.transaction_costs
            )
            record.option_net_pnl = mark_round_trip.net_execution_pnl
            record.option_price_source = (
                "HISTORICAL_OPTION_COMPLETED_CANDLE_CLOSE_MARK"
            )
            record.option_data_status = "AVAILABLE"
            record.option_data_quality_reason = None
        else:
            record.option_data_status = "UNAVAILABLE"
            record.option_data_quality_reason = (
                "Historical option completed mark unavailable at entry or exit"
            )

        entry_evidence = await provider.price_evidence(
            instrument_id=instrument_id,
            signal_id=record.replay_signal_id,
            event_time=record.simulated_entry_timestamp,
            side="BUY",
            selection=selection,
        )
        exit_evidence = await provider.price_evidence(
            instrument_id=instrument_id,
            signal_id=record.replay_signal_id,
            event_time=record.exit_timestamp,
            side="SELL",
            selection=None,
        )
        execution = estimate_round_trip_execution(
            entry_evidence=entry_evidence,
            exit_evidence=exit_evidence,
            quantity=quantity,
            risk_config=risk_config,
        )
        recorder.set_execution_estimate(
            record.replay_signal_id,
            entry_fill_price=execution.entry.executable_price,
            exit_fill_price=execution.exit.executable_price,
            entry_method=execution.entry.method,
            exit_method=execution.exit.method,
            entry_basis=execution.entry.evidence_basis,
            exit_basis=execution.exit.evidence_basis,
            quote_equivalent=(
                execution.entry.executable_quote_equivalent
                and execution.exit.executable_quote_equivalent
            ),
            gross_pnl=execution.gross_execution_pnl,
            slippage_cost=execution.slippage_cost,
            transaction_costs=execution.transaction_costs,
            net_pnl=execution.net_execution_pnl,
            cost_breakdown={
                "brokerage": execution.brokerage,
                "exchange_charges": execution.exchange_charges,
                "stt": execution.stt,
                "gst": execution.gst,
                "sebi_charges": execution.sebi_charges,
                "stamp_duty": execution.stamp_duty,
                "cost_assumption_version": (
                    execution.cost_assumption_version
                ),
            },
            provenance={
                "entry": execution.entry.to_dict(),
                "exit": execution.exit.to_dict(),
                "partial_option_exits_modeled": False,
                "limitation": (
                    "Execution estimate currently models one option entry and "
                    "the final option exit; partial option exits remain deferred."
                ),
            },
        )
        record.historical_option_provenance = {
            "contract_selection": (
                selection.to_manifest_metadata()
                if selection is not None
                else {}
            ),
            "mark_entry": {
                "basis": mark_entry.basis,
                "source": mark_entry.source,
                "evidence_timestamp": (
                    mark_entry.evidence_timestamp.isoformat()
                    if mark_entry.evidence_timestamp
                    else None
                ),
                "mark_price": mark_entry.mark_price,
                "freshness_seconds": mark_entry.freshness_seconds,
            },
            "mark_exit": {
                "basis": mark_exit.basis,
                "source": mark_exit.source,
                "evidence_timestamp": (
                    mark_exit.evidence_timestamp.isoformat()
                    if mark_exit.evidence_timestamp
                    else None
                ),
                "mark_price": mark_exit.mark_price,
                "freshness_seconds": mark_exit.freshness_seconds,
            },
            "execution_estimate": execution.to_dict(),
            "bid_ask_available_for_both_fills": (
                record.simulated_fill_quote_equivalent
            ),
            "historical_marks_are_executable_fills": False,
        }

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
        option_by_id = {
            str(instrument.instrument_id): instrument
            for instrument in option_instruments
        }
        sizing_locked_contracts = 0
        for record in records:
            sizing_contract_id = str(
                getattr(record, "sizing_contract_instrument_id", "") or ""
            )
            if sizing_contract_id:
                locked = option_by_id.get(sizing_contract_id)
                if locked is not None:
                    selected[sizing_contract_id] = locked
                    sizing_locked_contracts += 1
                    continue

            direction = "CALL" if record.direction == "CALL" else "PUT"
            candidates = [
                instrument for instrument in option_instruments
                if str(
                    getattr(
                        getattr(instrument, "option_right", None),
                        "value",
                        "",
                    )
                ).upper()
                in {direction, "CE" if direction == "CALL" else "PE"}
            ]
            if not candidates:
                continue
            expiry = min(str(instrument.expiry) for instrument in candidates)
            same_expiry = [
                instrument
                for instrument in candidates
                if str(instrument.expiry) == expiry
            ]
            contract = min(
                same_expiry,
                key=lambda instrument: abs(
                    float(instrument.strike) - record.simulated_entry_price
                ),
            )
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
            "selection": (
                "sizing_locked_contract_when_available_else_"
                "nearest_strike_first_expiry_on_or_after_replay_date"
            ),
            "sizing_locked_contracts": sizing_locked_contracts,
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

    async def _load_replay_one_minute_candles(
        self,
        date_str: str,
        instrument_id: str,
        historical_source: HistoricalReplaySource,
    ) -> list[Candle]:
        """Load authoritative 1-minute candles used only for intrabar ordering."""
        if not self.hist_svc or not hasattr(self.hist_svc, "repo"):
            return []
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            9,
            15,
            tzinfo=IST,
        ).astimezone(timezone.utc)
        end = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            15,
            30,
            tzinfo=IST,
        ).astimezone(timezone.utc)
        try:
            candles = await self.hist_svc.repo.get_candles(
                instrument_id,
                "1m",
                start_time=start,
                end_time=end,
                limit=1000,
            )
        except Exception as exc:
            logger.warning("Historical 1m replay query error: %s", exc)
            return []
        allowed = (
            {"BREEZE", "KITE", "LIVE"}
            if historical_source == HistoricalReplaySource.MIXED
            else {historical_source.value}
        )
        return [candle for candle in candles if candle.source in allowed]

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

        cfg = self.tunables
        strategy_registry = ReplayStrategyRegistry.default(
            cfg,
            self.session_config,
        )
        override_values = overrides.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )
        supported_replay_overrides = strategy_registry.supported_override_fields()
        applied_overrides = {
            name: value
            for name, value in override_values.items()
            if name in supported_replay_overrides
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
            if name not in supported_replay_overrides
            and name != "bypass_entry_window"
        }
        effective_overrides = ThresholdOverrides.model_validate(applied_overrides)

        risk_updates: dict[str, Any] = {}
        applied_request_controls: dict[str, Any] = {}
        not_applied_request_controls: dict[str, Any] = {}
        if request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY:
            if "capital" in request.model_fields_set:
                risk_updates["account_equity"] = request.capital
                applied_request_controls["capital"] = request.capital
            if request.risk_per_trade_pct is not None:
                risk_updates["risk_per_trade_pct_of_account"] = (
                    request.risk_per_trade_pct
                )
                applied_request_controls["risk_per_trade_pct"] = (
                    request.risk_per_trade_pct
                )
            if "max_trades_per_day" in request.model_fields_set:
                risk_updates["max_trades_per_day"] = request.max_trades_per_day
                applied_request_controls["max_trades_per_day"] = (
                    request.max_trades_per_day
                )
        else:
            not_applied_request_controls = {
                "capital": {
                    "value": request.capital,
                    "reason": (
                        "Research replay does not apply portfolio sizing."
                    ),
                },
                "risk_per_trade_pct": {
                    "value": request.risk_per_trade_pct,
                    "reason": (
                        "Research replay does not apply portfolio sizing."
                    ),
                },
                "max_trades_per_day": {
                    "value": request.max_trades_per_day,
                    "reason": (
                        "Research replay intentionally does not apply "
                        "chronological daily trade gates."
                    ),
                },
            }
        effective_risk_config = self.risk_config.model_copy(
            update=risk_updates
        )

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
            overrides=effective_overrides,
            tunables=cfg,
            session=self.session_config,
            execution_parity=(
                {
                    "mode": request.replay_mode.value,
                    "risk": effective_risk_config.model_dump(mode="json"),
                    "contract_selection": {
                        "desired_method": "PRODUCTION_CONTRACT_SELECTOR",
                        "exact_evidence": (
                            "EXACT_STRATEGY_SIGNAL_ID_POINT_IN_TIME_CHAIN_SNAPSHOT"
                        ),
                        "fallback": "APPROXIMATED_SELECTION",
                        "premium_cap_override": (
                            overrides.max_option_premium_cap
                            or overrides.max_option_premium
                        ),
                    },
                    "sizing": {
                        "strategy_a_fallback_delta_proxy": (
                            self.option_selection_config.preferred_delta_min
                            + self.option_selection_config.preferred_delta_max
                        )
                        / 2.0,
                        "exact_selection_price_basis": "POINT_IN_TIME_ASK",
                        "approximate_selection_price_basis": (
                            "HISTORICAL_OPTION_COMPLETED_CANDLE_CLOSE_MARK"
                        ),
                    },
                    "execution_model": {
                        "entry": (
                            "ask_plus_slippage_when_bid_ask_available_else_"
                            "completed_mark_plus_slippage_estimate"
                        ),
                        "exit": (
                            "bid_minus_slippage_when_bid_ask_available_else_"
                            "completed_mark_minus_slippage_estimate"
                        ),
                        "cost_assumptions": (
                            effective_risk_config.model_dump(mode="json")
                        ),
                    },
                }
                if request.replay_mode
                == HistoricalReplayMode.EXECUTION_PARITY
                else None
            ),
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
            "requested_replay_mode": request.replay_mode.value,
            "bypass_entry_window": bypass_entry_window,
            "missing_data": sorted(set(missing_data)),
            "strategy_registry": strategy_registry.metadata_snapshot(),
            "control_application": {
                "applied_overrides": applied_overrides,
                "not_applied_overrides": not_applied_overrides,
                "applied_request_controls": applied_request_controls,
                "not_applied_request_controls": not_applied_request_controls,
            },
            "effective_risk_config": {
                "account_equity": effective_risk_config.account_equity,
                "risk_per_trade_pct_of_account": (
                    effective_risk_config.risk_per_trade_pct_of_account
                ),
                "max_trade_capital": effective_risk_config.max_trade_capital,
                "max_lots_per_trade": effective_risk_config.max_lots_per_trade,
                "max_trades_per_day": effective_risk_config.max_trades_per_day,
                "max_trades_per_strategy_per_day": (
                    effective_risk_config.max_trades_per_strategy_per_day
                ),
                "max_failed_trades_per_strategy": (
                    effective_risk_config.max_failed_trades_per_strategy
                ),
                "max_concurrent_positions": (
                    effective_risk_config.max_concurrent_positions
                ),
                "cooldown_after_loss_min": (
                    effective_risk_config.cooldown_after_loss_min
                ),
                "max_daily_loss_r": effective_risk_config.max_daily_loss_r,
            },
            "execution_authority_scope": (
                {
                    "session_entry_windows": "APPLIED",
                    "kill_switch": (
                        "NOT_REPLAYED_POINT_IN_TIME_OPERATIONAL_STATE_UNAVAILABLE"
                    ),
                    "auto_trade_enabled": (
                        "NOT_REPLAYED_POINT_IN_TIME_OPERATIONAL_STATE_UNAVAILABLE"
                    ),
                    "live_system_armed": (
                        "NOT_APPLICABLE_TO_HISTORICAL_EXECUTION_PARITY"
                    ),
                    "daily_loss_pct": (
                        "APPLIED_WHEN_ALL_PRIOR_RESOLVED_TRADES_HAVE_"
                        "ESTIMATED_EXECUTABLE_NET_PNL"
                    ),
                }
                if request.replay_mode
                == HistoricalReplayMode.EXECUTION_PARITY
                else None
            ),
        }
        one_minute_candles = await self._load_replay_one_minute_candles(
            date_str,
            request.instrument_id,
            historical_source,
        )
        contract_provider = (
            HistoricalContractSelectionProvider(
                historical_service=self.hist_svc,
                strategy_repository=self.strategy_repo,
                option_config=self.option_selection_config,
            )
            if request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY
            else None
        )
        if contract_provider is not None:
            await contract_provider.prepare_session(
                date_str=date_str,
                historical_source=historical_source,
            )
        replay_selection_decisions: dict[
            str, ReplayContractSelectionDecision
        ] = {}
        execution_economics_applied: set[str] = set()
        timeline, logs = [], []
        replay_trigger_diagnostics: list[dict[str, Any]] = []
        replay_diagnostic_keys: set[tuple[str, str, str]] = set()
        replay_event_keys: dict[StrategyName, set[tuple[str, str, str | None]]] = {}
        replay_event_counts: dict[StrategyName, Counter[str]] = {}
        replay_manifest_recorder = self.replay_manifest_recorder or ReplayManifestRecorder()
        replay_manifest_recorder.set_replay_metadata(replay_metadata)
        replay_session = ReplaySessionContext(
            trading_date=date_str,
            instrument_id=request.instrument_id,
            overrides=effective_overrides,
            recorder=replay_manifest_recorder,
        )
        strategy_registry.prepare_session(replay_session)
        lifecycle_replayer = HistoricalPositionManagerReplayer(
            risk_config=effective_risk_config,
            session_config=self.session_config,
            strategy_config=self.tunables,
            recorder=replay_manifest_recorder,
            instrument_id=request.instrument_id,
            warmup_candles=warmup,
            session_candles=session,
            futures_candles=futures_history,
            one_minute_candles=one_minute_candles,
        )
        chronological_executor = (
            ChronologicalReplayExecutor(
                lifecycle_replayer=lifecycle_replayer,
                registry=strategy_registry,
                risk_config=effective_risk_config,
            )
            if request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY
            else None
        )

        running = list(warmup)
        for idx, bar in enumerate(session):
            running.append(bar)
            macro = self.resample_to_15m(running, request.instrument_id)
            futures = [c for c in futures_history if c.end_time <= bar.end_time]
            strategy_futures = [
                c for c in strategy_a_futures_history if c.end_time <= bar.end_time
            ]
            features = FeatureEngine.compute_all_features(
                running,
                macro,
                futures,
                spot_price=bar.close,
                as_of=bar.end_time,
            )
            clock = bar.end_time.astimezone(IST)
            in_window = strategy_registry.any_evaluation_window_active(
                bar.end_time,
                bypass_entry_window=bypass_entry_window,
            )
            base_allow_evaluation = bool(
                in_window
                and (
                    strategy_futures
                    or features.data_ready
                    or features.breakout_data_ready
                )
            )
            bar_context = ReplayBarContext(
                session=replay_session,
                bar=bar,
                features=features,
                spot_candles_5m=running,
                spot_candles_15m=macro,
                futures_candles=strategy_futures,
            )

            event, details = None, None
            evaluations = []
            signal = None

            if chronological_executor is not None:
                closed_records = chronological_executor.manage_completed_bar(
                    bar,
                    running,
                )
                if contract_provider is not None:
                    for closed_record in closed_records:
                        await self._attach_execution_parity_economics(
                            record=closed_record,
                            provider=contract_provider,
                            risk_config=effective_risk_config,
                            selection=replay_selection_decisions.get(
                                closed_record.replay_signal_id
                            ),
                            recorder=replay_manifest_recorder,
                        )
                        chronological_executor.apply_execution_economics(
                            closed_record
                        )
                        execution_economics_applied.add(
                            closed_record.replay_signal_id
                        )
                global_gate = chronological_executor.global_entry_gate(
                    bar.end_time
                )
                if not global_gate.allowed:
                    strategy_registry.reset_all(bar.end_time)
                    chronological_executor.note_entry_evaluation_suppressed(
                        global_gate.status
                    )
                    evaluations = strategy_registry.evaluate_completed_bar(
                        bar_context,
                        allow_evaluation=False,
                    )
                    event = global_gate.status
                    details = (
                        "New entry evaluation suppressed by execution-parity "
                        f"risk gate: {global_gate.status}."
                    )
                else:
                    evaluations = strategy_registry.evaluate_completed_bar(
                        bar_context,
                        allow_evaluation=base_allow_evaluation,
                        stop_after_signal=True,
                    )
                    signal = strategy_registry.first_signal(evaluations)
                    if signal is not None:
                        strategy_gate = (
                            chronological_executor.strategy_entry_gate(signal)
                        )
                        if not strategy_gate.allowed:
                            chronological_executor.record_rejection(
                                at=bar.end_time,
                                status=strategy_gate.status,
                                strategy=signal.strategy,
                                signal_id=signal.signal_id,
                                details=strategy_gate.details,
                            )
                            event = "ENTRY_REJECTED_RISK"
                            details = strategy_gate.status
                            logs.append(
                                DecisionLogEntry(
                                    id=f"SIM-{idx}",
                                    timestamp=bar.end_time,
                                    category="RISK",
                                    strategy=signal.strategy.value,
                                    message=details,
                                    details={
                                        **signal.model_dump(mode="json"),
                                        "risk_gate": (
                                            strategy_gate.details or {}
                                        ),
                                    },
                                )
                            )
                        else:
                            if contract_provider is None:
                                raise RuntimeError(
                                    "execution-parity contract provider missing"
                                )
                            selection = await contract_provider.select_contract(
                                signal,
                                override_premium_cap=(
                                    overrides.max_option_premium_cap
                                    or overrides.max_option_premium
                                ),
                            )
                            selection_meta = (
                                selection.to_manifest_metadata()
                            )
                            if (
                                not selection.selected
                                or selection.entry_reference_price is None
                            ):
                                status = (
                                    selection.rejection_reason
                                    or "CONTRACT_SELECTION_FAILED"
                                )
                                chronological_executor.record_rejection(
                                    at=bar.end_time,
                                    status="CONTRACT_SELECTION_REJECTED",
                                    strategy=signal.strategy,
                                    signal_id=signal.signal_id,
                                    details=selection_meta,
                                )
                                strategy_registry.notify_execution_rejected(
                                    signal,
                                    (
                                        "EXECUTION_REJECTED_CONTRACT_SELECTION:"
                                        f"{status}"
                                    ),
                                )
                                event = "ENTRY_REJECTED_CONTRACT_SELECTION"
                                details = status
                                logs.append(
                                    DecisionLogEntry(
                                        id=f"SIM-{idx}",
                                        timestamp=bar.end_time,
                                        category="CONTRACT_SELECTION",
                                        strategy=signal.strategy.value,
                                        message=status,
                                        details=selection_meta,
                                    )
                                )
                            else:
                                contract = selection.selected_contract
                                option_delta = getattr(
                                    contract,
                                    "delta",
                                    None,
                                )
                                option_delta_source = getattr(
                                    contract,
                                    "greek_source",
                                    None,
                                )
                                sizing = calculate_replay_sizing(
                                    signal=signal,
                                    contract=contract,
                                    entry_mark=float(
                                        selection.entry_reference_price
                                    ),
                                    risk_config=effective_risk_config,
                                    option_selection=(
                                        self.option_selection_config
                                    ),
                                    session_config=self.session_config,
                                    strategy_config=self.tunables,
                                    account_equity=(
                                        effective_risk_config.account_equity
                                    ),
                                    option_delta=option_delta,
                                    option_delta_source=option_delta_source,
                                    price_basis=selection.entry_price_basis,
                                )
                                if sizing.status != "APPLIED":
                                    status = (
                                        sizing.rejection_reason
                                        or "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"
                                    )
                                    chronological_executor.record_rejection(
                                        at=bar.end_time,
                                        status="SIZING_REJECTED",
                                        strategy=signal.strategy,
                                        signal_id=signal.signal_id,
                                        details={
                                            "reason": status,
                                            "lots": sizing.lots,
                                            "quantity": sizing.quantity,
                                            "method": sizing.method,
                                            "contract_selection": (
                                                selection_meta
                                            ),
                                        },
                                    )
                                    strategy_registry.notify_execution_rejected(
                                        signal,
                                        (
                                            "EXECUTION_REJECTED_SIZING:"
                                            f"{status}"
                                        ),
                                    )
                                    event = "ENTRY_REJECTED_SIZING"
                                    details = status
                                else:
                                    replay_selection_decisions[
                                        signal.signal_id
                                    ] = selection
                                    record = (
                                        chronological_executor.accept_signal(
                                            signal,
                                            bar_context,
                                            sizing=sizing,
                                            selection=selection,
                                        )
                                    )
                                    evaluations = (
                                        strategy_registry.refresh_diagnostics(
                                            evaluations,
                                            bar_context,
                                        )
                                    )
                                    event = "ENTRY_ACCEPTED"
                                    details = (
                                        "Qualified signal accepted into "
                                        "execution-parity replay using "
                                        f"{selection.evidence_status}; "
                                        f"{sizing.lots} lot(s) / "
                                        f"{sizing.quantity} quantity."
                                    )
                                    if record.lifecycle_status != "PENDING":
                                        event = "ENTRY_RESOLUTION"
                                        details = (
                                            "Signal entry was resolved on its "
                                            "entry candle: "
                                            f"{record.lifecycle_status}."
                                        )
                                        await self._attach_execution_parity_economics(
                                            record=record,
                                            provider=contract_provider,
                                            risk_config=effective_risk_config,
                                            selection=selection,
                                            recorder=replay_manifest_recorder,
                                        )
                                        chronological_executor.apply_execution_economics(
                                            record
                                        )
                                        execution_economics_applied.add(
                                            record.replay_signal_id
                                        )
                                    logs.append(
                                        DecisionLogEntry(
                                            id=f"SIM-{idx}",
                                            timestamp=bar.end_time,
                                            category="SETUP",
                                            strategy=signal.strategy.value,
                                            message=details,
                                            details={
                                                **signal.model_dump(
                                                    mode="json"
                                                ),
                                                "contract_selection": (
                                                    selection_meta
                                                ),
                                                "sizing": {
                                                    "method": sizing.method,
                                                    "lots": sizing.lots,
                                                    "quantity": (
                                                        sizing.quantity
                                                    ),
                                                    "risk_budget": (
                                                        sizing.risk_budget
                                                    ),
                                                    "price_basis": (
                                                        sizing.price_basis
                                                    ),
                                                },
                                            },
                                        )
                                    )
                    elif not base_allow_evaluation:
                        strategy_registry.reset_all(bar.end_time)
                        details = (
                            features.data_reason
                            if not features.data_ready
                            else "Outside entry window"
                        )
            else:
                evaluations = strategy_registry.evaluate_completed_bar(
                    bar_context,
                    allow_evaluation=base_allow_evaluation,
                )
                qualified_signals = [
                    item.signal
                    for item in evaluations
                    if item.signal is not None
                ]
                for qualified_signal in qualified_signals:
                    strategy_registry.confirm_entry(
                        qualified_signal,
                        bar_context,
                    )
                if qualified_signals:
                    evaluations = strategy_registry.refresh_diagnostics(
                        evaluations,
                        bar_context,
                    )
                    signal = qualified_signals[0]
                    event = "SIGNAL_ONLY"
                    details = (
                        "Qualified signal; historical executable option quotes unavailable"
                    )
                    logs.append(
                        DecisionLogEntry(
                            id=f"SIM-{idx}",
                            timestamp=bar.end_time,
                            category="SETUP",
                            strategy=signal.strategy.value,
                            message=details,
                            details=signal.model_dump(mode="json"),
                        )
                    )
                elif not base_allow_evaluation:
                    strategy_registry.reset_all(bar.end_time)
                    details = (
                        features.data_reason
                        if not features.data_ready
                        else "Outside entry window"
                    )

            for evaluation in evaluations:
                metadata = evaluation.metadata
                in_strategy_window = strategy_registry.evaluation_window_active(
                    metadata,
                    bar.end_time,
                    bypass_entry_window=bypass_entry_window,
                )
                if not in_strategy_window:
                    continue

                if (
                    metadata.audit_events
                    and evaluation.event is not None
                    and evaluation.event.event != "DUPLICATE_IGNORED"
                ):
                    event_keys = replay_event_keys.setdefault(
                        metadata.strategy,
                        set(),
                    )
                    event_counts = replay_event_counts.setdefault(
                        metadata.strategy,
                        Counter(),
                    )
                    event_key = (
                        evaluation.event.event,
                        evaluation.event.timestamp.isoformat(),
                        evaluation.event.reason,
                    )
                    if event_key not in event_keys:
                        event_keys.add(event_key)
                        event_counts[str(evaluation.event.event)] += 1

                if metadata.audit_diagnostics:
                    for row in evaluation.audit_records:
                        key = (
                            str(row.get("strategy") or metadata.strategy.value),
                            str(row.get("direction") or ""),
                            str(row.get("completed_futures_candle") or ""),
                        )
                        if key in replay_diagnostic_keys:
                            continue
                        replay_diagnostic_keys.add(key)
                        replay_trigger_diagnostics.append(row)

            phases = strategy_registry.timeline_phases(evaluations)
            active_trade_id = None
            if (
                chronological_executor is not None
                and chronological_executor.state.active_positions
            ):
                active_trade_id = (
                    chronological_executor.state.active_positions[0]
                    .trade.trade_id
                )
            timeline.append(
                SimulationBarSnapshot(
                    bar_index=idx,
                    timestamp=bar.end_time.isoformat(),
                    ist_time=clock.strftime("%H:%M"),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    spot=bar.close,
                    ema9_5m=features.ema9_5m,
                    ema20_5m=features.ema20_5m,
                    supertrend=features.supertrend_direction,
                    adx_15m=features.adx_15m,
                    rvol_5m=features.rvol_5m,
                    bb_width_percentile=features.bb_width_percentile,
                    strategy_a_phase=phases.get("strategy_a_phase", "FLAT"),
                    strategy_b_phase=phases.get("strategy_b_phase", "RESET"),
                    active_trade_id=active_trade_id,
                    event=event,
                    event_details=details,
                )
            )

        strategy_a_event_counts = replay_event_counts.get(
            StrategyName.TREND_PULLBACK,
            Counter(),
        )

        if chronological_executor is not None:
            chronological_executor.finalize_session()
            if contract_provider is not None:
                for record in replay_manifest_recorder.records():
                    if (
                        record.lifecycle_status == "RESOLVED"
                        and record.replay_signal_id
                        not in execution_economics_applied
                    ):
                        await self._attach_execution_parity_economics(
                            record=record,
                            provider=contract_provider,
                            risk_config=effective_risk_config,
                            selection=replay_selection_decisions.get(
                                record.replay_signal_id
                            ),
                            recorder=replay_manifest_recorder,
                        )
                        chronological_executor.apply_execution_economics(
                            record
                        )
                        execution_economics_applied.add(
                            record.replay_signal_id
                        )
            replay_metadata["execution_parity"] = (
                chronological_executor.metadata()
            )
            lifecycle_resolver = {
                "resolver": dict(sorted(lifecycle_replayer.stats.items())),
                "one_minute_candles": len(one_minute_candles),
            }
        else:
            lifecycle_resolver = lifecycle_replayer.replay(
                replay_manifest_recorder.records()
            )
        lifecycle_report = build_lifecycle_report(replay_manifest_recorder.records(), lifecycle_resolver)
        if contract_provider is not None:
            option_data = {
                "status": "EVIDENCE_AWARE_EXECUTION_PARITY",
                "desired_selection_method": "PRODUCTION_CONTRACT_SELECTOR",
                "point_in_time_chain_snapshots": len(
                    contract_provider.snapshots
                ),
                "point_in_time_option_quotes": len(
                    contract_provider.quotes
                ),
                "option_contract_metadata_count": len(
                    contract_provider.option_universe
                ),
                "contract_selection_fallback": "APPROXIMATED_SELECTION",
                "mark_policy": "latest_completed_candle_close_at_event",
                "fill_policy": (
                    "point_in_time_bid_ask_plus_slippage_when_available_"
                    "else_completed_mark_plus_slippage_estimate"
                ),
                "partial_option_exits_modeled": False,
            }
        else:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            option_session_start = datetime(
                target_date.year,
                target_date.month,
                target_date.day,
                9,
                15,
                tzinfo=IST,
            ).astimezone(timezone.utc)
            option_session_end = datetime(
                target_date.year,
                target_date.month,
                target_date.day,
                15,
                30,
                tzinfo=IST,
            ).astimezone(timezone.utc)
            option_contracts, option_candles, option_data = (
                await self._fetch_replay_option_candles(
                    replay_manifest_recorder.records(),
                    date_str,
                    option_session_start,
                    option_session_end,
                    historical_source,
                )
            )
            attach_historical_option_prices(
                replay_manifest_recorder.records(),
                option_contracts,
                option_candles,
                effective_risk_config,
            )
        replay_metadata["historical_option_data"] = option_data
        trades = build_simulated_trade_records(replay_manifest_recorder.records())
        option_mark_summary = summarize_historical_option_marks(
            replay_manifest_recorder.records()
        )
        resolved_records = [
            record for record in replay_manifest_recorder.records()
            if record.lifecycle_status == "RESOLVED" and record.realized_r is not None
        ]
        option_complete = bool(resolved_records) and bool(
            option_mark_summary["all_resolved_trades_priced"]
        )
        selection_evidence_counts = Counter(
            record.contract_selection_evidence_status or "NOT_APPLIED"
            for record in resolved_records
        )
        fill_method_counts = Counter()
        for record in resolved_records:
            if record.simulated_entry_fill_method:
                fill_method_counts[
                    f"ENTRY:{record.simulated_entry_fill_method}"
                ] += 1
            if record.simulated_exit_fill_method:
                fill_method_counts[
                    f"EXIT:{record.simulated_exit_fill_method}"
                ] += 1
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
        elif (
            request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY
            and option_complete
        ):
            limitation = (
                "Historical option completed-candle marks are reported separately "
                "from estimated executable fills. Contract selection uses the "
                "production selector only for exact point-in-time signal snapshots; "
                "otherwise it is explicitly APPROXIMATED_SELECTION. Estimated fills "
                "use point-in-time bid/ask plus configured slippage when available, "
                "otherwise completed-mark +/- slippage. Partial option exits are not "
                "modeled in this increment."
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
        if (
            chronological_executor is not None
            and chronological_executor.state.chronology_indeterminate
        ):
            limitation = (
                limitation
                + " Execution-parity entry evaluation was halted after chronology "
                "became indeterminate; no optimistic later entries were assumed."
            )
        lifecycle_report["manifest_validation"] = replay_manifest_recorder.validate_complete(expected_count=len(replay_manifest_recorder.records()))
        replay_lifecycle = lifecycle_report

        signal_metrics = ReplaySignalMetrics(
            calculation_basis=(
                "PRODUCTION_PRIORITY_SIGNAL_DISCOVERY_WITH_ACTIVE_POSITION_SUPPRESSION"
                if request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY
                else "STRATEGY_SIGNAL_DISCOVERY_ON_COMPLETED_HISTORICAL_BARS"
            ),
            total_bars_evaluated=len(session),
            qualified_signals=lifecycle_report["total_signals"],
            ambiguous_signals=lifecycle_report["ambiguous"],
            unresolved_signals=lifecycle_report["unresolved"],
        )
        underlying_lifecycle_metrics = ReplayUnderlyingLifecycleMetrics(
            resolved_trades=lifecycle_report["resolved"],
            winning_trades=lifecycle_report["winners"],
            losing_trades=lifecycle_report["losers"],
            breakeven_trades=lifecycle_report["breakeven"],
            win_rate_pct=lifecycle_report["win_rate_pct"],
            total_realized_r=lifecycle_report["total_r"],
            average_realized_r=lifecycle_report["average_r"],
            median_realized_r=lifecycle_report["median_r"],
            average_winner_r=lifecycle_report["average_winner_r"],
            average_loser_r=lifecycle_report["average_loser_r"],
            profit_factor_r=lifecycle_report["profit_factor"],
            max_drawdown_r=lifecycle_report["max_drawdown_r"],
            max_consecutive_losses=lifecycle_report["max_consecutive_losses"],
        )
        option_mark_metrics = ReplayOptionMarkMetrics(
            calculation_basis=(
                "SEPARATE_HISTORICAL_MARK_AND_ESTIMATED_EXECUTABLE_ECONOMICS"
                if request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY
                else (
                    "REPLAY_QUANTITY_USING_REPLAY_CONTRACT_APPROXIMATION_"
                    "AND_PAPER_COST_SCHEDULE"
                )
            ),
            priced_trades=option_mark_summary["priced_trades"],
            unpriced_trades=option_mark_summary["unpriced_trades"],
            all_resolved_trades_priced=option_mark_summary[
                "all_resolved_trades_priced"
            ],
            gross_mark_pnl=option_mark_summary["gross_mark_pnl"],
            estimated_transaction_costs=option_mark_summary[
                "estimated_transaction_costs"
            ],
            net_mark_pnl=option_mark_summary["net_mark_pnl"],
            execution_estimated_trades=option_mark_summary[
                "execution_estimated_trades"
            ],
            execution_unavailable_trades=option_mark_summary[
                "execution_unavailable_trades"
            ],
            bid_ask_supported_trades=option_mark_summary[
                "bid_ask_supported_trades"
            ],
            mark_fallback_fill_trades=option_mark_summary[
                "mark_fallback_fill_trades"
            ],
            gross_estimated_executable_pnl=option_mark_summary[
                "gross_estimated_executable_pnl"
            ],
            estimated_slippage_costs=option_mark_summary[
                "estimated_slippage_costs"
            ],
            estimated_execution_transaction_costs=option_mark_summary[
                "estimated_execution_transaction_costs"
            ],
            net_estimated_executable_pnl=option_mark_summary[
                "net_estimated_executable_pnl"
            ],
        )
        portfolio_metrics = (
            ReplayPortfolioMetrics(
                calculation_basis=(
                    "CHRONOLOGICAL_EXECUTION_AVAILABLE_PORTFOLIO_ANALYTICS_NOT_IMPLEMENTED"
                ),
                limitation=(
                    "Execution-parity replay is chronological, but portfolio equity, "
                    "capital utilisation and portfolio drawdown reporting are deferred "
                    "to the portfolio-analytics increment."
                ),
            )
            if request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY
            else ReplayPortfolioMetrics()
        )
        data_quality = ReplayDataQuality(
            historical_source=historical_source.value,
            missing_data=sorted(set(missing_data)),
            underlying_issue_counts=dict(data_quality_counts.most_common()),
            option_mark_available_trades=option_mark_summary["priced_trades"],
            option_mark_unavailable_trades=option_mark_summary["unpriced_trades"],
            option_mark_quality_reasons=option_mark_summary["quality_reasons"],
            contract_selection_evidence_counts=dict(
                selection_evidence_counts
            ),
            execution_fill_method_counts=dict(fill_method_counts),
        )

        return SimulationResult(
            replay_mode=(
                "EXECUTION_PARITY"
                if request.replay_mode == HistoricalReplayMode.EXECUTION_PARITY
                else "POSITION_MANAGER_REPLAY"
            ),
            limitation=limitation,
            session_date=date_str,
            signal_metrics=signal_metrics,
            underlying_lifecycle_metrics=underlying_lifecycle_metrics,
            option_mark_metrics=option_mark_metrics,
            portfolio_metrics=portfolio_metrics,
            data_quality=data_quality,
            trades=trades, timeline=timeline, decision_logs=logs,
            replay_trigger_diagnostics=replay_trigger_diagnostics,
            replay_manifests=[record.model_dump(mode="json") for record in replay_manifest_recorder.records()],
            replay_metadata=replay_metadata,
            replay_lifecycle=replay_lifecycle,
        )
