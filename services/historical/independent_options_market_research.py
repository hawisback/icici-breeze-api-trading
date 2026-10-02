"""Research-only NIFTY near-strike option candle collection.

This module extends an existing independent NIFTY futures research dataset with
raw Breeze NIFTY option candles. It intentionally does not calculate indicators,
signals, strategy outcomes, or stitched option-premium series.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time as clock
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
DEFAULT_STRIKE_STEP = 50
DEFAULT_STRIKE_RADIUS = 4
DEFAULT_OUTPUT = Path("data/independent_nifty_options_research.json")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_local_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _usable_secret(name: str) -> str | None:
    value = os.getenv(name)
    if not value:
        return None
    lowered = value.lower()
    if "your_" in lowered or "change_me" in lowered:
        return None
    return value


def _parse_expiries(values: list[str]) -> list[date]:
    expiries = sorted({date.fromisoformat(value) for value in values})
    if not expiries:
        raise ValueError("At least one explicit --option-expiry is required")
    return expiries


def _nearest_expiry_for_day(day: date, expiries: list[date]) -> date:
    eligible = [expiry for expiry in expiries if expiry >= day]
    if not eligible:
        raise ValueError(
            f"No supplied option expiry covers session {day.isoformat()}; "
            "supply that session's nearest non-expired NIFTY option expiry."
        )
    return min(eligible)


def _atm_strike(price: float, step: int = DEFAULT_STRIKE_STEP) -> int:
    if step <= 0:
        raise ValueError("strike step must be positive")
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"invalid underlying price {price!r}")
    # Deterministic half-up rounding; Python's round() uses bankers rounding.
    return int(math.floor(price / step + 0.5) * step)


def _strike_band(price: float, step: int, radius: int) -> list[int]:
    if radius < 0:
        raise ValueError("strike radius cannot be negative")
    atm = _atm_strike(price, step)
    return [atm + offset * step for offset in range(-radius, radius + 1)]


def _underlying_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(payload.get("canonical_market_rows") or [])
    if not rows:
        raise ValueError("underlying dataset has no canonical_market_rows")
    required = ("timestamp", "futures_close")
    for idx, row in enumerate(rows):
        missing = [key for key in required if row.get(key) is None]
        if missing:
            raise ValueError(f"underlying row {idx} missing {missing}")
    return rows


def _filter_rows_by_date(
    rows: list[dict[str, Any]],
    start_date: date | None,
    end_date: date | None,
) -> list[dict[str, Any]]:
    if start_date and end_date and end_date < start_date:
        raise ValueError("end date must not precede start date")
    filtered = []
    for row in rows:
        day = datetime.fromisoformat(str(row["timestamp"])).date()
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        filtered.append(row)
    if not filtered:
        raise ValueError("date filter selected no underlying rows")
    return filtered


def _selection_plan(
    rows: list[dict[str, Any]],
    expiries: list[date],
    *,
    strike_step: int,
    strike_radius: int,
) -> tuple[dict[str, str], dict[str, list[int]], dict[str, list[str]]]:
    dates = sorted({datetime.fromisoformat(str(row["timestamp"])).date() for row in rows})
    contract_by_date = {
        day.isoformat(): _nearest_expiry_for_day(day, expiries).isoformat()
        for day in dates
    }

    strikes_by_expiry: dict[str, set[int]] = defaultdict(set)
    dates_by_expiry: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        day = ts.date().isoformat()
        expiry = contract_by_date[day]
        strikes_by_expiry[expiry].update(
            _strike_band(float(row["futures_close"]), strike_step, strike_radius)
        )
        dates_by_expiry[expiry].add(day)

    return (
        contract_by_date,
        {key: sorted(value) for key, value in sorted(strikes_by_expiry.items())},
        {key: sorted(value) for key, value in sorted(dates_by_expiry.items())},
    )


class OptionHistoryClient(Protocol):
    source: str

    def history(
        self,
        session_dates: list[date],
        expiry_date: str,
        strike_price: int,
        right: str,
    ) -> list[dict[str, Any]]: ...


class BreezeOptionsClient:
    """Fetch explicit NIFTY option contracts through Breeze Historical V2."""

    source = "BREEZE"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session_token: str,
        breeze_factory: Any | None = None,
        calls_per_minute: int = 80,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_token = session_token
        self._factory = breeze_factory
        if calls_per_minute <= 0:
            raise ValueError("calls_per_minute must be positive")
        self._sdk: Any | None = None
        self._min_request_interval = 60.0 / calls_per_minute
        self._last_request_at: float | None = None
        self.request_diagnostics: list[dict[str, Any]] = []

    def _client(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        factory = self._factory
        if factory is None:
            from breeze_connect import BreezeConnect

            factory = BreezeConnect
        sdk = factory(api_key=self.api_key)
        sdk.generate_session(api_secret=self.api_secret, session_token=self.session_token)
        self._sdk = sdk
        return sdk

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = clock.monotonic() - self._last_request_at
            remaining = self._min_request_interval - elapsed
            if remaining > 0:
                clock.sleep(remaining)
        self._last_request_at = clock.monotonic()

    @staticmethod
    def _request_chunks(session_dates: list[date], max_span_days: int = 9) -> list[list[date]]:
        ordered = sorted(set(session_dates))
        chunks: list[list[date]] = []
        for day in ordered:
            if not chunks or (day - chunks[-1][0]).days > max_span_days:
                chunks.append([day])
            else:
                chunks[-1].append(day)
        return chunks

    def history(
        self,
        session_dates: list[date],
        expiry_date: str,
        strike_price: int,
        right: str,
    ) -> list[dict[str, Any]]:
        if right not in {"call", "put"}:
            raise ValueError("right must be 'call' or 'put'")
        if not session_dates:
            return []

        sdk = self._client()
        expiry = date.fromisoformat(expiry_date)
        expiry_arg = f"{expiry.isoformat()}T07:00:00.000Z"
        wanted = set(session_dates)
        normalized: list[dict[str, Any]] = []

        for chunk in self._request_chunks(session_dates):
            first, last = min(chunk), max(chunk)
            self._throttle()
            response = sdk.get_historical_data_v2(
                interval="5minute",
                from_date=f"{first.isoformat()}T09:15:00.000Z",
                to_date=f"{last.isoformat()}T15:30:00.000Z",
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="options",
                expiry_date=expiry_arg,
                right=right,
                strike_price=str(strike_price),
            )
            success = list((response or {}).get("Success") or [])
            self.request_diagnostics.append(
                {
                    "expiry": expiry.isoformat(),
                    "strike": strike_price,
                    "right": right,
                    "start": first.isoformat(),
                    "end": last.isoformat(),
                    "status": (response or {}).get("Status"),
                    "error": (response or {}).get("Error"),
                    "raw_count": len(success),
                }
            )
            for item in success:
                raw_ts = str(item.get("datetime", ""))
                try:
                    ts = datetime.fromisoformat(raw_ts)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=IST)
                else:
                    ts = ts.astimezone(IST)
                ts = ts.replace(second=0, microsecond=0)
                if ts.date() not in wanted or not (SESSION_START <= ts.time() < SESSION_END):
                    continue
                values = [_number(item.get(key)) for key in ("open", "high", "low", "close")]
                if any(value is None for value in values):
                    continue
                normalized.append(
                    {
                        "timestamp": ts.isoformat(),
                        "expiry": expiry.isoformat(),
                        "strike": int(strike_price),
                        "right": "CE" if right == "call" else "PE",
                        "open": values[0],
                        "high": values[1],
                        "low": values[2],
                        "close": values[3],
                        "volume": _number(item.get("volume")),
                        "open_interest": _number(item.get("open_interest")),
                        "instrument": f"NIFTY {expiry.isoformat()} {strike_price} {'CE' if right == 'call' else 'PE'}",
                        "source": self.source,
                    }
                )

        dedup = {
            (row["timestamp"], row["expiry"], row["strike"], row["right"]): row
            for row in normalized
        }
        return sorted(
            dedup.values(),
            key=lambda row: (row["timestamp"], row["expiry"], row["strike"], row["right"]),
        )


def _quality(
    underlying_rows: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
    contract_by_date: dict[str, str],
    *,
    strike_step: int,
    strike_radius: int,
) -> dict[str, Any]:
    keys = [(r["timestamp"], r["expiry"], r["strike"], r["right"]) for r in option_rows]
    duplicate_rows = len(keys) - len(set(keys))
    invalid_ohlc = sum(
        not (float(r["low"]) <= float(r["open"]) <= float(r["high"]) and
             float(r["low"]) <= float(r["close"]) <= float(r["high"]))
        for r in option_rows
    )
    missing_volume = sum(r.get("volume") is None for r in option_rows)
    missing_oi = sum(r.get("open_interest") is None for r in option_rows)

    available = {
        (r["timestamp"], r["expiry"], int(r["strike"]), r["right"])
        for r in option_rows
    }
    atm_pair = 0
    full_band = 0
    for row in underlying_rows:
        ts = str(row["timestamp"])
        day = datetime.fromisoformat(ts).date().isoformat()
        expiry = contract_by_date[day]
        strikes = _strike_band(float(row["futures_close"]), strike_step, strike_radius)
        atm = _atm_strike(float(row["futures_close"]), strike_step)
        if all((ts, expiry, atm, right) in available for right in ("CE", "PE")):
            atm_pair += 1
        if all((ts, expiry, strike, right) in available for strike in strikes for right in ("CE", "PE")):
            full_band += 1

    total = len(underlying_rows)
    return {
        "underlying_rows": total,
        "option_rows": len(option_rows),
        "duplicate_option_rows": duplicate_rows,
        "invalid_option_ohlc_rows": invalid_ohlc,
        "option_rows_missing_volume": missing_volume,
        "option_rows_missing_open_interest": missing_oi,
        "atm_ce_pe_pair_rows": atm_pair,
        "atm_ce_pe_pair_coverage_pct": round(100.0 * atm_pair / total, 4) if total else 0.0,
        "full_requested_band_rows": full_band,
        "full_requested_band_coverage_pct": round(100.0 * full_band / total, 4) if total else 0.0,
    }


def build_options_dataset(
    underlying_payload: dict[str, Any],
    option_expiries: list[str],
    *,
    client: OptionHistoryClient,
    strike_step: int = DEFAULT_STRIKE_STEP,
    strike_radius: int = DEFAULT_STRIKE_RADIUS,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    rows = _filter_rows_by_date(_underlying_rows(underlying_payload), start_date, end_date)
    expiries = _parse_expiries(option_expiries)
    contract_by_date, strikes_by_expiry, dates_by_expiry = _selection_plan(
        rows,
        expiries,
        strike_step=strike_step,
        strike_radius=strike_radius,
    )

    option_rows: list[dict[str, Any]] = []
    for expiry, strikes in strikes_by_expiry.items():
        session_dates = [date.fromisoformat(value) for value in dates_by_expiry[expiry]]
        for strike in strikes:
            for right in ("call", "put"):
                option_rows.extend(client.history(session_dates, expiry, strike, right))

    dedup = {
        (row["timestamp"], row["expiry"], row["strike"], row["right"]): row
        for row in option_rows
    }
    option_rows = sorted(
        dedup.values(),
        key=lambda row: (row["timestamp"], row["expiry"], row["strike"], row["right"]),
    )

    result = {
        "research_type": "INDEPENDENT_NIFTY_OPTIONS_MARKET_DATA",
        "research_only": True,
        "interval_minutes": 5,
        "selection_policy": {
            "underlying_reference": "futures_close",
            "option_expiry_policy": "nearest supplied expiry on or after session date",
            "option_expiries_explicit": [expiry.isoformat() for expiry in expiries],
            "strike_step_points": strike_step,
            "strike_radius_each_side": strike_radius,
            "rights": ["CE", "PE"],
            "contract_stitching": False,
            "indicators_computed": False,
            "strategy_signals_computed": False,
        },
        "session_dates": sorted(contract_by_date),
        "contract_by_date": contract_by_date,
        "strikes_by_expiry": strikes_by_expiry,
        "dates_by_expiry": dates_by_expiry,
        "underlying_market_rows": rows,
        "option_candles": option_rows,
        "quality": _quality(
            rows,
            option_rows,
            contract_by_date,
            strike_step=strike_step,
            strike_radius=strike_radius,
        ),
    }
    diagnostics = getattr(client, "request_diagnostics", None)
    if diagnostics is not None:
        result["request_diagnostics"] = diagnostics
        result["quality"]["failed_requests"] = sum(
            bool(row.get("error")) or row.get("status") not in (None, 200)
            for row in diagnostics
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch raw near-strike NIFTY option candles for independent research"
    )
    parser.add_argument("--underlying-input", type=Path, required=True)
    parser.add_argument("--option-expiry", action="append", default=[], required=True)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--strike-step", type=int, default=DEFAULT_STRIKE_STEP)
    parser.add_argument("--strike-radius", type=int, default=DEFAULT_STRIKE_RADIUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    _load_local_env()
    key = _usable_secret("BREEZE_API_KEY")
    secret = _usable_secret("BREEZE_SECRET_KEY")
    token = _usable_secret("BREEZE_SESSION_TOKEN")
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )

    payload = json.loads(args.underlying_input.read_text(encoding="utf-8"))
    report = build_options_dataset(
        payload,
        args.option_expiry,
        client=BreezeOptionsClient(key, secret, token),
        strike_step=args.strike_step,
        strike_radius=args.strike_radius,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "quality": report["quality"]}, indent=2))


if __name__ == "__main__":
    main()
