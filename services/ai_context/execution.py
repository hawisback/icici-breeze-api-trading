"""AI-controlled broker execution: PAPER or Kite LIVE, no strategy/OMS workers.

The AI decides orders and protective-stop adjustments. This small service owns
mode selection, idempotency, a durable submission journal and safety checks.
LIVE is always disarmed on process restart; pending orders are never retried
blindly after uncertain broker responses.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import aiosqlite

from libs.broker_models.adapter import BrokerOrderRequest
from libs.contracts.models import TradingMode, utc_now
from libs.market_time import IST
from services.ai_context.trading import AITradeError
from services.broker_gateway.service import BrokerGatewayService

_ACTIVE = {"RESERVED", "UNKNOWN", "OPEN", "PLACED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "TRIGGER_PENDING", "MODIFY_PENDING", "CANCEL_PENDING"}
_FINISHED = {"FILLED", "COMPLETE", "CANCELLED", "REJECTED", "EXPIRED"}
_CONTRACT = re.compile(r"INST-NIFTY-(\d{4}-\d{2}-\d{2})-(\d+)-(CE|PE)$")


class AIExecutionService:
    """A single-process, single-position execution API with SQLite evidence."""

    def __init__(self, *, settings: Any, gateway: BrokerGatewayService,
                 chain: Any, sessions: Any, live_gate: Any) -> None:
        self.settings = settings
        self.gateway = gateway
        self.chain = chain
        self.sessions = sessions
        self.live_gate = live_gate
        self.mode = TradingMode.PAPER  # deliberately NEVER restore LIVE on restart
        self.path = Path(settings.data_root) / "ai_execution.db"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS execution_orders (
                order_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                client_order_id TEXT UNIQUE NOT NULL, mode TEXT NOT NULL,
                kind TEXT NOT NULL, parent_order_id TEXT,
                instrument_id TEXT NOT NULL, symbol TEXT NOT NULL,
                quantity INTEGER NOT NULL, price REAL NOT NULL,
                trigger_price REAL, status TEXT NOT NULL,
                broker_order_id TEXT, filled_quantity INTEGER NOT NULL DEFAULT 0,
                average_price REAL NOT NULL DEFAULT 0,
                message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                request_json TEXT NOT NULL
            )""")
            await db.commit()

    def _enabled(self) -> None:
        if not self.settings.ai_execution_enabled:
            raise AITradeError("AI_EXECUTION_DISABLED", 403)
        if str(self.settings.broker_backend.value) != "kite":
            raise AITradeError("AI_EXECUTION_REQUIRES_KITE_BROKER", 409)

    async def status(self) -> dict[str, Any]:
        server_enabled = bool(self.settings.live_trading_enabled)
        agent_arming = bool(self.settings.ai_execution_live_switch_enabled)
        return {
            "mode": self.mode.value,
            "execution_enabled": self.settings.ai_execution_enabled,
            "live_capability_enabled": server_enabled,
            "live_authorized": server_enabled and agent_arming,
            "live_authorization_reason": (
                "AUTHORIZED_AI_LOCAL_EXECUTION_KEY" if server_enabled and agent_arming
                else "OPERATOR_MUST_ENABLE_LIVE_CAPABILITY_AND_AI_LIVE_SWITCH"
            ),
            "execution_broker": "kite",
            "market_data_backend": self.settings.market_data_backend.value,
            "paper_simulation": "IMMEDIATE_LIMIT_FILL_RESTING_STOP_NO_AUTOMATIC_TRIGGER",
            "mode_resets_to_paper_on_restart": True,
        }

    async def _all(self, limit: int = 200) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM execution_orders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def _one(self, order_id: str) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM execution_orders WHERE order_id=?", (order_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            raise AITradeError("EXECUTION_ORDER_NOT_FOUND", 404)
        return dict(row)

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k != "request_json"}

    async def _save(self, row: dict[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE execution_orders SET status=?,broker_order_id=?,
                   filled_quantity=?,average_price=?,price=?,trigger_price=?,
                   message=?,updated_at=? WHERE order_id=?""",
                (row["status"], row["broker_order_id"], row["filled_quantity"],
                 row["average_price"], row["price"], row["trigger_price"],
                 row["message"], utc_now().isoformat(), row["order_id"]),
            )
            await db.commit()

    async def _refresh(self, row: dict[str, Any]) -> dict[str, Any]:
        mode = TradingMode(row["mode"])
        try:
            if row["broker_order_id"]:
                resp = await self.gateway.get_order_status(
                    row["broker_order_id"], mode=mode
                )
            elif row["status"] in {"UNKNOWN", "RESERVED"}:
                resp = await self.gateway.find_order_by_client_id(
                    row["client_order_id"], mode=mode
                )
            else:
                resp = None
        except Exception:
            resp = None
        if resp:
            new_status = str(resp.status or "UNKNOWN").upper().replace(" ", "_")
            if new_status in {"COMPLETE", "COMPLETED"}:
                new_status = "FILLED"
            # Never regress a terminal status to transient due to stale broker data.
            if row["status"] not in _FINISHED:
                row["status"] = new_status
            row["broker_order_id"] = resp.broker_order_id or row["broker_order_id"]
            row["filled_quantity"] = max(
                int(row["filled_quantity"]), int(resp.filled_quantity or 0)
            )
            if resp.average_price:
                row["average_price"] = float(resp.average_price)
            row["message"] = resp.message
            await self._save(row)
        return row

    async def get_order(self, order_id: str) -> dict[str, Any]:
        self._enabled()
        return self._public(await self._refresh(await self._one(order_id)))

    async def list_orders(self) -> list[dict[str, Any]]:
        self._enabled()
        async with self._lock:
            return [self._public(await self._refresh(row)) for row in await self._all()]

    async def positions(self) -> dict[str, Any]:
        self._enabled()
        if self.mode == TradingMode.LIVE:
            await self._check_session()
        rows = await self.gateway.get_positions(mode=self.mode)
        return {"mode": self.mode.value, "positions": [p.model_dump() for p in rows]}

    async def _check_session(self) -> None:
        session = await self.sessions.get_session_status("kite")
        if not session.get("connected") or not self.gateway.kite_adapter.is_active:
            raise AITradeError("KITE_SESSION_DISCONNECTED", 409)

    async def _validate_live_entry(self) -> None:
        if not self.settings.live_trading_enabled:
            raise AITradeError("LIVE_CAPABILITY_DISABLED", 403)
        # The dedicated local AI execution API uses independent, explicit
        # server-owned LIVE permission rather than the legacy strategy ARM UX.
        # API key + loopback peer are checked at the HTTP boundary.
        if not self.settings.ai_execution_live_switch_enabled:
            raise AITradeError("AI_LIVE_SWITCH_NOT_AUTHORIZED", 403)
        await self._check_session()

    async def _flat(self) -> None:
        """No mode switching while *either* broker environment has exposure."""
        rows = await self._all(500)
        if any(r["mode"] == TradingMode.LIVE.value for r in rows):
            if not self.gateway.kite_adapter.is_active:
                raise AITradeError("LIVE_RECONCILIATION_REQUIRES_KITE_SESSION", 409)
            await self._check_session()
        for row in rows:
            row = await self._refresh(row)
            if row["status"] in _ACTIVE:
                raise AITradeError("UNRESOLVED_ORDER_BLOCKS_MODE_SWITCH", 409)
        paper_positions = await self.gateway.get_positions(mode=TradingMode.PAPER)
        if any(int(p.quantity) != 0 for p in paper_positions):
            raise AITradeError("PAPER_POSITIONS_BLOCK_MODE_SWITCH", 409)
        if self.gateway.kite_adapter.is_active:
            live_positions = await self.gateway.get_positions(mode=TradingMode.LIVE)
            if any(int(p.quantity) != 0 for p in live_positions):
                raise AITradeError("LIVE_POSITIONS_BLOCK_MODE_SWITCH", 409)

    async def set_mode(self, mode: TradingMode) -> dict[str, Any]:
        self._enabled()
        if mode not in {TradingMode.PAPER, TradingMode.LIVE}:
            raise AITradeError("INVALID_EXECUTION_MODE", 422)
        async with self._lock:
            if mode != self.mode:
                if mode == TradingMode.LIVE:
                    await self._validate_live_entry()
                    # A restart always resets authority to PAPER, but existing
                    # broker-held LIVE positions/stops must remain manageable.
                    # Explicitly rearm LIVE only after verifying PAPER is flat,
                    # Kite is connected, and any LIVE exposure is journal-owned.
                    paper_orders = [await self._refresh(r) for r in await self._all(500)
                                    if r["mode"] == TradingMode.PAPER.value]
                    if any(r["status"] in _ACTIVE for r in paper_orders):
                        raise AITradeError("PAPER_PENDING_ORDER_BLOCKS_LIVE", 409)
                    paper_positions = await self.gateway.get_positions(mode=TradingMode.PAPER)
                    if any(int(p.quantity) != 0 for p in paper_positions):
                        raise AITradeError("PAPER_POSITIONS_BLOCK_LIVE", 409)
                    live_positions = await self.gateway.get_positions(mode=TradingMode.LIVE)
                    if any(int(p.quantity) != 0 for p in live_positions):
                        live_entries = [r for r in await self._all(500)
                                        if r["kind"] == "ENTRY" and r["mode"] == "LIVE"
                                        and int(r["filled_quantity"]) > 0]
                        symbols = {r["symbol"] for r in live_entries}
                        if any(p.stock_code not in symbols for p in live_positions
                               if int(p.quantity) != 0):
                            raise AITradeError("UNTRACKED_LIVE_POSITION_REQUIRES_OPERATOR", 409)
                else:
                    await self._flat()
                self.mode = mode
        return await self.status()

    @staticmethod
    def _valid_price(price: float) -> bool:
        return price >= 0.05 and abs(round(price * 20) - price * 20) < 1e-5

    async def _quote(self, instrument_id: str) -> dict[str, Any]:
        if not _CONTRACT.fullmatch(instrument_id):
            raise AITradeError("ONLY_LISTED_NIFTY_OPTIONS", 422)
        leg = await self.chain.get_contract_quote(instrument_id)
        if not leg or str(leg.get("source") or "").upper() != "KITE":
            raise AITradeError("KITE_CONTRACT_QUOTE_UNAVAILABLE", 409)
        stamp = leg.get("market_timestamp")
        try:
            dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if dt.utcoffset() is None:
                raise ValueError("missing tz")
            age = (utc_now() - dt).total_seconds()
        except (ValueError, TypeError):
            raise AITradeError("OPTION_QUOTE_TIMESTAMP_INVALID", 409) from None
        if not -1 <= age <= 15:
            raise AITradeError("OPTION_QUOTE_STALE", 409)
        bid, ask = float(leg.get("bid") or 0), float(leg.get("ask") or 0)
        if bid <= 0 or ask < bid or (ask - bid) / ((ask + bid) / 2) > .03:
            raise AITradeError("OPTION_SPREAD_INVALID", 409)
        if not leg.get("symbol") or int(leg.get("lot_size") or 0) <= 0:
            raise AITradeError("KITE_CONTRACT_NOT_TRADABLE", 409)
        return leg

    async def _insert_and_send(
        self, *, request_id: str, kind: str, instrument_id: str,
        symbol: str, quantity: int, price: float,
        trigger_price: Optional[float], parent_order_id: Optional[str],
        side: str, order_type: str,
    ) -> dict[str, Any]:
        # Persist submission intent *before* sending to Kite. Ambiguous errors
        # remain UNKNOWN until resolved by broker order tag; never blind retry.
        payload = json.dumps(dict(kind=kind, instrument_id=instrument_id,
                                  quantity=quantity, price=price,
                                  trigger_price=trigger_price,
                                  parent_order_id=parent_order_id),
                             sort_keys=True)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM execution_orders WHERE request_id=?", (request_id,)
            ) as cursor:
                existing = await cursor.fetchone()
            if existing:
                row = dict(existing)
                if row["request_json"] != payload:
                    raise AITradeError("REQUEST_ID_CONFLICT", 409)
                return self._public(await self._refresh(row))
            order_id = uuid4().hex
            client_id = "AI" + uuid4().hex[:18]  # Kite tag: alphanumeric <=20
            now = utc_now().isoformat()
            await db.execute(
                """INSERT INTO execution_orders
                   (order_id,request_id,client_order_id,mode,kind,parent_order_id,
                   instrument_id,symbol,quantity,price,trigger_price,status,
                   created_at,updated_at,request_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'UNKNOWN',?,?,?)""",
                (order_id, request_id, client_id, self.mode.value, kind,
                 parent_order_id, instrument_id, symbol, quantity, price,
                 trigger_price, now, now, payload),
            )
            await db.commit()

        row = await self._one(order_id)
        try:
            resp = await self.gateway.place_order(
                BrokerOrderRequest(
                    client_order_id=client_id, stock_code=symbol, exchange_code="NFO",
                    action=side, order_type=order_type, quantity=quantity,
                    price=price, trigger_price=trigger_price, user_remark=client_id,
                ), mode=self.mode
            )
            row["status"] = str(resp.status or "UNKNOWN").upper()
            row["broker_order_id"] = resp.broker_order_id
            row["filled_quantity"] = int(resp.filled_quantity or 0)
            row["average_price"] = float(resp.average_price or 0)
            row["message"] = resp.message
            # Kite PLACED is order acceptance, never assume a fill.
            if row["status"] == "PLACED":
                row["status"] = "OPEN"
            if row["status"] == "COMPLETE":
                row["status"] = "FILLED"
        except Exception as exc:
            row["status"] = "UNKNOWN"
            row["message"] = "SUBMISSION_OUTCOME_UNKNOWN:" + type(exc).__name__
        await self._save(row)
        return self._public(row)

    async def entry(self, *, request_id: str, instrument_id: str,
                    quantity: int, limit_price: float) -> dict[str, Any]:
        self._enabled()
        async with self._lock:
            if self.mode == TradingMode.LIVE:
                await self._validate_live_entry()
            # Idempotent replay comes before mutable position / quote checks.
            existing = next((r for r in await self._all(500)
                             if r["request_id"] == request_id), None)
            if existing:
                if existing["kind"] != "ENTRY" or existing["instrument_id"] != instrument_id or existing["quantity"] != quantity or existing["price"] != limit_price:
                    raise AITradeError("REQUEST_ID_CONFLICT", 409)
                return self._public(await self._refresh(existing))
            if not self._valid_price(limit_price):
                raise AITradeError("INVALID_LIMIT_TICK", 422)
            if quantity <= 0 or quantity > self.settings.ai_trade_max_quantity:
                raise AITradeError("QUANTITY_LIMIT_EXCEEDED", 422)
            leg = await self._quote(instrument_id)
            lot = int(leg["lot_size"])
            if quantity % lot:
                raise AITradeError("INVALID_LOT_QUANTITY", 422)
            if quantity * limit_price > min(
                self.settings.ai_trade_max_premium_notional,
                self.settings.live_max_order_notional,
            ):
                raise AITradeError("ORDER_NOTIONAL_LIMIT_EXCEEDED", 409)
            ask = float(leg["ask"])
            if not (ask * .97 <= limit_price <= ask * 1.03):
                raise AITradeError("LIMIT_PRICE_OUTSIDE_QUOTE_BAND", 409)
            prior = [await self._refresh(x) for x in await self._all(500)]
            if any(r["kind"] in {"ENTRY", "EXIT"} and r["status"] in _ACTIVE
                   and r["mode"] == self.mode.value for r in prior):
                raise AITradeError("ACTIVE_ENTRY_OR_EXIT_EXISTS", 409)
            if self.mode == TradingMode.LIVE:
                count = sum(
                    1 for r in prior
                    if r["kind"] == "ENTRY" and r["mode"] == "LIVE"
                    and datetime.fromisoformat(r["created_at"]).astimezone(IST).date()
                        == utc_now().astimezone(IST).date()
                )
                if count >= self.settings.ai_execution_live_max_daily_entries:
                    raise AITradeError("LIVE_DAILY_ENTRY_CAP_REACHED", 409)
            positions = await self.gateway.get_positions(mode=self.mode)
            if any(int(p.quantity) != 0 for p in positions):
                raise AITradeError("EXISTING_POSITION_BLOCKS_NEW_ENTRY", 409)
            return await self._insert_and_send(
                request_id=request_id, kind="ENTRY", instrument_id=instrument_id,
                symbol=str(leg["symbol"]), quantity=quantity, price=limit_price,
                trigger_price=None, parent_order_id=None, side="BUY",
                order_type="LIMIT",
            )

    async def _held(self, entry: dict[str, Any]) -> int:
        if self.mode == TradingMode.LIVE:
            await self._check_session()
        positions = await self.gateway.get_positions(mode=self.mode)
        return sum(max(0, int(p.quantity)) for p in positions
                   if p.stock_code == entry["symbol"] and p.exchange_code == "NFO")

    async def _entry_for_close(self, entry_id: str) -> dict[str, Any]:
        entry = await self._refresh(await self._one(entry_id))
        if entry["kind"] != "ENTRY" or entry["mode"] != self.mode.value:
            raise AITradeError("ENTRY_NOT_IN_ACTIVE_MODE", 409)
        if int(entry["filled_quantity"]) <= 0:
            raise AITradeError("ENTRY_NOT_FILLED", 409)
        return entry

    async def _children(self, entry_id: str) -> list[dict[str, Any]]:
        return [await self._refresh(row) for row in await self._all(500)
                if row["parent_order_id"] == entry_id]

    async def place_stop(self, *, request_id: str, entry_order_id: str,
                         trigger_price: float, limit_price: float) -> dict[str, Any]:
        self._enabled()
        async with self._lock:
            existing = next((r for r in await self._all(500)
                             if r["request_id"] == request_id), None)
            if existing:
                if existing["kind"] != "STOP" or existing["parent_order_id"] != entry_order_id or existing["price"] != limit_price or existing["trigger_price"] != trigger_price:
                    raise AITradeError("REQUEST_ID_CONFLICT", 409)
                return self._public(await self._refresh(existing))
            entry = await self._entry_for_close(entry_order_id)
            if not self._valid_price(limit_price) or not self._valid_price(trigger_price) or limit_price >= trigger_price:
                raise AITradeError("INVALID_SELL_STOP_LIMIT_GEOMETRY", 422)
            children = await self._children(entry_order_id)
            if any(r["status"] in _ACTIVE for r in children):
                raise AITradeError("ACTIVE_EXIT_OR_STOP_EXISTS", 409)
            held = await self._held(entry)
            qty = min(held, int(entry["filled_quantity"]))
            if qty <= 0:
                raise AITradeError("NO_VERIFIED_POSITION_TO_PROTECT", 409)
            return await self._insert_and_send(
                request_id=request_id, kind="STOP", instrument_id=entry["instrument_id"],
                symbol=entry["symbol"], quantity=qty, price=limit_price,
                trigger_price=trigger_price, parent_order_id=entry_order_id,
                side="SELL", order_type="STOP_LIMIT",
            )

    async def modify_stop(self, *, stop_order_id: str, trigger_price: float,
                          limit_price: float) -> dict[str, Any]:
        self._enabled()
        async with self._lock:
            if not self._valid_price(limit_price) or not self._valid_price(trigger_price) or limit_price >= trigger_price:
                raise AITradeError("INVALID_SELL_STOP_LIMIT_GEOMETRY", 422)
            row = await self._refresh(await self._one(stop_order_id))
            if row["kind"] != "STOP" or row["mode"] != self.mode.value:
                raise AITradeError("NOT_AN_ACTIVE_STOP", 409)
            if row["status"] not in {"OPEN", "TRIGGER_PENDING", "ACKNOWLEDGED", "PARTIALLY_FILLED"} or not row["broker_order_id"]:
                raise AITradeError("STOP_NOT_MODIFIABLE", 409)
            entry = await self._entry_for_close(row["parent_order_id"])
            if await self._held(entry) <= 0:
                raise AITradeError("NO_VERIFIED_POSITION_TO_PROTECT", 409)
            try:
                resp = await self.gateway.modify_order(
                    row["broker_order_id"], price=limit_price,
                    trigger_price=trigger_price, mode=self.mode
                )
            except Exception:
                row["status"] = "UNKNOWN"
                row["message"] = "STOP_MODIFICATION_OUTCOME_UNKNOWN"
                await self._save(row)
                raise AITradeError("STOP_MODIFICATION_OUTCOME_UNKNOWN", 409) from None
            if not resp.success:
                # A timeout may be UNKNOWN, so reject writes until reconciled.
                if str(resp.status or "").upper() == "UNKNOWN":
                    row["status"] = "UNKNOWN"
                    row["message"] = "STOP_MODIFICATION_OUTCOME_UNKNOWN"
                    await self._save(row)
                    raise AITradeError("STOP_MODIFICATION_OUTCOME_UNKNOWN", 409)
                raise AITradeError("STOP_MODIFICATION_REJECTED", 409)
            row["price"], row["trigger_price"] = limit_price, trigger_price
            row["message"] = resp.message
            await self._save(row)
            return self._public(await self._refresh(row))

    async def cancel_stop(self, stop_order_id: str) -> dict[str, Any]:
        self._enabled()
        async with self._lock:
            row = await self._refresh(await self._one(stop_order_id))
            if row["kind"] != "STOP" or row["mode"] != self.mode.value:
                raise AITradeError("NOT_AN_ACTIVE_STOP", 409)
            if row["status"] in _FINISHED:
                return self._public(row)
            if not row["broker_order_id"]:
                raise AITradeError("STOP_ORDER_OUTCOME_UNKNOWN", 409)
            try:
                resp = await self.gateway.cancel_order(
                    row["broker_order_id"], mode=self.mode
                )
            except Exception:
                row["status"] = "UNKNOWN"
                row["message"] = "STOP_CANCEL_OUTCOME_UNKNOWN"
                await self._save(row)
                raise AITradeError("STOP_CANCEL_OUTCOME_UNKNOWN", 409) from None
            if not resp.success:
                if str(resp.status or "").upper() == "UNKNOWN":
                    row["status"] = "UNKNOWN"
                    row["message"] = "STOP_CANCEL_OUTCOME_UNKNOWN"
                    await self._save(row)
                    raise AITradeError("STOP_CANCEL_OUTCOME_UNKNOWN", 409)
                raise AITradeError("STOP_CANCEL_REJECTED", 409)
            # Check the broker's order book; ACK is not proof of cancellation.
            updated = await self._refresh(row)
            return self._public(updated)

    async def exit(self, *, request_id: str, entry_order_id: str,
                   limit_price: float) -> dict[str, Any]:
        self._enabled()
        async with self._lock:
            existing = next((r for r in await self._all(500)
                             if r["request_id"] == request_id), None)
            if existing:
                if existing["kind"] != "EXIT" or existing["parent_order_id"] != entry_order_id or existing["price"] != limit_price:
                    raise AITradeError("REQUEST_ID_CONFLICT", 409)
                return self._public(await self._refresh(existing))
            if not self._valid_price(limit_price):
                raise AITradeError("INVALID_LIMIT_TICK", 422)
            entry = await self._entry_for_close(entry_order_id)
            children = await self._children(entry_order_id)
            for child in children:
                if child["status"] in _ACTIVE and child["kind"] == "STOP":
                    # The AI must cancel its stop first and verify cancellation.
                    raise AITradeError("CANCEL_AND_VERIFY_STOP_BEFORE_EXIT", 409)
                if child["status"] in _ACTIVE and child["kind"] == "EXIT":
                    raise AITradeError("EXIT_ALREADY_PENDING", 409)
            qty = min(await self._held(entry), int(entry["filled_quantity"]))
            if qty <= 0:
                raise AITradeError("NO_VERIFIED_POSITION_TO_EXIT", 409)
            leg = await self._quote(entry["instrument_id"])
            if not (.97 * float(leg["bid"]) <= limit_price <= 1.03 * float(leg["bid"])):
                raise AITradeError("EXIT_LIMIT_OUTSIDE_QUOTE_BAND", 409)
            return await self._insert_and_send(
                request_id=request_id, kind="EXIT", instrument_id=entry["instrument_id"],
                symbol=entry["symbol"], quantity=qty, price=limit_price,
                trigger_price=None, parent_order_id=entry_order_id,
                side="SELL", order_type="LIMIT",
            )


    async def cancel_pending_order(self, order_id: str) -> dict[str, Any]:
        """Allow AI to cancel an unfilled/part-filled ENTRY or EXIT.

        A cancellation acknowledgment is not a confirmed broker cancellation.
        GET the order again before placing another order for the residual size.
        """
        self._enabled()
        async with self._lock:
            row = await self._refresh(await self._one(order_id))
            if row["mode"] != self.mode.value or row["kind"] not in {"ENTRY", "EXIT"}:
                raise AITradeError("NOT_AN_ACTIVE_ENTRY_OR_EXIT", 409)
            if row["status"] in _FINISHED:
                return self._public(row)
            if not row["broker_order_id"]:
                raise AITradeError("ORDER_OUTCOME_UNKNOWN", 409)
            try:
                resp = await self.gateway.cancel_order(
                    row["broker_order_id"], mode=self.mode
                )
            except Exception:
                row["status"] = "UNKNOWN"
                row["message"] = "ORDER_CANCEL_OUTCOME_UNKNOWN"
                await self._save(row)
                raise AITradeError("ORDER_CANCEL_OUTCOME_UNKNOWN", 409) from None
            if not resp.success:
                if str(resp.status or "").upper() == "UNKNOWN":
                    row["status"] = "UNKNOWN"
                    row["message"] = "ORDER_CANCEL_OUTCOME_UNKNOWN"
                    await self._save(row)
                    raise AITradeError("ORDER_CANCEL_OUTCOME_UNKNOWN", 409)
                raise AITradeError("ORDER_CANCEL_REJECTED", 409)
            return self._public(await self._refresh(row))
