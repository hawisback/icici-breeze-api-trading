"""Historical option contract selection with explicit evidence grading.

The provider has one rule: replay may only claim production contract-selection
parity when an exact point-in-time production snapshot exists for the replay
signal. Otherwise it falls back to a deterministic metadata/mark approximation
and labels that result explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from libs.contracts.models import Candle
from libs.market_time import IST
from services.strategy.contract_selector import ContractSelector, trading_sessions_remaining
from services.strategy.models import (
    HistoricalReplaySource,
    OptionSelectionConfig,
    SelectedContract,
    StrategyName,
    StrategySignal,
)


def _aware_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo and value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    return None


def _completed_candle_at(
    candles: list[Candle],
    event_time: datetime | None,
) -> Candle | None:
    if event_time is None or event_time.tzinfo is None or event_time.utcoffset() is None:
        return None
    eligible = [
        candle
        for candle in candles
        if candle.end_time <= event_time
    ]
    return max(eligible, key=lambda candle: candle.end_time) if eligible else None


@dataclass(frozen=True)
class ReplayContractSelectionDecision:
    desired_method: str
    actual_method: str
    evidence_status: str
    production_rules_applied: bool
    selected_contract: Any | None
    inspected_candidates: tuple[dict[str, Any], ...] = ()
    rejection_reason: str | None = None
    snapshot_id: str | None = None
    snapshot_timestamp: datetime | None = None
    entry_price_basis: str | None = None
    entry_reference_price: float | None = None
    unsupported_evidence: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def selected(self) -> bool:
        return self.selected_contract is not None

    def to_manifest_metadata(self) -> dict[str, Any]:
        contract = self.selected_contract
        selected = None
        if contract is not None:
            if hasattr(contract, "model_dump"):
                selected = contract.model_dump(mode="json")
            else:
                selected = {
                    "instrument_id": getattr(contract, "instrument_id", None),
                    "symbol": (
                        getattr(contract, "stock_code", None)
                        or getattr(contract, "symbol", None)
                    ),
                    "expiry": str(getattr(contract, "expiry", "") or ""),
                    "strike": float(getattr(contract, "strike", 0.0) or 0.0),
                    "lot_size": int(getattr(contract, "lot_size", 0) or 0),
                }
        return {
            "desired_method": self.desired_method,
            "actual_method": self.actual_method,
            "evidence_status": self.evidence_status,
            "production_rules_applied": self.production_rules_applied,
            "snapshot_id": self.snapshot_id,
            "snapshot_timestamp": (
                self.snapshot_timestamp.isoformat()
                if self.snapshot_timestamp is not None
                else None
            ),
            "entry_price_basis": self.entry_price_basis,
            "entry_reference_price": self.entry_reference_price,
            "unsupported_evidence": list(self.unsupported_evidence),
            "rejection_reason": self.rejection_reason,
            "selected_contract": selected,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class ReplayPriceEvidence:
    status: str
    basis: str
    source: str
    event_timestamp: datetime
    evidence_timestamp: datetime | None
    mark_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    ltp: float | None = None
    freshness_seconds: float | None = None
    reason: str | None = None

    @property
    def bid_ask_available(self) -> bool:
        return bool(
            self.bid is not None
            and self.ask is not None
            and self.bid > 0
            and self.ask > 0
            and self.ask >= self.bid
        )


class HistoricalContractSelectionProvider:
    """Load and resolve point-in-time or approximated historical option evidence."""

    def __init__(
        self,
        *,
        historical_service: Any | None,
        strategy_repository: Any | None,
        option_config: OptionSelectionConfig,
    ) -> None:
        self.hist_svc = historical_service
        self.strategy_repo = strategy_repository
        self.option_config = option_config
        self.selector = ContractSelector(option_config)
        self.date_str: str | None = None
        self.historical_source = HistoricalReplaySource.BREEZE
        self.option_universe: list[Any] = []
        self.snapshots: list[dict[str, Any]] = []
        self.quotes: list[dict[str, Any]] = []
        self.candle_cache: dict[str, list[Candle]] = {}

    async def prepare_session(
        self,
        *,
        date_str: str,
        historical_source: HistoricalReplaySource,
    ) -> None:
        self.date_str = date_str
        self.historical_source = historical_source
        self.option_universe = await self._load_option_universe(date_str)
        if self.strategy_repo is not None:
            try:
                self.snapshots = await self.strategy_repo.list_option_chain_snapshots(
                    date_str
                )
            except Exception:
                self.snapshots = []
            try:
                self.quotes = await self.strategy_repo.list_option_quotes(date_str)
            except Exception:
                self.quotes = []

    async def _load_option_universe(self, date_str: str) -> list[Any]:
        inst_svc = getattr(self.hist_svc, "instrument_service", None)
        if not inst_svc:
            return []
        try:
            instruments = await inst_svc.repo.search(
                query="NIFTY",
                underlying="NIFTY",
                limit=10000,
            )
        except Exception:
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

    async def _load_option_candles(self, instrument_id: str) -> list[Candle]:
        if instrument_id in self.candle_cache:
            return self.candle_cache[instrument_id]
        if not self.date_str:
            return []
        target = datetime.strptime(self.date_str, "%Y-%m-%d").date()
        start = datetime(
            target.year, target.month, target.day, 9, 15, tzinfo=IST
        ).astimezone()
        end = datetime(
            target.year, target.month, target.day, 15, 30, tzinfo=IST
        ).astimezone()
        candles: list[Candle] = []
        if self.hist_svc is not None and hasattr(self.hist_svc, "repo"):
            try:
                candles = await self.hist_svc.repo.get_candles(
                    instrument_id,
                    "1m",
                    start_time=start,
                    end_time=end,
                    limit=1000,
                )
            except Exception:
                candles = []
        if (
            not candles
            and self.hist_svc is not None
            and hasattr(self.hist_svc, "fetch_candles_from_breeze_window")
            and self.historical_source
            in (HistoricalReplaySource.BREEZE, HistoricalReplaySource.MIXED)
        ):
            try:
                candles = await self.hist_svc.fetch_candles_from_breeze_window(
                    instrument_id,
                    interval="1m",
                    start_time=start,
                    end_time=end,
                )
            except Exception:
                candles = []
        allowed = (
            {"BREEZE", "KITE", "LIVE"}
            if self.historical_source == HistoricalReplaySource.MIXED
            else {self.historical_source.value}
        )
        candles = sorted(
            [candle for candle in candles if candle.source in allowed],
            key=lambda candle: candle.start_time,
        )
        self.candle_cache[instrument_id] = candles
        return candles

    @staticmethod
    def _snapshot_chain(
        snapshot: dict[str, Any],
        signal: StrategySignal,
    ) -> dict[str, Any]:
        right_key = "call" if signal.direction.value == "BULLISH" else "put"
        rows: list[dict[str, Any]] = []
        for item in snapshot.get("selector_candidates") or []:
            strike = item.get("strike")
            if strike is None:
                continue
            leg = {
                "expiry": item.get("expiry"),
                "delta": item.get("delta"),
                "gamma": item.get("gamma"),
                "delta_source": item.get("greek_source"),
                "greek_timestamp": item.get("greek_timestamp"),
                "bid": item.get("bid"),
                "ask": item.get("ask"),
                "ltp": item.get("ltp"),
                "quote_timestamp": item.get("quote_timestamp"),
                "open_interest": item.get("open_interest"),
                "volume": item.get("volume"),
                "lot_size": item.get("lot_size"),
                "instrument_id": item.get("instrument_id"),
                "symbol": item.get("symbol"),
                "instrument_token": item.get("instrument_token"),
            }
            rows.append({
                "strike": float(strike),
                right_key: leg,
                "expiry": item.get("expiry"),
            })
        snapshot_time = (
            snapshot.get("selector_timestamp")
            or snapshot.get("chain_snapshot_timestamp")
            or snapshot.get("captured_at")
        )
        verified_delta = any(
            item.get("delta") is not None
            and str(item.get("greek_source") or "").upper() != "UNAVAILABLE"
            for item in snapshot.get("selector_candidates") or []
        )
        return {
            "timestamp": snapshot_time,
            "captured_at": snapshot.get("captured_at"),
            "spot_price": snapshot.get("spot_price"),
            "expiry": snapshot.get("expiry"),
            "source": snapshot.get("source", "UNAVAILABLE"),
            "strikes": rows,
            "capabilities": {
                "verified_delta_available": verified_delta,
                "verified_greeks_available": verified_delta,
                "strategy_a_contract_selection_ready": verified_delta,
                "strategy_a_rejection_reason": (
                    None if verified_delta else "VERIFIED_OPTION_DELTA_UNAVAILABLE"
                ),
            },
        }

    def _exact_snapshot(self, signal: StrategySignal) -> dict[str, Any] | None:
        exact = [
            row
            for row in self.snapshots
            if str(row.get("strategy_signal_id") or "") == signal.signal_id
        ]
        if not exact:
            return None
        exact.sort(
            key=lambda row: (
                _aware_timestamp(row.get("selector_timestamp"))
                or _aware_timestamp(row.get("captured_at"))
                or signal.timestamp
            )
        )
        return exact[0]

    async def select_contract(
        self,
        signal: StrategySignal,
        *,
        override_premium_cap: float | None = None,
    ) -> ReplayContractSelectionDecision:
        snapshot = self._exact_snapshot(signal)
        underlying = float(
            signal.underlying_entry_price or signal.spot_reference_price
        )
        strategy_a = signal.strategy == StrategyName.TREND_PULLBACK

        if snapshot is not None:
            chain = self._snapshot_chain(snapshot, signal)
            as_of = (
                _aware_timestamp(snapshot.get("selector_timestamp"))
                or _aware_timestamp(snapshot.get("chain_snapshot_timestamp"))
                or _aware_timestamp(snapshot.get("captured_at"))
            )
            selected, inspected, reason = self.selector.select_contract(
                direction=signal.direction,
                underlying_price=underlying,
                option_chain=chain,
                override_premium_cap=override_premium_cap,
                strategy_a=strategy_a,
                as_of=as_of,
            )
            return ReplayContractSelectionDecision(
                desired_method="PRODUCTION_CONTRACT_SELECTOR",
                actual_method="PRODUCTION_CONTRACT_SELECTOR",
                evidence_status="POINT_IN_TIME_CHAIN_SNAPSHOT",
                production_rules_applied=True,
                selected_contract=selected,
                inspected_candidates=tuple(inspected),
                rejection_reason=reason,
                snapshot_id=str(snapshot.get("snapshot_id") or "") or None,
                snapshot_timestamp=as_of,
                entry_price_basis=(
                    "POINT_IN_TIME_ASK" if selected is not None else None
                ),
                entry_reference_price=(
                    float(selected.ask_price) if selected is not None else None
                ),
                unsupported_evidence=(),
                provenance={
                    "source": snapshot.get("source"),
                    "matched_by": "EXACT_STRATEGY_SIGNAL_ID",
                    "stored_selector_result": snapshot.get("selector_result"),
                    "stored_rejection_reason": snapshot.get("rejection_reason"),
                },
            )

        contract = self._approximate_contract(signal)
        if contract is None:
            return ReplayContractSelectionDecision(
                desired_method="PRODUCTION_CONTRACT_SELECTOR",
                actual_method="NO_SELECTION",
                evidence_status="UNAVAILABLE",
                production_rules_applied=False,
                selected_contract=None,
                rejection_reason="NO_HISTORICAL_OPTION_CONTRACT_METADATA",
                unsupported_evidence=(
                    "POINT_IN_TIME_CHAIN",
                    "DELTA",
                    "BID_ASK",
                    "OPEN_INTEREST",
                    "VOLUME",
                    "SPREAD",
                    "PREMIUM_RULES",
                ),
            )
        candles = await self._load_option_candles(str(contract.instrument_id))
        candle = _completed_candle_at(candles, signal.timestamp)
        if candle is None:
            return ReplayContractSelectionDecision(
                desired_method="PRODUCTION_CONTRACT_SELECTOR",
                actual_method="APPROXIMATED_SELECTION",
                evidence_status="APPROXIMATED_SELECTION",
                production_rules_applied=False,
                selected_contract=contract,
                rejection_reason="HISTORICAL_OPTION_ENTRY_MARK_UNAVAILABLE",
                unsupported_evidence=(
                    "POINT_IN_TIME_CHAIN",
                    "DELTA",
                    "BID_ASK",
                    "OPEN_INTEREST",
                    "VOLUME",
                    "SPREAD",
                    "PREMIUM_RULES",
                ),
                provenance={
                    "selection_rule": (
                        "nearest_strike_earliest_metadata_expiry_meeting_"
                        "configured_minimum_sessions"
                    ),
                },
            )
        return ReplayContractSelectionDecision(
            desired_method="PRODUCTION_CONTRACT_SELECTOR",
            actual_method="APPROXIMATED_SELECTION",
            evidence_status="APPROXIMATED_SELECTION",
            production_rules_applied=False,
            selected_contract=contract,
            entry_price_basis="HISTORICAL_COMPLETED_CANDLE_CLOSE_MARK",
            entry_reference_price=float(candle.close),
            unsupported_evidence=(
                "POINT_IN_TIME_CHAIN",
                "DELTA",
                "BID_ASK",
                "OPEN_INTEREST",
                "VOLUME",
                "SPREAD",
                "PREMIUM_RULES",
            ),
            provenance={
                "selection_rule": (
                    "nearest_strike_earliest_metadata_expiry_meeting_"
                    "configured_minimum_sessions"
                ),
                "entry_mark_candle_end": candle.end_time.isoformat(),
                "entry_mark_source": candle.source,
            },
        )

    def _approximate_contract(self, signal: StrategySignal) -> Any | None:
        if not self.date_str:
            return None
        direction = "CALL" if signal.direction.value == "BULLISH" else "PUT"
        rights = {direction, "CE" if direction == "CALL" else "PE"}
        replay_date = date.fromisoformat(self.date_str)
        holidays = set(self.option_config.exchange_holidays)
        candidates = []
        for instrument in self.option_universe:
            right = str(
                getattr(
                    getattr(instrument, "option_right", None),
                    "value",
                    getattr(instrument, "option_right", None),
                )
            ).upper()
            if right not in rights:
                continue
            expiry_value = str(getattr(instrument, "expiry", "") or "")
            try:
                expiry_date = date.fromisoformat(expiry_value[:10])
            except ValueError:
                continue
            if trading_sessions_remaining(
                replay_date,
                expiry_date,
                holidays,
            ) < self.option_config.minimum_expiry_sessions_remaining:
                continue
            candidates.append(instrument)
        if not candidates:
            return None
        expiry = min(str(item.expiry) for item in candidates)
        same_expiry = [item for item in candidates if str(item.expiry) == expiry]
        underlying = float(
            signal.underlying_entry_price or signal.spot_reference_price
        )
        return min(
            same_expiry,
            key=lambda item: (
                abs(float(item.strike) - underlying),
                float(item.strike),
                str(item.instrument_id),
            ),
        )

    async def price_evidence(
        self,
        *,
        instrument_id: str,
        signal_id: str,
        event_time: datetime,
        side: str,
        selection: ReplayContractSelectionDecision | None = None,
    ) -> ReplayPriceEvidence:
        if side == "BUY" and selection is not None:
            contract = selection.selected_contract
            if (
                selection.evidence_status == "POINT_IN_TIME_CHAIN_SNAPSHOT"
                and isinstance(contract, SelectedContract)
                and contract.bid_price > 0
                and contract.ask_price > 0
            ):
                timestamp = (
                    contract.quote_timestamp
                    or selection.snapshot_timestamp
                )
                freshness = (
                    (selection.snapshot_timestamp - timestamp).total_seconds()
                    if selection.snapshot_timestamp is not None
                    and timestamp is not None
                    else contract.quote_freshness_seconds
                )
                return ReplayPriceEvidence(
                    status="AVAILABLE",
                    basis="POINT_IN_TIME_BID_ASK",
                    source=str(selection.provenance.get("source") or "UNKNOWN"),
                    event_timestamp=event_time,
                    evidence_timestamp=timestamp,
                    bid=float(contract.bid_price),
                    ask=float(contract.ask_price),
                    ltp=float(contract.ltp or 0.0) or None,
                    freshness_seconds=freshness,
                )

        quote = self._latest_quote(
            instrument_id=instrument_id,
            signal_id=signal_id,
            event_time=event_time,
        )
        if quote is not None:
            timestamp = _aware_timestamp(quote.get("quote_timestamp"))
            return ReplayPriceEvidence(
                status="AVAILABLE",
                basis="POINT_IN_TIME_BID_ASK",
                source=str(quote.get("source") or "UNKNOWN"),
                event_timestamp=event_time,
                evidence_timestamp=timestamp,
                bid=float(quote.get("bid") or 0.0) or None,
                ask=float(quote.get("ask") or 0.0) or None,
                ltp=float(quote.get("ltp") or 0.0) or None,
                freshness_seconds=(
                    (event_time - timestamp).total_seconds()
                    if timestamp is not None
                    else None
                ),
            )

        candles = await self._load_option_candles(instrument_id)
        candle = _completed_candle_at(candles, event_time)
        if candle is None:
            return ReplayPriceEvidence(
                status="UNAVAILABLE",
                basis="NO_PRICE_EVIDENCE",
                source="UNAVAILABLE",
                event_timestamp=event_time,
                evidence_timestamp=None,
                reason="NO_POINT_IN_TIME_QUOTE_OR_COMPLETED_MARK",
            )
        return ReplayPriceEvidence(
            status="AVAILABLE",
            basis="HISTORICAL_COMPLETED_CANDLE_CLOSE_MARK",
            source=candle.source,
            event_timestamp=event_time,
            evidence_timestamp=candle.end_time,
            mark_price=float(candle.close),
            freshness_seconds=(event_time - candle.end_time).total_seconds(),
        )

    def _latest_quote(
        self,
        *,
        instrument_id: str,
        signal_id: str,
        event_time: datetime,
    ) -> dict[str, Any] | None:
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for quote in self.quotes:
            if str(quote.get("instrument_id") or "") != instrument_id:
                continue
            quote_signal = str(quote.get("strategy_signal_id") or "")
            if quote_signal and quote_signal != signal_id:
                continue
            timestamp = _aware_timestamp(quote.get("quote_timestamp"))
            if timestamp is None or timestamp > event_time:
                continue
            bid = float(quote.get("bid") or 0.0)
            ask = float(quote.get("ask") or 0.0)
            status = str(quote.get("status") or "")
            if status != "VALID" or bid <= 0 or ask <= 0 or ask < bid:
                continue
            age = (event_time - timestamp).total_seconds()
            if age > self.option_config.max_quote_age_seconds:
                continue
            candidates.append((timestamp, quote))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]
