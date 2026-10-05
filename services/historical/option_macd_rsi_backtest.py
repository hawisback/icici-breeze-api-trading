"""Read-only NIFTY PE MACD/RSI backtest on 3-minute option-price candles."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Sequence
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 30)


@dataclass(frozen=True, slots=True)
class Config:
    days: int = 10
    warmup_days: int = 5
    quantity: int = 65
    source: str = "BREEZE"
    fast: int = 3
    slow: int = 10
    signal: int = 16
    rsi: int = 14
    rsi_min: float = 50.0


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    expiry: date
    strike: float
    valid_from: datetime | None
    valid_to: datetime | None


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: str
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _chunks(values: Sequence[str], n: int = 500):
    for i in range(0, len(values), n):
        yield values[i:i + n]


def load_puts(path: Path) -> list[Instrument]:
    with _ro(path) as conn:
        rows = conn.execute(
            """SELECT instrument_id, expiry, strike, valid_from, valid_to
               FROM instruments
               WHERE segment='OPTIONS' AND underlying='NIFTY'
                 AND option_right='PUT' AND expiry IS NOT NULL AND strike IS NOT NULL
               ORDER BY expiry, strike"""
        ).fetchall()
    out = []
    for row in rows:
        try:
            out.append(Instrument(
                str(row["instrument_id"]), date.fromisoformat(str(row["expiry"])),
                float(row["strike"]), _dt(row["valid_from"]), _dt(row["valid_to"]),
            ))
        except (TypeError, ValueError):
            pass
    return out


def discover_days(path: Path, ids: Sequence[str], source: str, count: int) -> list[date]:
    found: set[date] = set()
    with _ro(path) as conn:
        for batch in _chunks(ids):
            marks = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"""SELECT DISTINCT substr(start_time,1,10) d
                    FROM historical_candles
                    WHERE instrument_id IN ({marks}) AND interval='1m' AND source=?
                    ORDER BY d DESC LIMIT ?""",
                [*batch, source.upper(), count * 3],
            ).fetchall()
            for row in rows:
                try:
                    found.add(date.fromisoformat(str(row["d"])))
                except ValueError:
                    pass
    return sorted(found, reverse=True)[:count][::-1]


def _valid(inst: Instrument, day: date) -> bool:
    if inst.expiry < day:
        return False
    start = datetime.combine(day, OPEN, tzinfo=IST).astimezone(timezone.utc)
    end = datetime.combine(day, CLOSE, tzinfo=IST).astimezone(timezone.utc)
    return not ((inst.valid_from and inst.valid_from > end) or (inst.valid_to and inst.valid_to < start))


def nearest_expiry(puts: Sequence[Instrument], day: date) -> list[Instrument]:
    eligible = [inst for inst in puts if _valid(inst, day)]
    if not eligible:
        return []
    expiry = min(inst.expiry for inst in eligible)
    return [inst for inst in eligible if inst.expiry == expiry]


def load_1m(path: Path, ids: Sequence[str], first: date, last: date, source: str) -> dict[str, list[Bar]]:
    data = {instrument_id: [] for instrument_id in ids}
    start = datetime.combine(first, OPEN, tzinfo=IST).astimezone(timezone.utc).isoformat()
    end = datetime.combine(last, CLOSE, tzinfo=IST).astimezone(timezone.utc).isoformat()
    with _ro(path) as conn:
        for batch in _chunks(ids):
            marks = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"""SELECT instrument_id,start_time,end_time,open,high,low,close,volume,open_interest
                    FROM historical_candles
                    WHERE instrument_id IN ({marks}) AND interval='1m' AND source=?
                      AND start_time>=? AND start_time<?
                    ORDER BY instrument_id,start_time""",
                [*batch, source.upper(), start, end],
            ).fetchall()
            for r in rows:
                bar = Bar(str(r["instrument_id"]), _dt(r["start_time"]), _dt(r["end_time"]),
                          float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                          int(r["volume"] or 0), int(r["open_interest"] or 0))
                data[bar.instrument_id].append(bar)
    return data


def aggregate_3m(bars: Sequence[Bar]) -> list[Bar]:
    buckets: dict[tuple[date, int], dict[datetime, Bar]] = {}
    for bar in bars:
        local = bar.start.astimezone(IST)
        start = datetime.combine(local.date(), OPEN, tzinfo=IST)
        end = datetime.combine(local.date(), CLOSE, tzinfo=IST)
        if not start <= local < end:
            continue
        offset = int((local - start).total_seconds() // 60)
        buckets.setdefault((local.date(), offset // 3), {})[local] = bar
    out = []
    for (day, bucket), rows in sorted(buckets.items()):
        start = datetime.combine(day, OPEN, tzinfo=IST) + timedelta(minutes=bucket * 3)
        expected = [start + timedelta(minutes=i) for i in range(3)]
        if any(ts not in rows for ts in expected):
            continue
        group = [rows[ts] for ts in expected]
        out.append(Bar(group[0].instrument_id, start.astimezone(timezone.utc),
                       (start + timedelta(minutes=3)).astimezone(timezone.utc),
                       group[0].open, max(x.high for x in group), min(x.low for x in group),
                       group[-1].close, sum(x.volume for x in group), group[-1].oi))
    return out


def sma(values: Sequence[float | None], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1:i + 1]
        if all(v is not None for v in window):
            out[i] = sum(float(v) for v in window) / length
    return out


def rsi_wilder(closes: Sequence[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= length:
        return out
    gains, losses = [], []
    for i in range(1, length + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0)); losses.append(max(-change, 0.0))
    gain, loss = sum(gains) / length, sum(losses) / length

    def value(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[length] = value(gain, loss)
    for i in range(length + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = (gain * (length - 1) + max(change, 0.0)) / length
        loss = (loss * (length - 1) + max(-change, 0.0)) / length
        out[i] = value(gain, loss)
    return out


def indicator_series(bars: Sequence[Bar], cfg: Config):
    closes = [b.close for b in bars]
    fast, slow = sma(closes, cfg.fast), sma(closes, cfg.slow)
    macd = [None if fast[i] is None or slow[i] is None else fast[i] - slow[i] for i in range(len(bars))]
    signal = sma(macd, cfg.signal)
    rsi = rsi_wilder(closes, cfg.rsi)
    return macd, signal, rsi


def cross_up(macd, signal, rsi, i: int, minimum: float = 50.0) -> bool:
    if i <= 0 or any(x is None for x in (macd[i-1], signal[i-1], macd[i], signal[i], rsi[i])):
        return False
    return macd[i-1] <= signal[i-1] and macd[i] > signal[i] and rsi[i] > minimum


def cross_down(macd, signal, i: int) -> bool:
    if i <= 0 or any(x is None for x in (macd[i-1], signal[i-1], macd[i], signal[i])):
        return False
    return macd[i-1] >= signal[i-1] and macd[i] < signal[i]


def pnl(entry: float, exit_: float, quantity: int = 65) -> float:
    return round((exit_ - entry) * quantity, 2)


def run(historical_db: Path, instruments_db: Path, cfg: Config = Config()) -> dict[str, Any]:
    puts = load_puts(instruments_db)
    if not puts:
        raise RuntimeError("No NIFTY PUT metadata found")
    days = discover_days(historical_db, [x.instrument_id for x in puts], cfg.source,
                         cfg.days + cfg.warmup_days)
    if len(days) < cfg.days:
        raise RuntimeError(f"Only {len(days)} option-data sessions found; need {cfg.days}")
    test_days = days[-cfg.days:]
    ids = sorted({x.instrument_id for d in days for x in nearest_expiry(puts, d)})
    raw = load_1m(historical_db, ids, days[0], days[-1], cfg.source)
    bars = {k: aggregate_3m(v) for k, v in raw.items()}
    indicators = {k: indicator_series(v, cfg) for k, v in bars.items() if v}
    end_index = {k: {b.end: i for i, b in enumerate(v)} for k, v in bars.items()}
    trades, candidate_count = [], 0

    for day in test_days:
        eligible = nearest_expiry(puts, day)
        eligible_ids = {x.instrument_id for x in eligible}
        events = sorted({b.end for k in eligible_ids for b in bars.get(k, [])
                         if b.end.astimezone(IST).date() == day})
        position = None
        for event in events:
            if position:
                k = position["instrument"].instrument_id
                i = end_index.get(k, {}).get(event)
                if i is not None and cross_down(*indicators[k][:2], i):
                    series = bars[k]
                    if i + 1 < len(series) and series[i+1].start.astimezone(IST).date() == day:
                        exit_bar = series[i+1]
                        trades.append(_trade(position, exit_bar.open, exit_bar.start,
                                             "BEARISH_MACD_CROSS", cfg, event))
                        position = None
                        continue
            if not position:
                candidates = []
                for inst in eligible:
                    k = inst.instrument_id
                    i = end_index.get(k, {}).get(event)
                    if i is None or k not in indicators:
                        continue
                    m, s, r = indicators[k]
                    if not cross_up(m, s, r, i, cfg.rsi_min):
                        continue
                    if i + 1 >= len(bars[k]) or bars[k][i+1].start.astimezone(IST).date() != day:
                        continue
                    candidates.append((inst.strike, inst,
                                       bars[k][i], bars[k][i+1], m[i], s[i], r[i]))
                candidate_count += len(candidates)
                if candidates:
                    _, inst, signal_bar, entry_bar, m, s, r = min(candidates, key=lambda x: x[0])
                    position = {"instrument": inst, "signal_bar": signal_bar, "entry_bar": entry_bar,
                                "macd": m, "signal": s, "rsi": r}
        if position:
            series = [b for b in bars[position["instrument"].instrument_id]
                      if b.end.astimezone(IST).date() == day]
            if series:
                last = series[-1]
                reason = "SESSION_FORCE_CLOSE" if last.end.astimezone(IST).time() >= CLOSE else "LAST_AVAILABLE_BAR"
                trades.append(_trade(position, last.close, last.end, reason, cfg, None))

    pnls = [t["gross_pnl"] for t in trades]
    wins = sum(x > 0 for x in pnls)
    return {
        "strategy": "NIFTY_PE_3M_MACD_SMA_3_10_SIGNAL16_RSI14",
        "test_days": [d.isoformat() for d in test_days],
        "rules": {"entry": "fresh bottom-up MACD cross AND RSI(14)>50", "exit": "top-down MACD cross",
                  "quantity": cfg.quantity, "timeframe": "3m",
                  "expiry": "nearest non-expired", "fill": "next 3m open"},
        "candidate_signals": candidate_count,
        "trades": trades,
        "summary": {"trades": len(trades), "wins": wins, "losses": sum(x < 0 for x in pnls),
                    "win_rate_pct": round(100*wins/len(trades), 2) if trades else 0.0,
                    "total_gross_pnl": round(sum(pnls), 2),
                    "average_gross_pnl": round(mean(pnls), 2) if pnls else 0.0,
                    "best_trade": max(pnls) if pnls else 0.0, "worst_trade": min(pnls) if pnls else 0.0},
        "notes": ["Gross P&L excludes charges/slippage.",
                  "Option premium is not used for eligibility or selection.",
                  "When multiple PE contracts signal on the same event, the lower strike is used only as a deterministic tie-breaker.",
                  "SQLite databases are opened read-only; no broker calls are made."],
    }


def _trade(position, exit_price: float, exit_time: datetime, reason: str, cfg: Config,
           exit_signal_time: datetime | None) -> dict[str, Any]:
    inst, signal_bar, entry_bar = position["instrument"], position["signal_bar"], position["entry_bar"]
    gross = pnl(entry_bar.open, exit_price, cfg.quantity)
    capital = round(entry_bar.open * cfg.quantity, 2)
    return {"date": entry_bar.start.astimezone(IST).date().isoformat(), "instrument_id": inst.instrument_id,
            "expiry": inst.expiry.isoformat(), "strike": inst.strike, "quantity": cfg.quantity,
            "signal_time_ist": signal_bar.end.astimezone(IST).isoformat(),
            "signal_close": round(signal_bar.close, 2),
            "entry_time_ist": entry_bar.start.astimezone(IST).isoformat(), "entry_price": round(entry_bar.open, 2),
            "entry_macd": round(position["macd"], 6), "entry_signal": round(position["signal"], 6),
            "entry_rsi": round(position["rsi"], 4),
            "exit_signal_time_ist": exit_signal_time.astimezone(IST).isoformat() if exit_signal_time else None,
            "exit_time_ist": exit_time.astimezone(IST).isoformat(), "exit_price": round(exit_price, 2),
            "exit_reason": reason, "capital_used": capital, "gross_pnl": gross,
            "return_pct": round(100*gross/capital, 4) if capital else 0.0}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--historical-db", type=Path, default=Path("data/market/historical.db"))
    p.add_argument("--instruments-db", type=Path, default=Path("data/instruments/instruments.db"))
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--quantity", type=int, default=65)
    p.add_argument("--source", default="BREEZE")
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    cfg = Config(days=a.days, quantity=a.quantity, source=a.source.upper())
    report = run(a.historical_db, a.instruments_db, cfg)
    for i, t in enumerate(report["trades"], 1):
        print(f"{i:>2} {t['date']} PE {t['strike']:.0f} {t['entry_price']:.2f}->{t['exit_price']:.2f} "
              f"x{t['quantity']} P&L {t['gross_pnl']:+.2f}")
    print(json.dumps(report["summary"], indent=2))
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()