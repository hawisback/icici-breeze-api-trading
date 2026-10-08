"""Test bounded live Kite option quotes instead of querying an entire expiry."""

from __future__ import annotations

from datetime import timedelta

import pytest

from libs.market_time import ist_today
from services.broker_gateway.zerodha_kite_adapter import ZerodhaKiteAdapter


class LargeNfoKite:
    def __init__(self, *, quotes_available: bool = True) -> None:
        self.expiry = (ist_today() + timedelta(days=7)).isoformat()
        self.quotes_available = quotes_available
        self.quote_requests: list[list[str]] = []
        self.spot_requests: list[list[str]] = []
        self.instrument_calls = 0

    def instruments(self, exchange: str):
        assert exchange == "NFO"
        self.instrument_calls += 1
        result = []
        # 600 strikes x CE/PE = 1200 contracts, above Kite's 500-quote limit.
        for strike in range(10000, 40000, 50):
            for right in ("CE", "PE"):
                result.append({
                    "name": "NIFTY",
                    "instrument_type": right,
                    "expiry": self.expiry,
                    "strike": float(strike),
                    "tradingsymbol": f"NIFTY-{self.expiry}-{strike}-{right}",
                    "lot_size": 65,
                })
        return result

    def ltp(self, keys: list[str]):
        self.spot_requests.append(list(keys))
        return {"NSE:NIFTY 50": {"last_price": 25025.0}}

    def quote(self, keys: list[str]):
        self.quote_requests.append(list(keys))
        if not self.quotes_available:
            return {}
        return {
            key: {
                "last_price": 100.0,
                "volume": 1000,
                "oi": 5000,
                "net_change": 1.0,
                "depth": {"buy": [{"price": 99.0}], "sell": [{"price": 101.0}]},
            }
            for key in keys
        }


@pytest.mark.asyncio
async def test_option_quotes_are_bounded_and_near_atm():
    client = LargeNfoKite()
    adapter = ZerodhaKiteAdapter(custom_client=client)
    adapter._access_token = "test-token"

    chain = await adapter.get_option_chain_view("NIFTY", client.expiry)

    assert chain["source"] == "KITE"
    assert chain["atm_strike"] == 25000
    assert len(client.quote_requests) == 1
    assert len(client.quote_requests[0]) <= 122
    assert chain["quoted_contract_count"] == len(client.quote_requests[0])
    assert chain["partial_quote_coverage"] is False
    assert all(abs(float(row["strike"]) - 25000) <= 1550 for row in chain["strikes"])
    assert client.instrument_calls == 1


@pytest.mark.asyncio
async def test_kite_missing_contract_quotes_are_not_represented_as_zero_oi():
    client = LargeNfoKite(quotes_available=False)
    adapter = ZerodhaKiteAdapter(custom_client=client)
    adapter._access_token = "test-token"

    chain = await adapter.get_option_chain_view("NIFTY", client.expiry)

    assert chain == {}
    assert len(client.quote_requests) == 1
