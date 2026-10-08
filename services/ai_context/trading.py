"""Independent, durable PAPER trade lifecycle for a mode-blind external AI.

No LIVE trade is routed by this adapter until broker-stop protection and
fill reconciliation have been integrated and validated. Enabling LIVE in
server settings deliberately fails closed, not over to PAPER.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from libs.contracts.models import TradingMode, generate_id, utc_now
from libs.market_time import IST

logger = logging.getLogger(__name__)
_REAL = {"KITE", "BREEZE", "LIVE"}


class AITradeError(Exception):
    """Public, non-sensitive error for a rejected trade intent."""

    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


class AITradeService:
    """PAPER fills from broker observations; deterministic background exits.

    Broker data is evidence for simulated prices, not a claim that paper
    orders execute at those prices in a real market.
    """

    def __init__(self, *, settings: Any, option_chain_service: Any,
                 ai_context_service_factory: Any) -> None:
        self.settings = settings
        self.chain = option_chain_service
        self.context_factory = ai_context_service_factory
        self.path = Path(settings.ai_trade_db_path)
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ai_paper_trades (
                    trade_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL UNIQUE,
                    instrument_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    expiry TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    entry_price REAL NOT NULL CHECK (entry_price > 0),
                    entry_bid REAL NOT NULL CHECK (entry_bid > 0),
                    initial_stop REAL NOT NULL,
                    current_stop REAL NOT NULL,
                    target_price REAL NOT NULL,
                    peak_bid REAL NOT NULL,
                    trailing_active INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
                    entry_time TEXT NOT NULL,
                    last_quote_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    data_status TEXT NOT NULL DEFAULT 'VALID',
                    exit_time TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    pnl REAL,
                    policy_json TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ai_one_open_trade
                ON ai_paper_trades(status) WHERE status='OPEN'
            """)
            await db.commit()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @staticmethod
    def _row(row: aiosqlite.Row) -> dict[str, Any]:
        record = dict(row)
        record["trailing_active"] = bool(record["trailing_active"])
        record.pop("policy_json", None)
        # Never expose the backend's execution mode to the external AI.
        return record

    async def get(self, trade_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM ai_paper_trades WHERE trade_id=?", (trade_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return self._row(row) if row else None

    async def list(self, limit: int = 25) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM ai_paper_trades ORDER BY entry_time DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    def operator_config(self) -> dict[str, Any]:
        s = self.settings
        return {
            "enabled": s.ai_trade_enabled,
            "effective_mode": s.ai_trade_mode.value,
            "live_api_routing_supported": False,
            "max_quantity": s.ai_trade_max_quantity,
            "max_premium_notional": s.ai_trade_max_premium_notional,
            "max_daily_entries": s.ai_trade_max_daily_entries,
            "initial_stop_pct": s.ai_trade_initial_stop_pct,
            "trail_activation_pct": s.ai_trade_trail_activation_pct,
            "trail_gap_pct": s.ai_trade_trail_gap_pct,
            "target_pct": s.ai_trade_target_pct,
            "max_hold_seconds": s.ai_trade_max_hold_seconds,
            "poll_seconds": s.ai_trade_poll_seconds,
        }

    async def _contract_quote(
        self, instrument_id: str, expiry: str | None = None,
    ) -> dict[str, Any]:
        """Get a real Kite contract, not synthetic option approximations."""
        chain = await self.chain.get_chain(
            underlying="NIFTY", expiry=expiry, provider="kite"
        )
        if str(chain.get("source") or "").upper() != "KITE":
            raise AITradeError("REAL_OPTION_CHAIN_UNAVAILABLE")
        for row in chain.get("strikes") or []:
            for right in ("call", "put"):
                leg = row.get(right) or {}
                if leg.get("instrument_id") != instrument_id:
                    continue
                timestamp = leg.get("market_timestamp")
                try:
                    observed = datetime.fromisoformat(
                        str(timestamp).replace("Z", "+00:00")
                    )
                    if observed.utcoffset() is None:
                        raise ValueError("missing timezone")
                except (ValueError, TypeError):
                    raise AITradeError("OPTION_EXCHANGE_TIMESTAMP_MISSING")
                age = (utc_now() - observed).total_seconds()
                if age < -1 or age > 15:
                    raise AITradeError("OPTION_QUOTE_STALE")
                bid, ask = float(leg.get("bid") or 0), float(leg.get("ask") or 0)
                if bid <= 0 or ask < bid or ask <= 0:
                    raise AITradeError("OPTION_SPREAD_INVALID")
                spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
                if spread_pct > 3.0:
                    raise AITradeError("OPTION_SPREAD_TOO_WIDE")
                return {
                    "instrument_id": instrument_id,
                    "symbol": str(leg.get("symbol") or ""),
                    "expiry": chain["expiry"],
                    "bid": bid, "ask": ask,
                    "market_timestamp": observed.isoformat(),
                    "lot_size": int(leg.get("lot_size") or 0),
                }
        raise AITradeError("CONTRACT_NOT_IN_LIVE_OPTION_CHAIN")

    async def submit(self, *, signal_id: str, instrument_id: str,
                     quantity: int) -> dict[str, Any]:
        if not self.settings.ai_trade_enabled:
            raise AITradeError("AI_TRADING_NOT_ENABLED", 403)
        # Never silently fall back to PAPER if the operator selected LIVE:
        # an unprotected LIVE route is not a safe "connecting the dots" patch.
        if self.settings.ai_trade_mode != TradingMode.PAPER:
            raise AITradeError("LIVE_PROTECTIVE_ORDER_INTEGRATION_NOT_ENABLED", 403)
        if quantity <= 0 or quantity > self.settings.ai_trade_max_quantity:
            raise AITradeError("QUANTITY_EXCEEDS_OPERATOR_LIMIT", 422)
        if not instrument_id.startswith("INST-NIFTY-"):
            raise AITradeError("ONLY_NIFTY_OPTION_CONTRACTS_ALLOWED", 422)
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM ai_paper_trades WHERE signal_id=?", (signal_id,)
                ) as cursor:
                    existing = await cursor.fetchone()
                if existing:
                    if existing["instrument_id"] != instrument_id or existing["quantity"] != quantity:
                        raise AITradeError("SIGNAL_ID_CONFLICT", 409)
                    return self._row(existing)
                async with db.execute(
                    "SELECT count(*) FROM ai_paper_trades WHERE status='OPEN'"
                ) as cursor:
                    if (await cursor.fetchone())[0] > 0:
                        raise AITradeError("ANOTHER_AI_TRADE_IS_OPEN")
                today = utc_now().astimezone(IST).date().isoformat()
                async with db.execute(
                    "SELECT count(*) FROM ai_paper_trades WHERE substr(entry_time,1,10)=?",
                    (today,),
                ) as cursor:
                    # Store entry_time in IST ISO format for local session limits.
                    if (await cursor.fetchone())[0] >= self.settings.ai_trade_max_daily_entries:
                        raise AITradeError("AI_DAILY_ENTRY_LIMIT_REACHED")

                snapshot = await self.context_factory().get_snapshot()
                if snapshot.get("entry_permitted") is not True:
                    raise AITradeError("ENTRY_CONTEXT_NOT_READY")
                quote = await self._contract_quote(instrument_id)
                lot_size = quote["lot_size"]
                if lot_size <= 0 or quantity % lot_size:
                    raise AITradeError("INVALID_LOT_QUANTITY", 422)
                entry = quote["ask"]
                if entry * quantity > self.settings.ai_trade_max_premium_notional:
                    raise AITradeError("AI_NOTIONAL_LIMIT_EXCEEDED")
                s = self.settings
                policy = {
                    "initial_stop_pct": s.ai_trade_initial_stop_pct,
                    "trail_activation_pct": s.ai_trade_trail_activation_pct,
                    "trail_gap_pct": s.ai_trade_trail_gap_pct,
                    "target_pct": s.ai_trade_target_pct,
                    "max_hold_seconds": s.ai_trade_max_hold_seconds,
                }
                trade_id = generate_id()
                now = utc_now().astimezone(IST).isoformat()
                stop = round(entry * (1 - policy["initial_stop_pct"] / 100), 2)
                target = round(entry * (1 + policy["target_pct"] / 100), 2)
                try:
                    await db.execute(
                        """INSERT INTO ai_paper_trades
                          (trade_id, signal_id, instrument_id, symbol, expiry,
                           quantity, entry_price, entry_bid, initial_stop,
                           current_stop, target_price, peak_bid, status, entry_time,
                           last_quote_at, policy_json)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (trade_id, signal_id, instrument_id, quote["symbol"],
                         quote["expiry"], quantity, entry, quote["bid"], stop,
                         stop, target, quote["bid"], "OPEN", now,
                         quote["market_timestamp"], json.dumps(policy)),
                    )
                    await db.commit()
                except aiosqlite.IntegrityError:
                    raise AITradeError("AI_TRADE_ALREADY_SUBMITTED")
        result = await self.get(trade_id)
        assert result is not None
        return result

    @staticmethod
    def transition(record: dict[str, Any], quote: dict[str, Any],
                   now: datetime) -> dict[str, Any]:
        """Pure, deterministic premium-based protection and exit decision."""
        if record["status"] != "OPEN":
            return record
        entry = float(record["entry_price"])
        bid = float(quote["bid"])
        policy = json.loads(record["policy_json"])
        peak = max(float(record["peak_bid"]), bid)
        current_stop = float(record["current_stop"])
        trailing = bool(record["trailing_active"])
        if peak >= entry * (1 + policy["trail_activation_pct"] / 100):
            trailing = True
        if trailing:
            current_stop = max(
                current_stop,
                entry,  # breakeven floor, fill remains subject to slippage
                round(peak * (1 - policy["trail_gap_pct"] / 100), 2),
            )
        entry_time = datetime.fromisoformat(record["entry_time"])
        held_seconds = (now - entry_time).total_seconds()
        reason = None
        if bid <= current_stop:
            reason = "TRAILING_STOP" if trailing else "INITIAL_STOP"
        elif bid >= float(record["target_price"]):
            reason = "PROFIT_TARGET"
        elif held_seconds >= policy["max_hold_seconds"]:
            reason = "MAX_HOLD_TIME"
        # Do not turn data fetch time into an exchange observation.
        result = dict(record)
        result.update(
            peak_bid=peak, current_stop=current_stop, trailing_active=int(trailing),
            last_quote_at=quote["market_timestamp"],
            last_checked_at=now.isoformat(), data_status="VALID",
        )
        if reason:
            result.update(
                status="CLOSED", exit_time=now.isoformat(),
                exit_price=bid, exit_reason=reason,
                pnl=round((bid-entry) * int(record["quantity"]), 2),
            )
        return result

    async def poll_once(self) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM ai_paper_trades WHERE status='OPEN'"
                ) as cursor:
                    rows = await cursor.fetchall()
                for row in rows:
                    record = dict(row)
                    try:
                        quote = await self._contract_quote(
                            record["instrument_id"], record["expiry"]
                        )
                    except Exception as exc:
                        # Never invent a bid/exit fill when market data is absent.
                        code = exc.code if isinstance(exc, AITradeError) else "QUOTE_FETCH_ERROR"
                        logger.warning("AI PAPER trade %s unprotected: %s", record["trade_id"], code)
                        await db.execute(
                            """UPDATE ai_paper_trades SET
                               data_status=?, last_checked_at=?
                               WHERE trade_id=? AND status='OPEN'""",
                            (code, utc_now().astimezone(IST).isoformat(), record["trade_id"]),
                        )
                        continue
                    now = utc_now().astimezone(IST)
                    revised = self.transition(record, quote, now)
                    await db.execute(
                        """UPDATE ai_paper_trades SET
                            peak_bid=?, current_stop=?, trailing_active=?,
                            last_quote_at=?, last_checked_at=?, data_status=?,
                            status=?, exit_time=?, exit_price=?, exit_reason=?, pnl=?
                           WHERE trade_id=? AND status='OPEN'""",
                        (
                            revised["peak_bid"], revised["current_stop"],
                            revised["trailing_active"], revised["last_quote_at"],
                            revised["last_checked_at"], revised["data_status"],
                            revised["status"], revised.get("exit_time"),
                            revised.get("exit_price"), revised.get("exit_reason"),
                            revised.get("pnl"), record["trade_id"],
                        ),
                    )
                await db.commit()

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("AI PAPER trade manager loop failed")
            await asyncio.sleep(self.settings.ai_trade_poll_seconds)
