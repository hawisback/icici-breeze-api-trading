"""Zerodha Kite Connect adapter for the platform's normalized broker contract.

Kite's request-token exchange and SDK calls are blocking, so every SDK call is
run in a worker thread and never blocks the asyncio event loop.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import logging
import re
from time import monotonic
from typing import Any, Optional
from zoneinfo import ZoneInfo

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
from libs.market_time import IST, exchange_datetime_to_utc, ist_today

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
        self._quote_lock = asyncio.Lock()
        self._last_quote_request_at: Optional[float] = None
        self._nse_instruments: Optional[list[dict[str, Any]]] = None
        self._nfo_instruments: Optional[list[dict[str, Any]]] = None
        self._market_quote_getter = None
        self._heavyweights_cache: tuple[float, dict[str, Any]] | None = None
        self._heavyweights_lock = asyncio.Lock()
        self.request_timeout_sec = request_timeout_sec

    def set_market_quote_getter(self, getter: Any) -> None:
        """Reuse latest Kite index tick when its exchange time is recent."""
        self._market_quote_getter = getter

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
            self._heavyweights_cache = None
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
            self._heavyweights_cache = None
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
        self._heavyweights_cache = None

    async def resolve_nearest_future(self, underlying: str = "NIFTY") -> Optional[dict[str, object]]:
        if not self.is_active:
            return None
        if self._nfo_instruments is None:
            self._nfo_instruments = await self._run(lambda: self._kite.instruments("NFO"))
        clean = "BANKNIFTY" if "BANK" in underlying.upper() else "NIFTY"
        today = ist_today().isoformat()
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
        today = ist_today().isoformat()
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

    async def get_heavyweights_quotes(self) -> dict[str, Any]:
        """One batched Kite read of five specified NIFTY constituent equities.

        Market evidence only, not a claim that these are the current five
        largest weights or that their move proves a directional market signal.
        """
        symbols = ("HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "TCS")
        if not self.is_active:
            return {"available": False, "source": "UNAVAILABLE",
                    "reason": "KITE_SESSION_UNAVAILABLE", "stocks": []}
        cached = self._heavyweights_cache
        if cached and monotonic() - cached[0] < 15.0:
            return deepcopy(cached[1])
        async with self._heavyweights_lock:
            cached = self._heavyweights_cache
            if cached and monotonic() - cached[0] < 15.0:
                return deepcopy(cached[1])
            keys = [f"NSE:{symbol}" for symbol in symbols]
            raw = await self._run_quote(lambda: self._kite.quote(keys))
            stocks: list[dict[str, Any]] = []
            for symbol in symbols:
                quote = raw.get(f"NSE:{symbol}") if isinstance(raw, dict) else None
                if not isinstance(quote, dict):
                    continue
                value = float(quote.get("last_price") or 0)
                timestamp = _parse_exchange_quote_datetime(
                    quote.get("timestamp") or quote.get("last_trade_time")
                )
                if value <= 0 or timestamp is None:
                    continue
                previous_close = float((quote.get("ohlc") or {}).get("close") or 0)
                stocks.append({
                    "symbol": symbol, "last_price": value,
                    "change_pct": (
                        round((value - previous_close) / previous_close * 100, 4)
                        if previous_close > 0 else None
                    ),
                    "market_timestamp": timestamp.isoformat(),
                    "age_seconds": round(max(0.0, (utc_now() - timestamp).total_seconds()), 3),
                    "source": "KITE",
                })
            response = {
                "available": bool(stocks), "source": "KITE",
                "stocks": stocks, "requested_symbols": list(symbols),
                "missing_symbols": [s for s in symbols if s not in {x["symbol"] for x in stocks}],
                "captured_at": utc_now().isoformat(),
                "reason": None if stocks else "NO_VALID_KITE_EQUITY_QUOTES",
            }
            if stocks:
                self._heavyweights_cache = (monotonic(), response)
            return deepcopy(response)

    async def get_index_quotes(self) -> list[Quote]:
        """Return the two index quotes used by the current market-data service."""
        if not self.is_active:
            return []
        raw = await self._run_quote(lambda: self._kite.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"]))
        result: list[Quote] = []
        for instrument_id, symbol in [
            ("INST-NIFTY-INDEX", "NIFTY 50"),
            ("INST-BANKNIFTY-INDEX", "NIFTY BANK"),
        ]:
            row = raw.get(f"NSE:{symbol}", {}) if isinstance(raw, dict) else {}
            last = float(row.get("last_price") or 0)
            exchange_timestamp = _parse_exchange_quote_datetime(
                row.get("timestamp") or row.get("last_trade_time")
            )
            if last <= 0 or exchange_timestamp is None:
                if last > 0:
                    logger.warning(
                        "Kite quote for %s omitted: exchange timestamp missing/unparseable",
                        instrument_id,
                    )
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
                    timestamp=exchange_timestamp,
                )
            )
        return result

    async def get_option_chain_view(self, underlying: str, expiry: str) -> dict[str, Any]:
        """Get ATM-centred option quotes from Kite, without any local instrument master.

        The NFO instrument dump is metadata only: request live market quotes for
        a bounded set of nearby contracts, not for every listed contract.
        """
        if not self.is_active:
            return {}
        clean_underlying = "BANKNIFTY" if "BANK" in underlying.upper() else "NIFTY"
        expiry_date = date.fromisoformat(expiry)
        if self._nfo_instruments is None:
            self._nfo_instruments = await self._run(lambda: self._kite.instruments("NFO"))
        rows = self._nfo_instruments or []
        contracts = [
            row for row in rows
            if str(row.get("name", "")).upper() == clean_underlying
            and str(row.get("expiry", ""))[:10] == expiry_date.isoformat()
            and str(row.get("instrument_type", "")).upper() in {"CE", "PE"}
            and row.get("tradingsymbol")
            and row.get("strike") is not None
        ]
        if not contracts:
            return {}

        spot_key = "NSE:NIFTY BANK" if clean_underlying == "BANKNIFTY" else "NSE:NIFTY 50"
        # Prefer the already-polled index tick (Kite only, <= 6s old).
        # This avoids one unnecessary broker LTP request per chain refresh.
        # Never use the seeded/simulated or Breeze quote to choose ATM.
        spot = 0.0
        if callable(self._market_quote_getter):
            index_id = (
                "INST-BANKNIFTY-INDEX" if clean_underlying == "BANKNIFTY"
                else "INST-NIFTY-INDEX"
            )
            cached = self._market_quote_getter(index_id)
            if cached is not None and str(getattr(cached, "source", "")).upper() == "KITE":
                observed = getattr(cached, "timestamp", None)
                if observed is not None and observed.tzinfo is not None:
                    seconds = (utc_now() - observed).total_seconds()
                    if 0 <= seconds <= 6.0:
                        spot = float(getattr(cached, "last_price", 0) or 0)
        if spot <= 0:
            # A fresh market-data tick is unavailable: ask Kite for live LTP
            # rather than guessing a strike from hardcoded/stale values.
            spot_data = await self._run_quote(lambda: self._kite.ltp([spot_key]))
            spot = float((spot_data or {}).get(spot_key, {}).get("last_price") or 0)
        if spot <= 0:
            logger.warning("Kite index LTP unavailable for %s", spot_key)
            return {}

        step = 100 if clean_underlying == "BANKNIFTY" else 50
        atm = round(spot / step) * step
        # 61 nearest strike levels x two rights = at most 122 contracts,
        # covering the AI API's maximum strike_window=30 on both sides of ATM.
        # This stays well below Kite's full-quote limit of 500 instruments.
        levels = sorted(
            {float(row["strike"]) for row in contracts},
            key=lambda strike: (abs(strike - atm), strike),
        )[:61]
        selected_strikes = set(levels)
        # Only one contract per strike/right belongs in an option matrix.
        nearby_by_side = {}
        for row in contracts:
            strike = float(row["strike"])
            right = str(row["instrument_type"]).upper()
            if strike in selected_strikes:
                nearby_by_side.setdefault((strike, right), row)
        nearby = list(nearby_by_side.values())
        quote_keys = [f"NFO:{row['tradingsymbol']}" for row in nearby]
        quotes = await self._run_quote(lambda: self._kite.quote(quote_keys))
        if not isinstance(quotes, dict):
            return {}

        strikes: dict[float, dict[str, Any]] = {}
        quoted_contracts = 0
        observed_times: list[datetime] = []
        for row in nearby:
            symbol = str(row["tradingsymbol"])
            quote = quotes.get(f"NFO:{symbol}")
            # Kite omits instrument keys for which it has no market quote.
            # Do not present absent quotes as zero-price / zero-OI evidence.
            if (
                not isinstance(quote, dict)
                or not quote
                or float(quote.get("last_price") or 0) <= 0
            ):
                continue
            quoted_contracts += 1
            strike = float(row["strike"])
            right = str(row["instrument_type"]).upper()
            depth = quote.get("depth") or {}
            buy_depth = depth.get("buy") or []
            sell_depth = depth.get("sell") or []
            quote_time = _parse_exchange_quote_datetime(
                quote.get("timestamp") or quote.get("last_trade_time")
            )
            if quote_time is not None:
                observed_times.append(quote_time)
            last_price = float(quote.get("last_price") or 0)
            previous_close = float((quote.get("ohlc") or {}).get("close") or 0)
            item = {
                "instrument_id": f"INST-{clean_underlying}-{expiry}-{int(strike)}-{right}",
                "symbol": symbol,
                "ltp": last_price,
                "change_pct": round(
                    (last_price - previous_close) / previous_close * 100, 4
                ) if previous_close > 0 else None,
                "volume": int(quote.get("volume") or 0),
                "open_interest": (
                    int(quote["oi"]) if quote.get("oi") is not None else None
                ),
                # Kite full quotes do not provide change in OI directly.
                "oi_change": None,
                "bid": float((buy_depth[0] if buy_depth else {}).get("price") or 0),
                "ask": float((sell_depth[0] if sell_depth else {}).get("price") or 0),
                "lot_size": int(row.get("lot_size") or 1),
                "market_timestamp": quote_time.isoformat() if quote_time else None,
            }
            bucket = strikes.setdefault(strike, {"strike": strike, "call": None, "put": None})
            bucket["call" if right == "CE" else "put"] = item

        if not quoted_contracts:
            logger.warning("Kite returned no quoted option contracts for %s %s", clean_underlying, expiry)
            return {}

        return {
            "underlying": clean_underlying,
            "spot_price": spot,
            "expiry": expiry,
            "available_expiries": await self.get_option_expiries(clean_underlying),
            "atm_strike": atm,
            "source": "KITE",
            "requested_contract_count": len(nearby),
            "quoted_contract_count": quoted_contracts,
            "partial_quote_coverage": quoted_contracts < len(nearby),
            # A conservative earliest quote timestamp, if every contract has
            # one; retrieval time alone is not proof of live quote freshness.
            "market_timestamp": (
                min(observed_times).isoformat()
                if len(observed_times) == quoted_contracts else None
            ),
            "strikes": [strikes[key] for key in sorted(strikes)],
        }

    async def get_option_contract_quote(self, instrument_id: str) -> dict[str, Any]:
        """Fetch ONE Kite option quote for an active AI PAPER trade.

        Unlike get_option_chain_view, this avoids refreshing ~122 contracts on
        each independent trailing-stop poll. Listed NFO metadata is the sole
        contract authority; no guessed symbols or synthetic fallback.
        """
        if not self.is_active:
            return {}
        match = re.fullmatch(
            r"INST-(NIFTY|BANKNIFTY)-(\d{4}-\d{2}-\d{2})-(\d+)-(CE|PE)",
            instrument_id,
        )
        if not match:
            return {}
        underlying, expiry, strike, right = match.groups()
        if self._nfo_instruments is None:
            self._nfo_instruments = await self._run(lambda: self._kite.instruments("NFO"))
        rows = [
            row for row in self._nfo_instruments or []
            if str(row.get("name", "")).upper() == underlying
            and str(row.get("expiry", ""))[:10] == expiry
            and int(float(row.get("strike") or 0)) == int(strike)
            and str(row.get("instrument_type", "")).upper() == right
            and row.get("tradingsymbol")
        ]
        if len(rows) != 1:
            return {}
        row = rows[0]
        symbol = str(row["tradingsymbol"])
        key = f"NFO:{symbol}"
        quotes = await self._run_quote(lambda: self._kite.quote([key]))
        quote = quotes.get(key) if isinstance(quotes, dict) else None
        if not isinstance(quote, dict) or float(quote.get("last_price") or 0) <= 0:
            return {}
        depth = quote.get("depth") or {}
        buy, sell = depth.get("buy") or [], depth.get("sell") or []
        timestamp = _parse_exchange_quote_datetime(
            quote.get("timestamp") or quote.get("last_trade_time")
        )
        return {
            "source": "KITE",
            "expiry": expiry,
            "instrument_id": instrument_id,
            "symbol": symbol,
            "bid": float((buy[0] if buy else {}).get("price") or 0),
            "ask": float((sell[0] if sell else {}).get("price") or 0),
            "lot_size": int(row.get("lot_size") or 0),
            "market_timestamp": timestamp.isoformat() if timestamp else None,
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
        exchange_tz = IST
        start_exchange = _as_exchange_datetime(start_time, exchange_tz)
        end_exchange = _as_exchange_datetime(end_time, exchange_tz)
        rows = await self._run(
            lambda: self._kite.historical_data(
                instrument_token=token,
                from_date=start_exchange,
                to_date=end_exchange,
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
        today = ist_today().isoformat()
        futures = [row for row in all_futures if str(row.get("expiry", ""))[:10] >= today]
        futures.sort(key=lambda row: str(row.get("expiry", ""))[:10])
        return int(futures[0]["instrument_token"]) if futures else None

    async def _run_quote(self, callback):
        """Serialize quote/LTP calls to respect Kite's one-request-per-second limit."""
        async with self._quote_lock:
            if self._last_quote_request_at is not None:
                delay = 1.05 - (monotonic() - self._last_quote_request_at)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_quote_request_at = monotonic()
            return await self._run(callback)

    async def _run(self, callback):
        return await asyncio.wait_for(
            asyncio.to_thread(callback),
            timeout=self.request_timeout_sec,
        )


def _as_exchange_datetime(
    value: datetime,
    exchange_tz: ZoneInfo,
) -> datetime:
    """Convert internal UTC/aware timestamps to Kite's exchange wall clock."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=IST)
    return aware.astimezone(exchange_tz)


def _parse_exchange_quote_datetime(value: Any) -> Optional[datetime]:
    """Parse Kite market timestamp without replacing missing data with now."""
    ist = IST
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ist)
        return parsed.astimezone(timezone.utc)
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ist)
    return parsed.astimezone(timezone.utc)


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
        return exchange_datetime_to_utc(value)
    if value:
        raw = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
            return exchange_datetime_to_utc(parsed)
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
