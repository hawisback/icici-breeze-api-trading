"""Instrument Service managing instrument definitions, strikes, and expiries.
"""

from __future__ import annotations

import logging
from typing import Optional

from libs.contracts.models import Instrument, OptionRight
from services.instrument.repository import InstrumentRepository

logger = logging.getLogger(__name__)


class InstrumentService:
    """Service providing instrument lookup, option contract resolution, and contract master seeding."""

    def __init__(self, repository: Optional[InstrumentRepository] = None) -> None:
        self.repo = repository or InstrumentRepository()

    async def initialize(self) -> None:
        await self.repo.initialize()
        await self.seed_default_instruments_if_empty()

    async def seed_default_instruments_if_empty(self) -> None:
        """Seed NIFTY and BANKNIFTY contracts if no instruments exist."""
        existing = await self.repo.search("NIFTY", limit=1)
        if existing:
            return

        logger.info("Seeding default NIFTY & BANKNIFTY option contracts into instruments.db")
        expiry = "2026-09-24"  # Default weekly expiry

        # Seed Spot / Futures indices
        await self.repo.save_instrument(
            Instrument(
                instrument_id="INST-NIFTY-INDEX",
                exchange="NSE",
                segment="EQUITY",
                underlying="NIFTY",
                stock_code="NIFTY 50",
                lot_size=1,
            )
        )
        await self.repo.save_instrument(
            Instrument(
                instrument_id="INST-BANKNIFTY-INDEX",
                exchange="NSE",
                segment="EQUITY",
                underlying="BANKNIFTY",
                stock_code="NIFTY BANK",
                lot_size=1,
            )
        )

        # Seed NIFTY options across strikes 24000 to 25500 (step 100)
        for strike in range(24000, 25600, 100):
            for right, opt_str in [(OptionRight.CALL, "CE"), (OptionRight.PUT, "PE")]:
                inst_id = f"INST-NIFTY-{expiry}-{strike}-{opt_str}"
                symbol = f"NIFTY{strike}{opt_str}"
                await self.repo.save_instrument(
                    Instrument(
                        instrument_id=inst_id,
                        exchange="NFO",
                        segment="OPTIONS",
                        underlying="NIFTY",
                        stock_code=symbol,
                        expiry=expiry,
                        strike=float(strike),
                        option_right=right,
                        lot_size=25,
                        tick_size=0.05,
                    )
                )

        # Seed BANKNIFTY options across strikes 51000 to 53500 (step 100)
        for strike in range(51000, 53600, 100):
            for right, opt_str in [(OptionRight.CALL, "CE"), (OptionRight.PUT, "PE")]:
                inst_id = f"INST-BANKNIFTY-{expiry}-{strike}-{opt_str}"
                symbol = f"BANKNIFTY{strike}{opt_str}"
                await self.repo.save_instrument(
                    Instrument(
                        instrument_id=inst_id,
                        exchange="NFO",
                        segment="OPTIONS",
                        underlying="BANKNIFTY",
                        stock_code=symbol,
                        expiry=expiry,
                        strike=float(strike),
                        option_right=right,
                        lot_size=15,
                        tick_size=0.05,
                    )
                )
        logger.info("Successfully seeded default instruments.")

    async def get_instrument(self, instrument_id: str) -> Optional[Instrument]:
        return await self.repo.get_by_id(instrument_id)

    async def get_by_symbol(self, symbol: str) -> Optional[Instrument]:
        return await self.repo.get_by_symbol(symbol)

    async def search(self, query: str, underlying: Optional[str] = None) -> list[Instrument]:
        return await self.repo.search(query=query, underlying=underlying)

    async def get_expiries(self, underlying: str) -> list[str]:
        return await self.repo.get_expiries(underlying=underlying)

    async def get_option_chain_instruments(self, underlying: str, expiry: str) -> list[Instrument]:
        return await self.repo.get_option_chain_instruments(underlying=underlying, expiry=expiry)

