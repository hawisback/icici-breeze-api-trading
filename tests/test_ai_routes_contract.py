from services.api_gateway.ai_routes import router


def test_ai_routes_are_read_only_and_expected():
    expected = {
        "/api/v1/ai/nifty/snapshot",
        "/api/v1/ai/nifty/candles",
        "/api/v1/ai/nifty/technicals",
        "/api/v1/ai/nifty/options",
        "/api/v1/ai/account/context",
        "/api/v1/ai/data-quality",
    }
    paths = {route.path for route in router.routes}
    assert paths == expected

    for route in router.routes:
        assert route.methods == {"GET"}
