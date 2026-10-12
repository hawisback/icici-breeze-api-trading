from services.api_gateway.ai_routes import router, require_ai_execution_key
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
    paths = {route.path for route in router.routes}
    assert expected.issubset(paths)
    assert "/api/v1/ai/execution/status" in paths
    assert "/api/v1/ai/execution/orders" in paths
    assert "/api/v1/ai/execution/stops/{stop_order_id}" in paths
    assert any(dependency.dependency is require_local_ai_client for dependency in router.dependencies)

    public_posts = {
        "/api/v1/ai/trades",
        "/api/v1/ai/broker/session/login-url",
        "/api/v1/ai/broker/session/activate",
    }
    for route in router.routes:
        if route.path in expected and route.methods == {"POST"}:
            assert route.path in public_posts
        if route.path.startswith("/api/v1/ai/execution/") and route.methods != {"GET"}:
            assert any(d.dependency is require_ai_execution_key for d in route.dependencies)
    assert len([r for r in router.routes if r.methods == {"POST"}]) > 3
