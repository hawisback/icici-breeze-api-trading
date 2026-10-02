"""Collect front-month NIFTY futures context for F5 Jul-Sep research."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time as clock
from datetime import date, datetime
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
from services.historical.strategy_f5_nifty_futures_confirmation_protocol import (
    MONTHLY_FUTURES_EXPIRIES,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    WINDOW,
)

RESEARCH_TYPE = "STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_RAW_MARKET_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nearest_expiry(day: date) -> date:
    expiries = [date.fromisoformat(x) for x in MONTHLY_FUTURES_EXPIRIES]
    eligible = [expiry for expiry in expiries if expiry >= day]
    if not eligible:
        raise ValueError(f"no frozen NIFTY futures expiry covers {day}")
    return min(eligible)


class BreezeFiveMinuteFuturesClient:
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

    def history(self, day: date, expiry: date) -> list[dict[str, Any]]:
        self._throttle()
        response = self._client().get_historical_data_v2(
            interval="5minute",
            from_date=f"{day.isoformat()}T09:15:00.000Z",
            to_date=f"{day.isoformat()}T15:30:00.000Z",
            stock_code="NIFTY",
            exchange_code="NFO",
            product_type="futures",
            expiry_date=f"{expiry.isoformat()}T07:00:00.000Z",
            right="others",
            strike_price="0",
        )
        success = list((response or {}).get("Success") or [])
        self.request_diagnostics.append({
            "date": day.isoformat(),
            "expiry": expiry.isoformat(),
            "status": (response or {}).get("Status"),
            "error": (response or {}).get("Error"),
            "raw_count": len(success),
        })
        error = (response or {}).get("Error")
        if error not in (None, "", "None"):
            raise RuntimeError(
                f"Breeze futures history error for {day} {expiry}: {error}"
            )

        rows: list[dict[str, Any]] = []
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
            if ts.date() != day:
                continue
            if not (SESSION_START <= ts.time() < SESSION_END):
                continue

            vals = [_number(item.get(k)) for k in ("open", "high", "low", "close")]
            if any(v is None or not math.isfinite(float(v)) for v in vals):
                continue
            o, h, l, c = [float(v) for v in vals]
            if not (l <= o <= h and l <= c <= h):
                continue

            rows.append({
                "timestamp": ts.isoformat(),
                "date": day.isoformat(),
                "expiry": expiry.isoformat(),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": _number(item.get("volume")),
                "open_interest": _number(item.get("open_interest")),
                "source": self.source,
                "instrument": f"NIFTY FUT {expiry.isoformat()}",
                "source_interval": "5minute",
            })

        dedup = {row["timestamp"]: row for row in rows}
        return [dedup[key] for key in sorted(dedup)]


def collect(
    f5_market: dict[str, Any],
    *,
    f5_market_sha256: str,
    client: BreezeFiveMinuteFuturesClient,
) -> dict[str, Any]:
    if f5_market.get("protocol_version") != F5_PROTOCOL_VERSION:
        raise ValueError("F5 market protocol mismatch")
    if f5_market.get("strategy_outcomes_scored") is not False:
        raise ValueError("F5 market must be outcome-unscored")

    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    spot_dates = sorted({
        date.fromisoformat(str(row["date"]))
        for row in list(f5_market.get("spot_rows") or [])
        if start <= date.fromisoformat(str(row["date"])) <= end
    })

    rows: list[dict[str, Any]] = []
    session_contracts = []
    for day in spot_dates:
        expiry = _nearest_expiry(day)
        fetched = client.history(day, expiry)
        rows.extend(fetched)
        session_contracts.append({
            "date": day.isoformat(),
            "expiry": expiry.isoformat(),
            "rows_fetched": len(fetched),
        })

    dedup = {(r["timestamp"], r["expiry"]): r for r in rows}
    rows = [dedup[key] for key in sorted(dedup)]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "research_only": True,
        "source_f5_market_sha256": f5_market_sha256,
        "window": WINDOW,
        "monthly_futures_expiries": MONTHLY_FUTURES_EXPIRIES,
        "session_contracts": session_contracts,
        "futures_rows_5m": rows,
        "quality": {
            "expected_sessions": len(spot_dates),
            "session_contracts": len(session_contracts),
            "sessions_with_rows": sum(
                int(x["rows_fetched"]) > 0 for x in session_contracts
            ),
            "futures_rows_5m": len(rows),
        },
        "request_diagnostics": client.request_diagnostics,
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect front-month NIFTY futures for F5 context research"
    )
    parser.add_argument("--f5-market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/strategy_f5_nifty_futures_confirmation_market_2026_07_09.json"
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
        client=BreezeFiveMinuteFuturesClient(
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
        "broker_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
