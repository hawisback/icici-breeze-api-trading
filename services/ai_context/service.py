"""Compose read-only AI context from existing platform services."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from libs.contracts.models import generate_id

from libs.contracts.models import TradingMode, utc_now
from libs.market_time import IST
from services.ai_context.features import (
    compute_technicals,
    is_real_market_source,
    summarize_option_chain,
)


class AIContextService:
    """Expose objective evidence for an AI to interpret on its own."""

    def __init__(
        self,
        *,
        market_data_service: Any,
        historical_service: Any,
        option_chain_service: Any,
        portfolio_service: Any,
        broker_gateway: Any,
        broker_session_service: Any,
        risk_service: Any,
        order_management_service: Any | None = None,
    ) -> None:
        self.market_svc = market_data_service
        self.historical_svc = historical_service
        self.option_chain_svc = option_chain_service
        self.portfolio_svc = portfolio_service
        self.gateway_svc = broker_gateway
        self.session_svc = broker_session_service
        self.risk_svc = risk_service
        self.oms_svc = order_management_service

    @staticmethod
    def _dump(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return value

    @staticmethod
    def _age_seconds(timestamp: datetime | None) -> float | None:
        if timestamp is None:
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return round(max(0.0, (utc_now() - timestamp).total_seconds()), 3)

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _completed_real_candles(candles: list[Any], interval: str) -> list[Any]:
        """Do not expose synthetic, malformed or in-progress bars as evidence."""
        step = {"1m": 60, "5m": 300, "15m": 900}.get(interval)
        if step is None:
            return []
        now = utc_now()
        validated: dict[datetime, Any] = {}
        for candle in candles:
            if not is_real_market_source(getattr(candle, "source", None)):
                continue
            if getattr(candle, "interval", None) != interval:
                continue
            start, end = candle.start_time, candle.end_time
            if start.tzinfo is None or end.tzinfo is None:
                continue
            if abs((end - start).total_seconds() - step) > 5 or end > now:
                continue
            if not (0 < candle.low <= min(candle.open, candle.close)
                    <= max(candle.open, candle.close) <= candle.high):
                continue
            if candle.volume < 0:
                continue
            validated[start] = candle
        return sorted(validated.values(), key=lambda bar: bar.end_time)

    @staticmethod
    def _recent_session_missing_bars(candles: list[Any]) -> int:
        """Count holes within the latest session only, not overnight closures."""
        if not candles:
            return 0
        session = candles[-1].start_time.astimezone(IST).date()
        recent = [c for c in candles if c.start_time.astimezone(IST).date() == session]
        if len(recent) < 2:
            return 0
        step = (recent[-1].end_time - recent[-1].start_time).total_seconds()
        missing = 0
        for previous, current in zip(recent, recent[1:]):
            gap = (current.start_time - previous.end_time).total_seconds()
            if gap > step / 2:
                missing += max(1, round(gap / step))
        return missing

    async def get_candles(
        self,
        *,
        instrument_id: str = "INST-NIFTY-INDEX",
        interval: str = "5m",
        limit: int = 100,
    ) -> dict[str, Any]:
        try:
            candles = await self.historical_svc.get_candles(
                instrument_id=instrument_id,
                interval=interval,
                limit=limit,
                allow_synthetic_fallback=False,
            )
        except Exception as exc:
            return {
                "available": False,
                "reason": "MARKET_CANDLE_FETCH_FAILED",
                "error_type": type(exc).__name__,
                "instrument_id": instrument_id,
                "interval": interval,
                "requested_limit": limit,
                "candles": [],
                "generated_at": utc_now().isoformat(),
            }
        real = self._completed_real_candles(candles, interval)
        if not real:
            return {
                "available": False,
                "reason": "NO_REAL_MARKET_CANDLES",
                "instrument_id": instrument_id,
                "interval": interval,
                "requested_limit": limit,
                "candles": [],
                "generated_at": utc_now().isoformat(),
            }
        ordered = sorted(real, key=lambda candle: candle.end_time)[-limit:]
        latest = ordered[-1]
        return {
            "available": True,
            "instrument_id": instrument_id,
            "interval": interval,
            "count": len(ordered),
            "data_through": latest.end_time.isoformat(),
            "age_seconds": self._age_seconds(latest.end_time),
            "sources": sorted({str(c.source).upper() for c in ordered}),
            "missing_recent_session_bars": self._recent_session_missing_bars(ordered),
            "candles": [c.model_dump(mode="json") for c in ordered],
            "generated_at": utc_now().isoformat(),
        }

    async def get_technicals(
        self,
        *,
        instrument_id: str = "INST-NIFTY-INDEX",
        interval: str = "5m",
        limit: int = 200,
    ) -> dict[str, Any]:
        try:
            candles = await self.historical_svc.get_candles(
                instrument_id=instrument_id,
                interval=interval,
                limit=limit,
                allow_synthetic_fallback=False,
            )
        except Exception as exc:
            return {
                "available": False,
                "reason": "MARKET_CANDLE_FETCH_FAILED",
                "error_type": type(exc).__name__,
                "instrument_id": instrument_id,
                "interval": interval,
                "metrics": compute_technicals([]),
                "calculated_at": utc_now().isoformat(),
            }
        real = self._completed_real_candles(candles, interval)
        if not real:
            return {
                "available": False,
                "reason": "NO_REAL_MARKET_CANDLES",
                "instrument_id": instrument_id,
                "interval": interval,
                "metrics": compute_technicals([]),
                "calculated_at": utc_now().isoformat(),
            }
        ordered = sorted(real, key=lambda candle: candle.end_time)
        latest = ordered[-1]
        return {
            "available": True,
            "instrument_id": instrument_id,
            "interval": interval,
            "data_through": latest.end_time.isoformat(),
            "age_seconds": self._age_seconds(latest.end_time),
            "sources": sorted({str(c.source).upper() for c in ordered}),
            "missing_recent_session_bars": self._recent_session_missing_bars(ordered),
            "metrics": compute_technicals(ordered),
            "calculated_at": utc_now().isoformat(),
        }

    async def get_options(
        self,
        *,
        underlying: str = "NIFTY",
        expiry: str | None = None,
        strike_window: int = 10,
    ) -> dict[str, Any]:
        try:
            chain = await self.option_chain_svc.get_chain(
                underlying=underlying,
                expiry=expiry,
                provider="kite",
            )
        except Exception as exc:
            chain = {
                "source": "UNAVAILABLE",
                "expiry": expiry,
                "strikes": [],
                "capabilities": {
                    "strategy_a_rejection_reason": "OPTION_CHAIN_FETCH_FAILED",
                },
                "error_type": type(exc).__name__,
            }
        source = str(chain.get("source") or "UNAVAILABLE").upper()
        if not is_real_market_source(source):
            capabilities = chain.get("capabilities", {}) or {}
            reason = (
                capabilities.get("strategy_a_rejection_reason")
                or "NO_REAL_OPTION_CHAIN"
            )
            return {
                "available": False,
                "reason": reason,
                "underlying": underlying.upper(),
                "source": source,
                "expiry": chain.get("expiry"),
                "available_expiries": chain.get("available_expiries", []),
                "error_type": chain.get("error_type"),
                "summary": summarize_option_chain({**chain, "strikes": []}),
                "strikes": [],
                "generated_at": utc_now().isoformat(),
            }

        strikes = sorted(
            chain.get("strikes", []) or [],
            key=lambda row: float(row.get("strike") or 0),
        )
        atm = float(chain.get("atm_strike") or 0)
        if strikes and strike_window >= 0:
            nearest_index = min(
                range(len(strikes)),
                key=lambda i: abs(float(strikes[i].get("strike") or 0) - atm),
            )
            start = max(0, nearest_index - strike_window)
            end = min(len(strikes), nearest_index + strike_window + 1)
            strikes = strikes[start:end]

        filtered = dict(chain)
        filtered["strikes"] = strikes
        # PCR is meaningful only for the advertised strike window. Capture
        # quote coverage and exchange-observed time for that same exact window.
        contracts = [
            row[side]
            for row in strikes
            for side in ("call", "put")
            if row.get(side)
        ]
        expected_contracts = len(strikes) * 2
        market_times = [
            self._parse_timestamp(contract.get("market_timestamp"))
            for contract in contracts
        ]
        times_complete = bool(contracts) and all(t is not None for t in market_times)
        captured = self._parse_timestamp(
            chain.get("captured_at") or chain.get("timestamp")
        )
        market_observed = (
            min(market_times) if times_complete else None
        )
        atm_row = (
            min(strikes, key=lambda row: abs(float(row.get("strike") or 0) - atm))
            if strikes else {}
        )
        atm_contracts = [atm_row.get(side) for side in ("call", "put")]
        atm_spreads: list[float] = []
        atm_quote_valid = len(atm_contracts) == 2 and all(atm_contracts)
        for contract in atm_contracts:
            if not contract:
                continue
            bid, ask = float(contract.get("bid") or 0), float(contract.get("ask") or 0)
            if bid <= 0 or ask < bid:
                atm_quote_valid = False
                continue
            atm_spreads.append((ask - bid) / ((ask + bid) / 2) * 100)
        quote_coverage = (len(contracts) / expected_contracts) if expected_contracts else 0.0
        market_age = self._age_seconds(market_observed)
        return {
            "available": True,
            "underlying": chain.get("underlying", underlying.upper()),
            "source": source,
            "expiry": chain.get("expiry"),
            "available_expiries": chain.get("available_expiries", []),
            "spot_price": chain.get("spot_price"),
            "atm_strike": chain.get("atm_strike"),
            "captured_at": captured.isoformat() if captured else None,
            "age_seconds": self._age_seconds(captured),
            "market_timestamp": market_observed.isoformat() if market_observed else None,
            "market_data_age_seconds": market_age,
            "quote_timestamps_complete": times_complete,
            "quote_coverage_ratio": round(quote_coverage, 4),
            "selected_contract_count": len(contracts),
            "expected_contract_count": expected_contracts,
            "pcr_scope": f"ATM_PLUS_MINUS_{strike_window}_STRIKES",
            "atm_quote_valid": bool(atm_quote_valid),
            "atm_max_spread_pct": round(max(atm_spreads), 3) if len(atm_spreads) == 2 else None,
            "summary": summarize_option_chain(filtered),
            "strikes": strikes,
            "capabilities": chain.get("capabilities", {}),
            "requested_contract_count": chain.get("requested_contract_count"),
            "quoted_contract_count": chain.get("quoted_contract_count"),
            "partial_quote_coverage": chain.get("partial_quote_coverage"),
            "generated_at": utc_now().isoformat(),
        }

    async def get_account_context(self) -> dict[str, Any]:
        """Read-only portfolio evidence; never equate local flat with broker flat."""
        positions = await self.portfolio_svc.get_positions()
        pnl = await self.portfolio_svc.get_pnl_summary()
        risk_mode = await self.risk_svc.get_system_mode()
        sessions = await self.session_svc.get_all_session_statuses()
        execution_session = await self.session_svc.get_session_status()

        broker_live: dict[str, Any] = {
            "available": False,
            "positions": [],
            "funds": None,
            "error": None,
        }
        if execution_session.get("connected"):
            try:
                broker_positions = await self.gateway_svc.get_positions(
                    mode=TradingMode.LIVE
                )
                funds = await self.gateway_svc.get_funds(mode=TradingMode.LIVE)
                broker_live = {
                    "available": True,
                    "positions": [self._dump(p) for p in broker_positions],
                    "funds": self._dump(funds),
                    "error": None,
                }
            except Exception as exc:
                broker_live["error"] = type(exc).__name__

        order_book_available = self.oms_svc is not None
        pending_orders: list[dict[str, Any]] = []
        order_error = None
        if order_book_available:
            try:
                orders = await self.oms_svc.list_orders(limit=500)
                terminal = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "FAILED_SAFE"}
                for order in orders:
                    state = getattr(order, "status", None)
                    state = getattr(state, "value", state)
                    if str(state).upper() not in terminal:
                        pending_orders.append(self._dump(order))
            except Exception as exc:
                order_book_available = False
                order_error = type(exc).__name__

        def nonzero(values: list[Any]) -> list[Any]:
            return [
                p for p in values
                if int((p.get("quantity", 0) if isinstance(p, dict)
                        else getattr(p, "quantity", 0)) or 0) != 0
            ]

        local_open = nonzero(positions)
        broker_open = nonzero(broker_live["positions"])
        blockers: list[str] = []
        if risk_mode.value != "NORMAL":
            blockers.append("SYSTEM_MODE_NOT_NORMAL")
        if not execution_session.get("connected"):
            blockers.append("EXECUTION_BROKER_DISCONNECTED")
        if not broker_live["available"]:
            blockers.append("BROKER_POSITIONS_UNVERIFIED")
        if local_open:
            blockers.append("LOCAL_POSITION_OPEN")
        if broker_open:
            blockers.append("BROKER_POSITION_OPEN")
        if bool(local_open) != bool(broker_open):
            blockers.append("BROKER_LOCAL_POSITION_MISMATCH")
        if not order_book_available:
            blockers.append("ORDER_BOOK_UNVERIFIED")
        if pending_orders:
            blockers.append("PENDING_OR_UNKNOWN_ORDERS")

        return {
            "as_of": utc_now().isoformat(),
            "risk_system_mode": risk_mode.value,
            "broker_sessions": sessions,
            "execution_session": execution_session,
            "local_portfolio": {
                "positions": [self._dump(p) for p in positions],
                "pnl": pnl,
            },
            "broker_live": broker_live,
            "order_book": {
                "available": order_book_available,
                "error": order_error,
                "pending_count": len(pending_orders),
                "pending_orders": pending_orders,
            },
            "local_open_positions_count": len(local_open),
            "broker_open_positions_count": len(broker_open),
            "entry_context_ready": not blockers,
            "entry_blockers": blockers,
            "note": "Entry context is evidence only; final live authorization remains in the risk/live-gate execution pipeline.",
        }

    async def get_data_quality(self) -> dict[str, Any]:
        """A recent response is not the same as a recent exchange observation."""
        quote = self.market_svc.get_latest_quote("INST-NIFTY-INDEX")
        quote_source = str(getattr(quote, "source", "") or "").upper() if quote else None
        quote_real = bool(quote and is_real_market_source(quote_source))
        quote_age = self._age_seconds(quote.timestamp) if quote_real else None
        future_quote = bool(
            quote_real and (quote.timestamp - utc_now()).total_seconds() > 1.0
        )
        quote_ready = bool(
            quote_real and quote.last_price > 0 and quote_age is not None
            and quote_age < 10.0 and not future_quote
        )
        sessions = await self.session_svc.get_all_session_statuses()
        feed = self.market_svc.get_feed_status()
        health_fn = getattr(self.market_svc, "get_execution_feed_health", None)
        if callable(health_fn):
            execution_feed = health_fn(max_age_seconds=10.0)
        else:
            execution_feed = {
                "healthy": False,
                "reasons": ["EXECUTION_FEED_HEALTH_UNAVAILABLE"],
            }
        return {
            "checked_at": utc_now().isoformat(),
            "market_feed": feed,
            "execution_feed": execution_feed,
            "nifty_quote": {
                "available": quote_real,
                "source": quote_source,
                "exchange_timestamp": quote.timestamp.isoformat() if quote_real else None,
                "age_seconds": quote_age,
                "future_timestamp": future_quote,
                "fresh": quote_ready,
            },
            "quote_ready": quote_ready and bool(execution_feed.get("healthy")),
            "broker_sessions": sessions,
        }

    @staticmethod
    def _session_context(expiry: str | None) -> dict[str, Any]:
        now_ist = utc_now().astimezone(IST)
        open_dt = datetime.combine(now_ist.date(), time(9, 15), tzinfo=IST)
        close_dt = datetime.combine(now_ist.date(), time(15, 30), tzinfo=IST)
        minutes_since_open = max(0, int((now_ist - open_dt).total_seconds() // 60))
        minutes_to_close = max(0, int((close_dt - now_ist).total_seconds() // 60))
        days_to_expiry = None
        if expiry:
            try:
                days_to_expiry = (datetime.fromisoformat(expiry).date() - now_ist.date()).days
            except ValueError:
                pass
        return {
            "time_ist": now_ist.isoformat(),
            "regular_session": now_ist.weekday() < 5 and open_dt <= now_ist <= close_dt,
            "calendar_authoritative": False,  # Weekdays only; feed verification catches closed-market stale quotes.
            "minutes_since_open": minutes_since_open,
            "minutes_to_close": minutes_to_close,
            "nearest_expiry": expiry,
            "days_to_expiry": days_to_expiry,
        }

    async def get_snapshot(self) -> dict[str, Any]:
        quote = self.market_svc.get_latest_quote("INST-NIFTY-INDEX")
        quote_source = str(getattr(quote, "source", "") or "").upper() if quote else None
        if quote and is_real_market_source(quote_source):
            underlying: dict[str, Any] = {
                "available": True,
                **quote.model_dump(mode="json"),
                "age_seconds": self._age_seconds(quote.timestamp),
            }
        else:
            underlying = {
                "available": False,
                "reason": "NO_REAL_MARKET_QUOTE",
                "source": quote_source,
            }

        technicals: dict[str, Any] = {}
        for interval in ("1m", "5m", "15m"):
            technicals[interval] = await self.get_technicals(
                interval=interval,
                limit=200,
            )

        options = await self.get_options(strike_window=5)
        local_positions = await self.portfolio_svc.get_positions()
        pnl = await self.portfolio_svc.get_pnl_summary()
        quality = await self.get_data_quality()

        return {
            "schema_version": "1.0",
            "as_of": utc_now().isoformat(),
            "principle": "OBJECTIVE_DATA_ONLY_AI_INTERPRETS",
            "underlying": underlying,
            "technicals": technicals,
            "options": {
                "available": options.get("available", False),
                "reason": options.get("reason"),
                "source": options.get("source"),
                "expiry": options.get("expiry"),
                "partial_quote_coverage": options.get("partial_quote_coverage"),
                "summary": options.get("summary"),
                "captured_at": options.get("captured_at"),
                "age_seconds": options.get("age_seconds"),
                "market_timestamp": options.get("market_timestamp"),
                "market_data_age_seconds": options.get("market_data_age_seconds"),
            },
            "session": self._session_context(options.get("expiry")),
            "account": {
                "open_positions_count": len(
                    [p for p in local_positions if int(p.quantity) != 0]
                ),
                "positions": [
                    p.model_dump(mode="json")
                    for p in local_positions
                    if int(p.quantity) != 0
                ],
                "pnl": pnl,
            },
            "data_quality": quality,
        }
