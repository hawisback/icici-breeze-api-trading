"""Zerodha Kite Connect adapter for the platform's normalized broker contract.

Kite's request-token exchange and SDK calls are blocking, so every SDK call is
run in a worker thread and never blocks the asyncio event loop.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import logging
import re
from typing import Any, Optional

from pydantic import SecretStr

from libs.broker_models.adapter import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerPositionResponse,
    BrokerTradeResponse,
)
from libs.contracts.models import Candle, Quote, utc_now

logger = logging.getLogger(__name__)


class ZerodhaKiteAdapter(BrokerAdapter):
    """Kite Connect implementation of the normalized live broker interface."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        product: str = "NRML",
        custom_client: Optional[Any] = None,
        request_timeout_sec: float = 15.0,
    ) -> None:
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.product = product.upper()
        self._kite = custom_client
        self._access_token = ""
        self._write_lock = asyncio.Lock()
        self._nse_instruments: Optional[list[dict[str, Any]]] = None
        self._nfo_instruments: Optional[list[dict[str, Any]]] = None
        self.request_timeout_sec = request_timeout_sec

    @property
    def is_active(self) -> bool:
        return self._kite is not None and bool(self._access_token)

    @property
    def access_token(self) -> str:
        return self._access_token

    def get_login_url(self, api_key: Optional[str] = None) -> str:
        key = api_key or self.api_key
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={key}"

    async def initialize(self) -> None:
        """No persistent initialization is required for Kite."""

    async def authenticate(self, api_key: str, secret_key: str, session_token: str) -> bool:
        """Exchange Kite's one-time request token for the daily access token."""
        self.api_key = _secret_value(api_key)
        self.api_secret = _secret_value(secret_key)
        request_token = _secret_value(session_token)
        if not self.api_key or not self.api_secret or not request_token:
            return False

        try:
            if self._kite is None:
                from kiteconnect import KiteConnect

                self._kite = KiteConnect(api_key=self.api_key)
            session_data = await self._run(
                lambda: self._kite.generate_session(request_token, api_secret=self.api_secret)
            )
            access_token = str(session_data.get("access_token") or "")
            if not access_token:
                raise RuntimeError("Kite session response did not contain access_token")
            self._access_token = access_token
            self._kite.set_access_token(access_token)
            self._nse_instruments = None
            self._nfo_instruments = None
            logger.info("Kite session successfully activated for account %s", self.api_key[:8])
            return True
        except Exception as exc:
            logger.error("Kite authentication failed: %s", exc)
            self._access_token = ""
            return False

    async def authenticate_access_token(self, api_key: str, access_token: str) -> bool:
        """Activate an already exchanged daily Kite access token."""
        self.api_key = _secret_value(api_key)
        token = _secret_value(access_token)
        if not self.api_key or not token:
            return False
        try:
            if self._kite is None:
                from kiteconnect import KiteConnect

                self._kite = KiteConnect(api_key=self.api_key)
            self._kite.set_access_token(token)
            self._access_token = token
            self._nse_instruments = None
            self._nfo_instruments = None
            await self._run(self._kite.profile)
            return True
        except Exception as exc:
            logger.error("Kite access-token activation failed: %s", exc)
            self._access_token = ""
            return False

    async def disconnect(self) -> None:
        self._access_token = ""
        self._kite = None
        self._nse_instruments = None
        self._nfo_instruments = None

    async def resolve_nearest_future(self, underlying: str = "NIFTY") -> Optional[dict[str, object]]:
        if not self.is_active:
            return None
        if self._nfo_instruments is None:
            self._nfo_instruments = await self._run(lambda: self._kite.instruments("NFO"))
        clean = "BANKNIFTY" if "BANK" in underlying.upper() else "NIFTY"
        today = date.today().isoformat()
        rows = [row for row in self._nfo_instruments or []
                if str(row.get("name", "")).upper() == clean
                and str(row.get("instrument_type", "")).upper() == "FUT"
                and str(row.get("expiry", ""))[:10] >= today]
        rows.sort(key=lambda row: str(row.get("expiry", ""))[:10])
        if not rows:
            return None
        row = rows[0]
        return {
            "underlying": clean, "expiry": str(row.get("expiry", ""))[:10],
            "stock_code": str(row.get("tradingsymbol") or clean),
            "symbol": str(row.get("tradingsymbol") or clean), "exchange": "NFO",
            "broker": "ZERODHA_KITE", "broker_token": str(row.get("instrument_token") or ""),
            "lot_size": int(row.get("lot_size") or 1), "tick_size": float(row.get("tick_size") or 0.05),
        }

    async def get_option_expiries(self, underlying: str = "NIFTY") -> list[str]:
        if not self.is_active:
            return []
        if self._nfo_instruments is None:
            self._nfo_instruments = await self._run(lambda: self._kite.instruments("NFO"))
        clean = "BANKNIFTY" if "BANK" in underlying.upper() else "NIFTY"
        today = date.today().isoformat()
        return sorted({str(row.get("expiry", ""))[:10] for row in self._nfo_instruments or []
                       if str(row.get("name", "")).upper() == clean
                       and str(row.get("instrument_type", "")).upper() in {"CE", "PE"}
                       and str(row.get("expiry", ""))[:10] >= today})

    async def get_funds(self) -> BrokerFunds:
        if not self.is_active:
            return BrokerFunds(available_margin=0.0, total_cash=0.0, used_margin=0.0)
        data = await self._run(self._kite.margins)
        equity = data.get("equity", {}) if isinstance(data, dict) else {}
        available = float(equity.get("available", {}).get("live_balance", 0) or 0)
        total = float(equity.get("available", {}).get("opening_balance", available) or available)
        used = float(equity.get("utilised", {}).get("debits", 0) or 0)
        return BrokerFunds(available_margin=available, total_cash=total, used_margin=used)

    async def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResponse:
        if not self.is_active:
            return _failure(request.client_order_id, "Kite session is not active")
        normalized_order_type = request.order_type.upper().replace("_", "-")
        kite_order_type = "SL" if normalized_order_type in {"STOP-LIMIT", "STOPLOSS"} else normalized_order_type
        params: dict[str, Any] = {
            "variety": "regular",
            "exchange": request.exchange_code.upper(),
            "tradingsymbol": request.stock_code,
            "transaction_type": "BUY" if request.action.lower() == "buy" else "SELL",
            "quantity": request.quantity,
            "product": self.product if request.product.lower() != "cash" else "CNC",
            "order_type": kite_order_type,
            "validity": request.validity.upper(),
        }
        if params["order_type"] in {"LIMIT", "SL"}:
            params["price"] = request.price
        if params["order_type"] == "SL":
            if request.trigger_price is None:
                return _failure(
                    request.client_order_id,
                    "STOP_LIMIT requires trigger_price",
                )
            params["trigger_price"] = request.trigger_price
        if request.user_remark:
            params["tag"] = request.user_remark[:20]

        try:
            async with self._write_lock:
                order_id = await self._run(lambda: self._kite.place_order(**params))
            return BrokerOrderResponse(
                success=True,
                broker_order_id=str(order_id),
                client_order_id=request.client_order_id,
                status="PLACED",
                message="Order placed successfully with Kite",
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.error("Kite submission outcome unknown for %s: %s", request.client_order_id, exc)
            return BrokerOrderResponse(
                success=False,
                client_order_id=request.client_order_id,
                status="UNKNOWN",
                message="Kite submission timed out; reconciliation required and blind retry is prohibited.",
            )
        except Exception as exc:
            logger.warning("Kite order rejected for %s: %s", request.client_order_id, exc)
            return _failure(request.client_order_id, str(exc))

    async def modify_order(
        self,
        broker_order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
    ) -> BrokerOrderResponse:
        if not self.is_active:
            return _failure("", "Kite session is not active", broker_order_id)
        params: dict[str, Any] = {"variety": "regular", "order_id": broker_order_id}
        if quantity is not None:
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price
        try:
            async with self._write_lock:
                await self._run(lambda: self._kite.modify_order(**params))
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="MODIFIED",
                message="Order modified successfully with Kite",
            )
        except Exception as exc:
            return _failure("", str(exc), broker_order_id)

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResponse:
        if not self.is_active:
            return _failure("", "Kite session is not active", broker_order_id)
        try:
            async with self._write_lock:
                await self._run(lambda: self._kite.cancel_order(variety="regular", order_id=broker_order_id))
            return BrokerOrderResponse(
                success=True,
                broker_order_id=broker_order_id,
                client_order_id="",
                status="CANCELLED",
                message="Order cancelled successfully with Kite",
            )
        except Exception as exc:
            return _failure("", str(exc), broker_order_id)

    async def get_order_status(self, broker_order_id: str) -> Optional[BrokerOrderResponse]:
        if not self.is_active:
            return None
        try:
            rows = await self._run(lambda: self._kite.order_history(broker_order_id))
            row = rows[-1] if rows else None
            if not row:
                return None
            status = str(row.get("status", "UNKNOWN")).upper()
            return BrokerOrderResponse(
                success=status not in {"REJECTED", "CANCELLED"},
                broker_order_id=broker_order_id,
                client_order_id=str(row.get("tag") or ""),
                status=status,
                message=row.get("status_message"),
                filled_quantity=int(row.get("filled_quantity") or 0),
                average_price=float(row.get("average_price") or 0),
            )
        except Exception as exc:
            logger.warning("Kite order status lookup failed: %s", exc)
            return None

    async def find_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrderResponse]:
        if not self.is_active:
            return None
        try:
            rows = await self._run(self._kite.orders)
            row = next(
                (item for item in reversed(rows or []) if str(item.get("tag") or "") == client_order_id),
                None,
            )
            if row is None:
                return None
            status = str(row.get("status", "UNKNOWN")).upper()
            return BrokerOrderResponse(
                success=status not in {"REJECTED", "CANCELLED"},
                broker_order_id=str(row.get("order_id") or ""),
                client_order_id=client_order_id,
                status=status,
                message=row.get("status_message"),
                filled_quantity=int(row.get("filled_quantity") or 0),
                average_price=float(row.get("average_price") or 0),
            )
        except Exception as exc:
            logger.warning("Kite client-id reconciliation failed: %s", exc)
            return None

    async def get_positions(self) -> list[BrokerPositionResponse]:
        if not self.is_active:
            return []
        data = await self._run(self._kite.positions)
        rows = (data.get("net", []) if isinstance(data, dict) else [])
        return [
            BrokerPositionResponse(
                stock_code=str(row.get("tradingsymbol", "")),
                exchange_code=str(row.get("exchange", "")),
                product_type=str(row.get("product", "")),
                quantity=int(row.get("quantity") or 0),
                average_price=float(row.get("average_price") or 0),
                ltp=float(row.get("last_price") or 0),
                pnl=float(row.get("pnl") or 0),
            )
            for row in rows
        ]

    async def get_trades(self) -> list[BrokerTradeResponse]:
        if not self.is_active:
            return []
        rows = await self._run(self._kite.trades)
        return [
            BrokerTradeResponse(
                trade_id=str(row.get("trade_id", "")),
                broker_order_id=str(row.get("order_id", "")),
                client_order_id=row.get("tag"),
                stock_code=str(row.get("tradingsymbol", "")),
                exchange_code=str(row.get("exchange", "")),
                action=str(row.get("transaction_type", "")),
                quantity=int(row.get("quantity") or 0),
                price=float(row.get("average_price") or row.get("price") or 0),
                trade_time=_parse_datetime(row.get("fill_timestamp") or row.get("order_timestamp")),
            )
            for row in rows
        ]

    async def get_index_quotes(self) -> list[Quote]:
        """Return the two index quotes used by the current market-data service."""
        if not self.is_active:
            return []
        raw = await self._run(lambda: self._kite.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"]))
        now = utc_now()
        result: list[Quote] = []
        for instrument_id, symbol in [
            ("INST-NIFTY-INDEX", "NIFTY 50"),
            ("INST-BANKNIFTY-INDEX", "NIFTY BANK"),
        ]:
            row = raw.get(f"NSE:{symbol}", {}) if isinstance(raw, dict) else {}
            last = float(row.get("last_price") or 0)
            if last <= 0:
                continue
            ohlc = row.get("ohlc", {}) or {}
            previous_close = float(ohlc.get("close") or last)
            result.append(
                Quote(
                    instrument_id=instrument_id,
                    source="KITE",
                    symbol=symbol,
                    last_price=last,
                    open=float(ohlc.get("open") or last),
                    high=float(ohlc.get("high") or last),
                    low=float(ohlc.get("low") or last),
                    close=float(ohlc.get("close") or last),
                    volume=int(row.get("volume") or 0),
                    change_pct=round(((last - previous_close) / previous_close) * 100, 4)
                    if previous_close
                    else 0.0,
                    timestamp=now,
                )
            )
        return result

    async def get_option_chain_view(self, underlying: str, expiry: str) -> dict[str, Any]:
        """Build the option-chain shape consumed by the platform UI from Kite NFO data."""
        if not self.is_active:
            return {}
        clean_underlying = "BANKNIFTY" if "BANK" in underlying.upper() else "NIFTY"
        expiry_date = date.fromisoformat(expiry)
        if self._nfo_instruments is None:
            self._nfo_instruments = await self._run(lambda: self._kite.instruments("NFO"))
        rows = self._nfo_instruments
        contracts = [
            row
            for row in rows
            if str(row.get("name", "")).upper() == clean_underlying
            and str(row.get("expiry", ""))[:10] == expiry_date.isoformat()
            and row.get("instrument_type") in {"CE", "PE"}
        ]
        if not contracts:
            return {}

        spot_key = "NSE:NIFTY BANK" if clean_underlying == "BANKNIFTY" else "NSE:NIFTY 50"
        quote_keys = [spot_key] + [f"NFO:{row['tradingsymbol']}" for row in contracts]
        spot_quotes = await self._run(lambda: self._kite.quote(quote_keys))
        spot = float(spot_quotes.get(spot_key, {}).get("last_price") or 0)
        step = 100 if clean_underlying == "BANKNIFTY" else 50
        atm = round(spot / step) * step if spot else float(contracts[len(contracts) // 2].get("strike", 0))
        contracts.sort(key=lambda row: abs(float(row.get("strike", 0)) - atm))
        contracts = contracts[:62]
        quotes = spot_quotes

        strikes: dict[float, dict[str, Any]] = {}
        for row in contracts:
            strike = float(row.get("strike", 0))
            right = str(row.get("instrument_type"))
            quote = quotes.get(f"NFO:{row['tradingsymbol']}", {}) or {}
            depth = quote.get("depth", {}) or {}
            buy_depth = depth.get("buy", []) or []
            sell_depth = depth.get("sell", []) or []
            item = {
                "instrument_id": f"INST-{clean_underlying}-{expiry}-{int(strike)}-{right}",
                "symbol": row["tradingsymbol"],
                "ltp": float(quote.get("last_price") or 0),
                "change_pct": float(quote.get("net_change") or 0),
                "volume": int(quote.get("volume") or 0),
                "open_interest": int(quote.get("oi") or 0),
                "oi_change": 0,
                "bid": float((buy_depth[0] if buy_depth else {}).get("price") or 0),
                "ask": float((sell_depth[0] if sell_depth else {}).get("price") or 0),
                "lot_size": int(row.get("lot_size") or 1),
            }
            bucket = strikes.setdefault(strike, {"strike": strike, "call": None, "put": None})
            bucket["call" if right == "CE" else "put"] = item

        return {
            "underlying": clean_underlying,
            "spot_price": spot,
            "expiry": expiry,
            "available_expiries": sorted({str(row.get("expiry"))[:10] for row in rows if row.get("expiry")}),
            "atm_strike": round(spot / step) * step if spot else atm,
            "source": "KITE",
            "strikes": [strikes[key] for key in sorted(strikes)],
        }

    async def fetch_historical_candles(
        self,
        instrument_id: str,
        interval: str = "5m",
        days_back: int = 5,
    ) -> list[Candle]:
        """Fetch a recent rolling window using Kite's historical API."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        return await self.fetch_historical_candles_window(
            instrument_id=instrument_id,
            interval=interval,
            start_time=start,
            end_time=end,
        )

    async def fetch_historical_candles_window(
        self,
        instrument_id: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Candle]:
        """Fetch the exact replay window instead of a window relative to now."""
        if not self.is_active or end_time < start_time:
            return []
        token = await self._find_instrument_token(instrument_id)
        if not token:
            logger.warning("No Kite instrument token found for %s", instrument_id)
            return []
        rows = await self._run(
            lambda: self._kite.historical_data(
                instrument_token=token,
                from_date=start_time,
                to_date=end_time,
                interval=_kite_interval(interval),
                oi=True,
            )
        )
        step_minutes = {"minute": 1, "5minute": 5, "15minute": 15, "30minute": 30, "day": 1440}.get(
            _kite_interval(interval), 5
        )
        candles_by_time: dict[datetime, Candle] = {}
        for row in rows:
            candle_start = _parse_datetime(row.get("date"))
            candle = Candle(
                instrument_id=instrument_id,
                interval=interval,
                start_time=candle_start,
                end_time=candle_start + timedelta(minutes=step_minutes),
                open=float(row.get("open") or 0),
                high=float(row.get("high") or 0),
                low=float(row.get("low") or 0),
                close=float(row.get("close") or 0),
                volume=int(row.get("volume") or 0),
                open_interest=int(row.get("oi") or 0),
                source="KITE",
            )
            if candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
                candles_by_time[candle_start] = candle
        return [candles_by_time[key] for key in sorted(candles_by_time)]

    async def _find_instrument_token(self, instrument_id: str) -> Optional[int]:
        is_index = "NIFTY-INDEX" in instrument_id or "BANKNIFTY-INDEX" in instrument_id
        if is_index:
            if self._nse_instruments is None:
                self._nse_instruments = await self._run(lambda: self._kite.instruments("NSE"))
            symbol = "NIFTY 50" if "BANK" not in instrument_id else "NIFTY BANK"
            for row in self._nse_instruments or []:
                if row.get("tradingsymbol") == symbol:
                    return int(row["instrument_token"])
            return None

        if self._nfo_instruments is None:
            self._nfo_instruments = await self._run(lambda: self._kite.instruments("NFO"))
        underlying = "BANKNIFTY" if "BANK" in instrument_id.upper() else "NIFTY"
        match = re.search(r"FUT-(\d{4}-\d{2}-\d{2})", instrument_id.upper())
        requested_expiry = match.group(1) if match else None
        all_futures = [row for row in self._nfo_instruments or []
                       if str(row.get("name", "")).upper() == underlying
                       and str(row.get("instrument_type", "")).upper() == "FUT"]
        if requested_expiry:
            exact = next(
                (row for row in all_futures if str(row.get("expiry", ""))[:10] == requested_expiry),
                None,
            )
            return int(exact["instrument_token"]) if exact else None
        today = date.today().isoformat()
        futures = [row for row in all_futures if str(row.get("expiry", ""))[:10] >= today]
        futures.sort(key=lambda row: str(row.get("expiry", ""))[:10])
        return int(futures[0]["instrument_token"]) if futures else None

    async def _run(self, callback):
        return await asyncio.wait_for(
            asyncio.to_thread(callback),
            timeout=self.request_timeout_sec,
        )


def _secret_value(value: Any) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")


def _failure(client_order_id: str, message: str, broker_order_id: Optional[str] = None) -> BrokerOrderResponse:
    return BrokerOrderResponse(
        success=False,
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        status="REJECTED",
        message=message,
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value:
        raw = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return utc_now()


def _kite_interval(interval: str) -> str:
    return {
        "1m": "minute",
        "5m": "5minute",
        "15m": "15minute",
        "30m": "30minute",
        "1D": "day",
        "1d": "day",
    }.get(interval, "5minute")
