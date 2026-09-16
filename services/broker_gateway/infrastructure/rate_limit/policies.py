"""Operational Rate Limiter for ICICI Breeze API.

Enforces documented Breeze limits with operational safety headroom:
- Combined API calls: 90 calls/minute (official 100/min)
- Daily API calls: 4,800 calls/day (official 5,000/day)
- Combined order writes: 8 writes/second (official 10 writes/sec)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Optional

from services.broker_gateway.domain.errors import BrokerRateLimitError

logger = logging.getLogger(__name__)


class BrokerRateLimiter:
    """Async multi-tier token bucket rate limiter for broker operations."""

    def __init__(
        self,
        calls_per_minute: int = 90,
        calls_per_day: int = 4800,
        writes_per_second: int = 8,
    ) -> None:
        self.calls_per_minute = calls_per_minute
        self.calls_per_day = calls_per_day
        self.writes_per_second = writes_per_second

        # Minute token bucket
        self._min_capacity = float(calls_per_minute)
        self._min_tokens = float(calls_per_minute)
        self._min_last_replenish = time.monotonic()

        # Second write token bucket
        self._sec_capacity = float(writes_per_second)
        self._sec_tokens = float(writes_per_second)
        self._sec_last_replenish = time.monotonic()

        # Day counter
        self._day_count = 0
        self._day_reset_timestamp = time.time() + 86400

        self._lock = asyncio.Lock()

    async def acquire_read(self) -> None:
        """Acquire rate limit permit for read queries (quotes, funds, history)."""
        async with self._lock:
            self._replenish()
            self._check_day_limit()

            if self._min_tokens < 1.0:
                # Wait for token replenishment
                needed = 1.0 - self._min_tokens
                wait_time = needed * (60.0 / self.calls_per_minute)
                if wait_time > 10.0:
                    raise BrokerRateLimitError(
                        f"Broker read rate limit exceeded. Please retry in {wait_time:.1f}s",
                        retry_after_sec=wait_time,
                    )
                logger.debug("Read rate limiter throttling for %.2f seconds", wait_time)
                await asyncio.sleep(wait_time)
                self._replenish()

            self._min_tokens -= 1.0
            self._day_count += 1

    async def acquire_write(self) -> None:
        """Acquire rate limit permit for write operations (place, modify, cancel, square-off)."""
        async with self._lock:
            self._replenish()
            self._check_day_limit()

            # Check minute bucket
            if self._min_tokens < 1.0:
                wait_time = (1.0 - self._min_tokens) * (60.0 / self.calls_per_minute)
                if wait_time > 5.0:
                    raise BrokerRateLimitError(
                        f"Broker overall rate limit exceeded. Retry in {wait_time:.1f}s",
                        retry_after_sec=wait_time,
                    )
                await asyncio.sleep(wait_time)
                self._replenish()

            # Check per-second write bucket
            if self._sec_tokens < 1.0:
                wait_sec = (1.0 - self._sec_tokens) * (1.0 / self.writes_per_second)
                await asyncio.sleep(wait_sec)
                self._replenish()

            self._min_tokens -= 1.0
            self._sec_tokens -= 1.0
            self._day_count += 1

    def _replenish(self) -> None:
        now = time.monotonic()

        # Replenish minute bucket
        elapsed_min = now - self._min_last_replenish
        self._min_last_replenish = now
        self._min_tokens = min(
            self._min_capacity,
            self._min_tokens + elapsed_min * (self.calls_per_minute / 60.0),
        )

        # Replenish second write bucket
        elapsed_sec = now - self._sec_last_replenish
        self._sec_last_replenish = now
        self._sec_tokens = min(
            self._sec_capacity,
            self._sec_tokens + elapsed_sec * self.writes_per_second,
        )

    def _check_day_limit(self) -> None:
        current_time = time.time()
        if current_time > self._day_reset_timestamp:
            self._day_count = 0
            self._day_reset_timestamp = current_time + 86400

        if self._day_count >= self.calls_per_day:
            raise BrokerRateLimitError(
                f"Daily broker API limit ({self.calls_per_day}) reached for the day.",
                retry_after_sec=self._day_reset_timestamp - current_time,
            )

