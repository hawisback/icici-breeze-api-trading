"""Option Chain Service synthesizing instrument definitions and live market quotes into chain matrix.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import logging
from time import monotonic
from typing import Any, Optional
from services.option_chain.oi_baselines import KiteSessionOIBaselines
import asyncio

from libs.contracts.models import OptionRight
from libs.market_time import ist_today
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
        oi_baselines: KiteSessionOIBaselines | None = None,
    ) -> None:
        self.inst_svc = instrument_service
        self.mkt_svc = market_data_service
        self.broker_gateway = broker_gateway
        self.oi_baselines = oi_baselines
        # Share one bounded Kite chain across AI, UI, strategy and PAPER worker.
        # Keep exchange timestamps unchanged: retrieval time is NOT quote freshness.
        self._kite_chain_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._kite_chain_cache_ttl = 10.0
        self._kite_chain_retry_after = 0.0
        self._kite_chain_lock = asyncio.Lock()
        # Expiry metadata changes slowly, unlike contract bid/ask quotes.
        self._kite_expiries_cache: dict[str, tuple[float, list[str]]] = {}
        self._kite_expiries_cache_ttl = 60.0
        self._kite_expiries_lock = asyncio.Lock()
        self._wire_kite_market_quote_getter()

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        self.broker_gateway = broker_gateway
        self._kite_chain_cache.clear()
        self._kite_expiries_cache.clear()
        self._wire_kite_market_quote_getter()

    def _wire_kite_market_quote_getter(self) -> None:
        """Reuse a genuinely fresh Kite index tick instead of an extra LTP call."""
        adapter = getattr(self.broker_gateway, "kite_adapter", None)
        setter = getattr(adapter, "set_market_quote_getter", None)
        getter = getattr(self.mkt_svc, "get_latest_quote", None)
        if callable(setter) and callable(getter):
            setter(getter)

    def _cached_kite_chain(self, underlying: str, expiry: str | None) -> dict[str, Any] | None:
        """Return a real-time bounded-cache hit before even reading expiry metadata."""
        now = monotonic()
        # An explicit far-expiry request must never change the implicit
        # nearest-listed-expiry default for other callers.
        if expiry is None:
            expiry_entry = self._kite_expiries_cache.get(underlying)
            if not expiry_entry or now - expiry_entry[0] >= self._kite_expiries_cache_ttl:
                return None
            future = [e for e in expiry_entry[1] if e >= ist_today().isoformat()]
            if not future:
                return None
            expiry = min(future)
        selected: tuple[str, dict[str, Any]] | None = None
        for (name, cached_expiry), (captured, chain) in self._kite_chain_cache.items():
            if name != underlying or (expiry and expiry != cached_expiry):
                continue
            if cached_expiry < ist_today().isoformat() or now - captured >= self._kite_chain_cache_ttl:
                continue
            if selected is None or cached_expiry < selected[0]:
                selected = (cached_expiry, chain)
        return deepcopy(selected[1]) if selected else None

    async def _kite_expiries(self, underlying: str, adapter: Any) -> list[str]:
        now = monotonic()
        existing = self._kite_expiries_cache.get(underlying)
        if existing and now - existing[0] < self._kite_expiries_cache_ttl:
            return [e for e in existing[1] if e >= ist_today().isoformat()]
        async with self._kite_expiries_lock:
            now = monotonic()
            existing = self._kite_expiries_cache.get(underlying)
            if existing and now - existing[0] < self._kite_expiries_cache_ttl:
                return [e for e in existing[1] if e >= ist_today().isoformat()]
            expiries = sorted({
                str(e)[:10]
                for e in await adapter.get_option_expiries(underlying)
                if str(e)[:10] >= ist_today().isoformat()
            })
            if expiries:
                self._kite_expiries_cache[underlying] = (monotonic(), expiries)
            return expiries

    async def get_contract_quote(self, instrument_id: str) -> dict[str, Any]:
        """One Kite NFO contract quote for AI trade entry/stop management.

        Shares Kite's already-loaded NFO instrument list and single broker
        quote pacing; never falls back to Breeze or the synthetic chain.
        """
        adapter = getattr(self.broker_gateway, "kite_adapter", None)
        if not adapter or not getattr(adapter, "is_active", False):
            return {}
        get_quote = getattr(adapter, "get_option_contract_quote", None)
        if not callable(get_quote):
            return {}
        return await get_quote(instrument_id)

    async def get_chain(
        self,
        underlying: str = "NIFTY",
        expiry: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build matrix of option strikes with Call and Put pricing."""
        clean_underlying = underlying.upper().strip()
        if "BANK" in clean_underlying:
            clean_underlying = "BANKNIFTY"
        else:
            clean_underlying = "NIFTY"

        # All normal option consumers (AI, UI and internal strategies) use Kite.
        # Explicit Breeze remains a legacy opt-in for diagnostics only.
        requested_provider = str(provider or ("kite" if self.broker_gateway else "")).strip().lower()
        if requested_provider and requested_provider not in {"kite", "breeze"}:
            raise ValueError(f"Unsupported option-chain provider: {provider}")

        # The fast path avoids scanning Kite's NFO expiry metadata and avoids
        # broker calls from repeated PAPER worker / UI / AI reads.
        kite = getattr(self.broker_gateway, "kite_adapter", None)
        if (
            requested_provider == "kite"
            and kite is not None
            and getattr(kite, "is_active", False)
        ):
            cached_chain = self._cached_kite_chain(clean_underlying, expiry)
            if cached_chain is not None:
                return cached_chain

        all_expiries: list[str] = []
        reference_provider = (
            str(
                getattr(
                    self.broker_gateway,
                    "reference_data_broker_name",
                    "",
                )
                or ""
            ).lower()
            if self.broker_gateway
            else ""
        )
        reference_adapter = (
            getattr(self.broker_gateway, "reference_data_adapter", None)
            if self.broker_gateway
            else None
        )
        if self.broker_gateway and reference_provider not in {"breeze", "kite"}:
            reference_provider = str(
                getattr(self.broker_gateway, "active_broker_name", "")
                or ""
            ).lower()
            reference_adapter = getattr(
                self.broker_gateway,
                "active_adapter",
                reference_adapter,
            )

        # AI/read-only callers can explicitly request Kite. In that mode Kite's
        # live NFO instrument dump is the source of truth for expiries/contracts;
        # the local option master must not gate the request.
        if requested_provider and self.broker_gateway:
            requested_adapter = getattr(
                self.broker_gateway,
                f"{requested_provider}_adapter",
                None,
            )
            if requested_adapter is None:
                adapter_for_broker = getattr(
                    self.broker_gateway,
                    "adapter_for_broker",
                    None,
                )
                if callable(adapter_for_broker):
                    try:
                        requested_adapter = adapter_for_broker(requested_provider)
                    except (TypeError, ValueError):
                        requested_adapter = None
            reference_provider = requested_provider
            reference_adapter = requested_adapter

        if (
            reference_provider == "kite"
            and reference_adapter
            and getattr(reference_adapter, "is_active", False)
        ):
            get_expiries = getattr(reference_adapter, "get_option_expiries", None)
            if callable(get_expiries):
                try:
                    all_expiries = await self._kite_expiries(
                        clean_underlying, reference_adapter
                    )
                except Exception as exc:
                    logger.warning("Unable to refresh Kite option expiries: %s", exc)

        # Preserve local-master fallback only for explicit legacy Breeze/offline
        # consumers. All default Kite requests intentionally fail closed:
        # if Kite cannot supply current expiries, do not substitute stale local
        # contracts or another broker.
        if not all_expiries and requested_provider != "kite":
            local_expiries = await self.inst_svc.get_expiries(clean_underlying)
            all_expiries = sorted(
                e for e in (local_expiries or [])
                if e >= ist_today().isoformat()
            )

        if not all_expiries:
            rejection_reason = (
                "KITE_OPTION_CHAIN_UNAVAILABLE"
                if requested_provider == "kite"
                else "OPTION_CHAIN_UNAVAILABLE"
            )
            return {
                "underlying": clean_underlying,
                "source": "UNAVAILABLE",
                "strikes": [],
                "capabilities": {
                    "verified_delta_available": False,
                    "verified_greeks_available": False,
                    "strategy_a_contract_selection_ready": False,
                    "strategy_a_rejection_reason": rejection_reason,
                },
            }
        # Never silently substitute a different expiry for a direct Kite AI call.
        if requested_provider == "kite" and expiry and expiry not in all_expiries:
            return {
                "underlying": clean_underlying,
                "source": "UNAVAILABLE",
                "expiry": expiry,
                "available_expiries": all_expiries,
                "strikes": [],
                "capabilities": {
                    "verified_delta_available": False,
                    "verified_greeks_available": False,
                    "strategy_a_contract_selection_ready": False,
                    "strategy_a_rejection_reason": "KITE_EXPIRY_NOT_AVAILABLE",
                },
            }
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

        # 2. Option-chain data defaults to Kite regardless of LIVE broker
        # execution ownership. Explicit Breeze is legacy opt-in only.
        breeze_active = False
        if self.broker_gateway and reference_provider == "breeze":
            breeze_adapter = reference_adapter
            if breeze_adapter and hasattr(breeze_adapter, "client_manager"):
                breeze_active = getattr(
                    breeze_adapter.client_manager,
                    "is_active",
                    False,
                )

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
                        metadata = await self.inst_svc.get_instrument(inst_id)
                        if not metadata or not metadata.tradable or metadata.lot_size <= 0:
                            continue

                        strikes_map[s][right_key] = {
                            "instrument_id": inst_id,
                            "symbol": opt_code,
                            "ltp": float(c.ltp),
                            "change_pct": 0.0,
                            "volume": c.volume or 0,
                            "open_interest": c.open_interest or 0,
                            "oi_change": c.oi_change,
                            "bid": float(c.bid or 0),
                            "ask": float(c.ask or 0),
                            "lot_size": metadata.lot_size,
                        }

                    sorted_strikes = [strikes_map[k] for k in sorted(strikes_map.keys())]
                    live_spot = float(breeze_chain.spot_price) if breeze_chain.spot_price else spot_price

                    captured_at = datetime.now(timezone.utc).isoformat()
                    return {
                        "underlying": clean_underlying,
                        "spot_price": live_spot,
                        "expiry": selected_expiry,
                        "available_expiries": all_expiries,
                        "atm_strike": round(live_spot / step) * step,
                        "source": "BREEZE",
                        "captured_at": captured_at,
                        "timestamp": captured_at,
                        "capabilities": {
                            "verified_delta_available": False,
                            "verified_greeks_available": False,
                            "strategy_a_contract_selection_ready": False,
                            "strategy_a_rejection_reason": (
                                "BREEZE_VERIFIED_OPTION_GREEKS_UNAVAILABLE"
                            ),
                        },
                        "strikes": sorted_strikes,
                    }
            except Exception as exc:
                logger.warning("Live Breeze option chain query error: %s; falling back to complete synthetic strikes.", exc)

        # Kite returns exchange-valid tradingsymbols, so route its live chain directly
        # to the UI shape and avoid rebuilding contracts from synthetic local symbols.
        if self.broker_gateway:
            if (
                reference_provider == "kite"
                and reference_adapter
                and callable(
                    getattr(reference_adapter, "get_option_chain_view", None)
                )
            ):
                cache_key = (clean_underlying, selected_expiry)
                cached = self._kite_chain_cache.get(cache_key)
                if cached and monotonic() - cached[0] < self._kite_chain_cache_ttl:
                    return deepcopy(cached[1])

                async with self._kite_chain_lock:
                    cached = self._kite_chain_cache.get(cache_key)
                    if cached and monotonic() - cached[0] < self._kite_chain_cache_ttl:
                        return deepcopy(cached[1])

                    if monotonic() >= self._kite_chain_retry_after:
                        try:
                            kite_chain = await reference_adapter.get_option_chain_view(
                                underlying=clean_underlying,
                                expiry=selected_expiry,
                            )
                            if kite_chain.get("strikes"):
                                if self.oi_baselines:
                                    await self.oi_baselines.enrich(kite_chain)
                                kite_chain["oi_change_basis"] = "FIRST_OBSERVED_SESSION" if self.oi_baselines else "UNAVAILABLE"
                                kite_chain["available_expiries"] = all_expiries or kite_chain.get("available_expiries", [])
                                captured_at = datetime.now(timezone.utc).isoformat()
                                kite_chain["captured_at"] = captured_at
                                kite_chain["timestamp"] = captured_at
                                kite_chain["capabilities"] = {
                                    "verified_delta_available": False,
                                    "verified_greeks_available": False,
                                    "strategy_a_contract_selection_ready": False,
                                    "strategy_a_rejection_reason": (
                                        "KITE_VERIFIED_OPTION_GREEKS_UNAVAILABLE"
                                    ),
                                }
                                self._kite_chain_cache[cache_key] = (monotonic(), kite_chain)
                                self._kite_chain_retry_after = 0.0
                                return deepcopy(kite_chain)
                        except Exception as exc:
                            self._kite_chain_retry_after = monotonic() + 15.0
                            logger.warning("Live Kite option chain query error: %s", type(exc).__name__)

        # A Kite option-data caller must never trigger Breeze or a simulated
        # local-master fallback, even on timeout or missing quotes.
        if requested_provider == "kite":
            return {
                "underlying": clean_underlying,
                "source": "UNAVAILABLE",
                "expiry": selected_expiry,
                "available_expiries": all_expiries,
                "strikes": [],
                "capabilities": {
                    "verified_delta_available": False,
                    "verified_greeks_available": False,
                    "strategy_a_contract_selection_ready": False,
                    "strategy_a_rejection_reason": "KITE_OPTION_CHAIN_UNAVAILABLE",
                },
            }

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
            "available_expiries": all_expiries,
            "atm_strike": atm_strike,
            "source": "SIMULATED",
            "capabilities": {
                "verified_delta_available": False,
                "verified_greeks_available": False,
                "strategy_a_contract_selection_ready": False,
                "strategy_a_rejection_reason": (
                    "SIMULATED_OPTION_CHAIN_NOT_EXECUTABLE"
                ),
            },
            "strikes": sorted_strikes,
        }
