"""External AI intent -> durable backend-owned PAPER trade and trailing tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from libs.contracts.models import TradingMode, utc_now
from services.ai_context.trading import AITradeError, AITradeService

INSTRUMENT = "INST-NIFTY-2026-10-13-22500-CE"


class Chain:
    bid = 99.0
    ask = 100.0
    source = "KITE"
    stale = False

    async def get_chain(self, *, underlying, expiry, provider):
        assert underlying == "NIFTY"
        assert provider == "kite"
        assert expiry in (None, "2026-10-13")
        from datetime import timedelta

        ts = utc_now() - timedelta(seconds=60 if self.stale else 1)
        return {
            "source": self.source,
            "expiry": "2026-10-13",
            "strikes": [{
                "strike": 22500,
                "call": {
                    "instrument_id": INSTRUMENT,
                    "symbol": "NIFTY26O1322500CE",
                    "bid": self.bid,
                    "ask": self.ask,
                    "lot_size": 65,
                    "market_timestamp": ts.isoformat(),
                },
                "put": None,
            }],
        }


def settings(path: Path, *, mode=TradingMode.PAPER, enabled=True):
    return SimpleNamespace(
        ai_trade_db_path=path,
        ai_trade_enabled=enabled,
        ai_trade_mode=mode,
        ai_trade_max_quantity=65,
        ai_trade_max_premium_notional=15000,
        ai_trade_max_daily_entries=3,
        ai_trade_initial_stop_pct=6.0,
        ai_trade_trail_activation_pct=5.0,
        ai_trade_trail_gap_pct=3.0,
        ai_trade_target_pct=7.0,
        ai_trade_max_hold_seconds=480,
        ai_trade_poll_seconds=2.5,
    )


def service(path: Path, *, chain=None, mode=TradingMode.PAPER, enabled=True):
    return AITradeService(
        settings=settings(path, mode=mode, enabled=enabled),
        option_chain_service=chain or Chain(),
    )


@pytest.mark.asyncio
async def test_signal_is_idempotent_and_survives_restart(tmp_path):
    path = tmp_path / "ai.db"
    manager = service(path)
    await manager.initialize()
    first = await manager.submit(signal_id="signal-001", instrument_id=INSTRUMENT, quantity=65)
    assert first["status"] == "OPEN"
    assert first["entry_price"] == 100.0
    assert first["initial_stop"] == 94.0
    assert first["target_price"] == 107.0
    assert "mode" not in first
    again = await manager.submit(signal_id="signal-001", instrument_id=INSTRUMENT, quantity=65)
    assert again["trade_id"] == first["trade_id"]

    restarted = service(path)
    await restarted.initialize()
    found = await restarted.get(first["trade_id"])
    assert found is not None
    assert found["status"] == "OPEN"
    with pytest.raises(AITradeError) as exc:
        await restarted.submit(signal_id="signal-002", instrument_id=INSTRUMENT, quantity=65)
    assert exc.value.code == "ANOTHER_AI_TRADE_IS_OPEN"


@pytest.mark.asyncio
async def test_independent_worker_trails_then_exits_at_bid(tmp_path):
    chain = Chain()
    manager = service(tmp_path / "ai.db", chain=chain)
    await manager.initialize()
    trade = await manager.submit(signal_id="signal-003", instrument_id=INSTRUMENT, quantity=65)

    chain.bid, chain.ask = 105.5, 106.0
    await manager.poll_once()
    rising = await manager.get(trade["trade_id"])
    assert rising is not None
    assert rising["trailing_active"] is True
    assert rising["current_stop"] >= 100.0
    assert rising["status"] == "OPEN"

    chain.bid, chain.ask = 101.0, 101.5
    await manager.poll_once()
    closed = await manager.get(trade["trade_id"])
    assert closed is not None
    assert closed["status"] == "CLOSED"
    assert closed["exit_reason"] == "TRAILING_STOP"
    assert closed["exit_price"] == 101.0
    assert closed["pnl"] == 65.0


@pytest.mark.asyncio
async def test_stale_market_never_creates_an_imaginary_exit(tmp_path):
    chain = Chain()
    manager = service(tmp_path / "ai.db", chain=chain)
    await manager.initialize()
    trade = await manager.submit(signal_id="signal-004", instrument_id=INSTRUMENT, quantity=65)
    chain.bid, chain.ask = 50.0, 51.0
    chain.stale = True
    await manager.poll_once()
    current = await manager.get(trade["trade_id"])
    assert current is not None
    assert current["status"] == "OPEN"
    assert current["data_status"] == "OPTION_QUOTE_STALE"
    assert current["exit_price"] is None


@pytest.mark.asyncio
async def test_live_selection_fails_closed_without_executing(tmp_path):
    manager = service(tmp_path / "ai.db", mode=TradingMode.LIVE)
    await manager.initialize()
    with pytest.raises(AITradeError) as exc:
        await manager.submit(signal_id="signal-005", instrument_id=INSTRUMENT, quantity=65)
    assert exc.value.code == "EXECUTION_MODE_NOT_READY"
    assert await manager.list() == []


@pytest.mark.asyncio
async def test_disabled_and_invalid_lots_never_create_trade(tmp_path):
    manager = service(tmp_path / "ai.db", enabled=False)
    await manager.initialize()
    with pytest.raises(AITradeError) as exc:
        await manager.submit(signal_id="signal-006", instrument_id=INSTRUMENT, quantity=65)
    assert exc.value.code == "AI_TRADING_NOT_ENABLED"

    manager = service(tmp_path / "ai.db")
    with pytest.raises(AITradeError) as exc:
        await manager.submit(signal_id="signal-007", instrument_id=INSTRUMENT, quantity=1)
    assert exc.value.code == "INVALID_LOT_QUANTITY"
    assert await manager.list() == []


@pytest.mark.asyncio
async def test_ai_paper_entry_has_no_strategy_oms_or_global_risk_dependency(tmp_path, monkeypatch):
    """Even a halted strategy or unresolved OMS order cannot veto isolated AI PAPER."""
    from services.ai_context.service import AIContextService
    from services.strategy.service import StrategyService

    async def do_not_consult_shared_context(*args, **kwargs):
        raise AssertionError("AI PAPER must not consult shared risk / OMS context")

    async def do_not_consult_strategy(*args, **kwargs):
        raise AssertionError("AI PAPER must not inspect internal strategy positions")

    monkeypatch.setattr(AIContextService, "get_snapshot", do_not_consult_shared_context)
    monkeypatch.setattr(StrategyService, "get_status", do_not_consult_strategy)

    manager = service(tmp_path / "ai.db")
    await manager.initialize()
    trade = await manager.submit(signal_id="signal-008", instrument_id=INSTRUMENT, quantity=65)
    assert trade["status"] == "OPEN"
    assert trade["entry_price"] == 100.0


@pytest.mark.asyncio
async def test_bad_quote_still_blocks_an_ai_paper_entry(tmp_path):
    chain = Chain()
    chain.stale = True
    manager = service(tmp_path / "ai.db", chain=chain)
    await manager.initialize()
    with pytest.raises(AITradeError) as exc:
        await manager.submit(signal_id="signal-009", instrument_id=INSTRUMENT, quantity=65)
    assert exc.value.code == "OPTION_QUOTE_STALE"
    assert await manager.list() == []

def test_default_config_enables_only_paper(monkeypatch):
    """Mode is owned by backend settings, never accepted from an AI request."""
    from libs.config.settings import PlatformSettings
    from services.api_gateway.ai_routes import AISignalIntent
    from pydantic import ValidationError

    monkeypatch.delenv("AI_TRADE_ENABLED", raising=False)
    monkeypatch.delenv("AI_TRADE_MODE", raising=False)
    config = PlatformSettings(_env_file=None)
    assert config.ai_trade_enabled is True
    assert config.ai_trade_mode == TradingMode.PAPER

    with pytest.raises(ValidationError):
        AISignalIntent(
            signal_id="signal-mode-test",
            instrument_id=INSTRUMENT,
            quantity=65,
            trading_mode="LIVE",
        )
    with pytest.raises(ValidationError):
        AISignalIntent(
            signal_id="signal-price-test",
            instrument_id=INSTRUMENT,
            quantity=65,
            price=100.0,
        )


def test_operator_can_disable_or_select_live_but_cannot_bypass_live_block(monkeypatch):
    from libs.config.settings import PlatformSettings

    monkeypatch.setenv("AI_TRADE_ENABLED", "false")
    monkeypatch.setenv("AI_TRADE_MODE", "PAPER")
    disabled = PlatformSettings(_env_file=None)
    assert disabled.ai_trade_enabled is False

    monkeypatch.setenv("AI_TRADE_ENABLED", "true")
    monkeypatch.setenv("AI_TRADE_MODE", "LIVE")
    live_selected = PlatformSettings(_env_file=None)
    assert live_selected.ai_trade_enabled is True
    assert live_selected.ai_trade_mode == TradingMode.LIVE


def test_both_deployment_templates_enable_paper_and_not_live():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for env_name in (".env.example", ".env.local-live.example"):
        content = (root / env_name).read_text(encoding="utf-8")
        assert "AI_TRADE_ENABLED=true" in content
        assert "AI_TRADE_MODE=PAPER" in content


@pytest.mark.asyncio
async def test_ai_paper_uses_targeted_single_kite_contract_quote_and_tracks_last_bid(tmp_path):
    class SingleKite:
        def __init__(self):
            self.calls = []
            self.bid = 99.0

        async def get_contract_quote(self, instrument_id):
            from libs.contracts.models import utc_now
            self.calls.append(instrument_id)
            return {
                "source": "KITE", "expiry": "2026-10-13",
                "instrument_id": instrument_id, "symbol": "NIFTY26O1322500CE",
                "bid": self.bid, "ask": self.bid + 1, "lot_size": 65,
                "market_timestamp": utc_now().isoformat(),
            }

        async def get_chain(self, **kwargs):
            raise AssertionError("Trailing PAPER stop must not fetch entire option chain")

    chain = SingleKite()
    manager = service(tmp_path / "ai.db", chain=chain)
    await manager.initialize()
    trade = await manager.submit(signal_id="signal-one-leg-01", instrument_id=INSTRUMENT, quantity=65)
    assert trade["last_bid"] == 99
    assert trade["unrealized_pnl"] == -65.0
    chain.bid = 105.5
    await manager.poll_once()
    updated = await manager.get(trade["trade_id"])
    assert updated["trailing_active"] is True
    assert updated["last_bid"] == 105.5
    assert updated["unrealized_pnl"] == 357.5
    assert len(chain.calls) == 2
    chain.bid = 101.0
    await manager.poll_once()
    closed = await manager.get(trade["trade_id"])
    assert closed["status"] == "CLOSED"
    assert closed["exit_reason"] == "TRAILING_STOP"
    assert closed["unrealized_pnl"] is None
    assert closed["pnl"] == 65.0


@pytest.mark.asyncio
async def test_old_ai_journal_is_migrated_for_last_bid(tmp_path):
    import aiosqlite
    path = tmp_path / "old.db"
    # Simulate an older persistent PAPER journal containing the prior table schema.
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            CREATE TABLE ai_paper_trades (
                trade_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL,
                instrument_id TEXT NOT NULL, symbol TEXT NOT NULL, expiry TEXT NOT NULL,
                quantity INTEGER NOT NULL, entry_price REAL NOT NULL, entry_bid REAL NOT NULL,
                initial_stop REAL NOT NULL, current_stop REAL NOT NULL,
                target_price REAL NOT NULL, peak_bid REAL NOT NULL,
                trailing_active INTEGER DEFAULT 0, status TEXT NOT NULL,
                entry_time TEXT NOT NULL, last_quote_at TEXT NOT NULL,
                last_checked_at TEXT, data_status TEXT DEFAULT 'VALID',
                exit_time TEXT, exit_price REAL, exit_reason TEXT, pnl REAL,
                policy_json TEXT NOT NULL
            )
        """)
        await db.commit()
    manager = service(path)
    await manager.initialize()
    async with aiosqlite.connect(path) as db:
        columns = [row[1] for row in await (await db.execute("PRAGMA table_info(ai_paper_trades)")).fetchall()]
        assert "last_bid" in columns
