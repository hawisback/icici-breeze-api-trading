"""AI-directed execution tests; no broker connection or LIVE order is made."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from libs.broker_models.adapter import BrokerOrderResponse
from libs.contracts.models import TradingMode, utc_now
from services.ai_context.execution import AIExecutionService
from services.ai_context.trading import AITradeError
from services.broker_gateway.paper_adapter import PaperBrokerAdapter

CONTRACT = "INST-NIFTY-2026-10-13-25000-CE"
SYMBOL = "NIFTY26OCT25000CE"


class Chain:
    async def get_contract_quote(self, instrument_id: str):
        assert instrument_id == CONTRACT
        return {
            "instrument_id": instrument_id, "source": "KITE",
            "symbol": SYMBOL, "bid": 99.0, "ask": 100.0,
            "lot_size": 65, "market_timestamp": utc_now().isoformat(),
        }


class Gateway:
    def __init__(self):
        self.paper = PaperBrokerAdapter()
        self.live = PaperBrokerAdapter()
        self.kite_adapter = SimpleNamespace(is_active=True)
        self.placed = []

    def _adapter(self, mode):
        return self.live if mode == TradingMode.LIVE else self.paper

    async def place_order(self, req, mode=TradingMode.PAPER):
        self.placed.append((req, mode))
        return await self._adapter(mode).place_order(req)

    async def get_positions(self, mode=TradingMode.PAPER):
        return await self._adapter(mode).get_positions()

    async def get_order_status(self, order_id, mode=TradingMode.PAPER):
        return await self._adapter(mode).get_order_status(order_id)

    async def find_order_by_client_id(self, client_id, mode=TradingMode.PAPER):
        return await self._adapter(mode).find_order_by_client_id(client_id)

    async def modify_order(self, order_id, quantity=None, price=None,
                           mode=TradingMode.PAPER, trigger_price=None):
        return await self._adapter(mode).modify_order(
            order_id, quantity, price, trigger_price,
        )

    async def cancel_order(self, order_id, mode=TradingMode.PAPER):
        return await self._adapter(mode).cancel_order(order_id)


class Sessions:
    def __init__(self):
        self.connected = True

    async def get_session_status(self, broker):
        assert broker == "kite"
        return {"connected": self.connected}


async def make_service(tmp_path):
    settings = SimpleNamespace(
        data_root=tmp_path, ai_execution_enabled=True,
        ai_execution_live_switch_enabled=True, live_trading_enabled=True,
        broker_backend=SimpleNamespace(value="kite"),
        market_data_backend=SimpleNamespace(value="hybrid"),
        ai_trade_max_quantity=65, ai_trade_max_premium_notional=15000,
        live_max_order_notional=50000,
        ai_execution_live_max_daily_entries=3,
    )
    gateway, sessions = Gateway(), Sessions()
    service = AIExecutionService(
        settings=settings, gateway=gateway, chain=Chain(), sessions=sessions,
        live_gate=SimpleNamespace(),
    )
    await service.initialize()
    return service, gateway, sessions


async def order_entry(svc, request_id="AI-entry-001"):
    return await svc.entry(
        request_id=request_id, instrument_id=CONTRACT, quantity=65,
        limit_price=100.0,
    )


@pytest.mark.asyncio
async def test_ai_controls_paper_entry_stop_modify_cancel_and_exit(tmp_path):
    svc, gateway, _ = await make_service(tmp_path)
    assert (await svc.status())["mode"] == "PAPER"
    entry = await order_entry(svc)
    assert entry["status"] == "FILLED"
    assert entry["mode"] == "PAPER"
    assert gateway.placed[-1][1] == TradingMode.PAPER

    stop = await svc.place_stop(
        request_id="AI-stop-0001", entry_order_id=entry["order_id"],
        trigger_price=94.0, limit_price=92.0,
    )
    assert stop["status"] == "OPEN"
    req = gateway.placed[-1][0]
    assert req.action == "SELL" and req.order_type == "STOP_LIMIT"
    assert req.trigger_price == 94.0
    assert req.quantity == 65

    modified = await svc.modify_stop(
        stop_order_id=stop["order_id"], trigger_price=95.0,
        limit_price=93.0,
    )
    assert modified["trigger_price"] == 95.0
    assert gateway.paper._orders[stop["broker_order_id"]]["trigger_price"] == 95.0

    with pytest.raises(AITradeError, match="CANCEL_AND_VERIFY_STOP"):
        await svc.exit(request_id="AI-exit-0001",
                       entry_order_id=entry["order_id"], limit_price=99.0)

    cancelled = await svc.cancel_stop(stop["order_id"])
    assert cancelled["status"] == "CANCELLED"
    exited = await svc.exit(
        request_id="AI-exit-0001", entry_order_id=entry["order_id"],
        limit_price=99.0,
    )
    assert exited["status"] == "FILLED"
    assert not (await svc.positions())["positions"]


@pytest.mark.asyncio
async def test_mode_switch_live_is_explicit_and_blocked_by_positions(tmp_path):
    svc, gateway, _ = await make_service(tmp_path)
    assert (await svc.set_mode(TradingMode.LIVE))["mode"] == "LIVE"
    entry = await order_entry(svc)
    assert gateway.placed[-1][1] == TradingMode.LIVE
    assert len(gateway.placed[-1][0].user_remark) == 20
    assert gateway.placed[-1][0].user_remark.isalnum()
    with pytest.raises(AITradeError, match="LIVE_POSITIONS_BLOCK"):
        await svc.set_mode(TradingMode.PAPER)
    assert (await svc.status())["mode"] == "LIVE"
    assert (await svc._entry_for_close(entry["order_id"]))["filled_quantity"] == 65
    another = AIExecutionService(
        settings=svc.settings, gateway=gateway, chain=Chain(),
        sessions=svc.sessions, live_gate=SimpleNamespace(),
    )
    await another.initialize()
    assert (await another.status())["mode"] == "PAPER"  # restart is disarmed


@pytest.mark.asyncio
async def test_failed_live_authorization_does_not_switch_mode(tmp_path):
    svc, _, sessions = await make_service(tmp_path)
    svc.settings.ai_execution_live_switch_enabled = False
    with pytest.raises(AITradeError, match="AI_LIVE_SWITCH_NOT_AUTHORIZED"):
        await svc.set_mode(TradingMode.LIVE)
    svc.settings.ai_execution_live_switch_enabled = True
    sessions.connected = False
    with pytest.raises(AITradeError, match="KITE_SESSION_DISCONNECTED"):
        await svc.set_mode(TradingMode.LIVE)
    assert svc.mode == TradingMode.PAPER


@pytest.mark.asyncio
async def test_idempotent_entry_and_request_conflict(tmp_path):
    svc, gateway, _ = await make_service(tmp_path)
    first = await order_entry(svc)
    second = await order_entry(svc)
    assert first["order_id"] == second["order_id"]
    assert len(gateway.placed) == 1
    with pytest.raises(AITradeError, match="REQUEST_ID_CONFLICT"):
        await svc.entry(
            request_id="AI-entry-001", instrument_id=CONTRACT,
            quantity=65, limit_price=100.05,
        )


@pytest.mark.asyncio
async def test_live_stop_is_rejected_without_filled_position(tmp_path):
    svc, _, _ = await make_service(tmp_path)
    await svc.set_mode(TradingMode.LIVE)
    entry = await order_entry(svc)
    await svc.exit(
        request_id="AI-exit-0002", entry_order_id=entry["order_id"],
        limit_price=99.0,
    )
    with pytest.raises(AITradeError, match="NO_VERIFIED_POSITION_TO_PROTECT"):
        await svc.place_stop(
            request_id="AI-stop-0002", entry_order_id=entry["order_id"],
            trigger_price=94.0, limit_price=92.0,
        )


@pytest.mark.asyncio
async def test_live_daily_count_cap_cannot_be_skipped(tmp_path):
    svc, _, _ = await make_service(tmp_path)
    svc.settings.ai_execution_live_max_daily_entries = 1
    await svc.set_mode(TradingMode.LIVE)
    first = await order_entry(svc)
    await svc.exit(
        request_id="AI-exit-0003", entry_order_id=first["order_id"],
        limit_price=99.0,
    )
    with pytest.raises(AITradeError, match="LIVE_DAILY_ENTRY_CAP_REACHED"):
        await order_entry(svc, request_id="AI-entry-002")


@pytest.mark.asyncio
async def test_restart_requires_rearm_but_live_stop_remains_manageable(tmp_path):
    svc, gateway, sessions = await make_service(tmp_path)
    await svc.set_mode(TradingMode.LIVE)
    entry = await order_entry(svc)
    stop = await svc.place_stop(
        request_id="AI-stop-restart-1", entry_order_id=entry["order_id"],
        trigger_price=94.0, limit_price=92.0,
    )
    resumed = AIExecutionService(
        settings=svc.settings, gateway=gateway, chain=Chain(),
        sessions=sessions, live_gate=SimpleNamespace(),
    )
    await resumed.initialize()
    assert resumed.mode == TradingMode.PAPER
    assert (await resumed.set_mode(TradingMode.LIVE))["mode"] == "LIVE"
    adjusted = await resumed.modify_stop(
        stop_order_id=stop["order_id"], trigger_price=95.0,
        limit_price=93.0,
    )
    assert adjusted["trigger_price"] == 95.0


@pytest.mark.asyncio
async def test_cancel_pending_entry_does_not_submit_duplicate(tmp_path):
    svc, gateway, _ = await make_service(tmp_path)
    await svc.set_mode(TradingMode.LIVE)
    original = gateway.place_order

    async def leave_entry_pending(req, mode=TradingMode.PAPER):
        if req.action.lower() == "buy":
            fake_id = "PENDING-ENTRY"
            gateway.live._orders[fake_id] = {
                "broker_order_id": fake_id, "client_order_id": req.client_order_id,
                "stock_code": req.stock_code, "action": "buy",
                "quantity": req.quantity, "price": req.price, "status": "OPEN",
                "filled_quantity": 0, "average_price": 0.0,
            }
            return BrokerOrderResponse(
                success=True, broker_order_id=fake_id,
                client_order_id=req.client_order_id, status="PLACED",
            )
        return await original(req, mode=mode)

    gateway.place_order = leave_entry_pending
    entry = await order_entry(svc)
    assert entry["status"] == "OPEN"
    with pytest.raises(AITradeError, match="ACTIVE_ENTRY_OR_EXIT_EXISTS"):
        await order_entry(svc, request_id="AI-entry-unique-2")
    cancelled = await svc.cancel_pending_order(entry["order_id"])
    assert cancelled["status"] == "CANCELLED"
    assert (await svc.get_order(entry["order_id"]))["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_execution_http_writes_require_local_peer_and_key(monkeypatch):
    from fastapi import FastAPI
    from pydantic import SecretStr
    import httpx
    import services.api_gateway.ai_routes as routes
    import services.api_gateway.ai_local_access as access

    class FakeExecution:
        async def status(self):
            return {"mode": "PAPER", "execution_enabled": True}
        async def set_mode(self, mode):
            return {"mode": mode.value}

    settings = SimpleNamespace(
        ai_trust_local_docker_gateway=False,
        ai_execution_enabled=True, ai_execution_api_key=SecretStr("a" * 40),
    )
    instance = SimpleNamespace(settings=settings, ai_execution_svc=FakeExecution())
    monkeypatch.setattr(routes, "get_services", lambda: instance)
    monkeypatch.setattr(access, "get_services", lambda: instance)
    app = FastAPI()
    app.include_router(routes.router)
    local = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=local, base_url="http://localhost:8000") as client:
        assert (await client.get("/api/v1/ai/execution/status")).status_code == 200
        path = "/api/v1/ai/execution/mode"
        assert (await client.post(path, json={"mode": "LIVE"})).status_code == 401
        assert (await client.post(path, json={"mode": "LIVE"},
                                  headers={"X-AI-Execution-Key": "bad"})).status_code == 401
        result = await client.post(
            path, json={"mode": "LIVE"},
            headers={"X-AI-Execution-Key": "a" * 40},
        )
        assert result.status_code == 200 and result.json()["mode"] == "LIVE"
    remote = httpx.ASGITransport(app=app, client=("198.51.100.10", 5000))
    async with httpx.AsyncClient(transport=remote, base_url="http://localhost:8000") as client:
        result = await client.post(
            "/api/v1/ai/execution/mode", json={"mode": "LIVE"},
            headers={"X-AI-Execution-Key": "a" * 40, "X-Forwarded-For": "127.0.0.1"},
        )
        assert result.status_code == 403
