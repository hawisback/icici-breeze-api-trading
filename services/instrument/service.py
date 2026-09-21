"""Instrument Service managing instrument definitions, strikes, and expiries.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
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
        await self.ensure_current_nifty_futures()

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

        # Seed NIFTY options centered around realistic spot (~23200) across strikes 22000 to 24400 (step 50)
        for strike in range(22000, 24450, 50):
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

        # Seed BANKNIFTY options centered around realistic spot (~56300) across strikes 54500 to 58000 (step 100)
        for strike in range(54500, 58100, 100):
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
        logger.info("Successfully seeded complete NIFTY & BANKNIFTY option strikes around spot.")

    @staticmethod
    def _monthly_expiry(year: int, month: int) -> date:
        """Return the scheduled NIFTY monthly expiry (last Tuesday).

        Exchange-holiday adjustments should come from a broker instrument
        master when one is available; this deterministic fallback keeps the
        Breeze futures path usable with the local instrument repository.
        """
        last_day = monthrange(year, month)[1]
        value = date(year, month, last_day)
        return value.fromordinal(value.toordinal() - ((value.weekday() - 1) % 7))

    async def ensure_current_nifty_futures(self, today: Optional[date] = None) -> list[Instrument]:
        """Ensure near and next NIFTY monthly futures exist in persistent metadata."""
        today = today or date.today()
        near = self._monthly_expiry(today.year, today.month)
        if near < today:
            year = today.year + (1 if today.month == 12 else 0)
            month = 1 if today.month == 12 else today.month + 1
            near = self._monthly_expiry(year, month)
        year = near.year + (1 if near.month == 12 else 0)
        month = 1 if near.month == 12 else near.month + 1
        nxt = self._monthly_expiry(year, month)

        result: list[Instrument] = []
        for expiry in (near, nxt):
            instrument = Instrument(
                instrument_id=f"INST-NIFTY-FUT-{expiry.isoformat()}",
                exchange="NFO",
                segment="FUTURES",
                underlying="NIFTY",
                stock_code="NIFTY",
                expiry=expiry.isoformat(),
                lot_size=25,
                tick_size=0.05,
            )
            await self.repo.save_instrument(instrument)
            result.append(instrument)
        return result

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

    async def seed_strikes_around_spot(
        self,
        underlying: str,
        spot_price: float,
        expiry: str,
    ) -> list[Instrument]:
        """Dynamically seed strikes centered around the actual spot price."""
        is_banknifty = "BANK" in underlying.upper()
        step = 100 if is_banknifty else 50
        lot_size = 15 if is_banknifty else 25
        atm = round(spot_price / step) * step
        start_strike = int(atm - 15 * step)
        end_strike = int(atm + 15 * step)

        seeded: list[Instrument] = []
        for strike in range(start_strike, end_strike + step, step):
            for right, opt_str in [(OptionRight.CALL, "CE"), (OptionRight.PUT, "PE")]:
                inst_id = f"INST-{underlying}-{expiry}-{strike}-{opt_str}"
                symbol = f"{underlying}{strike}{opt_str}"
                inst = Instrument(
                    instrument_id=inst_id,
                    exchange="NFO",
                    segment="OPTIONS",
                    underlying=underlying,
                    stock_code=symbol,
                    expiry=expiry,
                    strike=float(strike),
                    option_right=right,
                    lot_size=lot_size,
                    tick_size=0.05,
                )
                await self.repo.save_instrument(inst)
                seeded.append(inst)
        return seeded


