"""Independent public-market data acquisition for NIFTY research.

Research-only. This module deliberately does not call Breeze or Kite. It talks to
NSE's public charting endpoints using the same protocol documented by the
open-source OpenChart client, normalizes the response, and preserves provenance.

The NIFTY 50 index is not a traded instrument, so its intraday volume is not used
as a liquidity feature. Futures volume (and OI when a provider exposes it) is the
appropriate volume input for strategy research.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
DEFAULT_OUTPUT = Path("data/independent_market_research_10_sessions.json")

SEARCH_URL = "https://charting.nseindia.com/v1/exchanges/symbolsDynamic"
HISTORY_URL = "https://charting.nseindia.com/v1/charts/symbolHistoricalData"
NSE_HOME = "https://www.nseindia.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://charting.nseindia.com",
    "Referer": "https://charting.nseindia.com/",
}


@dataclass(frozen=True)
class Instrument:
    symbol: str
    token: str
    instrument_type: str
    segment: str


@dataclass(frozen=True)
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    open_interest: float | None
    source: str
    instrument: str
    instrument_type: str


class PublicNseChartClient:
    """Minimal client for NSE public charting data; no broker credentials required."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True)
        self._cookies_warmed = False
        self.last_history_debug: dict[str, Any] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "PublicNseChartClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _warm_cookies(self) -> None:
        if self._cookies_warmed:
            return
        try:
            self.client.get(NSE_HOME)
        except httpx.HTTPError:
            pass
        self._cookies_warmed = True

    def search(self, symbol: str, segment: str) -> list[dict[str, Any]]:
        self._warm_cookies()
        response = self.client.post(SEARCH_URL, json={"symbol": symbol, "segment": segment})
        response.raise_for_status()
        payload = response.json()
        if not payload.get("status"):
            return []
        return list(payload.get("data") or [])

    def resolve_exact(self, symbol: str, segment: str) -> Instrument:
        rows = self.search(symbol, segment)
        target = symbol.upper()
        for row in rows:
            if str(row.get("symbol", "")).upper() == target:
                return Instrument(
                    symbol=str(row["symbol"]),
                    token=str(row["scripcode"]),
                    instrument_type=str(row["type"]),
                    segment=segment,
                )
        if not rows:
            raise RuntimeError(f"No public NSE charting symbol found for {symbol!r} in {segment}")
        raise RuntimeError(
            f"Exact public NSE charting symbol {symbol!r} not found; "
            f"candidates={[row.get('symbol') for row in rows[:10]]}"
        )

    def history(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        interval_minutes: int = 5,
    ) -> list[Candle]:
        self._warm_cookies()
        response = self.client.post(
            HISTORY_URL,
            json={
                "token": instrument.token,
                "fromDate": int(start.timestamp()),
                "toDate": int(end.timestamp()),
                "symbol": instrument.symbol,
                "symbolType": instrument.instrument_type,
                "chartType": "I",
                "timeInterval": interval_minutes,
            },
        )
        response.raise_for_status()
        payload = response.json()
        raw_rows = list(payload.get("data") or [])
        if not payload.get("status"):
            self.last_history_debug = {
                "symbol": instrument.symbol,
                "status": payload.get("status"),
                "raw_count": len(raw_rows),
            }
            return []
        normalized = _normalize_candles(raw_rows, instrument, interval_minutes)
        self.last_history_debug = {
            "symbol": instrument.symbol,
            "status": payload.get("status"),
            "raw_count": len(raw_rows),
            "raw_first_time": raw_rows[0].get("time") if raw_rows else None,
            "raw_last_time": raw_rows[-1].get("time") if raw_rows else None,
            "normalized_count": len(normalized),
            "normalized_first": normalized[0].timestamp if normalized else None,
            "normalized_last": normalized[-1].timestamp if normalized else None,
        }
        return normalized


