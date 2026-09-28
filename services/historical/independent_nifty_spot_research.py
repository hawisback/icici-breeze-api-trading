"""Collect NIFTY cash-index candles for development-only lead/lag attribution.

The output is a raw research dataset. NIFTY index volume is not treated as a
liquidity feature; this collector exists only to distinguish options-implied
underlying movement from genuine incremental options information.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
DEFAULT_OUTPUT = Path("data/independent_nifty_spot_research.json")


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


def _session_dates(payload: dict[str, Any]) -> list[date]:
    values = payload.get("session_dates") or []
    dates = sorted({date.fromisoformat(str(value)) for value in values})
    if not dates:
        raise ValueError("session source has no session_dates")
    return dates


class SpotHistoryClient(Protocol):
    source: str

    def history(self, session_dates: list[date]) -> list[dict[str, Any]]: ...


class BreezeNiftySpotClient:
    source = "BREEZE"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session_token: str,
        breeze_factory: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_token = session_token
        self._factory = breeze_factory
        self._sdk: Any | None = None
        self.request_diagnostics: list[dict[str, Any]] = []

    def _client(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        factory = self._factory
        if factory is None:
            from breeze_connect import BreezeConnect

            factory = BreezeConnect
        sdk = factory(api_key=self.api_key)
        sdk.generate_session(
            api_secret=self.api_secret,
            session_token=self.session_token,
        )
        self._sdk = sdk
        return sdk

    @staticmethod
    def _request_chunks(
        session_dates: list[date], max_span_days: int = 9
    ) -> list[list[date]]:
        ordered = sorted(set(session_dates))
        chunks: list[list[date]] = []
        for day in ordered:
            if not chunks or (day - chunks[-1][0]).days > max_span_days:
                chunks.append([day])
            else:
                chunks[-1].append(day)
        return chunks

    def history(self, session_dates: list[date]) -> list[dict[str, Any]]:
        if not session_dates:
            return []
        sdk = self._client()
        wanted = set(session_dates)
        rows: list[dict[str, Any]] = []
        for chunk in self._request_chunks(session_dates):
            first, last = min(chunk), max(chunk)
            response = sdk.get_historical_data_v2(
                interval="5minute",
                from_date=f"{first.isoformat()}T09:15:00.000Z",
                to_date=f"{last.isoformat()}T15:30:00.000Z",
                stock_code="NIFTY",
                exchange_code="NSE",
                product_type="cash",
            )
            success = list((response or {}).get("Success") or [])
            self.request_diagnostics.append(
                {
                    "start": first.isoformat(),
                    "end": last.isoformat(),
                    "status": (response or {}).get("Status"),
                    "error": (response or {}).get("Error"),
                    "raw_count": len(success),
                }
            )
            for item in success:
                try:
                    ts = datetime.fromisoformat(str(item.get("datetime", "")))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=IST)
                else:
                    ts = ts.astimezone(IST)
                ts = ts.replace(second=0, microsecond=0)
                if ts.date() not in wanted or not (
                    SESSION_START <= ts.time() < SESSION_END
                ):
                    continue
                values = [
                    _number(item.get(key))
                    for key in ("open", "high", "low", "close")
                ]
                if any(value is None for value in values):
                    continue
                rows.append(
                    {
                        "timestamp": ts.isoformat(),
                        "open": values[0],
                        "high": values[1],
                        "low": values[2],
                        "close": values[3],
                        "source": self.source,
                        "instrument": "NIFTY 50",
                    }
                )
        dedup = {row["timestamp"]: row for row in rows}
        return sorted(dedup.values(), key=lambda row: row["timestamp"])


def _quality(session_dates: list[date], rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {day.isoformat(): 0 for day in session_dates}
    invalid_ohlc = 0
    for row in rows:
        day = datetime.fromisoformat(str(row["timestamp"])).date().isoformat()
        if day in counts:
            counts[day] += 1
        if not (
            float(row["low"]) <= float(row["open"]) <= float(row["high"])
            and float(row["low"]) <= float(row["close"]) <= float(row["high"])
        ):
            invalid_ohlc += 1
    timestamps = [str(row["timestamp"]) for row in rows]
    return {
        "sessions": len(session_dates),
        "rows": len(rows),
        "duplicate_rows": len(timestamps) - len(set(timestamps)),
        "invalid_ohlc_rows": invalid_ohlc,
        "rows_by_session": counts,
        "complete_75_bar_sessions": sum(value == 75 for value in counts.values()),
    }


def build_spot_dataset(
    session_source: dict[str, Any], client: SpotHistoryClient
) -> dict[str, Any]:
    dates = _session_dates(session_source)
    rows = client.history(dates)
    result = {
        "research_type": "INDEPENDENT_NIFTY_SPOT_ATTRIBUTION_DATA",
        "research_only": True,
        "purpose": (
            "development-only attribution: distinguish options-implied movement "
            "from NIFTY cash/index movement"
        ),
        "interval_minutes": 5,
        "session_dates": [day.isoformat() for day in dates],
        "spot_rows": rows,
        "quality": _quality(dates, rows),
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
        description="Collect NIFTY cash-index data for development lead/lag attribution"
    )
    parser.add_argument("--session-source", type=Path, required=True)
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

    source = json.loads(args.session_source.read_text(encoding="utf-8"))
    report = build_spot_dataset(
        source,
        BreezeNiftySpotClient(key, secret, token),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "quality": report["quality"]}, indent=2))


if __name__ == "__main__":
    main()
