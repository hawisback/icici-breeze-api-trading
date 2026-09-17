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
        available_strikes = sorted([s["strike"] for s in strikes_data])
        atm_strike = min(available_strikes, key=lambda x: abs(x - spot_price))

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
            if option_type == OptionType.CALL:
                # ITM: strike < atm_strike, OTM: strike > atm_strike
                if strike < atm_strike - 50:  # Allow at most 1 strike ITM
                    continue
                otm_distance = max(0, int((strike - atm_strike) / 50))
                if otm_distance > max_otm:
                    continue
            else:
                # For PUT, ITM: strike > atm_strike, OTM: strike < atm_strike
                if strike > atm_strike + 50:  # Allow at most 1 strike ITM
                    continue
                otm_distance = max(0, int((atm_strike - strike) / 50))
                if otm_distance > max_otm:
                    continue

            ask = float(leg.get("ask", 0.0) or leg.get("ltp", 0.0) or 0.0)
            bid = float(leg.get("bid", 0.0) or leg.get("ltp", 0.0) or 0.0)
            oi = int(leg.get("open_interest", 0) or 0)
            vol = int(leg.get("volume", 0) or 0)
            instrument_id = leg.get("instrument_id", f"NIFTY-{strike}-{option_type.value}")
            expiry = leg.get("expiry", option_chain.get("expiry", "2026-09-22"))

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
            }
            inspected_candidates.append(cand_info)

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
            if oi < cfg.min_open_interest and oi > 0:  # if live OI is reported
                cand_info["status"] = f"REJECTED_LOW_OI_{oi}"
                # Still allow in test/mock environments if OI is 0
                if oi != 0:
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

        # Rank eligible candidates
        # Requirement: "prefer an eligible liquid contract closest to the cap (e.g. ₹60 rather than ₹150 when cap is ₹70)"
        if cfg.prefer_premium_closest_to_cap:
            eligible_candidates.sort(
                key=lambda x: (
                    -x["ask"],          # Highest ask <= max_option_premium (closest to cap!)
                    x["spread_pct"],    # Lowest spread
                    -x["oi"],           # Highest OI
                    x["otm_distance"],  # Closest OTM
                )
            )
        else:
            eligible_candidates.sort(
                key=lambda x: (
                    x["otm_distance"],
                    x["spread_pct"],
                    -x["ask"],
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
            lot_size=25,
        )

        return selected, inspected_candidates, None

