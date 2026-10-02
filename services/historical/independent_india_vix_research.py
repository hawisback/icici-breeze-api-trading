"""Collect INDIA VIX candles for development-only options attribution.

The collector uses the exact session dates from an existing research corpus and
the repository's Kite historical adapter. VIX volume/OI are not used.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from services.historical.research_provider_clients import KiteHistoricalClient

IST = ZoneInfo("Asia/Kolkata")
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
DEFAULT_OUTPUT = Path("data/independent_india_vix_research.json")


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


def _chunks(values: list[date], size: int = 10) -> list[list[date]]:
    ordered = sorted(set(values))
    return [ordered[index : index + size] for index in range(0, len(ordered), size)]


class VixHistoryClient(Protocol):
    source: str

    def resolve_india_vix_token(self) -> str: ...

    def history(
        self,
        instrument_token: str,
        start: datetime,
        end: datetime,
        *,
        instrument_name: str,
        include_oi: bool,
    ) -> list[dict[str, Any]]: ...


def build_vix_dataset(
    session_source: dict[str, Any],
    client: VixHistoryClient,
    instrument_token: str | None = None,
) -> dict[str, Any]:
    dates = _session_dates(session_source)
    token = instrument_token or client.resolve_india_vix_token()
    wanted = {day.isoformat() for day in dates}
    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for chunk in _chunks(dates):
        first, last = chunk[0], chunk[-1]
        start = datetime.combine(first, SESSION_START, tzinfo=IST)
        end = datetime.combine(last, SESSION_END, tzinfo=IST)
        fetched = client.history(
            token,
            start,
            end,
            instrument_name="NSE:INDIA VIX",
            include_oi=False,
        )
        filtered = [
            row
            for row in fetched
            if str(row["timestamp"])[:10] in wanted
        ]
        rows.extend(filtered)
        diagnostics.append(
            {
                "start": first.isoformat(),
                "end": last.isoformat(),
                "returned_rows": len(fetched),
                "selected_rows": len(filtered),
            }
        )

    dedup = {str(row["timestamp"]): row for row in rows}
    ordered = sorted(dedup.values(), key=lambda row: str(row["timestamp"]))
    counts = {day.isoformat(): 0 for day in dates}
    invalid_ohlc = 0
    for row in ordered:
        day = str(row["timestamp"])[:10]
        if day in counts:
            counts[day] += 1
        invalid_ohlc += not (
            float(row["low"]) <= float(row["open"]) <= float(row["high"])
            and float(row["low"]) <= float(row["close"]) <= float(row["high"])
        )

    return {
        "research_type": "INDEPENDENT_INDIA_VIX_ATTRIBUTION_DATA",
        "research_only": True,
        "purpose": (
            "development-only attribution benchmark for options-implied "
            "movement-regime research"
        ),
        "interval_minutes": 5,
        "session_dates": [day.isoformat() for day in dates],
        "instrument_token": str(token),
        "vix_rows": ordered,
        "quality": {
            "sessions": len(dates),
            "rows": len(ordered),
            "duplicate_rows": len(rows) - len(ordered),
            "invalid_ohlc_rows": int(invalid_ohlc),
            "complete_75_bar_sessions": sum(value == 75 for value in counts.values()),
            "rows_by_session": counts,
        },
        "request_diagnostics": diagnostics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect INDIA VIX for development-only options attribution"
    )
    parser.add_argument("--session-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--instrument-token")
    args = parser.parse_args()

    _load_local_env()
    key = _usable_secret("KITE_API_KEY")
    access = _usable_secret("KITE_ACCESS_TOKEN")
    if not (key and access):
        raise RuntimeError("KITE_API_KEY and KITE_ACCESS_TOKEN are required")

    source = json.loads(args.session_source.read_text(encoding="utf-8"))
    report = build_vix_dataset(
        source,
        KiteHistoricalClient(key, access),
        instrument_token=(
            args.instrument_token
            or os.getenv("RESEARCH_KITE_INDIA_VIX_INSTRUMENT_TOKEN")
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "quality": report["quality"]}, indent=2))


if __name__ == "__main__":
    main()
