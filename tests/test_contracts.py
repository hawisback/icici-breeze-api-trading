"""Unit tests for domain contracts, UUIDv7 generation, and models."""

import uuid
import pytest
from libs.contracts import (
    Instrument,
    OptionRight,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    ProductType,
    RiskDecision,
    SystemMode,
    TradingMode,
    generate_id,
    utc_now,
)
from libs.database.sqlite import SQLiteConfig, SQLiteEngine


def test_uuid7_and_utc():
    """Verify UUID generation is non-empty and UTC dates are timezone-aware."""
    uid = generate_id()
    assert uid is not None
    assert len(uid) > 0

    now = utc_now()
    assert now.tzinfo is not None


def test_order_intent_model():
    """Verify OrderIntent validation and default values."""
    intent = OrderIntent(
        instrument_id="INST-NIFTY-24000-CE",
        symbol="NIFTY26SEP24000CE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=50,
        price=120.5,
        trading_mode=TradingMode.PAPER,
    )
    assert intent.side == OrderSide.BUY
    assert intent.quantity == 50
    assert intent.price == 120.5
    assert intent.trading_mode == TradingMode.PAPER
    assert intent.intent_id is not None


@pytest.mark.asyncio
async def test_sqlite_engine_pragmas(tmp_path):
    """Verify SQLite WAL pragma configuration and table creation."""
    db_file = tmp_path / "test.db"
    config = SQLiteConfig(db_path=db_file, synchronous="FULL")
    engine = SQLiteEngine(config)
    await engine.initialize()

    async with engine.connect() as conn:
        cursor = await conn.execute("PRAGMA journal_mode;")
        row = await cursor.fetchone()
        assert row[0].upper() == "WAL"

        cursor = await conn.execute("PRAGMA synchronous;")
        row = await cursor.fetchone()
        # In SQLite: 0=OFF, 1=NORMAL, 2=FULL
        assert row[0] == 2  # FULL

