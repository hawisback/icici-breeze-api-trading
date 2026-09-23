"""Strategy Service package exports.

Keep the heavyweight StrategyService import lazy. Importing a lightweight
strategy submodule (for example services.strategy.futures_signal) first
executes this package initializer. Eagerly importing StrategyService here
pulls in Strategy C, which imports historical helpers, and can create a
circular import for standalone research/backtest commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from services.strategy.repository import StrategyRepository

if TYPE_CHECKING:
    from services.strategy.service import StrategyService

__all__ = ["StrategyRepository", "StrategyService"]


def __getattr__(name: str) -> Any:
    """Lazy-load heavyweight public exports without changing the API."""
    if name == "StrategyService":
        from services.strategy.service import StrategyService

        return StrategyService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
