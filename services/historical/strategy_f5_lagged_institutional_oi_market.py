"""Collect official NSE participant-wise F&O OI for F5 institutional research."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import requests

from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_lagged_institutional_oi_protocol import (
    NSE_PARTICIPANT_OI_URL_PATTERN,
    PARTICIPANTS,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RAW_MARKET_V1"

_CANONICAL_FIELDS = {
    "clienttype": "participant",
    "futureindexlong": "future_index_long",
    "futureindexshort": "future_index_short",
    "futurestocklong": "future_stock_long",
    "futurestockshort": "future_stock_short",
    "optionindexcalllong": "option_index_call_long",
    "optionindexputlong": "option_index_put_long",
    "optionindexcallshort": "option_index_call_short",
    "optionindexputshort": "option_index_put_short",
    "optionstockcalllong": "option_stock_call_long",
    "optionstockputlong": "option_stock_put_long",
    "optionstockcallshort": "option_stock_call_short",
    "optionstockputshort": "option_stock_put_short",
    "totallongcontracts": "total_long_contracts",
    "totalshortcontracts": "total_short_contracts",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _parse_int(value: Any) -> int | None:
    if value in (None, "", "None", "-"):
        return None
    text = str(value).strip().replace(",", "")
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_participant_oi_csv(
    text: str,
    *,
    report_date: date,
    source_url: str,
) -> list[dict[str, Any]]:
    rows = list(csv.reader(io.StringIO(text)))
    header_index = None
    for i, row in enumerate(rows):
        normalized = [_normalize_header(x) for x in row]
        if "clienttype" in normalized and "futureindexlong" in normalized:
            header_index = i
            break
    if header_index is None:
        raise ValueError(f"participant OI header not found for {report_date}")

    header = rows[header_index]
    canonical = [
        _CANONICAL_FIELDS.get(_normalize_header(value))
        for value in header
    ]
    if "participant" not in canonical:
        raise ValueError(f"participant column not found for {report_date}")

    output: list[dict[str, Any]] = []
    expected = {x.lower(): x for x in PARTICIPANTS}
    for row in rows[header_index + 1:]:
        if not row:
            continue
        padded = list(row) + [""] * max(0, len(canonical) - len(row))
        mapped = {
            canonical[i]: padded[i]
            for i in range(len(canonical))
            if canonical[i] is not None
        }
        raw_participant = str(mapped.get("participant") or "").strip()
        participant = expected.get(raw_participant.lower())
        if participant is None:
            continue

        record: dict[str, Any] = {
            "date": report_date.isoformat(),
            "participant": participant,
            "source": "NSE_PARTICIPANT_WISE_OI",
            "source_url": source_url,
        }
        for field in _CANONICAL_FIELDS.values():
            if field == "participant":
                continue
            record[field] = _parse_int(mapped.get(field))
        output.append(record)

    found = {row["participant"] for row in output}
    missing = [p for p in PARTICIPANTS if p not in found]
    if missing:
        raise ValueError(
            f"participant OI rows missing for {report_date}: {missing}"
        )
    return output


class NSEParticipantOIClient:
    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
            "Accept": "text/csv,text/plain,*/*",
            "Referer": "https://www.nseindia.com/all-reports-derivatives",
        })
        self.request_diagnostics: list[dict[str, Any]] = []

    def fetch(self, day: date) -> list[dict[str, Any]]:
        ddmmyyyy = day.strftime("%d%m%Y")
        url = NSE_PARTICIPANT_OI_URL_PATTERN.format(ddmmyyyy=ddmmyyyy)
        response = self.session.get(url, timeout=self.timeout_seconds)
        self.request_diagnostics.append({
            "date": day.isoformat(),
            "url": url,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content),
        })
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return _parse_participant_oi_csv(
            response.text,
            report_date=day,
            source_url=url,
        )


def collect(
    f5_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    client: NSEParticipantOIClient,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    all_sessions = sorted({
        date.fromisoformat(str(row["date"]))
        for row in list(f5_market.get("spot_rows") or [])
    })
    target_sessions = [d for d in all_sessions if start <= d <= end]
    if not target_sessions:
        raise ValueError("no target Jul-Sep sessions in F5 market")

    before = [d for d in all_sessions if d < target_sessions[0]]
    needed = sorted(set(before[-2:] + target_sessions))

    rows: list[dict[str, Any]] = []
    session_quality: list[dict[str, Any]] = []
    for day in needed:
        fetched = client.fetch(day)
        rows.extend(fetched)
        found = sorted({r["participant"] for r in fetched})
        session_quality.append({
            "date": day.isoformat(),
            "participants_found": found,
            "participant_count": len(found),
            "complete": set(found) == set(PARTICIPANTS),
        })

    rows = sorted(
        rows,
        key=lambda r: (str(r["date"]), str(r["participant"])),
    )

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "window": WINDOW,
        "source": {
            "name": "NSE_FO_PARTICIPANT_WISE_OPEN_INTEREST",
            "url_pattern": NSE_PARTICIPANT_OI_URL_PATTERN,
            "lag_policy": "USE_T_MINUS_1_FOR_SESSION_T",
        },
        "participant_oi_rows": rows,
        "session_quality": session_quality,
        "quality": {
            "target_sessions": len(target_sessions),
            "reports_requested": len(needed),
            "complete_reports": sum(bool(x["complete"]) for x in session_quality),
            "participant_rows": len(rows),
        },
        "request_diagnostics": client.request_diagnostics,
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect lagged NSE participant-wise OI for F5 research"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_lagged_institutional_oi_market_2026_07_09.json"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()

    f5_sha = _sha256(args.f5_market)
    f5_market = json.loads(args.f5_market.read_text(encoding="utf-8"))
    report = collect(
        f5_market,
        f5_market_sha256=f5_sha,
        client=NSEParticipantOIClient(timeout_seconds=args.timeout_seconds),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = _sha256(args.output)
    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "source_f5_market_sha256": f5_sha,
        "quality": report["quality"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
