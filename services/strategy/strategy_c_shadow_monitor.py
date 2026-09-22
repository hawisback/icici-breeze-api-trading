"""Passive live evidence capture for frozen Strategy C candidate V1.

The monitor is intentionally isolated from OMS and auto-trade state. It:
- fetches completed native 5m and 1m futures candles,
- replays the frozen Strategy C observer through the current timestamp,
- captures real option-chain selector inputs at new candidate entries,
- records one selected-contract quote per completed underlying minute,
- records the frozen underlying lifecycle outcome when resolved.

It never creates an ActiveTrade or OrderIntent and never calls the broker OMS.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from libs.contracts.models import generate_id
from services.historical.strategy_a_data_audit import IST
from services.historical.strategy_c_candidate_manifest import (
    CANDIDATE_ID,
    _spec_fingerprint,
)
from services.historical.strategy_c_forward_validation import FREEZE_DATE
from services.historical.strategy_c_shadow_observer import replay_strategy_c_to_as_of
from services.strategy.models import (
    AutoTradingMode,
    DecisionLogEntry,
    TradeDirection,
)


logger = logging.getLogger(__name__)
RUNTIME_KEY = "strategy_c_shadow_v1"
ALLOWED_MARKET_SOURCES = {"BREEZE", "KITE", "LIVE"}
MAX_PAPER_ENTRY_LATENCY_SECONDS = 300.0
MAX_PAPER_EXIT_QUOTE_LATENCY_SECONDS = 120.0


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo and value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
        except ValueError:
            return None
    return None


class StrategyCShadowMonitor:
    """Research sidecar for post-freeze Strategy C forward evidence."""

    def __init__(
        self,
        *,
        repository: Any,
        historical_service: Any = None,
        option_chain_service: Any = None,
        market_data_service: Any = None,
        contract_selector: Any = None,
        risk_sizer: Any = None,
        risk_config: Any = None,
    ) -> None:
        self.repo = repository
        self.hist_svc = historical_service
        self.chain_svc = option_chain_service
        self.mkt_svc = market_data_service
        self.contract_selector = contract_selector
        self.risk_sizer = risk_sizer
        self.risk_config = risk_config
        self.initialized = False
        self.disabled_reason: str | None = None
        self.runtime: dict[str, Any] = self._empty_runtime()

    @staticmethod
    def _empty_runtime() -> dict[str, Any]:
        return {
            "candidate_id": CANDIDATE_ID,
            "candidate_spec_fingerprint": _spec_fingerprint(),
            "seen_signal_ids": [],
            "resolved_signal_ids": [],
            "paper_closed_signal_ids": [],
            "tracked": {},
        }

    def refresh_dependencies(
        self,
        *,
        contract_selector: Any = None,
        risk_sizer: Any = None,
        risk_config: Any = None,
    ) -> None:
        if contract_selector is not None:
            self.contract_selector = contract_selector
        if risk_sizer is not None:
            self.risk_sizer = risk_sizer
        if risk_config is not None:
            self.risk_config = risk_config

    async def initialize(self) -> None:
        self.initialized = True
        if not self.repo or not hasattr(self.repo, "get_runtime"):
            return
        try:
            stored = await self.repo.get_runtime(RUNTIME_KEY)
        except Exception:
            logger.exception("Strategy C shadow runtime restore failed")
            return
        if not stored:
            return
        expected = _spec_fingerprint()
        if stored.get("candidate_spec_fingerprint") != expected:
            self.disabled_reason = "FROZEN_CANDIDATE_FINGERPRINT_MISMATCH"
            logger.error(
                "Strategy C shadow disabled: stored fingerprint %r != frozen %r",
                stored.get("candidate_spec_fingerprint"),
                expected,
            )
            return
        self.runtime = {
            **self._empty_runtime(),
            **stored,
            "seen_signal_ids": list(stored.get("seen_signal_ids") or []),
            "resolved_signal_ids": list(stored.get("resolved_signal_ids") or []),
            "paper_closed_signal_ids": list(stored.get("paper_closed_signal_ids") or []),
            "tracked": dict(stored.get("tracked") or {}),
        }

    async def _save_runtime(self) -> None:
        if self.repo and hasattr(self.repo, "save_runtime"):
            await self.repo.save_runtime(self.runtime, RUNTIME_KEY)

    async def _log(self, *, timestamp: datetime, message: str, details: dict[str, Any]) -> None:
        if not self.repo or not hasattr(self.repo, "save_decision_log"):
            return
        entry = DecisionLogEntry(
            id=f"TEL-C-{generate_id()}",
            timestamp=timestamp,
            category="STRATEGY_C_SHADOW",
            strategy=CANDIDATE_ID,
            message=message,
            details=details,
        )
        await self.repo.save_decision_log(entry)

    async def _native_candles(
        self,
        instrument_id: str,
        interval: str,
        *,
        now: datetime,
    ) -> list[Any]:
        if not self.hist_svc:
            return []
        candles = await self.hist_svc.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            limit=500,
            requested_source="MIXED",
            allow_provider_fallback=True,
            allow_synthetic_fallback=False,
        )
        expected_seconds = {"1m": 60, "5m": 300}[interval]
        return sorted(
            {
                candle.start_time: candle
                for candle in candles
                if candle.source in ALLOWED_MARKET_SOURCES
                and candle.interval == interval
                and candle.end_time <= now
                and abs((candle.end_time - candle.start_time).total_seconds() - expected_seconds) <= 5
            }.values(),
            key=lambda candle: candle.start_time,
        )

    async def _get_chain(self) -> dict[str, Any]:
        if not self.chain_svc:
            return {"source": "UNAVAILABLE", "strikes": []}
        try:
            chain = await self.chain_svc.get_chain(underlying="NIFTY")
        except Exception:
            logger.exception("Strategy C shadow option-chain retrieval failed")
            return {"source": "UNAVAILABLE", "strikes": []}
        if str(chain.get("source", "")).upper() not in ALLOWED_MARKET_SOURCES:
            return {**chain, "source": "UNAVAILABLE"}
        return chain

    @staticmethod
    def _direction(value: str) -> TradeDirection:
        return TradeDirection.BULLISH if value == "CALL" else TradeDirection.BEARISH

    @staticmethod
    def _chain_candidates(
        chain: dict[str, Any],
        inspected: list[dict[str, Any]],
        direction: str,
    ) -> list[dict[str, Any]]:
        inspected_by_strike = {
            float(row["strike"]): row
            for row in inspected
            if row.get("strike") is not None
        }
        side = "call" if direction == "CALL" else "put"
        rows: list[dict[str, Any]] = []
        for strike_row in chain.get("strikes", []):
            try:
                strike = float(strike_row.get("strike"))
            except (TypeError, ValueError):
                continue
            if inspected_by_strike and strike not in inspected_by_strike:
                continue
            leg = strike_row.get(side) or {}
            inspected_row = inspected_by_strike.get(strike, {})
            rows.append({
                "strike": strike,
                "right": "CE" if direction == "CALL" else "PE",
                "instrument_id": leg.get("instrument_id"),
                "instrument_token": leg.get("instrument_token") or leg.get("token"),
                "expiry": leg.get("expiry") or strike_row.get("expiry") or chain.get("expiry"),
                "bid": float(leg.get("bid", 0) or 0),
                "ask": float(leg.get("ask", 0) or 0),
                "ltp": float(leg.get("ltp", 0) or 0),
                "delta": (
                    leg.get("delta")
                    if leg.get("delta") is not None
                    else (leg.get("greeks") or {}).get("delta")
                ),
                "gamma": (
                    leg.get("gamma")
                    if leg.get("gamma") is not None
                    else (leg.get("greeks") or {}).get("gamma")
                ),
                "open_interest": int(leg.get("open_interest", 0) or 0),
                "volume": int(leg.get("volume", 0) or 0),
                "lot_size": int(leg.get("lot_size", 0) or 0),
                "quote_timestamp": leg.get("quote_timestamp") or leg.get("timestamp") or chain.get("timestamp"),
                "selector_status": inspected_row.get("status"),
            })
        return rows

    async def _capture_entry(
        self,
        row: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> dict[str, Any]:
        signal_id = str(row["candidate_signal_id"])
        chain = await self._get_chain()
        selected = None
        inspected: list[dict[str, Any]] = []
        reason = "CONTRACT_SELECTOR_UNAVAILABLE"
        if self.contract_selector is not None:
            selected, inspected, reason = self.contract_selector.select_contract(
                self._direction(row["direction"]),
                underlying_price=float(row["entry_price"]),
                option_chain=chain,
                as_of=observed_at,
                strategy_a=True,
            )

        selected_payload = selected.model_dump(mode="json") if selected else None
        chain_rows = self._chain_candidates(chain, inspected, row["direction"])
        captured_at = observed_at.isoformat()
        if self.repo and hasattr(self.repo, "save_option_chain_snapshot"):
            await self.repo.save_option_chain_snapshot({
                "snapshot_id": f"OPTCHAIN-C-{generate_id()}",
                "strategy_signal_id": signal_id,
                "captured_at": captured_at,
                "selector_timestamp": captured_at,
                "chain_snapshot_timestamp": chain.get("captured_at") or chain.get("timestamp") or captured_at,
                "signal_timestamp": row["entry_time"],
                "strategy": CANDIDATE_ID,
                "execution_mode": AutoTradingMode.PAPER.value,
                "direction": row["direction"],
                "spot_price": float(row["entry_price"]),
                "expiry": chain.get("expiry"),
                "source": chain.get("source", "UNAVAILABLE"),
                "selector_candidates": inspected,
                "chain_candidates": chain_rows,
                "selected_contract": selected_payload,
                "selector_result": "SELECTED" if selected else "REJECTED",
                "rejection_reason": reason,
            })

        risk_per_lot = None
        risk_method = None
        capital_per_lot = None
        entry_executable_price = None
        paper_status = "SKIPPED"
        paper_rejection_reason = reason or "CONTRACT_SELECTION_REJECTED"
        lots = 0
        quantity = 0
        risk_budget = None
        signal_time = datetime.fromisoformat(row["entry_time"].replace("Z", "+00:00"))
        signal_latency_seconds = max(0.0, (observed_at - signal_time).total_seconds())
        lifecycle_status = str((row.get("lifecycle") or {}).get("status") or "UNKNOWN")

        if selected is not None:
            slippage = float(getattr(self.risk_config, "paper_slippage_points", 0.0) or 0.0)
            entry_executable_price = round(float(selected.ask_price) + slippage, 6)
            capital_per_lot = round(entry_executable_price * int(selected.lot_size), 2)
            if self.risk_sizer is not None:
                try:
                    sizing = self.risk_sizer.size(
                        underlying_entry=float(row["entry_price"]),
                        underlying_stop=float(row["initial_stop"]),
                        option_delta=selected.delta,
                        lot_size=int(selected.lot_size),
                        option_entry=entry_executable_price,
                        account_equity=float(getattr(self.risk_config, "account_equity", 0.0) or 0.0) or None,
                    )
                    risk_per_lot = sizing.option_loss_per_lot
                    risk_method = sizing.method
                    risk_budget = sizing.risk_budget
                    lots = int(sizing.lots)
                    quantity = int(sizing.quantity)
                    paper_rejection_reason = sizing.rejection_reason
                except ValueError:
                    risk_per_lot = None
                    risk_method = "UNAVAILABLE"
                    paper_rejection_reason = "OPTION_RISK_UNAVAILABLE"
            else:
                paper_rejection_reason = "RISK_SIZER_UNAVAILABLE"

            if lifecycle_status != "OPEN":
                paper_rejection_reason = "SIGNAL_ALREADY_RESOLVED_WHEN_OBSERVED"
            elif signal_latency_seconds > MAX_PAPER_ENTRY_LATENCY_SECONDS:
                paper_rejection_reason = "STALE_SIGNAL_FOR_PAPER_ENTRY"
            elif lots < 1 or quantity < 1:
                paper_rejection_reason = paper_rejection_reason or "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"
            else:
                paper_status = "OPEN"
                paper_rejection_reason = None

        tracked = {
            "signal_id": signal_id,
            "entry_time": row["entry_time"],
            "direction": row["direction"],
            "underlying_entry_price": row["entry_price"],
            "underlying_stop": row["initial_stop"],
            "selected_contract": selected_payload,
            "entry_raw_ask": float(selected.ask_price) if selected is not None else None,
            "entry_executable_price": entry_executable_price,
            "entry_observed_at": observed_at.isoformat(),
            "signal_to_observation_seconds": signal_latency_seconds,
            "capital_per_lot": capital_per_lot,
            "estimated_option_loss_per_lot": risk_per_lot,
            "option_risk_method": risk_method,
            "risk_budget": risk_budget,
            "paper_status": paper_status,
            "paper_rejection_reason": paper_rejection_reason,
            "paper_lots": lots,
            "paper_quantity": quantity,
            "last_quote_bar_end": None,
            "entry_selector_result": "SELECTED" if selected else "REJECTED",
            "entry_rejection_reason": reason,
        }
        await self._log(
            timestamp=observed_at,
            message="CANDIDATE_SIGNAL_OBSERVED",
            details={
                "signal": row,
                "selector_result": tracked["entry_selector_result"],
                "rejection_reason": reason,
                "selected_contract": selected_payload,
                "capital_per_lot": capital_per_lot,
                "estimated_option_loss_per_lot": risk_per_lot,
                "option_risk_method": risk_method,
                "signal_to_observation_seconds": signal_latency_seconds,
                "paper_status": paper_status,
                "paper_rejection_reason": paper_rejection_reason,
                "paper_lots": lots,
                "paper_quantity": quantity,
                "risk_budget": risk_budget,
            },
        )
        if paper_status == "OPEN":
            if self.repo and hasattr(self.repo, "save_execution_ledger"):
                await self.repo.save_execution_ledger({
                    "ledger_id": f"PAPER-C-BUY-{generate_id()}",
                    "trade_id": f"PAPER-C:{signal_id}",
                    "side": "BUY",
                    "timestamp": observed_at.isoformat(),
                    "raw_bid": float(selected.bid_price),
                    "raw_ask": float(selected.ask_price),
                    "raw_ltp": float(selected.ltp),
                    "executable_price": entry_executable_price,
                    "slippage_points": float(getattr(self.risk_config, "paper_slippage_points", 0.0) or 0.0),
                    "quantity": quantity,
                    "source": chain.get("source", "UNKNOWN"),
                    "cost_assumption_version": getattr(self.risk_config, "paper_cost_assumption_version", "unknown"),
                    "reason": "STRATEGY_C_PAPER_ENTRY",
                })
            await self._log(
                timestamp=observed_at,
                message="PAPER_ENTRY_OPENED",
                details={
                    "signal_id": signal_id,
                    "selected_contract": selected_payload,
                    "lots": lots,
                    "quantity": quantity,
                    "entry_raw_ask": float(selected.ask_price),
                    "entry_executable_price": entry_executable_price,
                    "risk_budget": risk_budget,
                    "estimated_option_loss_per_lot": risk_per_lot,
                    "capital_per_lot": capital_per_lot,
                    "execution_mode": AutoTradingMode.PAPER.value,
                    "broker_called": False,
                },
            )
        return tracked

    async def _quote_for_contract(
        self,
        selected: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        instrument_id = selected.get("instrument_id")
        if not instrument_id:
            return {"status": "UNAVAILABLE", "reason": "MISSING_INSTRUMENT_ID"}

        if self.mkt_svc is not None:
            quote = self.mkt_svc.get_latest_quote(instrument_id)
            if quote is not None:
                freshness = max(0.0, (now - quote.timestamp).total_seconds())
                result = {
                    "instrument_id": instrument_id,
                    "quote_timestamp": quote.timestamp.isoformat(),
                    "source": getattr(quote, "source", "UNKNOWN"),
                    "freshness_seconds": freshness,
                    "bid": float(getattr(quote, "best_bid", 0) or 0),
                    "ask": float(getattr(quote, "best_ask", 0) or 0),
                    "ltp": float(getattr(quote, "last_price", 0) or 0),
                    "volume": int(getattr(quote, "volume", 0) or 0),
                    "open_interest": int(getattr(quote, "open_interest", 0) or 0),
                }
                if (
                    result["source"] in ALLOWED_MARKET_SOURCES
                    and freshness <= 30
                    and result["bid"] > 0
                    and result["ask"] > 0
                    and result["bid"] <= result["ask"]
                ):
                    return {**result, "status": "VALID", "reason": None}

        if self.chain_svc is not None:
            try:
                chain = await self.chain_svc.get_chain(
                    underlying="NIFTY",
                    expiry=selected.get("expiry"),
                )
            except Exception:
                logger.exception("Strategy C shadow selected-contract quote refresh failed")
                chain = {}
            source = str(chain.get("source", "UNKNOWN")).upper()
            raw_time = chain.get("captured_at") or chain.get("timestamp")
            quote_time = _aware(raw_time) or now
            freshness = max(0.0, (now - quote_time).total_seconds())
            for strike_row in chain.get("strikes", []):
                for side in ("call", "put"):
                    leg = strike_row.get(side) or {}
                    if leg.get("instrument_id") != instrument_id:
                        continue
                    bid = float(leg.get("bid", 0) or 0)
                    ask = float(leg.get("ask", 0) or 0)
                    result = {
                        "instrument_id": instrument_id,
                        "quote_timestamp": quote_time.isoformat(),
                        "source": source,
                        "freshness_seconds": freshness,
                        "bid": bid,
                        "ask": ask,
                        "ltp": float(leg.get("ltp", 0) or 0),
                        "volume": int(leg.get("volume", 0) or 0),
                        "open_interest": int(leg.get("open_interest", 0) or 0),
                    }
                    if (
                        source in ALLOWED_MARKET_SOURCES
                        and freshness <= 30
                        and bid > 0
                        and ask > 0
                        and bid <= ask
                    ):
                        return {**result, "status": "VALID", "reason": None}
                    return {**result, "status": "INVALID", "reason": "STALE_OR_NON_EXECUTABLE_QUOTE"}

        return {
            "instrument_id": instrument_id,
            "quote_timestamp": now.isoformat(),
            "source": "UNAVAILABLE",
            "freshness_seconds": None,
            "bid": 0.0,
            "ask": 0.0,
            "ltp": 0.0,
            "volume": 0,
            "open_interest": 0,
            "status": "UNAVAILABLE",
            "reason": "NO_FRESH_SELECTED_CONTRACT_QUOTE",
        }

    async def _record_quote(
        self,
        signal_id: str,
        tracked: dict[str, Any],
        *,
        now: datetime,
        quote_bar_end: str,
    ) -> dict[str, Any] | None:
        selected = tracked.get("selected_contract")
        if not selected:
            return None
        quote = await self._quote_for_contract(selected, now=now)
        if self.repo and hasattr(self.repo, "save_option_quote"):
            await self.repo.save_option_quote({
                "quote_id": f"OPTQUOTE-C-{generate_id()}",
                "trade_id": f"SHADOW-C:{signal_id}",
                "strategy_signal_id": signal_id,
                **quote,
                "underlying_quote_bar_end": quote_bar_end,
            })
        tracked["last_quote_bar_end"] = quote_bar_end
        tracked["last_quote"] = quote
        return quote

    async def observe(
        self,
        *,
        active_futures_instrument: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if not self.initialized:
            await self.initialize()
        if self.disabled_reason:
            return {"status": "DISABLED", "reason": self.disabled_reason}
        local_day = now.astimezone(IST).date()
        if local_day <= FREEZE_DATE:
            return {
                "status": "WAITING_FOR_POST_FREEZE_SESSION",
                "freeze_date": FREEZE_DATE.isoformat(),
                "current_session_date": local_day.isoformat(),
            }
        if not active_futures_instrument or not self.hist_svc:
            return {"status": "NO_ACTIVE_FUTURES_DATA"}

        try:
            futures_5m = await self._native_candles(active_futures_instrument, "5m", now=now)
            futures_1m = await self._native_candles(active_futures_instrument, "1m", now=now)
        except Exception:
            logger.exception("Strategy C shadow native futures retrieval failed")
            return {"status": "DATA_RETRIEVAL_FAILED"}

        if not futures_5m or not futures_1m:
            return {
                "status": "NATIVE_FUTURES_UNAVAILABLE",
                "futures_5m": len(futures_5m),
                "futures_1m": len(futures_1m),
            }

        report = replay_strategy_c_to_as_of(
            futures_5m,
            futures_1m,
            as_of=now,
        )
        latest_1m_end = futures_1m[-1].end_time.isoformat()
        seen = set(self.runtime.get("seen_signal_ids") or [])
        resolved = set(self.runtime.get("resolved_signal_ids") or [])
        tracked_map = self.runtime.setdefault("tracked", {})
        changed = False

        for row in report.get("candidate_entries") or []:
            signal_id = str(row["candidate_signal_id"])
            if signal_id not in seen:
                tracked_map[signal_id] = await self._capture_entry(row, observed_at=now)
                seen.add(signal_id)
                changed = True

            tracked = tracked_map.get(signal_id) or {}
            lifecycle = row.get("lifecycle") or {}
            if (
                lifecycle.get("status") == "OPEN"
                and tracked.get("selected_contract")
                and tracked.get("last_quote_bar_end") != latest_1m_end
            ):
                await self._record_quote(
                    signal_id,
                    tracked,
                    now=now,
                    quote_bar_end=latest_1m_end,
                )
                changed = True

            if lifecycle.get("status") == "RESOLVED" and signal_id not in resolved:
                final_quote = None
                if tracked.get("selected_contract"):
                    final_quote = await self._record_quote(
                        signal_id,
                        tracked,
                        now=now,
                        quote_bar_end=latest_1m_end,
                    )
                await self._log(
                    timestamp=now,
                    message="CANDIDATE_UNDERLYING_RESOLVED",
                    details={
                        "signal_id": signal_id,
                        "underlying_lifecycle": lifecycle,
                        "selected_contract": tracked.get("selected_contract"),
                        "entry_executable_price": tracked.get("entry_executable_price"),
                        "final_observed_option_quote": final_quote,
                        "note": "Observed option quote is evidence only; no order or synthetic fill was created.",
                    },
                )
                resolved.add(signal_id)
                changed = True

        if changed:
            self.runtime["seen_signal_ids"] = sorted(seen)
            self.runtime["resolved_signal_ids"] = sorted(resolved)
            await self._save_runtime()

        return {
            "status": report.get("status"),
            "candidate_id": CANDIDATE_ID,
            "candidate_spec_fingerprint": _spec_fingerprint(),
            "native_5m_candles": len(futures_5m),
            "native_1m_candles": len(futures_1m),
            "raw_entries_today": len(report.get("raw_entries") or []),
            "candidate_entries_today": len(report.get("candidate_entries") or []),
            "seen_candidate_signals": len(seen),
            "resolved_candidate_signals": len(resolved),
            "active_raw_trade": report.get("active_raw_trade"),
            "broker_called": False,
            "orders_created": False,
        }
