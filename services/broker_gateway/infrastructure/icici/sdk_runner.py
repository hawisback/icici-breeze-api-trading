"""Off-loop execution runner for synchronous Breeze SDK methods.

Protects the asyncio event loop by dispatching blocking broker SDK calls
onto dedicated worker threads with timeout guards.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Optional, TypeVar

from services.broker_gateway.domain.errors import BrokerTimeoutError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SdkRunner:
    """Executes blocking Breeze SDK operations in worker threads."""

    def __init__(self, default_timeout_sec: float = 10.0) -> None:
        self.default_timeout_sec = default_timeout_sec

    async def run(
        self,
        func: Callable[..., T],
        *args: Any,
        timeout_sec: Optional[float] = None,
        **kwargs: Any,
    ) -> T:
        """Execute a blocking function in a separate thread with a timeout guard."""
        t = timeout_sec if timeout_sec is not None else self.default_timeout_sec
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func, *args, **kwargs),
                timeout=t,
            )
        except asyncio.TimeoutError as exc:
            func_name = getattr(func, "__name__", str(func))
            logger.error("Breeze SDK operation '%s' timed out after %.2f seconds", func_name, t)
            raise BrokerTimeoutError(f"Breeze SDK operation '{func_name}' timed out after {t:.1f}s") from exc
        except Exception as exc:
            logger.debug("Breeze SDK operation '%s' raised %s: %s", getattr(func, "__name__", str(func)), type(exc).__name__, exc)
            raise

