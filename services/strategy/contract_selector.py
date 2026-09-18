"""Contract Selection Engine for NIFTY Intraday Options Auto-Trading.
Implements Section 16 of NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
Enforces max option premium cap (default ₹70), liquidity criteria, and strike discovery.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from services.strategy.models import (
    OptionSelectionConfig,
    OptionType,
    SelectedContract,
    TradeDirection,
)

logger = logging.getLogger(__name__)


class ContractSelector:
    """Discovers and ranks candidate option contracts based on premium cap and liquidity."""

    def __init__(self, config: Optional[OptionSelectionConfig] = None) -> None:
        self.config = config or OptionSelectionConfig()

    def select_contract(
        self,
        direction: TradeDirection,
        spot_price: float,
        option_chain: dict[str, Any],
        override_premium_cap: Optional[float] = None,
    ) -> tuple[Optional[SelectedContract], list[dict[str, Any]], Optional[str]]:
        """Selects the best option contract meeting all criteria under the max premium cap.
        
        Returns:
            (selected_contract, candidates_examined, rejection_reason)
        """
        option_type = OptionType.CALL if direction == TradeDirection.BULLISH else OptionType.PUT
        chain_type_key = "call" if option_type == OptionType.CALL else "put"

        strikes_data = option_chain.get("strikes", [])
        if not strikes_data:
            return None, [], "NO_STRIKES_IN_OPTION_CHAIN"

        # Determine ATM strike
        available_strikes = sorted({s["strike"] for s in strikes_data})
        atm_strike = min(available_strikes, key=lambda x: abs(x - spot_price))
        atm_index = available_strikes.index(atm_strike)

        # Filter candidates based on direction and max_otm_strikes
        # For CALL: ATM and OTM strikes (strike >= atm_strike) up to max_otm_strikes, plus 1 ITM strike
        # For PUT: ATM and OTM strikes (strike <= atm_strike) down to max_otm_strikes, plus 1 ITM strike
        eligible_candidates: list[dict[str, Any]] = []
        inspected_candidates: list[dict[str, Any]] = []

        cfg = self.config
        max_otm = cfg.max_otm_strikes
        effective_max_premium = override_premium_cap if override_premium_cap is not None else cfg.max_option_premium

        for s in strikes_data:
            strike = s["strike"]
            leg = s.get(chain_type_key)
            if not leg:
                continue

            # Determine OTM distance in strikes
            distance = (available_strikes.index(strike)-atm_index) * (1 if option_type == OptionType.CALL else -1)
            if distance < -1 or distance > max_otm:
                continue
            otm_distance = abs(distance)

            ask = float(leg.get("ask", 0.0) or 0.0)
            bid = float(leg.get("bid", 0.0) or 0.0)
            oi = int(leg.get("open_interest", 0) or 0)
            vol = int(leg.get("volume", 0) or 0)
            instrument_id = leg.get("instrument_id")
            expiry = leg.get("expiry") or option_chain.get("expiry")

            mid = (bid + ask) / 2.0 if (bid + ask) > 0 else ask
            spread_pct = round(((ask - bid) / mid * 100.0), 2) if mid > 0 else 0.0

            cand_info = {
                "strike": strike,
                "option_type": option_type.value,
                "ask": ask,
                "bid": bid,
                "oi": oi,
                "volume": vol,
                "spread_pct": spread_pct,
                "otm_distance": otm_distance,
                "instrument_id": instrument_id,
                "expiry": expiry,
                "lot_size": int(leg.get("lot_size", 0) or 0),
            }
            inspected_candidates.append(cand_info)
            if not instrument_id or not expiry:
                cand_info["status"] = "REJECTED_MISSING_CONTRACT_METADATA"
                continue
            if cand_info["lot_size"] <= 0:
                cand_info["status"] = "REJECTED_MISSING_LOT_METADATA"
                continue
            if bid <= 0 or bid > ask:
                cand_info["status"] = "REJECTED_INVALID_QUOTE"
                continue

            # Verification Filters
            # 1. Prices valid
            if ask <= 0:
                cand_info["status"] = "REJECTED_ZERO_ASK"
                continue
            # 2. Under Max Option Premium Cap
            if ask > effective_max_premium:
                cand_info["status"] = f"REJECTED_EXCEEDS_CAP_{effective_max_premium}"
                continue
            # 3. Above Min Option Premium Floor
            if ask < cfg.min_option_premium:
                cand_info["status"] = f"REJECTED_BELOW_FLOOR_{cfg.min_option_premium}"
                continue
            # 4. Open Interest threshold
            if oi < cfg.min_open_interest:
                cand_info["status"] = f"REJECTED_LOW_OI_{oi}"
                continue
            # 5. Spread limit
            if spread_pct > cfg.max_bid_ask_spread_pct and bid > 0:
                cand_info["status"] = f"REJECTED_WIDE_SPREAD_{spread_pct}%"
                continue

            cand_info["status"] = "ELIGIBLE"
            eligible_candidates.append(cand_info)

        if not eligible_candidates:
            # Check why candidates failed
            reason = "NO_ELIGIBLE_CONTRACTS_UNDER_CAP"
            if any("REJECTED_EXCEEDS_CAP" in c.get("status", "") for c in inspected_candidates):
                reason = f"ALL_STRIKES_EXCEED_PREMIUM_CAP_INR_{cfg.max_option_premium}"
            elif any("REJECTED_BELOW_FLOOR" in c.get("status", "") for c in inspected_candidates):
                reason = f"ALL_STRIKES_BELOW_PREMIUM_FLOOR_INR_{cfg.min_option_premium}"
            return None, inspected_candidates, reason

        # Rank eligible candidates per Section 13.4:
        # 1. Prefer contract closest to ATM / highest usable delta (otm_distance)
        # 2. Then prefer executable ask closest to, but not above, the premium cap (-ask)
        # 3. Prefer tighter spread if otherwise equal (spread_pct)
        # 4. Prefer highest open interest (-oi)
        eligible_candidates.sort(
            key=lambda x: (
                x["otm_distance"],   # Closest to ATM / highest delta first
                -x["ask"],           # Highest ask <= max_option_premium (closest to cap)
                x["spread_pct"],     # Lowest spread
                -x["oi"],            # Highest OI
            )
        )

        best = eligible_candidates[0]
        selected = SelectedContract(
            instrument_id=best["instrument_id"],
            symbol=f"NIFTY {best['strike']} {best['option_type']}",
            expiry=str(best["expiry"]),
            strike=float(best["strike"]),
            option_type=option_type,
            ask_price=float(best["ask"]),
            bid_price=float(best["bid"]),
            open_interest=int(best["oi"]),
            volume=int(best["volume"]),
            spread_pct=float(best["spread_pct"]),
            lot_size=best["lot_size"],
        )

        return selected, inspected_candidates, None
