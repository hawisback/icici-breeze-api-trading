"""Rate-limit regression coverage for read-heavy UI polling."""

import pytest

from libs.config.settings import PlatformSettings
from services.api_gateway.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_general_endpoint_scopes_do_not_starve_each_other():
    settings = PlatformSettings(
        _env_file=None,
        rate_limit_enabled=True,
        rate_limit_general_per_minute=1,
    )
    limiter = RateLimiter(settings=settings)

    allowed, _ = await limiter.check_rate_limit(
        client_id="ip:127.0.0.1",
        tier="GENERAL",
        scope="/api/v1/quotes",
    )
    assert allowed is True

    allowed, retry_after = await limiter.check_rate_limit(
        client_id="ip:127.0.0.1",
        tier="GENERAL",
        scope="/api/v1/quotes",
    )
    assert allowed is False
    assert retry_after > 0

    # A noisy quotes poller must not consume the available-dates bucket.
    allowed, _ = await limiter.check_rate_limit(
        client_id="ip:127.0.0.1",
        tier="GENERAL",
        scope="/api/v1/strategies/simulate/available-dates",
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_command_bucket_remains_shared_without_scope():
    settings = PlatformSettings(
        _env_file=None,
        rate_limit_enabled=True,
        rate_limit_orders_per_minute=1,
    )
    limiter = RateLimiter(settings=settings)

    allowed, _ = await limiter.check_rate_limit(
        client_id="user:operator",
        tier="COMMANDS",
    )
    assert allowed is True

    allowed, retry_after = await limiter.check_rate_limit(
        client_id="user:operator",
        tier="COMMANDS",
    )
    assert allowed is False
    assert retry_after > 0
