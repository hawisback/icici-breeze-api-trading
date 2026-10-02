"""Collect NIFTY 50 constituent breadth points from Breeze for F5 research."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time as clock
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from services.historical.independent_options_market_research import (
    IST,
    SESSION_END,
    SESSION_START,
    _load_local_env,
    _number,
    _usable_secret,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    PROTOCOL_VERSION as F5_PROTOCOL_VERSION,
)
from services.historical.strategy_f5_nifty50_breadth_protocol import (
    NIFTY50_JUNE_2026,
    PROTOCOL_VERSION,
    SEPTEMBER_30_CHANGE,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_NIFTY50_BREADTH_RAW_MARKET_V1"
KEEP_TIMES = {"09:15", "15:20"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _membership(day: date) -> list[str]:
    members = list(NIFTY50_JUNE_2026)
    effective = date.fromisoformat(SEPTEMBER_30_CHANGE["effective_date"])
    if day >= effective:
        members.remove(str(SEPTEMBER_30_CHANGE["exclude"]))
        members.append(str(SEPTEMBER_30_CHANGE["include"]))
    return sorted(members)


def _extract_isec_stock_code(payload: Any) -> str | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        candidates.append(payload)
        for key in ("Success", "success", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                candidates.append(value)
            elif isinstance(value, list):
                candidates.extend(x for x in value if isinstance(x, dict))
    elif isinstance(payload, list):
        candidates.extend(x for x in payload if isinstance(x, dict))

    for item in candidates:
        for key in ("isec_stock_code", "isecStockCode", "stock_code"):
            value = item.get(key)
            if value not in (None, "", "None"):
                return str(value)
    return None


class BreezeBreadthClient:
    source = "BREEZE"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session_token: str,
        *,
        breeze_factory: Any | None = None,
        calls_per_minute: int = 80,
    ) -> None:
        if calls_per_minute <= 0:
            raise ValueError("calls_per_minute must be positive")
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_token = session_token
        self._factory = breeze_factory
        self._sdk: Any | None = None
        self._min_request_interval = 60.0 / calls_per_minute
        self._last_request_at: float | None = None
        self.mapping_diagnostics: list[dict[str, Any]] = []
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

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = clock.monotonic() - self._last_request_at
            remaining = self._min_request_interval - elapsed
            if remaining > 0:
                clock.sleep(remaining)
        self._last_request_at = clock.monotonic()

    @staticmethod
    def _chunks(start: date, end: date, span_days: int = 8):
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + timedelta(days=span_days))
            yield cursor, chunk_end
            cursor = chunk_end + timedelta(days=1)

    def resolve(self, nse_symbol: str) -> str | None:
        self._throttle()
        payload = self._client().get_names(
            exchange_code="NSE",
            stock_code=nse_symbol,
        )
        mapped = _extract_isec_stock_code(payload)
        self.mapping_diagnostics.append({
            "nse_symbol": nse_symbol,
            "isec_stock_code": mapped,
            "resolved": mapped is not None,
        })
        return mapped

    def history_points(
        self,
        *,
        nse_symbol: str,
        isec_stock_code: str,
        start: date,
        end: date,
        target_dates: set[date],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        sdk = self._client()

        for first, last in self._chunks(start, end):
            self._throttle()
            response = sdk.get_historical_data_v2(
                interval="5minute",
                from_date=f"{first.isoformat()}T09:15:00.000Z",
                to_date=f"{last.isoformat()}T15:30:00.000Z",
                stock_code=isec_stock_code,
                exchange_code="NSE",
                product_type="cash",
                expiry_date="",
                right="others",
                strike_price="0",
            )
            success = list((response or {}).get("Success") or [])
            self.request_diagnostics.append({
                "nse_symbol": nse_symbol,
                "isec_stock_code": isec_stock_code,
                "start": first.isoformat(),
                "end": last.isoformat(),
                "status": (response or {}).get("Status"),
                "error": (response or {}).get("Error"),
                "raw_count": len(success),
            })
            if (response or {}).get("Error") not in (None, "", "None"):
                raise RuntimeError(
                    f"Breeze breadth history error for {nse_symbol} "
                    f"{first}..{last}: {(response or {}).get('Error')}"
                )

            for item in success:
                raw_ts = str(item.get("datetime") or "")
                try:
                    ts = datetime.fromisoformat(raw_ts)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=IST)
                else:
                    ts = ts.astimezone(IST)
                ts = ts.replace(second=0, microsecond=0)
                if ts.date() not in target_dates:
                    continue
                if not (SESSION_START <= ts.time() < SESSION_END):
                    continue
                hhmm = ts.strftime("%H:%M")
                if hhmm not in KEEP_TIMES:
                    continue

                values = [
                    _number(item.get(k)) for k in ("open", "high", "low", "close")
                ]
                if any(
                    v is None
                    or not math.isfinite(float(v))
                    or float(v) <= 0
                    for v in values
                ):
                    continue
                o, h, l, c = [float(v) for v in values]
                if not (l <= o <= h and l <= c <= h):
                    continue

                normalized.append({
                    "timestamp": ts.isoformat(),
                    "date": ts.date().isoformat(),
                    "time": hhmm,
                    "nse_symbol": nse_symbol,
                    "isec_stock_code": isec_stock_code,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": _number(item.get("volume")),
                    "source": self.source,
                    "source_interval": "5minute",
                })

        dedup = {
            (r["date"], r["nse_symbol"], r["time"]): r
            for r in normalized
        }
        return [dedup[key] for key in sorted(dedup)]


def collect(
    f5_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    client: BreezeBreadthClient,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    target_dates = sorted({
        date.fromisoformat(str(row["date"]))
        for row in list(f5_market.get("spot_rows") or [])
        if start <= date.fromisoformat(str(row["date"])) <= end
    })
    target_set = set(target_dates)

    symbols = sorted(
        set(NIFTY50_JUNE_2026)
        | {str(SEPTEMBER_30_CHANGE["include"])}
    )
    symbol_map: dict[str, str] = {}
    for symbol in symbols:
        mapped = client.resolve(symbol)
        if mapped is not None:
            symbol_map[symbol] = mapped

    rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    effective = date.fromisoformat(SEPTEMBER_30_CHANGE["effective_date"])

    for symbol in symbols:
        mapped = symbol_map.get(symbol)
        if mapped is None:
            continue
        if symbol == str(SEPTEMBER_30_CHANGE["include"]):
            symbol_start, symbol_end = effective, end
        elif symbol == str(SEPTEMBER_30_CHANGE["exclude"]):
            symbol_start, symbol_end = start, effective - timedelta(days=1)
        else:
            symbol_start, symbol_end = start, end
        active_dates = {
            d
            for d in target_set
            if symbol_start <= d <= symbol_end
            and symbol in _membership(d)
        }
        if not active_dates:
            continue
        fetched = client.history_points(
            nse_symbol=symbol,
            isec_stock_code=mapped,
            start=min(active_dates),
            end=max(active_dates),
            target_dates=active_dates,
        )
        rows.extend(fetched)
        requests.append({
            "nse_symbol": symbol,
            "isec_stock_code": mapped,
            "active_start": min(active_dates).isoformat(),
            "active_end": max(active_dates).isoformat(),
            "target_sessions": len(active_dates),
            "kept_rows": len(fetched),
        })

    dedup = {
        (r["date"], r["nse_symbol"], r["time"]): r
        for r in rows
    }
    rows = [dedup[key] for key in sorted(dedup)]

    rows_by_day: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        rows_by_day.setdefault(str(row["date"]), set()).add(
            (str(row["nse_symbol"]), str(row["time"]))
        )

    session_quality = []
    for day in target_dates:
        expected = _membership(day)
        pairs = rows_by_day.get(day.isoformat(), set())
        complete = sum(
            (symbol, "09:15") in pairs and (symbol, "15:20") in pairs
            for symbol in expected
        )
        session_quality.append({
            "date": day.isoformat(),
            "expected_constituents": len(expected),
            "constituents_with_both_points": complete,
            "coverage_pct": round(complete / len(expected) * 100.0, 2),
        })

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "window": WINDOW,
        "constituent_universe": {
            "june_2026": NIFTY50_JUNE_2026,
            "september_30_change": SEPTEMBER_30_CHANGE,
        },
        "symbol_map": symbol_map,
        "constituent_points_5m": rows,
        "constituent_requests": requests,
        "session_quality": session_quality,
        "quality": {
            "expected_sessions": len(target_dates),
            "symbols_expected_across_period": len(symbols),
            "symbols_resolved": len(symbol_map),
            "mapping_coverage_pct": round(
                len(symbol_map) / len(symbols) * 100.0, 2
            ),
            "constituent_point_rows": len(rows),
            "sessions_with_90pct_coverage": sum(
                float(x["coverage_pct"]) >= 90.0 for x in session_quality
            ),
        },
        "request_diagnostics": {
            "mapping": client.mapping_diagnostics,
            "history": client.request_diagnostics,
        },
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect NIFTY 50 breadth points for F5 research"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_nifty50_breadth_market_2026_07_09.json"
        ),
    )
    parser.add_argument("--calls-per-minute", type=int, default=80)
    args = parser.parse_args()

    _load_local_env()
    key = _usable_secret("BREEZE_API_KEY")
    secret = _usable_secret("BREEZE_SECRET_KEY")
    token = _usable_secret("BREEZE_SESSION_TOKEN")
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )

    f5_sha = _sha256(args.f5_market)
    f5_market = json.loads(args.f5_market.read_text(encoding="utf-8"))
    report = collect(
        f5_market,
        f5_market_sha256=f5_sha,
        client=BreezeBreadthClient(
            key,
            secret,
            token,
            calls_per_minute=args.calls_per_minute,
        ),
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
        "session_quality": report["session_quality"],
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
