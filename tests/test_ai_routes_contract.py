from services.api_gateway.ai_routes import router
from services.api_gateway.ai_local_access import require_local_ai_client


def test_ai_routes_are_locally_accessible_without_tokens_for_trades_and_broker_login():
    expected = {
        "/api/v1/ai/nifty/snapshot",
        "/api/v1/ai/nifty/candles",
        "/api/v1/ai/nifty/technicals",
        "/api/v1/ai/nifty/options",
        "/api/v1/ai/heavyweights",
        "/api/v1/ai/broker/sessions",
        "/api/v1/ai/broker/session/login-url",
        "/api/v1/ai/broker/session/activate",
        "/api/v1/ai/account/context",
        "/api/v1/ai/data-quality",
        "/api/v1/ai/trades",
        "/api/v1/ai/trades/{trade_id}",
    }
    assert {route.path for route in router.routes} == expected
    assert any(dependency.dependency is require_local_ai_client for dependency in router.dependencies)
    for route in router.routes:
        assert route.methods in ({"GET"}, {"POST"})
        if route.methods == {"POST"}:
            assert route.path in {
                "/api/v1/ai/trades",
                "/api/v1/ai/broker/session/login-url",
                "/api/v1/ai/broker/session/activate",
            }
    assert len([r for r in router.routes if r.methods == {"POST"}]) == 3
