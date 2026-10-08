"""Regression tests for AI option-chain reads sourced directly from Kite."""

from __future__ import annotations

from datetime import timedelta

import pytest

from libs.market_time import ist_today
from services.option_chain.service import OptionChainService


class _NoLocalOptionMaster:
    async def get_expiries(self, underlying: str) -> list[str]:
        raise AssertionError("explicit Kite reads must not consult local option expiries")

    async def get_option_chain_instruments(self, underlying: str, expiry: str):
        raise AssertionError("explicit Kite reads must not use local option contracts")

    async def seed_strikes_around_spot(self, underlying: str, spot_price: float, expiry: str):
        raise AssertionError("explicit Kite reads must not seed local option contracts")


class _MarketData:
    def get_latest_quote(self, instrument_id: str):
        return None


class _KiteAdapter:
    def __init__(self, expiry: str, *, active: bool = True) -> None:
        self.expiry = expiry
        self.is_active = active
        self.expiry_requests: list[str] = []
        self.chain_requests: list[tuple[str, str]] = []

    async def get_option_expiries(self, underlying: str) -> list[str]:
        self.expiry_requests.append(underlying)
        return [self.expiry]

    async def get_option_chain_view(self, underlying: str, expiry: str):
        self.chain_requests.append((underlying, expiry))
        return {
            "underlying": underlying,
            "spot_price": 25000.0,
            "expiry": expiry,
            "available_expiries": [expiry],
            "atm_strike": 25000.0,
            "source": "KITE",
            "strikes": [
                {
                    "strike": 25000.0,
                    "call": {
                        "instrument_id": f"INST-NIFTY-{expiry}-25000-CE",
                        "symbol": "NIFTY-CE",
                        "ltp": 100.0,
                        "change_pct": 0.0,
                        "volume": 1000,
                        "open_interest": 5000,
                        "oi_change": 0,
                        "bid": 99.5,
                        "ask": 100.5,
                        "lot_size": 25,
                    },
                    "put": {
                        "instrument_id": f"INST-NIFTY-{expiry}-25000-PE",
                        "symbol": "NIFTY-PE",
                        "ltp": 105.0,
                        "change_pct": 0.0,
                        "volume": 900,
                        "open_interest": 4800,
                        "oi_change": 0,
                        "bid": 104.5,
                        "ask": 105.5,
                        "lot_size": 25,
                    },
                }
            ],
        }


class _Gateway:
    def __init__(self, kite_adapter: _KiteAdapter) -> None:
        self.kite_adapter = kite_adapter
        self.reference_data_broker_name = "breeze"
        self.reference_data_adapter = None
        self.active_broker_name = "breeze"
        self.active_adapter = None


@pytest.mark.asyncio
async def test_explicit_kite_chain_uses_live_expiries_without_local_master():
    expiry = (ist_today() + timedelta(days=7)).isoformat()
    kite = _KiteAdapter(expiry)
    service = OptionChainService(
        instrument_service=_NoLocalOptionMaster(),
        market_data_service=_MarketData(),
        broker_gateway=_Gateway(kite),
    )

    chain = await service.get_chain(
        underlying="NIFTY",
        provider="kite",
    )

    assert chain["source"] == "KITE"
    assert chain["expiry"] == expiry
    assert chain["available_expiries"] == [expiry]
    assert chain["strikes"]
    assert kite.expiry_requests == ["NIFTY"]
    assert kite.chain_requests == [("NIFTY", expiry)]


@pytest.mark.asyncio
async def test_explicit_kite_chain_fails_closed_when_kite_is_inactive():
    expiry = (ist_today() + timedelta(days=7)).isoformat()
    kite = _KiteAdapter(expiry, active=False)
    service = OptionChainService(
        instrument_service=_NoLocalOptionMaster(),
        market_data_service=_MarketData(),
        broker_gateway=_Gateway(kite),
    )

    chain = await service.get_chain(
        underlying="NIFTY",
        provider="kite",
    )

    assert chain["source"] == "UNAVAILABLE"
    assert chain["strikes"] == []
    assert (
        chain["capabilities"]["strategy_a_rejection_reason"]
        == "KITE_OPTION_CHAIN_UNAVAILABLE"
    )
    assert kite.expiry_requests == []
    assert kite.chain_requests == []
