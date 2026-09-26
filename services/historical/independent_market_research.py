"""Independent public-market data acquisition for NIFTY research.

Research-only. Credential-free index discovery is kept separate from optional
credentialed validation sources. Provider observations are normalized and
preserved independently before a canonical research series is selected.

The NIFTY 50 index is not a traded instrument, so its intraday volume is not used
as a liquidity feature. Futures volume (and OI when a provider exposes it) is the
appropriate volume input for strategy research.
"""

from __future__ import annotations

import argparse
import json
import os
from itertools import combinations
from statistics import median
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from services.historical.research_provider_clients import (
    BreezeFuturesClient,
    DhanHistoricalClient,
    UpstoxHistoricalClient,
)
from services.historical.yahoo_chart import YahooChartClient

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
        """Fetch intraday history in small windows and merge it deterministically.

        NSE's public charting endpoint can return status=true with an empty data
        array for larger intraday ranges. OpenChart's documented intraday example
        uses a five-day request, so keep each request at or below that size.
        """
        self._warm_cookies()
        if end <= start:
            return []

        chunk_start = start
        merged: dict[tuple[str, str], Candle] = {}
        chunks: list[dict[str, Any]] = []
        all_status_ok = True

        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=5), end)
            response = self.client.post(
                HISTORY_URL,
                json={
                    "token": instrument.token,
                    "fromDate": int(chunk_start.timestamp()),
                    "toDate": int(chunk_end.timestamp()),
                    "symbol": instrument.symbol,
                    "symbolType": instrument.instrument_type,
                    "chartType": "I",
                    "timeInterval": interval_minutes,
                },
            )
            response.raise_for_status()
            payload = response.json()
            status_ok = bool(payload.get("status"))
            all_status_ok = all_status_ok and status_ok
            raw_rows = list(payload.get("data") or [])
            normalized = (
                _normalize_candles(raw_rows, instrument, interval_minutes)
                if status_ok
                else []
            )
            for candle in normalized:
                merged[(candle.instrument, candle.timestamp)] = candle

            chunks.append(
                {
                    "start": chunk_start.isoformat(),
                    "end": chunk_end.isoformat(),
                    "status": payload.get("status"),
                    "raw_count": len(raw_rows),
                    "raw_first_time": raw_rows[0].get("time") if raw_rows else None,
                    "raw_last_time": raw_rows[-1].get("time") if raw_rows else None,
                    "normalized_count": len(normalized),
                    "normalized_first": normalized[0].timestamp if normalized else None,
                    "normalized_last": normalized[-1].timestamp if normalized else None,
                }
            )
            chunk_start = chunk_end

        result = sorted(merged.values(), key=lambda candle: candle.timestamp)
        self.last_history_debug = {
            "symbol": instrument.symbol,
            "status": all_status_ok,
            "chunk_days": 5,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "normalized_count": len(result),
            "normalized_first": result[0].timestamp if result else None,
            "normalized_last": result[-1].timestamp if result else None,
        }
        return result


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


