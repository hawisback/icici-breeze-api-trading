"""Collect the self-contained Breeze market artifact for Strategy F.

The collector fetches:
1. NIFTY spot 5-minute bars including a pre-window MACD warmup.
2. Only the NIFTY option contracts required by frozen MACD crossover entry
   intents during September 2026.

It does not calculate trade P&L or strategy outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time as clock
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from services.historical.independent_options_market_research import (
    BreezeOptionsClient,
    _load_local_env,
    _number,
    _usable_secret,
)
from services.historical.strategy_f_macd_options_protocol import (
    BACKTEST_WINDOW,
    EXECUTION,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    STRATEGY_NAME,
)

IST = ZoneInfo("Asia/Kolkata")
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
RESEARCH_TYPE = "STRATEGY_F_MACD_OPTIONS_MARKET_V1"


class BreezeSpotClient:
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

    @staticmethod
    def _chunks(start: date, end: date, span_days: int = 8):
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + timedelta(days=span_days))
            yield cursor, chunk_end
            cursor = chunk_end + timedelta(days=1)

    def history(self, start: date, end: date) -> list[dict[str, Any]]:
        sdk = self._client()
        normalized: list[dict[str, Any]] = []
        for first, last in self._chunks(start, end):
            self._throttle()
            response = sdk.get_historical_data_v2(
                interval="5minute",
                from_date=f"{first.isoformat()}T09:15:00.000Z",
                to_date=f"{last.isoformat()}T15:30:00.000Z",
                stock_code="NIFTY",
                exchange_code="NSE",
                product_type="cash",
                expiry_date="",
                right="others",
                strike_price="0",
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
            if (response or {}).get("Error") not in (None, "", "None"):
                raise RuntimeError(
                    f"Breeze spot history error for {first}..{last}: "
                    f"{(response or {}).get('Error')}"
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
                if not (start <= ts.date() <= end):
                    continue
                if not (SESSION_START <= ts.time() < SESSION_END):
                    continue
                values = [_number(item.get(k)) for k in ("open", "high", "low", "close")]
                if any(v is None or not math.isfinite(float(v)) or float(v) <= 0 for v in values):
                    continue
                o, h, l, c = [float(v) for v in values]
                if not (l <= o <= h and l <= c <= h):
                    continue
                normalized.append(
                    {
                        "timestamp": ts.isoformat(),
                        "date": ts.date().isoformat(),
                        "open": o,
                        "high": h,
                        "low": l,
                        "close": c,
                        "source": self.source,
                        "instrument": "NIFTY SPOT",
                    }
                )
        dedup = {row["timestamp"]: row for row in normalized}
        return [dedup[key] for key in sorted(dedup)]


def _atm_strike(price: float) -> int:
    step = int(OPTION_SELECTION["strike_step"])
    return int(math.floor(price / step + 0.5) * step)


def _nearest_expiry(day: date) -> date:
    expiries = [date.fromisoformat(x) for x in OPTION_SELECTION["weekly_expiries"]]
    eligible = [expiry for expiry in expiries if expiry >= day]
    if not eligible:
        raise ValueError(f"no frozen Strategy F option expiry covers {day}")
    return min(eligible)


def _macd_frame(spot_rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not spot_rows:
        raise ValueError("no NIFTY spot rows")
    frame = pd.DataFrame(spot_rows).sort_values("timestamp").reset_index(drop=True)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    close = frame["close"].astype(float)
    fast = close.ewm(
        span=int(MACD["fast_length"]),
        adjust=bool(MACD["ema_adjust"]),
    ).mean()
    slow = close.ewm(
        span=int(MACD["slow_length"]),
        adjust=bool(MACD["ema_adjust"]),
    ).mean()
    frame["macd"] = fast - slow
    frame["signal"] = frame["macd"].ewm(
        span=int(MACD["signal_length"]),
        adjust=bool(MACD["ema_adjust"]),
    ).mean()
    frame["macd_prev"] = frame["macd"].shift(1)
    frame["signal_prev"] = frame["signal"].shift(1)
    return frame


def _entry_signal_events(spot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = _macd_frame(spot_rows)
    start = date.fromisoformat(BACKTEST_WINDOW["start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    force_exit = time.fromisoformat(BACKTEST_WINDOW["force_exit_time"])
    events: list[dict[str, Any]] = []

    for row in frame.itertuples(index=False):
        if pd.isna(row.macd_prev) or pd.isna(row.signal_prev):
            continue
        bullish = row.macd_prev <= row.signal_prev and row.macd > row.signal
        bearish = row.macd_prev >= row.signal_prev and row.macd < row.signal
        if not (bullish or bearish):
            continue
        bar_start = row.timestamp.to_pydatetime()
        if bar_start.tzinfo is None:
            bar_start = bar_start.replace(tzinfo=IST)
        day = bar_start.date()
        if not (start <= day <= end):
            continue
        event_time = bar_start + timedelta(minutes=5)
        # A signal known at/after force exit can close an existing position but
        # cannot create a new Strategy F entry.
        if event_time.time() >= force_exit:
            continue
        direction = "BULLISH" if bullish else "BEARISH"
        right = "CE" if bullish else "PE"
        expiry = _nearest_expiry(day)
        strike = _atm_strike(float(row.close))
        events.append(
            {
                "signal_bar_start": bar_start.isoformat(),
                "signal_bar_end": event_time.isoformat(),
                "entry_timestamp": event_time.isoformat(),
                "date": day.isoformat(),
                "direction": direction,
                "right": right,
                "signal_close": float(row.close),
                "macd": float(row.macd),
                "macd_signal": float(row.signal),
                "expiry": expiry.isoformat(),
                "strike": strike,
            }
        )
    return events


def _contract_request_plan(events: list[dict[str, Any]]) -> dict[tuple[str, int, str], set[date]]:
    plan: dict[tuple[str, int, str], set[date]] = {}
    for event in events:
        key = (
            str(event["expiry"]),
            int(event["strike"]),
            str(event["right"]),
        )
        plan.setdefault(key, set()).add(date.fromisoformat(str(event["date"])))
    return plan


def _complete_session_quality(spot_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, int] = {}
    for row in spot_rows:
        by_day[str(row["date"])] = by_day.get(str(row["date"]), 0) + 1
    complete = sorted(day for day, count in by_day.items() if count == 75)
    partial = [
        {"date": day, "rows": count}
        for day, count in sorted(by_day.items())
        if count != 75
    ]
    return {
        "spot_dates": sorted(by_day),
        "complete_75_bar_sessions": complete,
        "complete_session_count": len(complete),
        "partial_sessions": partial,
    }


def collect_strategy_f_market(
    *,
    spot_client: BreezeSpotClient,
    options_client: BreezeOptionsClient,
) -> dict[str, Any]:
    warmup_start = date.fromisoformat(BACKTEST_WINDOW["warmup_start"])
    end = date.fromisoformat(BACKTEST_WINDOW["end"])
    spot_rows = spot_client.history(warmup_start, end)
    events = _entry_signal_events(spot_rows)
    plan = _contract_request_plan(events)

    option_rows: list[dict[str, Any]] = []
    contract_requests: list[dict[str, Any]] = []
    for (expiry, strike, right), days in sorted(plan.items()):
        side = "call" if right == "CE" else "put"
        fetched = options_client.history(
            sorted(days),
            expiry,
            strike,
            side,
        )
        option_rows.extend(fetched)
        contract_requests.append(
            {
                "expiry": expiry,
                "strike": strike,
                "right": right,
                "session_dates": [d.isoformat() for d in sorted(days)],
                "rows_fetched": len(fetched),
            }
        )

    option_dedup = {
        (
            row["timestamp"],
            row["expiry"],
            int(row["strike"]),
            row["right"],
        ): row
        for row in option_rows
    }
    option_rows = [
        option_dedup[key] for key in sorted(option_dedup)
    ]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "research_only": True,
        "backtest_window": BACKTEST_WINDOW,
        "signal_contract": {
            "macd": MACD,
            "option_selection": OPTION_SELECTION,
            "execution": EXECUTION,
        },
        "spot_rows": spot_rows,
        "entry_signal_events_for_collection_only": events,
        "option_contract_requests": contract_requests,
        "option_rows": option_rows,
        "quality": {
            **_complete_session_quality(spot_rows),
            "spot_rows": len(spot_rows),
            "entry_signal_events": len(events),
            "option_contract_request_count": len(contract_requests),
            "option_rows": len(option_rows),
        },
        "request_diagnostics": {
            "spot": spot_client.request_diagnostics,
            "options": options_client.request_diagnostics,
        },
        "strategy_outcomes_scored": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Breeze market data for Strategy F MACD options backtest"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_f_macd_options_market_2026_09.json"),
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

    report = collect_strategy_f_market(
        spot_client=BreezeSpotClient(
            key,
            secret,
            token,
            calls_per_minute=args.calls_per_minute,
        ),
        options_client=BreezeOptionsClient(
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
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "quality": report["quality"],
                "strategy_outcomes_scored": False,
                "broker_called": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
