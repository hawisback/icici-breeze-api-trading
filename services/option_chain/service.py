"""Option Chain Service synthesizing instrument definitions and live market quotes into chain matrix.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import logging
from time import monotonic
from typing import Any, Optional
import asyncio

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
        self._live_chain_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._live_chain_cache_ttl = 5.0
        self._provider_retry_after: dict[str, float] = {}
        self._live_chain_lock = asyncio.Lock()

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
        all_expiries = sorted(e for e in (expiries or []) if e >= date.today().isoformat())
        provider_order = ()
        if self.broker_gateway:
            route = getattr(self.broker_gateway, "provider_order", None)
            routed = route("option_chain") if callable(route) else None
            provider_order = (
                tuple(routed)
                if isinstance(routed, (list, tuple)) and routed
                else (getattr(self.broker_gateway, "active_broker_name", ""),)
            )

        # Prefer the primary provider's authoritative expiry universe when available.
        for provider in provider_order:
            name = provider.value if hasattr(provider, "value") else str(provider)
            if name.lower() != "kite":
                continue
            try:
                adapter = self.broker_gateway.get_broker_adapter(provider)
                if getattr(adapter, "is_active", False):
                    get_expiries = getattr(adapter, "get_option_expiries", None)
                    if callable(get_expiries):
                        live_expiries = await get_expiries(clean_underlying)
                        if live_expiries:
                            all_expiries = live_expiries
            except Exception as exc:
                logger.warning("Unable to refresh Kite option expiries: %s", exc)
            break

        if not all_expiries:
            return {"underlying": clean_underlying, "source": "UNAVAILABLE", "strikes": []}
        selected_expiry = expiry if (expiry and expiry in all_expiries) else all_expiries[0]

        # 1. Resolve realistic spot price from the shared quote cache.
        inst_spot_id = f"INST-{clean_underlying}-INDEX"
        spot_quote = self.mkt_svc.get_latest_quote(inst_spot_id)
        if spot_quote:
            spot_price = spot_quote.last_price
        else:
            spot_price = 56292.45 if clean_underlying == "BANKNIFTY" else 23217.60

        step = 100 if clean_underlying == "BANKNIFTY" else 50
        atm_strike = round(spot_price / step) * step

        # 2. Use a shared provider-aware cache and only fall back when the
        # configured primary is unavailable or fails. This prevents strategy/UI
        # callers from multiplying broker REST traffic.
        for provider in provider_order:
            name = (provider.value if hasattr(provider, "value") else str(provider)).lower()
            cache_key = (name, clean_underlying, selected_expiry)
            cached = self._live_chain_cache.get(cache_key)
            if cached and monotonic() - cached[0] < self._live_chain_cache_ttl:
                return deepcopy(cached[1])
            if monotonic() < self._provider_retry_after.get(name, 0.0):
                continue
            try:
                adapter = self.broker_gateway.get_broker_adapter(provider)
                if not getattr(adapter, "is_active", False):
                    continue

                async with self._live_chain_lock:
                    cached = self._live_chain_cache.get(cache_key)
                    if cached and monotonic() - cached[0] < self._live_chain_cache_ttl:
                        return deepcopy(cached[1])

                    if name == "kite":
                        fetch = getattr(adapter, "get_option_chain_view", None)
                        if not callable(fetch):
                            continue
                        chain = await fetch(
                            underlying=clean_underlying,
                            expiry=selected_expiry,
                        )
                        if chain.get("strikes"):
                            chain["available_expiries"] = all_expiries
                            chain["captured_at"] = datetime.now(timezone.utc).isoformat()
                            self._live_chain_cache[cache_key] = (monotonic(), chain)
                            self._provider_retry_after.pop(name, None)
                            return deepcopy(chain)

                    elif name == "breeze":
                        exp_date = datetime.strptime(selected_expiry, "%Y-%m-%d").date()
                        breeze_chain = await self.broker_gateway.clean_breeze_service.get_option_chain(
                            underlying=clean_underlying,
                            expiry=exp_date,
                            exchange="NFO",
                        )
                        if breeze_chain and breeze_chain.contracts:
                            strikes_map: dict[float, dict[str, Any]] = {}
                            for contract in breeze_chain.contracts:
                                strike = float(contract.strike_price)
                                strikes_map.setdefault(
                                    strike,
                                    {"strike": strike, "call": None, "put": None},
                                )
                                right_key = "call" if contract.right == OptionRight.CALL else "put"
                                suffix = "CE" if contract.right == OptionRight.CALL else "PE"
                                instrument_id = (
                                    f"INST-{clean_underlying}-{selected_expiry}-{int(strike)}-{suffix}"
                                )
                                metadata = await self.inst_svc.get_instrument(instrument_id)
                                if not metadata or not metadata.tradable or metadata.lot_size <= 0:
                                    continue
                                strikes_map[strike][right_key] = {
                                    "instrument_id": instrument_id,
                                    "symbol": f"{clean_underlying}{int(strike)}{suffix}",
                                    "ltp": float(contract.ltp),
                                    "change_pct": 0.0,
                                    "volume": contract.volume or 0,
                                    "open_interest": contract.open_interest or 0,
                                    "oi_change": contract.oi_change,
                                    "bid": float(contract.bid or 0),
                                    "ask": float(contract.ask or 0),
                                    "lot_size": metadata.lot_size,
                                }
                            live_spot = (
                                float(breeze_chain.spot_price)
                                if breeze_chain.spot_price
                                else spot_price
                            )
                            chain = {
                                "underlying": clean_underlying,
                                "spot_price": live_spot,
                                "expiry": selected_expiry,
                                "available_expiries": all_expiries,
                                "atm_strike": round(live_spot / step) * step,
                                "source": "BREEZE",
                                "captured_at": datetime.now(timezone.utc).isoformat(),
                                "strikes": [
                                    strikes_map[key] for key in sorted(strikes_map)
                                ],
                            }
                            if chain["strikes"]:
                                self._live_chain_cache[cache_key] = (monotonic(), chain)
                                self._provider_retry_after.pop(name, None)
                                return deepcopy(chain)
            except Exception as exc:
                self._provider_retry_after[name] = monotonic() + 10.0
                logger.warning("%s option chain query failed: %s", name, exc)

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
