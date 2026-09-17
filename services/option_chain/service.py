"""Option Chain Service synthesizing instrument definitions and live market quotes into chain matrix.
"""

from __future__ import annotations

from datetime import date, datetime
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
        broker_gateway: Optional[Any] = None,
    ) -> None:
        self.inst_svc = instrument_service
        self.mkt_svc = market_data_service
        self.broker_gateway = broker_gateway

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        self.broker_gateway = broker_gateway

    async def get_chain(
        self,
        underlying: str = "NIFTY",
        expiry: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build matrix of option strikes with Call and Put pricing."""
        clean_underlying = underlying.upper().strip()
        if "BANK" in clean_underlying:
            clean_underlying = "BANKNIFTY"
        else:
            clean_underlying = "NIFTY"

        if clean_underlying == "BANKNIFTY":
            default_expiries = ["2026-09-29", "2026-10-27", "2026-11-23"]
        else:
            default_expiries = ["2026-09-22", "2026-09-29", "2026-10-06", "2026-10-13", "2026-10-27", "2026-11-23"]

        expiries = await self.inst_svc.get_expiries(clean_underlying)
        all_expiries = list(dict.fromkeys(default_expiries + (expiries or [])))
        selected_expiry = expiry if (expiry and expiry in all_expiries) else all_expiries[0]

        # 1. Resolve realistic spot price
        inst_spot_id = f"INST-{clean_underlying}-INDEX"
        spot_quote = self.mkt_svc.get_latest_quote(inst_spot_id)
        if spot_quote:
            spot_price = spot_quote.last_price
        else:
            spot_price = 56292.45 if clean_underlying == "BANKNIFTY" else 23217.60

        step = 100 if clean_underlying == "BANKNIFTY" else 50
        atm_strike = round(spot_price / step) * step

        # 2. Attempt to fetch live option chain directly from ICICI Breeze SDK
        breeze_active = False
        if self.broker_gateway:
            breeze_adapter = getattr(self.broker_gateway, "breeze_adapter", None)
            if breeze_adapter and hasattr(breeze_adapter, "client_manager"):
                breeze_active = getattr(breeze_adapter.client_manager, "is_active", False)

        if breeze_active:
            try:
                try:
                    exp_date = datetime.strptime(selected_expiry, "%Y-%m-%d").date()
                except Exception:
                    exp_date = date(2026, 9, 24)

                logger.info(
                    "Fetching live Option Chain from ICICI Breeze: underlying=%s expiry=%s",
                    clean_underlying,
                    exp_date,
                )
                breeze_chain = await self.broker_gateway.clean_breeze_service.get_option_chain(
                    underlying=clean_underlying,
                    expiry=exp_date,
                    exchange="NFO",
                )

                if breeze_chain and breeze_chain.contracts:
                    strikes_map: dict[float, dict[str, Any]] = {}
                    for c in breeze_chain.contracts:
                        s = float(c.strike_price)
                        if s not in strikes_map:
                            strikes_map[s] = {"strike": s, "call": None, "put": None}

                        right_key = "call" if c.right == OptionRight.CALL else "put"
                        opt_code = f"{clean_underlying}{int(s)}{'CE' if c.right == OptionRight.CALL else 'PE'}"
                        inst_id = f"INST-{clean_underlying}-{selected_expiry}-{int(s)}-{'CE' if c.right == OptionRight.CALL else 'PE'}"

                        strikes_map[s][right_key] = {
                            "instrument_id": inst_id,
                            "symbol": opt_code,
                            "ltp": float(c.ltp),
                            "change_pct": 0.0,
                            "volume": c.volume or 0,
                            "open_interest": c.open_interest or 0,
                            "bid": float(c.bid) if c.bid else round(max(0.05, float(c.ltp) - 0.25), 2),
                            "ask": float(c.ask) if c.ask else round(float(c.ltp) + 0.25, 2),
                            "lot_size": 15 if clean_underlying == "BANKNIFTY" else 25,
                        }

                    sorted_strikes = [strikes_map[k] for k in sorted(strikes_map.keys())]
                    live_spot = float(breeze_chain.spot_price) if breeze_chain.spot_price else spot_price

                    return {
                        "underlying": clean_underlying,
                        "spot_price": live_spot,
                        "expiry": selected_expiry,
                        "available_expiries": all_expiries,
                        "atm_strike": round(live_spot / step) * step,
                        "source": "BREEZE",
                        "strikes": sorted_strikes,
                    }
            except Exception as exc:
                logger.warning("Live Breeze option chain query error: %s; falling back to complete synthetic strikes.", exc)

        # 3. Fallback / Offline / Market Closed Complete Strike Matrix
        instruments = await self.inst_svc.get_option_chain_instruments(
            underlying=clean_underlying, expiry=selected_expiry
        )

        # If instruments are empty or missing strikes around spot (e.g. only > 24000 strikes were present):
        existing_strikes = [i.strike for i in instruments if i.strike is not None]
        has_atm_coverage = bool(existing_strikes and min(existing_strikes) <= (atm_strike - 200) and max(existing_strikes) >= (atm_strike + 200))
        if not has_atm_coverage:
            logger.info("Reseeding complete strike spectrum around spot ₹%s for %s", spot_price, clean_underlying)
            instruments = await self.inst_svc.seed_strikes_around_spot(
                underlying=clean_underlying,
                spot_price=spot_price,
                expiry=selected_expiry,
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

            # Lookup live quote or calculate realistic options premium
            quote = self.mkt_svc.get_latest_quote(inst.instrument_id)
            if quote:
                ltp = quote.last_price
                change = quote.change_pct
                vol = quote.volume
                oi = quote.open_interest
            else:
                # Black-Scholes / Intrinsic approximation
                diff = (spot_price - strike) if inst.option_right == OptionRight.CALL else (strike - spot_price)
                intrinsic = max(0.0, diff)
                time_val = max(5.0, 180.0 - abs(diff) * 0.12)
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
            "underlying": clean_underlying,
            "spot_price": spot_price,
            "expiry": selected_expiry,
            "available_expiries": expiries,
            "atm_strike": atm_strike,
            "source": "SIMULATED",
            "strikes": sorted_strikes,
        }
