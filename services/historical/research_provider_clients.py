"""Credentialed historical-data adapters for independent NIFTY research.

These adapters are research-only. They normalize provider-specific responses into
plain dictionaries without deciding which source is authoritative. The research
collector keeps each provider separate, compares overlapping observations, and
only then chooses a canonical series.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)


def _regular_session(ts: datetime) -> bool:
    return SESSION_START <= ts.time() < SESSION_END


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_utc(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class BreezeFuturesClient:
    """Fetch NIFTY futures 5-minute OHLCV/OI through Breeze Historical V2."""

    source = "BREEZE"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session_token: str,
        breeze_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_token = session_token
        self._factory = breeze_factory
        self.last_history_debug: dict[str, Any] = {}

    def _client(self) -> Any:
        factory = self._factory
        if factory is None:
            from breeze_connect import BreezeConnect

            factory = BreezeConnect
        client = factory(api_key=self.api_key)
        client.generate_session(
            api_secret=self.api_secret,
            session_token=self.session_token,
        )
        return client

    def history(
        self,
        session_dates: list[date],
        expiry_date: str,
    ) -> list[dict[str, Any]]:
        if not session_dates:
            return []

        client = self._client()
        rows: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        expiry = datetime.fromisoformat(expiry_date).date()
        expiry_arg = f"{expiry.isoformat()}T07:00:00.000Z"

        for day in sorted(session_dates):
            start = datetime.combine(day, SESSION_START, tzinfo=IST)
            end = datetime.combine(day, SESSION_END, tzinfo=IST)
            response = client.get_historical_data_v2(
                interval="5minute",
                from_date=_iso_utc(start),
                to_date=_iso_utc(end),
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="futures",
                expiry_date=expiry_arg,
                right="others",
                strike_price="0",
            )
            success = list((response or {}).get("Success") or [])
            diagnostics.append(
                {
                    "date": day.isoformat(),
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
                if not _regular_session(ts):
                    continue
                if ts.date() != day:
                    continue
                open_ = _float(item.get("open"))
                high = _float(item.get("high"))
                low = _float(item.get("low"))
                close = _float(item.get("close"))
                if None in (open_, high, low, close):
                    continue
                rows.append(
                    {
                        "timestamp": ts.isoformat(),
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": _float(item.get("volume")),
                        "open_interest": _float(item.get("open_interest")),
                        "instrument": f"NIFTY FUT {expiry.isoformat()}",
                        "source": self.source,
                    }
                )

        dedup = {(row["timestamp"], row["instrument"]): row for row in rows}
        result = sorted(dedup.values(), key=lambda row: row["timestamp"])
        self.last_history_debug = {
            "expiry_date": expiry.isoformat(),
            "requests": diagnostics,
            "normalized_count": len(result),
            "normalized_first": result[0]["timestamp"] if result else None,
            "normalized_last": result[-1]["timestamp"] if result else None,
        }
        return result


class UpstoxHistoricalClient:
    """Fetch Upstox V3 historical candles for futures or India VIX."""

    source = "UPSTOX"
    BASE_URL = "https://api.upstox.com/v3/historical-candle"

    def __init__(
        self,
        access_token: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=20.0, follow_redirects=True)
        self.access_token = access_token
        self.last_history_debug: dict[str, Any] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "UpstoxHistoricalClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def history(
        self,
        instrument_key: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        encoded = quote(instrument_key, safe="")
        url = (
            f"{self.BASE_URL}/{encoded}/minutes/5/"
            f"{end.date().isoformat()}/{start.date().isoformat()}"
        )
        response = self.client.get(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            },
        )
        response.raise_for_status()
        payload = response.json()
        raw = list(((payload.get("data") or {}).get("candles")) or [])
        rows: list[dict[str, Any]] = []

        for candle in raw:
            if len(candle) < 5:
                continue
            try:
                ts = datetime.fromisoformat(str(candle[0]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            else:
                ts = ts.astimezone(IST)
            ts = ts.replace(second=0, microsecond=0)
            if not _regular_session(ts):
                continue
            if ts < start or ts > end:
                continue
            values = [_float(value) for value in candle[1:5]]
            if any(value is None for value in values):
                continue
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": values[0],
                    "high": values[1],
                    "low": values[2],
                    "close": values[3],
                    "volume": _float(candle[5]) if len(candle) > 5 else None,
                    "open_interest": _float(candle[6]) if len(candle) > 6 else None,
                    "instrument": instrument_key,
                    "source": self.source,
                }
            )

        dedup = {(row["timestamp"], row["instrument"]): row for row in rows}
        result = sorted(dedup.values(), key=lambda row: row["timestamp"])
        self.last_history_debug = {
            "instrument_key": instrument_key,
            "status": payload.get("status"),
            "raw_count": len(raw),
            "normalized_count": len(result),
            "normalized_first": result[0]["timestamp"] if result else None,
            "normalized_last": result[-1]["timestamp"] if result else None,
        }
        return result


class KiteHistoricalClient:
    """Fetch current-contract futures or India VIX 5-minute candles via Kite."""

    source = "KITE"

    def __init__(
        self,
        api_key: str,
        access_token: str,
        kite_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self._factory = kite_factory
        self.last_history_debug: dict[str, Any] = {}

    def _client(self) -> Any:
        factory = self._factory
        if factory is None:
            from kiteconnect import KiteConnect

            factory = KiteConnect
        client = factory(api_key=self.api_key)
        client.set_access_token(self.access_token)
        return client

    def history(
        self,
        instrument_token: str,
        start: datetime,
        end: datetime,
        *,
        instrument_name: str,
        include_oi: bool,
    ) -> list[dict[str, Any]]:
        client = self._client()
        raw = list(
            client.historical_data(
                int(instrument_token),
                start,
                end,
                "5minute",
                continuous=False,
                oi=include_oi,
            )
            or []
        )
        rows: list[dict[str, Any]] = []
        for item in raw:
            raw_ts = item.get("date")
            if isinstance(raw_ts, datetime):
                ts = raw_ts
            else:
                try:
                    ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                except ValueError:
                    continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            else:
                ts = ts.astimezone(IST)
            ts = ts.replace(second=0, microsecond=0)
            if not _regular_session(ts) or ts < start or ts > end:
                continue
            values = [_float(item.get(key)) for key in ("open", "high", "low", "close")]
            if any(value is None for value in values):
                continue
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": values[0],
                    "high": values[1],
                    "low": values[2],
                    "close": values[3],
                    "volume": _float(item.get("volume")),
                    "open_interest": _float(item.get("oi")) if include_oi else None,
                    "instrument": instrument_name,
                    "source": self.source,
                }
            )

        dedup = {(row["timestamp"], row["instrument"]): row for row in rows}
        result = sorted(dedup.values(), key=lambda row: row["timestamp"])
        self.last_history_debug = {
            "instrument_token": str(instrument_token),
            "instrument_name": instrument_name,
            "include_oi": include_oi,
            "raw_count": len(raw),
            "normalized_count": len(result),
            "normalized_first": result[0]["timestamp"] if result else None,
            "normalized_last": result[-1]["timestamp"] if result else None,
        }
        return result


class DhanHistoricalClient:
    """Fetch Dhan V2 NIFTY futures 5-minute OHLCV/OI."""

    source = "DHAN"
    URL = "https://api.dhan.co/v2/charts/intraday"

    def __init__(
        self,
        access_token: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=20.0, follow_redirects=True)
        self.access_token = access_token
        self.last_history_debug: dict[str, Any] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "DhanHistoricalClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def history(
        self,
        security_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        response = self.client.post(
            self.URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "access-token": self.access_token,
            },
            json={
                "securityId": str(security_id),
                "exchangeSegment": "NSE_FNO",
                "instrument": "FUTIDX",
                "interval": "5",
                "oi": True,
                "fromDate": start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                "toDate": end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        response.raise_for_status()
        payload = response.json()

        timestamps = list(payload.get("timestamp") or [])
        opens = list(payload.get("open") or [])
        highs = list(payload.get("high") or [])
        lows = list(payload.get("low") or [])
        closes = list(payload.get("close") or [])
        volumes = list(payload.get("volume") or [])
        oi_values = list(payload.get("open_interest") or payload.get("openInterest") or [])

        rows: list[dict[str, Any]] = []
        for idx, raw_ts in enumerate(timestamps):
            if idx >= min(len(opens), len(highs), len(lows), len(closes)):
                continue
            ts = datetime.fromtimestamp(float(raw_ts), tz=UTC).astimezone(IST)
            ts = ts.replace(second=0, microsecond=0)
            if not _regular_session(ts) or ts < start or ts > end:
                continue
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": float(opens[idx]),
                    "high": float(highs[idx]),
                    "low": float(lows[idx]),
                    "close": float(closes[idx]),
                    "volume": _float(volumes[idx]) if idx < len(volumes) else None,
                    "open_interest": _float(oi_values[idx]) if idx < len(oi_values) else None,
                    "instrument": str(security_id),
                    "source": self.source,
                }
            )

        dedup = {(row["timestamp"], row["instrument"]): row for row in rows}
        result = sorted(dedup.values(), key=lambda row: row["timestamp"])
        self.last_history_debug = {
            "security_id": str(security_id),
            "raw_count": len(timestamps),
            "normalized_count": len(result),
            "normalized_first": result[0]["timestamp"] if result else None,
            "normalized_last": result[-1]["timestamp"] if result else None,
        }
        return result
