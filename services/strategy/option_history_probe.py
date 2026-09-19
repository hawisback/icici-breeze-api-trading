"""Read-only Breeze historical option-candle probe used by the validation task."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from dataclasses import asdict
from decimal import Decimal

from libs.config.settings import get_platform_settings
from services.broker_gateway.domain.enums import Exchange, FeedInterval, OptionRight, ProductType
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter


async def main() -> None:
    settings = get_platform_settings()
    adapter = IciciBreezeAdapter(
        api_key=settings.breeze_api_key.get_secret_value() if settings.breeze_api_key else "",
        secret_key=settings.breeze_secret_key.get_secret_value() if settings.breeze_secret_key else "",
        session_token=settings.breeze_session_token.get_secret_value() if settings.breeze_session_token else "",
    )
    ok = await adapter.authenticate(adapter.api_key, adapter.secret_key, adapter.session_token)
    print({"authenticated": ok, "active": adapter.client_manager.is_active})
    if not ok:
        return
    instrument = BrokerInstrumentRef(
        internal_instrument_id="probe",
        exchange=Exchange.NFO,
        stock_code="NIFTY",
        product_type=ProductType.OPTIONS,
        expiry=date(2025, 1, 30),
        strike=Decimal("23000"),
        option_right=OptionRight.PUT,
    )
    try:
        candles = await adapter.market_adapter.get_historical(
            instrument=instrument,
            interval=FeedInterval.ONE_MINUTE,
            from_date=datetime(2025, 1, 10, 3, 45, tzinfo=timezone.utc),
            to_date=datetime(2025, 1, 10, 10, 0, tzinfo=timezone.utc),
        )
        print({
            "count": len(candles),
            "first": asdict(candles[0]) if candles else None,
            "last": asdict(candles[-1]) if candles else None,
        })
    finally:
        await adapter.client_manager.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
