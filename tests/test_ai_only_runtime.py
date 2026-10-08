"""AI-only local runtime does not start unrelated strategy/OMS/broker workers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.api_gateway.ai_routes as ai_routes
import services.api_gateway.service_container as container_module
from libs.config.settings import PlatformSettings
from services.api_gateway.service_container import initialize_services


@pytest.mark.asyncio
async def test_ai_only_mode_starts_stop_manager_but_not_legacy_pollers(tmp_path):
    settings = PlatformSettings(
        _env_file=None,
        data_root=tmp_path,
        ai_trade_db_path=tmp_path / "ai_trades.db",
        ai_only_mode=True,
        market_data_backend="kite",
        KITE_API_KEY="test-kite-key",
        KITE_API_SECRET="test-kite-secret",
    )
    services = await initialize_services(settings=settings)
    try:
        assert services.settings.ai_only_mode is True
        assert services.strategy_svc._loop_task is None
        assert services.market_svc._simulation_task is None
        assert services.oms_svc._outbox_worker_task is None
        assert services.risk_svc._outbox_worker_task is None
        assert services.exec_svc._reconciliation_task is None
        assert services.ai_trade_svc._task is not None
        assert not services.ai_trade_svc._task.done()
    finally:
        await services.ai_trade_svc.stop()
        await services.market_svc.stop_feed_loop()
        await services.exec_svc.stop()
        await services.risk_svc.stop()
        await services.oms_svc.stop_outbox_worker()
        await services.event_bus.stop()
        # Prevent leaking this AI-only test container into subsequent gateway
        # tests that intentionally initialize the default full platform.
        container_module._container = None


@pytest.mark.asyncio
async def test_ai_route_on_demand_index_refresh_is_coalesced(monkeypatch):
    calls: list[str] = []

    class FakeMarket:
        async def sync_quotes_from_broker(self):
            calls.append("kite")
            return True

    fake = SimpleNamespace(
        settings=SimpleNamespace(ai_only_mode=True),
        market_svc=FakeMarket(),
    )
    monkeypatch.setattr(ai_routes, "get_services", lambda: fake)
    monkeypatch.setattr(ai_routes, "_last_index_refresh", 0.0)
    await ai_routes._refresh_ai_only_index()
    await ai_routes._refresh_ai_only_index()
    assert calls == ["kite"]


def test_ai_only_deployment_is_kite_and_not_live():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text()
    assert "AI_ONLY_MODE=true" in compose
    assert "MARKET_DATA_BACKEND=kite" in compose
    for filename in (".env.example", ".env.local-live.example"):
        template = (root / filename).read_text()
        assert "AI_ONLY_MODE=true" in template
        assert "MARKET_DATA_BACKEND=kite" in template
        assert "AI_TRADE_MODE=PAPER" in template
