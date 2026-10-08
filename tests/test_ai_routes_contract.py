from services.api_gateway.ai_routes import router
from services.api_gateway.dependencies import get_current_user


def test_ai_routes_are_read_only_except_authenticated_trade_intent():
    expected = {
        "/api/v1/ai/nifty/snapshot",
        "/api/v1/ai/nifty/candles",
        "/api/v1/ai/nifty/technicals",
        "/api/v1/ai/nifty/options",
        "/api/v1/ai/account/context",
        "/api/v1/ai/data-quality",
        "/api/v1/ai/trades",
        "/api/v1/ai/trades/{trade_id}",
    }
    assert {route.path for route in router.routes} == expected
    assert any(dependency.dependency is get_current_user for dependency in router.dependencies)
    for route in router.routes:
        assert route.methods in ({"GET"}, {"POST"})
        if route.methods == {"POST"}:
            assert route.path == "/api/v1/ai/trades"
    assert len([r for r in router.routes if r.methods == {"POST"}]) == 1
