"""Persist first-observed Kite option OI for each IST trading session.

This is NOT yesterday's exchange closing OI. A missing/old quote never
establishes a baseline. First observation yields oi_change=None; later fresh
exchange observations compute the delta against that persisted observation.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
import asyncio
import logging

import aiosqlite

from libs.contracts.models import utc_now
from libs.market_time import IST

logger = logging.getLogger(__name__)


class KiteSessionOIBaselines:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = asyncio.Lock()

    async def enrich(self, chain: dict, *, now: datetime | None = None) -> None:
        now = now or utc_now()
        today = now.astimezone(IST).date()
        # Never initialize today's baseline from yesterday's or stale quotes.
        valid: list[tuple[dict, str, int, datetime]] = []
        for row in chain.get("strikes", []) or []:
            for side in ("call", "put"):
                item = row.get(side)
                if not item:
                    continue
                item["oi_change"] = None
                item["oi_change_basis"] = "FIRST_OBSERVED_SESSION"
                item["oi_baseline_at"] = None
                raw_time = item.get("market_timestamp")
                try:
                    observed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if observed.tzinfo is None:
                    continue
                local = observed.astimezone(IST)
                if (
                    local.date() != today
                    or not (time(9, 15) <= local.time() <= time(15, 30))
                    or not (0 <= (now - observed).total_seconds() <= 60)
                ):
                    continue
                instrument = str(item.get("instrument_id") or "")
                oi = item.get("open_interest")
                if not instrument or oi is None:
                    continue
                try:
                    oi_int = int(oi)
                except (ValueError, TypeError):
                    continue
                if oi_int < 0:
                    continue
                valid.append((item, instrument, oi_int, observed))

        if not valid:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("PRAGMA busy_timeout=2000")
                    await db.execute("""
                        CREATE TABLE IF NOT EXISTS kite_session_oi_baselines (
                            session_date TEXT NOT NULL,
                            instrument_id TEXT NOT NULL,
                            baseline_oi INTEGER NOT NULL CHECK (baseline_oi >= 0),
                            observed_at TEXT NOT NULL,
                            PRIMARY KEY (session_date, instrument_id)
                        )
                    """)
                    for item, instrument, oi, observed in valid:
                        await db.execute(
                            "INSERT OR IGNORE INTO kite_session_oi_baselines "
                            "(session_date, instrument_id, baseline_oi, observed_at) VALUES (?,?,?,?)",
                            (today.isoformat(), instrument, oi, observed.isoformat()),
                        )
                        cursor = await db.execute(
                            "SELECT baseline_oi, observed_at FROM kite_session_oi_baselines "
                            "WHERE session_date=? AND instrument_id=?",
                            (today.isoformat(), instrument),
                        )
                        baseline = await cursor.fetchone()
                        if not baseline:
                            continue
                        base_oi, base_time = int(baseline[0]), datetime.fromisoformat(baseline[1])
                        item["oi_baseline_at"] = base_time.isoformat()
                        # First sampled tick isn't an OI change. Observations
                        # older than that baseline cannot be compared either.
                        if observed > base_time:
                            item["oi_change"] = oi - base_oi
                    await db.commit()
            except (aiosqlite.Error, OSError, ValueError) as exc:
                logger.warning("Unable to persist Kite OI baseline: %s", type(exc).__name__)
                for item, _, _, _ in valid:
                    item["oi_change"] = None
                    item["oi_baseline_at"] = None
