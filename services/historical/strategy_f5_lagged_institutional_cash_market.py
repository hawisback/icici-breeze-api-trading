"""Collect/normalize lagged FII/FPI and DII cash activity for F5 research."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_lagged_institutional_cash_protocol import (
    NSE_API_URL,
    NSE_PAGE_URL,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RAW_MARKET_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("cash-flow row has no date")
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unsupported cash-flow date: {text!r}")


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "None"):
        return None
    text = str(value).replace(",", "").replace("₹", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _normalize_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize NSE generic-category or legacy wide rows into one row per date."""
    if isinstance(payload, dict):
        for key in ("data", "rows", "records"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("cash-flow payload must contain a list of rows")

    by_date: dict[str, dict[str, Any]] = {}
    generic = False
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        raw_category = str(
            _first(raw, "category", "clientType") or ""
        ).strip().upper()
        category = re.sub(r"[^A-Z/]+", "", raw_category)
        if category in {"FII/FPI", "FII", "DII"}:
            generic = True
            day = _parse_date(_first(raw, "date", "tradeDate"))
            slot = by_date.setdefault(day, {"date": day})
            prefix = "FII" if category in {"FII/FPI", "FII"} else "DII"
            slot[f"{prefix}_buy_cr"] = _number(_first(raw, "buyValue", "buy"))
            slot[f"{prefix}_sell_cr"] = _number(_first(raw, "sellValue", "sell"))
            slot[f"{prefix}_net_cr"] = _number(_first(raw, "netValue", "net"))

    if generic:
        return [
            row for _, row in sorted(by_date.items())
            if row.get("FII_net_cr") is not None
            and row.get("DII_net_cr") is not None
        ]

    output: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        day_value = _first(raw, "date", "tradeDate")
        if day_value in (None, ""):
            continue
        row = {
            "date": _parse_date(day_value),
            "FII_buy_cr": _number(_first(raw, "fiibuy", "fii_buy_cr", "fii_buy")),
            "FII_sell_cr": _number(_first(raw, "fiisell", "fii_sell_cr", "fii_sell")),
            "FII_net_cr": _number(_first(raw, "fiinet", "fii_net_cr", "fii_net")),
            "DII_buy_cr": _number(_first(raw, "diibuy", "dii_buy_cr", "dii_buy")),
            "DII_sell_cr": _number(_first(raw, "diisell", "dii_sell_cr", "dii_sell")),
            "DII_net_cr": _number(_first(raw, "diinet", "dii_net_cr", "dii_net")),
        }
        if (
            row["FII_net_cr"] is not None
            and row["DII_net_cr"] is not None
        ):
            output.append(row)
    return sorted(output, key=lambda r: str(r["date"]))


def _load_source_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        return _normalize_payload(json.loads(text))
    rows = list(csv.DictReader(io.StringIO(text)))
    return _normalize_payload(rows)


class NSECashFlowClient:
    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": NSE_PAGE_URL,
        })
        self.request_diagnostics: list[dict[str, Any]] = []

    def fetch_snapshot(self) -> list[dict[str, Any]]:
        warm = self.session.get(NSE_PAGE_URL, timeout=self.timeout_seconds)
        self.request_diagnostics.append({
            "url": NSE_PAGE_URL,
            "status_code": warm.status_code,
            "purpose": "COOKIE_WARMUP",
        })
        response = self.session.get(NSE_API_URL, timeout=self.timeout_seconds)
        self.request_diagnostics.append({
            "url": NSE_API_URL,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content),
            "purpose": "PROVISIONAL_CASH_SNAPSHOT",
        })
        response.raise_for_status()
        return _normalize_payload(response.json())


def collect(
    f5_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    source_rows: list[dict[str, Any]],
    source_description: str,
    source_sha256: str | None = None,
    request_diagnostics: list[dict[str, Any]] | None = None,
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
    needed = set(before[-1:] + target_sessions[:-1])

    deduped = {str(row["date"]): row for row in source_rows}
    rows = [
        {**deduped[d.isoformat()], "source": source_description}
        for d in sorted(needed)
        if d.isoformat() in deduped
    ]
    available_dates = {str(row["date"]) for row in rows}
    missing_needed = [
        d.isoformat() for d in sorted(needed) if d.isoformat() not in available_dates
    ]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "window": WINDOW,
        "source": {
            "description": source_description,
            "source_file_sha256": source_sha256,
            "nse_page_url": NSE_PAGE_URL,
            "nse_api_url": NSE_API_URL,
            "units": "INR_CRORE",
            "lag_policy": "USE_T_MINUS_1_FOR_SESSION_T",
            "provisional": True,
        },
        "cash_rows": rows,
        "quality": {
            "target_sessions": len(target_sessions),
            "needed_source_sessions": len(needed),
            "available_source_sessions": len(rows),
            "missing_needed_source_sessions": missing_needed,
        },
        "request_diagnostics": request_diagnostics or [],
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect/normalize lagged institutional cash flow for F5"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument(
        "--source-file",
        type=Path,
        help="Archived NSE-format JSON/CSV. Omit to use the live NSE snapshot.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_lagged_institutional_cash_market_2026_07_09.json"
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()

    f5_sha = _sha256(args.f5_market)
    f5_market = json.loads(args.f5_market.read_text(encoding="utf-8"))
    if args.source_file:
        source_rows = _load_source_file(args.source_file)
        source_description = f"ARCHIVED_NSE_FORMAT:{args.source_file.name}"
        source_sha = _sha256(args.source_file)
        diagnostics: list[dict[str, Any]] = []
    else:
        client = NSECashFlowClient(timeout_seconds=args.timeout_seconds)
        source_rows = client.fetch_snapshot()
        source_description = "NSE_FIIDII_PROVISIONAL_API_SNAPSHOT"
        source_sha = None
        diagnostics = client.request_diagnostics

    report = collect(
        f5_market,
        f5_market_sha256=f5_sha,
        source_rows=source_rows,
        source_description=source_description,
        source_sha256=source_sha,
        request_diagnostics=diagnostics,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "sha256": _sha256(args.output),
        "source_f5_market_sha256": f5_sha,
        "quality": report["quality"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
