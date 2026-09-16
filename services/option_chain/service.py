"""Option Chain Service synthesizing instrument definitions and live market quotes into chain matrix.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from libs.contracts.models import OptionRight
from services.instrument.service import InstrumentService
from services.market_data.service import MarketDataService

logger = logging.getLogger(__name__)


class OptionChainService:
    """Aggregates option strikes, prices, OI, and volume for interactive chain displays."""

    def __init__(
        self,
        instrument_service: InstrumentService,
        market_data_service: MarketDataService,
    ) -> None:
        self.inst_svc = instrument_service
        self.mkt_svc = market_data_service

    async def get_chain(
        self,
        underlying: str = "NIFTY",
        expiry: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build matrix of option strikes with Call and Put pricing."""
        expiries = await self.inst_svc.get_expiries(underlying)
        if not expiries:
            return {"underlying": underlying, "spot_price": 0.0, "expiry": None, "strikes": []}

        selected_expiry = expiry if expiry in expiries else expiries[0]

        # Get spot price
        spot_quote = self.mkt_svc.get_latest_quote(f"INST-{underlying}-INDEX")
        spot_price = spot_quote.last_price if spot_quote else 24850.0

        instruments = await self.inst_svc.get_option_chain_instruments(
            underlying=underlying, expiry=selected_expiry
        )

        strikes_map: dict[float, dict[str, Any]] = {}
        for inst in instruments:
            if inst.strike is None or inst.option_right is None:
                continue

            strike = inst.strike
            if strike not in strikes_map:
                strikes_map[strike] = {
                    "strike": strike,
                    "call": None,
                    "put": None,
                }

            # Lookup live quote or calculate simulated premium
            quote = self.mkt_svc.get_latest_quote(inst.instrument_id)
            if quote:
                ltp = quote.last_price
                change = quote.change_pct
                vol = quote.volume
                oi = quote.open_interest
            else:
                # Intrinsic value approximation
                diff = (spot_price - strike) if inst.option_right == OptionRight.CALL else (strike - spot_price)
                intrinsic = max(0.0, diff)
                time_val = max(10.0, 150.0 - abs(diff) * 0.15)
                ltp = round(intrinsic + time_val, 2)
                change = 0.5
                vol = 12500
                oi = 45000

            data = {
                "instrument_id": inst.instrument_id,
                "symbol": inst.stock_code,
                "ltp": ltp,
                "change_pct": change,
                "volume": vol,
                "open_interest": oi,
                "bid": round(max(0.05, ltp - 0.25), 2),
                "ask": round(ltp + 0.25, 2),
                "lot_size": inst.lot_size,
            }

            if inst.option_right == OptionRight.CALL:
                strikes_map[strike]["call"] = data
            else:
                strikes_map[strike]["put"] = data

        sorted_strikes = [strikes_map[k] for k in sorted(strikes_map.keys())]

        return {
            "underlying": underlying,
            "spot_price": spot_price,
            "expiry": selected_expiry,
            "available_expiries": expiries,
            "strikes": sorted_strikes,
        }

