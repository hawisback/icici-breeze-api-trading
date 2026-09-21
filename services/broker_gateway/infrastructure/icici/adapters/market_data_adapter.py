"""Breeze Market Data Adapter implementing BrokerMarketDataPort.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
from typing import Any, Optional

from services.broker_gateway.domain.enums import FeedInterval, OptionRight
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.models.market_data import (
    Candle,
    OptionContractQuote,
    OptionChainSnapshot,
    Quote,
)
from services.broker_gateway.domain.ports.market_data_port import BrokerMarketDataPort
from services.broker_gateway.infrastructure.icici.breeze_client import BreezeClientManager
from services.broker_gateway.infrastructure.icici.datetime_mapper import (
    parse_breeze_datetime,
    to_breeze_date_str,
    to_breeze_iso,
)
from services.broker_gateway.infrastructure.icici.request_mapper import (
    map_exchange_to_breeze,
    map_option_right_to_breeze,
    map_product_to_breeze,
)
from services.broker_gateway.infrastructure.icici.response_mapper import BreezeResponseValidator
from services.broker_gateway.infrastructure.rate_limit.policies import BrokerRateLimiter

logger = logging.getLogger(__name__)


class BreezeMarketDataAdapter(BrokerMarketDataPort):
    """Fetches quotes, historical candles, and option chain snapshots from ICICI Breeze."""

    def __init__(
        self,
        client_manager: BreezeClientManager,
        rate_limiter: Optional[BrokerRateLimiter] = None,
    ) -> None:
        self.client_manager = client_manager
        self.rate_limiter = rate_limiter or BrokerRateLimiter()

    async def get_quote(self, instrument: BrokerInstrumentRef) -> Quote:
        """Fetch current quote for an instrument."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()

        expiry_str = ""
        if instrument.expiry:
            expiry_str = f"{to_breeze_date_str(instrument.expiry)}T06:00:00.000Z"

        strike_str = str(instrument.strike) if instrument.strike is not None else "0"

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_quotes(
                stock_code=instrument.stock_code,
                exchange_code=map_exchange_to_breeze(instrument.exchange),
                expiry_date=expiry_str,
                product_type=map_product_to_breeze(instrument.product_type),
                right=map_option_right_to_breeze(instrument.option_right),
                strike_price=strike_str,
            ),
            timeout_sec=10.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)
        if isinstance(data, list) and len(data) > 0:
            item = data[0]
        elif isinstance(data, dict):
            item = data
        else:
            item = {}

        ltp_val = item.get("ltp") or item.get("last_price") or "0"
        ltp = Decimal(str(ltp_val))

        def _to_decimal(key: str) -> Optional[Decimal]:
            val = item.get(key)
            return Decimal(str(val)) if val is not None and str(val).strip() != "" else None

        def _to_int(key: str) -> Optional[int]:
            val = item.get(key)
            return int(val) if val is not None and str(val).strip() != "" else None

        dt_str = item.get("datetime") or item.get("quote_time") or ""
        ts = parse_breeze_datetime(dt_str) if dt_str else datetime.now(timezone.utc)

        return Quote(
            instrument=instrument,
            ltp=ltp,
            best_bid_price=_to_decimal("best_bid_price") or _to_decimal("bid_price"),
            best_bid_qty=_to_int("best_bid_quantity") or _to_int("bid_quantity"),
            best_ask_price=(
                _to_decimal("best_offer_price")
                or _to_decimal("best_ask_price")
                or _to_decimal("ask_price")
            ),
            best_ask_qty=(
                _to_int("best_offer_quantity")
                or _to_int("best_ask_quantity")
                or _to_int("ask_quantity")
            ),
            open=_to_decimal("open"),
            high=_to_decimal("high"),
            low=_to_decimal("low"),
            close=_to_decimal("close") or _to_decimal("previous_close"),
            volume=_to_int("total_quantity_traded") or _to_int("volume"),
            open_interest=_to_int("open_interest"),
            timestamp=ts,
        )

    async def get_historical(
        self,
        instrument: BrokerInstrumentRef,
        interval: FeedInterval,
        from_date: datetime,
        to_date: datetime,
    ) -> list[Candle]:
        """Fetch historical candlestick series using Breeze V2 endpoint."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()

        expiry_str = ""
        if instrument.expiry:
            expiry_str = f"{to_breeze_date_str(instrument.expiry)}T06:00:00.000Z"

        strike_str = str(instrument.strike) if instrument.strike is not None else "0"

        raw_resp = await self.client_manager.sdk_runner.run(
            lambda: sdk.get_historical_data_v2(
                interval=interval.value,
                from_date=to_breeze_iso(from_date),
                to_date=to_breeze_iso(to_date),
                stock_code=instrument.stock_code,
                exchange_code=map_exchange_to_breeze(instrument.exchange),
                product_type=map_product_to_breeze(instrument.product_type),
                expiry_date=expiry_str,
                right=map_option_right_to_breeze(instrument.option_right),
                strike_price=strike_str,
            ),
            timeout_sec=15.0,
        )
        data = BreezeResponseValidator.unwrap_success(raw_resp)
        rows: list[dict[str, Any]] = data if isinstance(data, list) else []
        interval_minutes = {
            FeedInterval.ONE_SECOND: 1 / 60,
            FeedInterval.ONE_MINUTE: 1,
            FeedInterval.FIVE_MINUTE: 5,
            FeedInterval.THIRTY_MINUTE: 30,
            FeedInterval.ONE_DAY: 1440,
        }.get(interval, 1)

        candles: list[Candle] = []
        for row in rows:
            dt_raw = row.get("datetime") or row.get("date") or ""
            start_time = parse_breeze_datetime(dt_raw) if dt_raw else datetime.now(timezone.utc)
            candles.append(
                Candle(
                    instrument=instrument,
                    interval=interval,
                    start_time=start_time,
                    end_time=start_time + timedelta(minutes=interval_minutes),
                    open=Decimal(str(row.get("open", "0"))),
                    high=Decimal(str(row.get("high", "0"))),
                    low=Decimal(str(row.get("low", "0"))),
                    close=Decimal(str(row.get("close", "0"))),
                    volume=int(row.get("volume", 0)),
                    open_interest=int(row["open_interest"]) if row.get("open_interest") is not None else None,
                )
            )
        return candles

    async def get_option_chain(
        self,
        underlying: str,
        expiry: date,
        exchange: str = "NFO",
    ) -> OptionChainSnapshot:
        """Fetch full option chain quote snapshot."""
        await self.rate_limiter.acquire_read()
        sdk = self.client_manager.get_sdk_client()

        stock_code = "CNXBAN" if "BANK" in underlying.upper() else "NIFTY"
        expiry_iso = f"{to_breeze_date_str(expiry)}T06:00:00.000Z"

        rows: list[dict[str, Any]] = []
        for opt_right in ["call", "put"]:
            try:
                raw_resp = await self.client_manager.sdk_runner.run(
                    lambda r=opt_right: sdk.get_option_chain_quotes(
                        stock_code=stock_code,
                        exchange_code=exchange,
                        expiry_date=expiry_iso,
                        product_type="options",
                        right=r,
                    ),
                    timeout_sec=15.0,
                )
                data = BreezeResponseValidator.unwrap_success(raw_resp)
                if isinstance(data, list):
                    rows.extend(data)
            except Exception as exc:
                logger.warning("Breeze %s option query for %s %s failed: %s", opt_right, stock_code, expiry, exc)

        contracts: list[OptionContractQuote] = []
        spot_price = Decimal("0")

        for row in rows:
            strike = Decimal(str(row.get("strike_price", "0")))
            right_raw = str(row.get("right", "")).lower()
            right = OptionRight.CALL if "call" in right_raw else OptionRight.PUT
            ltp = Decimal(str(row.get("ltp", "0")))
            bid_raw = row.get("best_bid_price") or row.get("bid_price")
            ask_raw = row.get("best_offer_price") or row.get("best_ask_price") or row.get("ask_price")
            vol_raw = (
                row.get("total_quantity_traded")
                if row.get("total_quantity_traded") is not None
                else row.get("volume")
            )
            oi_change_raw = (
                row.get("chnge_oi")
                if row.get("chnge_oi") is not None
                else row.get("change_in_oi")
            )
            bid = Decimal(str(bid_raw)) if bid_raw not in (None, "") else None
            ask = Decimal(str(ask_raw)) if ask_raw not in (None, "") else None
            vol = int(vol_raw) if vol_raw not in (None, "") else None
            oi = int(row["open_interest"]) if row.get("open_interest") not in (None, "") else None
            oi_change = int(oi_change_raw) if oi_change_raw not in (None, "") else None

            if row.get("spot_price") and spot_price == Decimal("0"):
                spot_price = Decimal(str(row["spot_price"]))

            contracts.append(
                OptionContractQuote(
                    strike_price=strike,
                    right=right,
                    ltp=ltp,
                    bid=bid,
                    ask=ask,
                    volume=vol,
                    open_interest=oi,
                    oi_change=oi_change,
                )
            )

        return OptionChainSnapshot(
            underlying=underlying,
            expiry=expiry,
            spot_price=spot_price,
            contracts=contracts,
            timestamp=datetime.now(timezone.utc),
        )
