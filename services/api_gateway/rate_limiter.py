"""Sliding-window memory rate limiter for API Gateway endpoints.

Provides tiered rate limiting:
- AUTH tier: login and session activation (strictest, e.g. 5 req/min)
- COMMANDS tier: order submission, cancellation, kill-switch, live gate (e.g. 30 req/min)
- GENERAL tier: quotes, candles, portfolio, options chain (e.g. 120 req/min)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import math
import time
from typing import Optional

from libs.config.settings import PlatformSettings, get_platform_settings


class RateLimiter:
    """Sliding-window in-memory rate limiter with per-tier thresholds."""

    def __init__(self, settings: Optional[PlatformSettings] = None) -> None:
        self.settings = settings or get_platform_settings()
        self._history: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    def _get_limit_for_tier(self, tier: str) -> int:
        tier_upper = tier.upper()
        if tier_upper == "AUTH":
            return self.settings.rate_limit_login_per_minute
        elif tier_upper == "COMMANDS":
            return self.settings.rate_limit_orders_per_minute
        else:
            return self.settings.rate_limit_general_per_minute

    async def check_rate_limit(
        self,
        client_id: str,
        tier: str = "GENERAL",
        scope: str = "",
    ) -> tuple[bool, int]:
        """Check if a request is permitted under a scoped sliding window.

        The optional scope allows read-heavy GENERAL endpoints to use
        independent buckets while AUTH and COMMANDS remain aggregated.

        Returns:
            (allowed: bool, retry_after_seconds: int)
        """
        if not self.settings.rate_limit_enabled:
            return True, 0

        limit = self._get_limit_for_tier(tier)
        window = 60.0  # 1 minute sliding window
        now = time.monotonic()
        key = (client_id, tier.upper(), str(scope or "").strip())

        async with self._lock:
            queue = self._history[key]
            # Evict timestamps outside current window
            while queue and queue[0] <= now - window:
                queue.popleft()

            if len(queue) >= limit:
                # Limit exceeded
                oldest = queue[0]
                retry_after = max(1, math.ceil((oldest + window) - now))
                return False, retry_after

            queue.append(now)
            return True, 0

    async def reset(self) -> None:
        """Clear all rate limit histories (used for testing)."""
        async with self._lock:
            self._history.clear()