def _candles_from_yahoo(
    rows: list[dict[str, Any]], symbol: str, instrument_type: str
) -> list[Candle]:
    return [
        Candle(
            timestamp=str(row["timestamp"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]) if row.get("volume") is not None else None,
            open_interest=None,
            source="YAHOO_CHART",
            instrument=symbol,
            instrument_type=instrument_type,
        )
        for row in rows
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


def _candles_from_provider_rows(
    rows: list[dict[str, Any]],
    instrument_type: str,
) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        candles.append(
            Candle(
                timestamp=str(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if row.get("volume") is not None else None,
                open_interest=(
                    float(row["open_interest"])
                    if row.get("open_interest") is not None
                    else None
                ),
                source=str(row["source"]),
                instrument=str(row["instrument"]),
                instrument_type=instrument_type,
            )
        )
    candles.sort(key=lambda candle: candle.timestamp)
    return candles


def _load_local_env(path: Path = Path(".env")) -> None:
    """Load missing values from a local .env without logging secrets."""

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


def _coverage(candles: list[Candle], wanted: set[date]) -> dict[str, Any]:
    selected = [
        row for row in candles if datetime.fromisoformat(row.timestamp).date() in wanted
    ]
    days = sorted(
        {datetime.fromisoformat(row.timestamp).date().isoformat() for row in selected}
    )
    complete = _last_complete_dates(selected, len(wanted)) if wanted else []
    return {
        "rows": len(selected),
        "dates": days,
        "complete_sessions": len([day for day in complete if day in wanted]),
        "volume_rows": sum(row.volume is not None for row in selected),
        "open_interest_rows": sum(row.open_interest is not None for row in selected),
    }


def _select_canonical_provider(
    by_provider: dict[str, list[Candle]],
    wanted: set[date],
    priority: list[str],
) -> tuple[str | None, list[Candle]]:
    if not by_provider:
        return None, []
    priority_rank = {source: idx for idx, source in enumerate(priority)}
    scored: list[tuple[int, int, int, str, list[Candle]]] = []
    for source, rows in by_provider.items():
        selected = [
            row for row in rows if datetime.fromisoformat(row.timestamp).date() in wanted
        ]
        complete_sessions = len(_last_complete_dates(selected, len(wanted)))
        scored.append(
            (
                complete_sessions,
                len(selected),
                -priority_rank.get(source, len(priority)),
                source,
                selected,
            )
        )
    scored.sort(reverse=True)
    _, _, _, source, rows = scored[0]
    rows.sort(key=lambda row: row.timestamp)
    return source, rows


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _compare_provider_pair(
    left_source: str,
    left: list[Candle],
    right_source: str,
    right: list[Candle],
    wanted: set[date],
) -> dict[str, Any]:
    left_map = {
        row.timestamp: row
        for row in left
        if datetime.fromisoformat(row.timestamp).date() in wanted
    }
    right_map = {
        row.timestamp: row
        for row in right
        if datetime.fromisoformat(row.timestamp).date() in wanted
    }
    overlap = sorted(set(left_map) & set(right_map))
    close_diff = [
        abs(left_map[ts].close - right_map[ts].close)
        for ts in overlap
    ]
    ohlc_max_diff = [
        max(
            abs(left_map[ts].open - right_map[ts].open),
            abs(left_map[ts].high - right_map[ts].high),
            abs(left_map[ts].low - right_map[ts].low),
            abs(left_map[ts].close - right_map[ts].close),
        )
        for ts in overlap
    ]
    volume_ratios: list[float] = []
    oi_diff: list[float] = []
    for ts in overlap:
        lrow = left_map[ts]
        rrow = right_map[ts]
        if lrow.volume not in (None, 0) and rrow.volume not in (None, 0):
            lo = min(abs(lrow.volume), abs(rrow.volume))
            hi = max(abs(lrow.volume), abs(rrow.volume))
            if lo:
                volume_ratios.append(hi / lo)
        if lrow.open_interest is not None and rrow.open_interest is not None:
            oi_diff.append(abs(lrow.open_interest - rrow.open_interest))

    return {
        "left": left_source,
        "right": right_source,
        "overlap_rows": len(overlap),
        "mean_abs_close_diff": _mean(close_diff),
        "max_abs_close_diff": max(close_diff) if close_diff else None,
        "mean_max_ohlc_diff": _mean(ohlc_max_diff),
        "max_ohlc_diff": max(ohlc_max_diff) if ohlc_max_diff else None,
        "volume_overlap_rows": len(volume_ratios),
        "median_larger_to_smaller_volume_ratio": (
            median(volume_ratios) if volume_ratios else None
        ),
        "open_interest_overlap_rows": len(oi_diff),
        "mean_abs_open_interest_diff": _mean(oi_diff),
        "max_abs_open_interest_diff": max(oi_diff) if oi_diff else None,
    }


def _provider_quality(
    by_provider: dict[str, list[Candle]],
    wanted: set[date],
    canonical_source: str | None,
) -> dict[str, Any]:
    sources = sorted(by_provider)
    return {
        "canonical_source": canonical_source,
        "selection_policy": (
            "Choose the provider with the most complete selected sessions, then "
            "the most rows; ties use a fixed provider priority. Never fill missing canonical "
            "timestamps from another provider."
        ),
        "providers": {
            source: _coverage(by_provider[source], wanted)
            for source in sources
        },
        "comparisons": [
            _compare_provider_pair(
                left,
                by_provider[left],
                right,
                by_provider[right],
                wanted,
            )
            for left, right in combinations(sources, 2)
        ],
    }


def _canonical_rows(
    index_rows: list[Candle],
    futures_rows: list[Candle],
    vix_rows: list[Candle],
    proxy_rows: list[Candle],
    futures_source: str | None,
    vix_source: str | None,
) -> list[dict[str, Any]]:
    futures = {row.timestamp: row for row in futures_rows}
    vix = {row.timestamp: row for row in vix_rows}
    proxy = {row.timestamp: row for row in proxy_rows}
    result: list[dict[str, Any]] = []

    for spot in sorted(index_rows, key=lambda row: row.timestamp):
        future = futures.get(spot.timestamp)
        vix_row = vix.get(spot.timestamp)
        proxy_row = proxy.get(spot.timestamp)
        result.append(
            {
                "timestamp": spot.timestamp,
                "spot_open": spot.open,
                "spot_high": spot.high,
                "spot_low": spot.low,
                "spot_close": spot.close,
                "spot_source": spot.source,
                "futures_open": future.open if future else None,
                "futures_high": future.high if future else None,
                "futures_low": future.low if future else None,
                "futures_close": future.close if future else None,
                "futures_volume": future.volume if future else None,
                "futures_open_interest": future.open_interest if future else None,
                "futures_basis_points": (
                    future.close - spot.close if future else None
                ),
                "futures_source": futures_source if future else None,
                "vix_open": vix_row.open if vix_row else None,
                "vix_high": vix_row.high if vix_row else None,
                "vix_low": vix_row.low if vix_row else None,
                "vix_close": vix_row.close if vix_row else None,
                "vix_source": vix_source if vix_row else None,
                "niftybees_volume": proxy_row.volume if proxy_row else None,
            }
        )
    return result


def build_research_dataset(
    sessions: int = 10,
    lookback_days: int = 30,
    *,
    breeze_futures_expiry: str | None = None,
    upstox_futures_key: str | None = None,
    dhan_futures_security_id: str | None = None,
) -> dict[str, Any]:
    if sessions <= 0:
        raise ValueError("sessions must be positive")

    _load_local_env()
    now = datetime.now(IST)
    start = datetime.combine(now.date() - timedelta(days=lookback_days), time.min, tzinfo=IST)
    end = now

    source_attempts: list[dict[str, Any]] = []
    nifty_rows: list[Candle] = []
    public_vix_rows: list[Candle] = []
    public_futures_rows: list[Candle] = []
    volume_proxy_rows: list[Candle] = []
    contract_by_date: dict[str, list[str]] = {}
    primary_source = ""
    vix_status: dict[str, Any] = {"available": False}
    volume_proxy_status: dict[str, Any] = {"available": False}

    # Credential-free price discovery remains the base layer.
    try:
        with PublicNseChartClient() as client:
            nifty = client.resolve_exact("NIFTY 50", "IDX")
            candidate_rows = client.history(nifty, start, end, 5)
            candidate_dates = _last_complete_dates(candidate_rows, sessions)
            source_attempts.append(
                {
                    "source": "NSE_PUBLIC_CHART",
                    "series": "NIFTY_INDEX",
                    "history_response": client.last_history_debug,
                    "complete_sessions": len(candidate_dates),
                    "session_shape": _session_diagnostics(candidate_rows),
                }
            )
            if len(candidate_dates) == sessions:
                primary_source = "NSE_PUBLIC_CHART"
                nifty_rows = candidate_rows

                try:
                    vix = _resolve_vix(client)
                    public_vix_rows = client.history(vix, start, end, 5)
                    vix_status = {
                        "available": bool(public_vix_rows),
                        "instrument": vix.symbol,
                        "source": "NSE_PUBLIC_CHART",
                    }
                except (RuntimeError, httpx.HTTPError) as exc:
                    vix_status = {
                        "available": False,
                        "source": "NSE_PUBLIC_CHART",
                        "error": str(exc),
                    }

                wanted_public = set(candidate_dates)
                candidates = _future_candidates(client)
                public_futures_rows, contract_by_date = _fetch_futures_covering_dates(
                    client, candidates, start, end, wanted_public
                )
    except (RuntimeError, httpx.HTTPError) as exc:
        source_attempts.append(
            {
                "source": "NSE_PUBLIC_CHART",
                "series": "NIFTY_INDEX",
                "error": str(exc),
                "complete_sessions": 0,
            }
        )

    if not primary_source:
        with YahooChartClient() as client:
            yahoo_nifty = _candles_from_yahoo(
                client.history("^NSEI", start, end, 5), "^NSEI", "Index"
            )
            nifty_debug = dict(client.last_history_debug)
            yahoo_dates = _last_complete_dates(yahoo_nifty, sessions)
            source_attempts.append(
                {
                    "source": "YAHOO_CHART",
                    "series": "NIFTY_INDEX",
                    "instrument": "^NSEI",
                    "history_response": nifty_debug,
                    "complete_sessions": len(yahoo_dates),
                    "session_shape": _session_diagnostics(yahoo_nifty),
                }
            )
            if len(yahoo_dates) == sessions:
                primary_source = "YAHOO_CHART"
                nifty_rows = yahoo_nifty

                public_vix_rows = _candles_from_yahoo(
                    client.history("^INDIAVIX", start, end, 5),
                    "^INDIAVIX",
                    "VolatilityIndex",
                )
                vix_debug = dict(client.last_history_debug)
                vix_status = {
                    "available": bool(public_vix_rows),
                    "instrument": "^INDIAVIX",
                    "source": "YAHOO_CHART",
                    "diagnostics": vix_debug,
                }

                volume_proxy_rows = _candles_from_yahoo(
                    client.history("NIFTYBEES.NS", start, end, 5),
                    "NIFTYBEES.NS",
                    "ETF",
                )
                proxy_debug = dict(client.last_history_debug)
                volume_proxy_status = {
                    "available": bool(volume_proxy_rows),
                    "instrument": "NIFTYBEES.NS",
                    "source": "YAHOO_CHART",
                    "semantics": "ETF traded-volume proxy; not NIFTY futures volume",
                    "diagnostics": proxy_debug,
                }

    dates = _last_complete_dates(nifty_rows, sessions)
    if len(dates) != sessions:
        diagnostics = {
            "request": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "sessions": sessions,
                "lookback_days": lookback_days,
            },
            "source_attempts": source_attempts,
        }
        raise RuntimeError(
            f"Only {len(dates)} complete NIFTY sessions found across available "
            f"credential-free sources. Diagnostics: "
            f"{json.dumps(diagnostics, separators=(',', ':'))}"
        )

    wanted = set(dates)
    query_start = datetime.combine(min(dates), SESSION_START, tzinfo=IST)
    query_end = datetime.combine(max(dates), SESSION_END, tzinfo=IST)

    futures_by_provider: dict[str, list[Candle]] = {}
    vix_by_provider: dict[str, list[Candle]] = {}
    if public_futures_rows:
        futures_by_provider[public_futures_rows[0].source] = public_futures_rows
    if public_vix_rows:
        vix_by_provider[public_vix_rows[0].source] = public_vix_rows

    credentialed_status: dict[str, Any] = {}
    broker_sources_used: list[str] = []

    # Breeze: credentials already exist in the project; expiry stays explicit.
    breeze_key = _usable_secret("BREEZE_API_KEY")
    breeze_secret = _usable_secret("BREEZE_SECRET_KEY")
    breeze_session = _usable_secret("BREEZE_SESSION_TOKEN")
    breeze_expiry = (
        breeze_futures_expiry
        or os.getenv("RESEARCH_BREEZE_NIFTY_FUT_EXPIRY")
        or os.getenv("RESEARCH_NIFTY_FUT_EXPIRY")
    )
    if breeze_key and breeze_secret and breeze_session and breeze_expiry:
        try:
            client = BreezeFuturesClient(breeze_key, breeze_secret, breeze_session)
            rows = _candles_from_provider_rows(
                client.history(dates, breeze_expiry),
                "Futures",
            )
            futures_by_provider["BREEZE"] = rows
            credentialed_status["BREEZE"] = {
                "available": bool(rows),
                "series": ["NIFTY_FUTURES"],
                "instrument_config": {"expiry_date": breeze_expiry},
                "diagnostics": client.last_history_debug,
            }
            if rows:
                broker_sources_used.append("BREEZE")
        except Exception as exc:
            credentialed_status["BREEZE"] = {
                "available": False,
                "series": ["NIFTY_FUTURES"],
                "error": f"{type(exc).__name__}: {exc}",
            }
    else:
        missing = []
        if not (breeze_key and breeze_secret and breeze_session):
            missing.append("BREEZE_API_KEY/BREEZE_SECRET_KEY/BREEZE_SESSION_TOKEN")
        if not breeze_expiry:
            missing.append("RESEARCH_BREEZE_NIFTY_FUT_EXPIRY")
        credentialed_status["BREEZE"] = {
            "available": False,
            "series": ["NIFTY_FUTURES"],
            "reason": "missing_configuration",
            "missing": missing,
        }

    # Upstox: the India VIX instrument key is stable and documented; the
    # futures instrument key is explicit so contract selection is reproducible.
    upstox_token = _usable_secret("UPSTOX_ACCESS_TOKEN")
    upstox_key = (
        upstox_futures_key
        or os.getenv("RESEARCH_UPSTOX_NIFTY_FUT_INSTRUMENT_KEY")
    )
    if upstox_token:
        try:
            with UpstoxHistoricalClient(upstox_token) as client:
                upstox_vix = _candles_from_provider_rows(
                    client.history("NSE_INDEX|India VIX", query_start, query_end),
                    "VolatilityIndex",
                )
                vix_debug = dict(client.last_history_debug)
                if upstox_vix:
                    vix_by_provider["UPSTOX"] = upstox_vix
                    broker_sources_used.append("UPSTOX")

                upstox_futures: list[Candle] = []
                futures_debug: dict[str, Any] | None = None
                if upstox_key:
                    upstox_futures = _candles_from_provider_rows(
                        client.history(upstox_key, query_start, query_end),
                        "Futures",
                    )
                    futures_debug = dict(client.last_history_debug)
                    if upstox_futures:
                        futures_by_provider["UPSTOX"] = upstox_futures

                credentialed_status["UPSTOX"] = {
                    "available": bool(upstox_vix or upstox_futures),
                    "series": [
                        "INDIA_VIX",
                        *(["NIFTY_FUTURES"] if upstox_key else []),
                    ],
                    "vix_diagnostics": vix_debug,
                    "futures_instrument_key": upstox_key,
                    "futures_diagnostics": futures_debug,
                    "futures_configuration_missing": not bool(upstox_key),
                }
        except (RuntimeError, httpx.HTTPError) as exc:
            credentialed_status["UPSTOX"] = {
                "available": False,
                "series": ["INDIA_VIX", "NIFTY_FUTURES"],
                "error": f"{type(exc).__name__}: {exc}",
            }
    else:
        credentialed_status["UPSTOX"] = {
            "available": False,
            "series": ["INDIA_VIX", "NIFTY_FUTURES"],
            "reason": "missing_configuration",
            "missing": ["UPSTOX_ACCESS_TOKEN"],
        }

    dhan_token = _usable_secret("DHAN_ACCESS_TOKEN")
    dhan_security_id = (
        dhan_futures_security_id
        or os.getenv("RESEARCH_DHAN_NIFTY_FUT_SECURITY_ID")
    )
    if dhan_token and dhan_security_id:
        try:
            with DhanHistoricalClient(dhan_token) as client:
                rows = _candles_from_provider_rows(
                    client.history(dhan_security_id, query_start, query_end),
                    "Futures",
                )
                futures_by_provider["DHAN"] = rows
                credentialed_status["DHAN"] = {
                    "available": bool(rows),
                    "series": ["NIFTY_FUTURES"],
                    "security_id": dhan_security_id,
                    "diagnostics": client.last_history_debug,
                }
                if rows:
                    broker_sources_used.append("DHAN")
        except (RuntimeError, httpx.HTTPError) as exc:
            credentialed_status["DHAN"] = {
                "available": False,
                "series": ["NIFTY_FUTURES"],
                "error": f"{type(exc).__name__}: {exc}",
            }
    else:
        missing = []
        if not dhan_token:
            missing.append("DHAN_ACCESS_TOKEN")
        if not dhan_security_id:
            missing.append("RESEARCH_DHAN_NIFTY_FUT_SECURITY_ID")
        credentialed_status["DHAN"] = {
            "available": False,
            "series": ["NIFTY_FUTURES"],
            "reason": "missing_configuration",
            "missing": missing,
        }

    futures_source, futures_rows = _select_canonical_provider(
        futures_by_provider,
        wanted,
        ["BREEZE", "UPSTOX", "DHAN", "NSE_PUBLIC_CHART"],
    )
    vix_source, vix_rows = _select_canonical_provider(
        vix_by_provider,
        wanted,
        ["UPSTOX", "YAHOO_CHART", "NSE_PUBLIC_CHART"],
    )

    index_selected_candles = [
        row for row in nifty_rows if datetime.fromisoformat(row.timestamp).date() in wanted
    ]
    proxy_selected_candles = [
        row
        for row in volume_proxy_rows
        if datetime.fromisoformat(row.timestamp).date() in wanted
    ]
    futures_selected = [
        row for row in futures_rows if datetime.fromisoformat(row.timestamp).date() in wanted
    ]
    vix_selected = [
        row for row in vix_rows if datetime.fromisoformat(row.timestamp).date() in wanted
    ]

    provider_series = {
        "nifty_futures": {
            source: _rows_for_dates(rows, wanted)
            for source, rows in sorted(futures_by_provider.items())
        },
        "india_vix": {
            source: _rows_for_dates(rows, wanted)
            for source, rows in sorted(vix_by_provider.items())
        },
    }

    return {
        "research_type": "INDEPENDENT_NIFTY_MARKET_DATA",
        "research_only": True,
        "broker_sources_used": sorted(set(broker_sources_used)),
        "primary_source": primary_source,
        "generated_at": now.isoformat(),
        "interval_minutes": 5,
        "session_dates": [day.isoformat() for day in dates],
        "source_attempts": source_attempts,
        "credentialed_sources": credentialed_status,
        "data_source_policy": {
            "raw_provider_series_preserved": True,
            "canonical_series_never_backfilled_from_secondary_provider": True,
            "contract_identifiers_explicit": True,
            "niftybees_is_only_a_volume_proxy": True,
        },
        "provenance": {
            "nifty_index": {
                "instrument": (
                    "NIFTY 50" if primary_source == "NSE_PUBLIC_CHART" else "^NSEI"
                ),
                "source": primary_source,
                "volume_semantics": "not_used",
            },
            "nifty_futures": {
                "canonical_source": futures_source,
                "available": bool(futures_selected),
                "contract_selection": "explicit provider contract identifier",
                "public_contracts_by_date": contract_by_date,
                "volume_semantics": "actual futures traded volume",
                "open_interest_semantics": "provider-reported futures open interest",
            },
            "india_vix": {
                "canonical_source": vix_source,
                "available": bool(vix_selected),
                "public_fallback": vix_status,
            },
            "nifty_volume_proxy": volume_proxy_status,
        },
        "coverage": {
            "nifty_index_rows": len(index_selected_candles),
            "nifty_futures_rows": len(futures_selected),
            "india_vix_rows": len(vix_selected),
            "nifty_volume_proxy_rows": len(proxy_selected_candles),
            "futures_dates": sorted(
                {
                    datetime.fromisoformat(row.timestamp).date().isoformat()
                    for row in futures_selected
                }
            ),
            "vix_dates": sorted(
                {
                    datetime.fromisoformat(row.timestamp).date().isoformat()
                    for row in vix_selected
                }
            ),
            "volume_proxy_dates": sorted(
                {
                    datetime.fromisoformat(row.timestamp).date().isoformat()
                    for row in proxy_selected_candles
                }
            ),
        },
        "provider_quality": {
            "nifty_futures": _provider_quality(
                futures_by_provider, wanted, futures_source
            ),
            "india_vix": _provider_quality(vix_by_provider, wanted, vix_source),
        },
        "canonical_market_rows": _canonical_rows(
            index_selected_candles,
            futures_selected,
            vix_selected,
            proxy_selected_candles,
            futures_source,
            vix_source,
        ),
        "provider_series": provider_series,
        "nifty_index": [asdict(row) for row in index_selected_candles],
        "nifty_futures": [asdict(row) for row in futures_selected],
        "india_vix": [asdict(row) for row in vix_selected],
        "nifty_volume_proxy": [asdict(row) for row in proxy_selected_candles],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch independent NIFTY market research data")
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--breeze-futures-expiry",
        help="Explicit NIFTY futures expiry YYYY-MM-DD; overrides env config.",
    )
    parser.add_argument(
        "--upstox-futures-key",
        help="Explicit Upstox NIFTY futures instrument_key; overrides env config.",
    )
    parser.add_argument(
        "--dhan-futures-security-id",
        help="Explicit Dhan NIFTY futures securityId; overrides env config.",
    )
    args = parser.parse_args()

    report = build_research_dataset(
        args.sessions,
        args.lookback_days,
        breeze_futures_expiry=args.breeze_futures_expiry,
        upstox_futures_key=args.upstox_futures_key,
        dhan_futures_security_id=args.dhan_futures_security_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "session_dates": report["session_dates"],
                "coverage": report["coverage"],
                "provenance": report["provenance"],
                "credentialed_sources": report["credentialed_sources"],
                "provider_quality": report["provider_quality"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