def _to_exchange_bar_start(raw: Any, interval_minutes: int) -> datetime:
    """Normalize NSE chart timestamps to an IST bar-start timestamp.

    The public chart feed encodes exchange wall-clock labels as epoch milliseconds.
    Intraday labels are bar-end values such as 15:29:59 for the 15:25-15:30
    five-minute candle. Treating that epoch as a real UTC instant shifts every
    candle by +05:30, so decode the UTC fields as exchange wall time and floor the
    label to the interval boundary.
    """
    value = float(raw)
    if value > 10_000_000_000:
        value /= 1000.0
    wall = datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)
    minute = (wall.minute // interval_minutes) * interval_minutes
    return wall.replace(minute=minute, second=0, microsecond=0, tzinfo=IST)


def _number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _normalize_candles(
    rows: list[dict[str, Any]], instrument: Instrument, interval_minutes: int = 5
) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        ts = _to_exchange_bar_start(row["time"], interval_minutes)
        if not (SESSION_START <= ts.time() < SESSION_END):
            continue
        candles.append(
            Candle(
                timestamp=ts.isoformat(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=_number(row, "volume", "tradedQty", "tradedqty"),
                open_interest=_number(row, "openInterest", "openinterest", "oi"),
                source="NSE_PUBLIC_CHART",
                instrument=instrument.symbol,
                instrument_type=instrument.instrument_type,
            )
        )
    candles.sort(key=lambda candle: candle.timestamp)
    return candles


def _session_dates(candles: list[Candle]) -> list[date]:
    return sorted({datetime.fromisoformat(candle.timestamp).date() for candle in candles})


def _last_complete_dates(candles: list[Candle], sessions: int) -> list[date]:
    by_day: dict[date, list[Candle]] = {}
    for candle in candles:
        day = datetime.fromisoformat(candle.timestamp).date()
        by_day.setdefault(day, []).append(candle)

    complete: list[date] = []
    for day, day_rows in sorted(by_day.items()):
        # 09:15 through 15:25 inclusive = 75 five-minute bars.
        starts = {datetime.fromisoformat(row.timestamp).time() for row in day_rows}
        expected = {
            (datetime.combine(day, SESSION_START) + timedelta(minutes=5 * idx)).time()
            for idx in range(75)
        }
        if expected.issubset(starts):
            complete.append(day)
    return complete[-sessions:]


def _session_diagnostics(candles: list[Candle]) -> dict[str, Any]:
    by_day: dict[date, list[Candle]] = {}
    for candle in candles:
        day = datetime.fromisoformat(candle.timestamp).date()
        by_day.setdefault(day, []).append(candle)

    days: dict[str, Any] = {}
    for day, rows in sorted(by_day.items()):
        starts = {datetime.fromisoformat(row.timestamp).time() for row in rows}
        expected = [
            (datetime.combine(day, SESSION_START) + timedelta(minutes=5 * idx)).time()
            for idx in range(75)
        ]
        missing = [slot.strftime("%H:%M:%S") for slot in expected if slot not in starts]
        ordered = sorted(rows, key=lambda row: row.timestamp)
        days[day.isoformat()] = {
            "bars": len(rows),
            "unique_starts": len(starts),
            "first": ordered[0].timestamp if ordered else None,
            "last": ordered[-1].timestamp if ordered else None,
            "missing_expected_count": len(missing),
            "missing_expected_first_10": missing[:10],
        }
    return {"normalized_rows": len(candles), "days": days}


def _rows_for_dates(candles: list[Candle], dates: set[date]) -> list[dict[str, Any]]:
    return [
        asdict(candle)
        for candle in candles
        if datetime.fromisoformat(candle.timestamp).date() in dates
    ]


def _resolve_vix(client: PublicNseChartClient) -> Instrument:
    candidates = ("INDIA VIX", "India VIX", "INDIAVIX")
    errors: list[str] = []
    for symbol in candidates:
        try:
            return client.resolve_exact(symbol, "IDX")
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError("Unable to resolve India VIX from public charting search: " + " | ".join(errors))


def _future_candidates(client: PublicNseChartClient) -> list[Instrument]:
    rows = client.search("NIFTY", "FO")
    instruments: list[Instrument] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        instrument_type = str(row.get("type", ""))
        if symbol.startswith("NIFTY") and symbol.endswith("FUT") and instrument_type.lower() == "futures":
            instruments.append(
                Instrument(
                    symbol=symbol,
                    token=str(row["scripcode"]),
                    instrument_type=instrument_type,
                    segment="FO",
                )
            )
    return instruments


def _fetch_futures_covering_dates(
    client: PublicNseChartClient,
    candidates: list[Instrument],
    start: datetime,
    end: datetime,
    wanted_dates: set[date],
) -> tuple[list[Candle], dict[str, list[str]]]:
    """Fetch candidate contracts and retain the highest-volume contract per date."""

    per_contract: dict[str, list[Candle]] = {}
    for instrument in candidates:
        rows = client.history(instrument, start, end, 5)
        if rows:
            per_contract[instrument.symbol] = rows

    selected: list[Candle] = []
    contract_by_date: dict[str, list[str]] = {}
    for day in sorted(wanted_dates):
        choices: list[tuple[float, str, list[Candle]]] = []
        for symbol, rows in per_contract.items():
            day_rows = [row for row in rows if datetime.fromisoformat(row.timestamp).date() == day]
            if not day_rows:
                continue
            volume = sum(row.volume or 0.0 for row in day_rows)
            choices.append((volume, symbol, day_rows))
        if not choices:
            contract_by_date[day.isoformat()] = []
            continue
        choices.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, symbol, day_rows = choices[0]
        selected.extend(day_rows)
        contract_by_date[day.isoformat()] = [symbol]
    selected.sort(key=lambda candle: candle.timestamp)
    return selected, contract_by_date


def build_research_dataset(sessions: int = 10, lookback_days: int = 30) -> dict[str, Any]:
    if sessions <= 0:
        raise ValueError("sessions must be positive")
    now = datetime.now(IST)
    start = datetime.combine(now.date() - timedelta(days=lookback_days), time.min, tzinfo=IST)
    end = now

    with PublicNseChartClient() as client:
        nifty = client.resolve_exact("NIFTY 50", "IDX")
        nifty_rows = client.history(nifty, start, end, 5)
        dates = _last_complete_dates(nifty_rows, sessions)
        if len(dates) != sessions:
            diagnostics = {
                "request": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "sessions": sessions,
                    "lookback_days": lookback_days,
                },
                "history_response": client.last_history_debug,
                "session_shape": _session_diagnostics(nifty_rows),
            }
            raise RuntimeError(
                f"Only {len(dates)} complete NIFTY sessions found in {lookback_days} calendar days. "
                f"Diagnostics: {json.dumps(diagnostics, separators=(',', ':'))}"
            )
        wanted = set(dates)

        vix_status: dict[str, Any]
        vix_rows: list[Candle] = []
        try:
            vix = _resolve_vix(client)
            vix_rows = client.history(vix, start, end, 5)
            vix_status = {"available": True, "instrument": vix.symbol}
        except (RuntimeError, httpx.HTTPError) as exc:
            vix_status = {"available": False, "error": str(exc)}

        future_candidates = _future_candidates(client)
        futures_rows, contract_by_date = _fetch_futures_covering_dates(
            client, future_candidates, start, end, wanted
        )

    index_selected = _rows_for_dates(nifty_rows, wanted)
    vix_selected = _rows_for_dates(vix_rows, wanted)
    futures_selected = _rows_for_dates(futures_rows, wanted)

    return {
        "research_type": "INDEPENDENT_NIFTY_MARKET_DATA",
        "research_only": True,
        "broker_sources_used": [],
        "primary_source": "NSE_PUBLIC_CHART",
        "generated_at": now.isoformat(),
        "interval_minutes": 5,
        "session_dates": [day.isoformat() for day in dates],
        "provenance": {
            "nifty_index": {"instrument": nifty.symbol, "volume_semantics": "not_used"},
            "nifty_futures": {
                "contract_selection": "highest observed session volume among returned NIFTY futures",
                "contracts_by_date": contract_by_date,
                "volume_semantics": "traded futures volume",
            },
            "india_vix": vix_status,
        },
        "coverage": {
            "nifty_index_rows": len(index_selected),
            "nifty_futures_rows": len(futures_selected),
            "india_vix_rows": len(vix_selected),
            "futures_dates": sorted(
                {
                    datetime.fromisoformat(row["timestamp"]).date().isoformat()
                    for row in futures_selected
                }
            ),
            "vix_dates": sorted(
                {
                    datetime.fromisoformat(row["timestamp"]).date().isoformat()
                    for row in vix_selected
                }
            ),
        },
        "nifty_index": index_selected,
        "nifty_futures": futures_selected,
        "india_vix": vix_selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch independent NIFTY market research data")
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = build_research_dataset(args.sessions, args.lookback_days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "session_dates": report["session_dates"],
                "coverage": report["coverage"],
                "provenance": report["provenance"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
