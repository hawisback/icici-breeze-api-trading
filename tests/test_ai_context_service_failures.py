"""AI-facing data sources fail with structured unavailable responses."""

from __future__ import annotations

import pytest

from services.ai_context.service import AIContextService


class _FailingHistorical:
    async def get_candles(self, **kwargs):
        raise ConnectionError("fake upstream market data outage")


class _FailingOptions:
    async def get_chain(self, **kwargs):
        raise TimeoutError("fake upstream option chain timeout")


def _service():
    return AIContextService(
        market_data_service=None,
        historical_service=_FailingHistorical(),
        option_chain_service=_FailingOptions(),
        portfolio_service=None,
        broker_gateway=None,
        broker_session_service=None,
        risk_service=None,
    )


@pytest.mark.asyncio
async def test_candles_return_unavailable_instead_of_500_on_upstream_error():
    result = await _service().get_candles()
    assert result["available"] is False
    assert result["reason"] == "MARKET_CANDLE_FETCH_FAILED"
    assert result["error_type"] == "ConnectionError"
    assert result["candles"] == []


@pytest.mark.asyncio
async def test_technicals_return_unavailable_instead_of_500_on_upstream_error():
    result = await _service().get_technicals()
    assert result["available"] is False
    assert result["reason"] == "MARKET_CANDLE_FETCH_FAILED"
    assert result["metrics"]["data_points"] == 0


@pytest.mark.asyncio
async def test_options_return_unavailable_instead_of_500_on_upstream_error():
    result = await _service().get_options()
    assert result["available"] is False
    assert result["reason"] == "OPTION_CHAIN_FETCH_FAILED"
    assert result["source"] == "UNAVAILABLE"
    assert result["error_type"] == "TimeoutError"
    assert result["strikes"] == []
