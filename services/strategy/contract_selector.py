"""Deterministic, delta-aware NIFTY option contract selection."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from services.strategy.models import OptionSelectionConfig, OptionType, SelectedContract, TradeDirection


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo and value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
        except ValueError:
            return None
    return None


def trading_sessions_remaining(as_of: date, expiry: date, holidays: set[date] | None = None) -> int:
    """Count exchange sessions strictly after ``as_of`` through expiry."""
    holidays = holidays or set()
    cursor = as_of + timedelta(days=1)
    count = 0
    while cursor <= expiry:
        if cursor.weekday() < 5 and cursor not in holidays:
            count += 1
        cursor += timedelta(days=1)
    return count


class ContractSelector:
    """Select the execution vehicle only after an underlying signal exists."""

    def __init__(self, config: Optional[OptionSelectionConfig] = None) -> None:
        self.config = config or OptionSelectionConfig()

    def _eligible_expiry(self, expiry: str | None, *, as_of: datetime, holidays: set[date]) -> bool:
        if not expiry:
            return False
        try:
            expiry_date = date.fromisoformat(str(expiry)[:10])
        except ValueError:
            return False
        return trading_sessions_remaining(as_of.date(), expiry_date, holidays) >= self.config.minimum_expiry_sessions_remaining

    def select_contract(
        self,
        direction: TradeDirection,
        spot_price: float | None = None,
        option_chain: dict[str, Any] | None = None,
        override_premium_cap: Optional[float] = None,
        *,
        underlying_price: float | None = None,
        as_of: datetime | None = None,
        strategy_a: bool = True,
    ) -> tuple[Optional[SelectedContract], list[dict[str, Any]], Optional[str]]:
        """Return ``(contract, inspected candidates, rejection reason)``.

        For Strategy A, premium is metadata only: it never filters moneyness.
        The optional ``strategy_a=False`` compatibility path is reserved for
        Strategy B and retains the old premium/liquidity behavior.
        """
        chain = option_chain or {}
        underlying = float(underlying_price if underlying_price is not None else (spot_price or 0.0))
        if underlying <= 0:
            return None, [], "INVALID_UNDERLYING_PRICE"
        now = as_of or _parse_timestamp(chain.get("timestamp"))
        if now is None:
            if strategy_a:
                return None, [], "MISSING_SELECTION_TIMESTAMP"
            now = datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            return None, [], "NAIVE_SELECTION_TIMESTAMP"
        option_type = OptionType.CALL if direction is TradeDirection.BULLISH else OptionType.PUT
        leg_key = "call" if option_type is OptionType.CALL else "put"
        strikes_data = chain.get("strikes", [])
        if not strikes_data:
            return None, [], "NO_STRIKES_IN_OPTION_CHAIN"
        holidays = set(self.config.exchange_holidays)
        holidays.update(date.fromisoformat(str(x)[:10]) for x in chain.get("holidays", []))
        expiry_values = sorted({str((s.get(leg_key) or {}).get("expiry") or s.get("expiry") or chain.get("expiry")) for s in strikes_data})
        eligible_expiries = [x for x in expiry_values if x != "None" and self._eligible_expiry(x, as_of=now, holidays=holidays)]
        if not eligible_expiries:
            return None, [], "NO_ELIGIBLE_EXPIRY_TWO_TRADING_SESSIONS"
        nearest_expiry = eligible_expiries[0]
        candidates: list[dict[str, Any]] = []
        inspected: list[dict[str, Any]] = []
        for row in sorted(strikes_data, key=lambda item: float(item.get("strike", 0))):
            strike = float(row.get("strike", 0) or 0)
            leg = row.get(leg_key) or {}
            expiry = str(leg.get("expiry") or row.get("expiry") or chain.get("expiry") or "")
            delta = leg.get("delta")
            if delta is None and isinstance(leg.get("greeks"), dict):
                delta = leg["greeks"].get("delta")
            source = str(leg.get("delta_source") or leg.get("greek_source") or ("BROKER" if delta is not None else "UNAVAILABLE")).upper()
            bid = float(leg.get("bid", 0) or 0)
            ask = float(leg.get("ask", 0) or 0)
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
            spread = ask - bid if bid > 0 and ask > 0 else 0.0
            spread_pct = spread / mid * 100 if mid else float("inf")
            raw_quote_timestamp = leg.get("quote_timestamp") or leg.get("timestamp") or chain.get("timestamp")
            quote_timestamp = _parse_timestamp(raw_quote_timestamp)
            freshness = (now - quote_timestamp).total_seconds() if quote_timestamp else None
            greeks = leg.get("greeks") if isinstance(leg.get("greeks"), dict) else {}
            # Broker snapshots commonly publish Greeks and quote together. If
            # no separate Greek timestamp exists, the quote timestamp is the
            # explicit provenance timestamp for that same snapshot.
            raw_greek_timestamp = leg.get("greek_timestamp") or greeks.get("timestamp") or raw_quote_timestamp
            greek_timestamp = _parse_timestamp(raw_greek_timestamp)
            info = {
                "strike": strike, "option_type": option_type.value, "expiry": expiry,
                "bid": bid, "ask": ask, "mid": mid, "spread_points": spread,
                "spread_pct": round(spread_pct, 4) if spread_pct != float("inf") else None,
                "delta": float(delta) if delta is not None else None,
                "gamma": float(leg["gamma"]) if leg.get("gamma") is not None else (float(leg["greeks"]["gamma"]) if isinstance(leg.get("greeks"), dict) and leg["greeks"].get("gamma") is not None else None),
                "greek_source": source, "greek_timestamp": greek_timestamp.isoformat() if greek_timestamp else None,
                "quote_timestamp": quote_timestamp.isoformat() if quote_timestamp else None,
                "quote_freshness_seconds": freshness, "open_interest": int(leg.get("open_interest", 0) or 0),
                "volume": int(leg.get("volume", 0) or 0), "lot_size": int(leg.get("lot_size", 0) or 0),
                "instrument_id": leg.get("instrument_id"), "instrument_token": leg.get("instrument_token") or leg.get("token"),
                "ltp": float(leg.get("ltp", 0) or 0),
            }
            info["status"] = "INSPECTED"
            inspected.append(info)
            if not strategy_a:
                if expiry != nearest_expiry or not info["instrument_id"] or info["lot_size"] <= 0 or bid <= 0 or ask < bid:
                    info["status"] = "REJECTED_METADATA_OR_QUOTE"; continue
                cap = override_premium_cap if override_premium_cap is not None else self.config.max_option_premium
                if ask > cap or ask < self.config.min_option_premium or info["open_interest"] < self.config.min_open_interest or spread_pct > self.config.max_bid_ask_spread_pct:
                    info["status"] = "REJECTED_LEGACY_LIQUIDITY"; continue
                info["status"] = "ELIGIBLE"; candidates.append(info); continue
            if expiry != nearest_expiry:
                info["status"] = "REJECTED_NOT_NEAREST_ELIGIBLE_EXPIRY"; continue
            if not info["instrument_id"] or info["lot_size"] <= 0:
                info["status"] = "REJECTED_MISSING_CONTRACT_METADATA"; continue
            if bid <= 0 or ask <= 0 or ask < bid:
                info["status"] = "REJECTED_INVALID_QUOTE"; continue
            if info["delta"] is None or not (self.config.allowed_delta_min <= abs(info["delta"]) <= self.config.allowed_delta_max):
                info["status"] = "REJECTED_DELTA_UNAVAILABLE_OR_OUT_OF_RANGE"; continue
            if raw_quote_timestamp is None:
                info["status"] = "REJECTED_MISSING_QUOTE_TIMESTAMP"; continue
            if quote_timestamp is None:
                info["status"] = "REJECTED_INVALID_QUOTE_TIMESTAMP"; continue
            if freshness < 0:
                info["status"] = "REJECTED_FUTURE_QUOTE_TIMESTAMP"; continue
            if freshness > self.config.max_quote_age_seconds:
                info["status"] = "REJECTED_STALE_QUOTE"; continue
            if spread_pct > self.config.max_bid_ask_spread_pct:
                info["status"] = "REJECTED_WIDE_SPREAD"; continue
            if info["volume"] < self.config.minimum_volume or info["open_interest"] < self.config.min_open_interest:
                info["status"] = "REJECTED_LIQUIDITY"; continue
            info["status"] = "ELIGIBLE"
            delta_abs = abs(info["delta"])
            info["preferred_delta"] = self.config.preferred_delta_min <= delta_abs <= self.config.preferred_delta_max
            info["delta_distance"] = 0.0 if info["preferred_delta"] else min(abs(delta_abs - self.config.preferred_delta_min), abs(delta_abs - self.config.preferred_delta_max))
            candidates.append(info)
        if not candidates:
            return None, inspected, "NO_ELIGIBLE_DELTA_AWARE_CONTRACT"
        if strategy_a:
            candidates.sort(key=lambda x: (0 if x["preferred_delta"] else 1, x["delta_distance"], x["quote_freshness_seconds"], x["spread_pct"] or float("inf"), -x["open_interest"], -x["volume"], x["strike"], x["instrument_id"] or ""))
        else:
            candidates.sort(key=lambda x: (-x["ask"], x["spread_pct"], -x["open_interest"], x["strike"], x["instrument_id"] or ""))
        best = candidates[0]
        selected = SelectedContract(
            instrument_id=best["instrument_id"], symbol=f"NIFTY {best['strike']} {best['option_type']}",
            expiry=best["expiry"], strike=best["strike"], option_type=option_type,
            ask_price=best["ask"], bid_price=best["bid"], open_interest=best["open_interest"], volume=best["volume"],
            spread_pct=float(best["spread_pct"] or 0), lot_size=best["lot_size"], ltp=best["ltp"],
            instrument_token=best["instrument_token"], premium=best["ask"], delta=best.get("delta"), gamma=best.get("gamma"),
            greek_source=best.get("greek_source", "UNAVAILABLE"), greek_timestamp=_parse_timestamp(best.get("greek_timestamp")),
            quote_timestamp=_parse_timestamp(best.get("quote_timestamp")), quote_freshness_seconds=best.get("quote_freshness_seconds"),
            mid_price=best.get("mid"), spread_points=best.get("spread_points"), selection_metadata={"strategy_a": strategy_a, "expiry_sessions_remaining": trading_sessions_remaining(now.date(), date.fromisoformat(best["expiry"][:10]), holidays), "ranking": "preferred_band, distance_to_band, freshness, spread, oi, volume, strike, instrument_id"},
        )
        return selected, inspected, None
