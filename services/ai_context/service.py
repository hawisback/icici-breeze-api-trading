"""Compose read-only AI context from existing platform services."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

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
    ) -> None:
        self.market_svc = market_data_service
        self.historical_svc = historical_service
        self.option_chain_svc = option_chain_service
        self.portfolio_svc = portfolio_service
        self.gateway_svc = broker_gateway
        self.session_svc = broker_session_service
        self.risk_svc = risk_service

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

    async def get_candles(
        self,
        *,
        instrument_id: str = "INST-NIFTY-INDEX",
        interval: str = "5m",
        limit: int = 100,
    ) -> dict[str, Any]:
        candles = await self.historical_svc.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            limit=limit,
            allow_synthetic_fallback=False,
        )
        real = [c for c in candles if is_real_market_source(c.source)]
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
        candles = await self.historical_svc.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            limit=limit,
            allow_synthetic_fallback=False,
        )
        real = [c for c in candles if is_real_market_source(c.source)]
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
        chain = await self.option_chain_svc.get_chain(
            underlying=underlying,
            expiry=expiry,
            provider="kite",
        )
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
        captured = self._parse_timestamp(
            chain.get("captured_at") or chain.get("timestamp")
        )
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
            "summary": summarize_option_chain(filtered),
            "strikes": strikes,
            "capabilities": chain.get("capabilities", {}),
            "generated_at": utc_now().isoformat(),
        }

    async def get_account_context(self) -> dict[str, Any]:
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

        return {
            "as_of": utc_now().isoformat(),
            "risk_system_mode": risk_mode.value,
            "broker_sessions": sessions,
            "execution_session": execution_session,
            "local_portfolio": {
                "positions": [p.model_dump(mode="json") for p in positions],
                "pnl": pnl,
            },
            "broker_live": broker_live,
        }

    async def get_data_quality(self) -> dict[str, Any]:
        quote = self.market_svc.get_latest_quote("INST-NIFTY-INDEX")
        quote_source = str(getattr(quote, "source", "") or "").upper() if quote else None
        quote_real = bool(quote and is_real_market_source(quote_source))
        sessions = await self.session_svc.get_all_session_statuses()
        return {
            "checked_at": utc_now().isoformat(),
            "market_feed": self.market_svc.get_feed_status(),
            "nifty_quote": {
                "available": quote_real,
                "source": quote_source,
                "exchange_timestamp": quote.timestamp.isoformat() if quote_real else None,
                "age_seconds": self._age_seconds(quote.timestamp) if quote_real else None,
            },
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
            "regular_session": open_dt <= now_ist <= close_dt,
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
                "source": options.get("source"),
                "summary": options.get("summary"),
                "captured_at": options.get("captured_at"),
                "age_seconds": options.get("age_seconds"),
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
