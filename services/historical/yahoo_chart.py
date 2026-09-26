"""Credential-free Yahoo Finance chart client for recent intraday research data.

This is a research fallback, not an execution-market authority. It is used when
NSE's public charting endpoint returns no usable rows. The caller is responsible
for preserving source provenance and for not treating ETF volume as futures volume.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


class YahooChartClient:
    """Fetch recent intraday candles from Yahoo's public chart endpoint."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            headers=HEADERS,
            timeout=20.0,
            follow_redirects=True,
        )
        self.last_history_debug: dict[str, Any] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "YahooChartClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval_minutes: int = 5,
    ) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/{quote(symbol, safe='')}"
        response = self.client.get(
            url,
            params={
                "period1": int(start.timestamp()),
                "period2": int(end.timestamp()),
                "interval": f"{interval_minutes}m",
                "includePrePost": "false",
                "events": "div,splits",
            },
        )
        response.raise_for_status()
        payload = response.json()
        chart = payload.get("chart") or {}
        error = chart.get("error")
        results = chart.get("result") or []
        if error or not results:
            self.last_history_debug = {
                "symbol": symbol,
                "error": error,
                "result_count": len(results),
            }
            return []

        result = results[0]
        timestamps = list(result.get("timestamp") or [])
        quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = list(quotes.get("open") or [])
        highs = list(quotes.get("high") or [])
        lows = list(quotes.get("low") or [])
        closes = list(quotes.get("close") or [])
        volumes = list(quotes.get("volume") or [])

        rows: list[dict[str, Any]] = []
        for idx, raw_ts in enumerate(timestamps):
            try:
                values = (opens[idx], highs[idx], lows[idx], closes[idx])
            except IndexError:
                continue
            if any(value is None for value in values):
                continue

            ts = datetime.fromtimestamp(float(raw_ts), tz=UTC).astimezone(IST)
            ts = ts.replace(second=0, microsecond=0)
            if not (SESSION_START <= ts.time() < SESSION_END):
                continue

            volume: float | None = None
            if idx < len(volumes) and volumes[idx] is not None:
                try:
                    volume = float(volumes[idx])
                except (TypeError, ValueError):
                    volume = None

            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": float(values[0]),
                    "high": float(values[1]),
                    "low": float(values[2]),
                    "close": float(values[3]),
                    "volume": volume,
                }
            )

        rows.sort(key=lambda row: row["timestamp"])
        meta = result.get("meta") or {}
        self.last_history_debug = {
            "symbol": symbol,
            "result_count": len(results),
            "raw_timestamp_count": len(timestamps),
            "normalized_count": len(rows),
            "normalized_first": rows[0]["timestamp"] if rows else None,
            "normalized_last": rows[-1]["timestamp"] if rows else None,
            "exchange_timezone": meta.get("exchangeTimezoneName"),
            "data_granularity": meta.get("dataGranularity"),
        }
        return rows
