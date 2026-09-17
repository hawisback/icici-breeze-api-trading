"""Repository for Strategy Service managing data/strategy/strategy.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import OrderSide, Signal, TradingMode, generate_id, utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine
from services.strategy.models import (
    ActiveTrade,
    AutoTradingConfig,
    DecisionLogEntry,
    StrategySignal,
)


class StrategyRepository:
    """Manages strategy definitions, instances, auto-trading configuration, active trades, and decision logs."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/strategy/strategy.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_definitions (
                    definition_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_instances (
                    instance_id TEXT PRIMARY KEY,
                    definition_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL, -- SHADOW, PAPER, LIVE
                    symbol TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    status TEXT NOT NULL, -- RUNNING, STOPPED
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (definition_id) REFERENCES strategy_definitions(definition_id)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    strategy_instance_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    suggested_price REAL,
                    suggested_quantity INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (strategy_instance_id) REFERENCES strategy_instances(instance_id)
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_instance ON signals(strategy_instance_id);")

            # Auto-trading tables
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_strategy_config (
                    id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_trades (
                    trade_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    option_type TEXT NOT NULL,
                    contract_symbol TEXT NOT NULL,
                    contract_instrument_id TEXT NOT NULL,
                    expiry TEXT NOT NULL,
                    strike REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    lot_size INTEGER NOT NULL,
                    lots INTEGER NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_option_price REAL NOT NULL,
                    entry_spot_price REAL NOT NULL,
                    initial_structural_stop REAL NOT NULL,
                    initial_r_points REAL NOT NULL,
                    current_option_price REAL NOT NULL,
                    current_spot_price REAL NOT NULL,
                    current_trailing_stop REAL NOT NULL,
                    option_hard_stop_price REAL NOT NULL,
                    current_r REAL NOT NULL,
                    peak_r REAL NOT NULL,
                    mfe_points REAL NOT NULL,
                    mae_points REAL NOT NULL,
                    reversal_score INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    exit_time TEXT,
                    exit_option_price REAL,
                    exit_spot_price REAL,
                    exit_reason TEXT,
                    gross_pnl REAL,
                    net_pnl REAL,
                    realized_r REAL
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_trades_state ON auto_trades(state);")

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS decision_logs (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    category TEXT NOT NULL,
                    strategy TEXT,
                    message TEXT NOT NULL,
                    details TEXT NOT NULL
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_logs_ts ON decision_logs(timestamp);")

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_signals (
                    signal_id TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    option_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    spot_reference_price REAL NOT NULL,
                    structural_stop REAL NOT NULL,
                    r_points REAL NOT NULL,
                    derivatives_score REAL NOT NULL,
                    features_snapshot TEXT NOT NULL,
                    block_reason TEXT,
                    passed INTEGER NOT NULL
                );
            """)
            await conn.commit()

    async def save_definition(self, definition_id: str, name: str, version: str, description: str) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_definitions (definition_id, name, version, description, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (definition_id, name, version, description, utc_now().isoformat()),
            )
            await conn.commit()

    async def save_instance(
        self,
        instance_id: str,
        definition_id: str,
        name: str,
        mode: TradingMode,
        symbol: str,
        parameters: dict[str, Any],
        status: str = "RUNNING",
    ) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_instances (
                    instance_id, definition_id, name, mode, symbol, parameters, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    definition_id,
                    name,
                    mode.value,
                    symbol,
                    json.dumps(parameters),
                    status,
                    utc_now().isoformat(),
                ),
            )
            await conn.commit()

    async def list_instances(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute("SELECT * FROM strategy_instances ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [
                {
                    "instance_id": r["instance_id"],
                    "definition_id": r["definition_id"],
                    "name": r["name"],
                    "mode": r["mode"],
                    "symbol": r["symbol"],
                    "parameters": json.loads(r["parameters"]),
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    async def save_signal(self, signal: Signal) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO signals (
                    signal_id, strategy_instance_id, symbol, side, suggested_price,
                    suggested_quantity, confidence, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.strategy_instance_id,
                    signal.symbol,
                    signal.side.value,
                    signal.suggested_price,
                    signal.suggested_quantity,
                    signal.confidence,
                    json.dumps(signal.metadata),
                    signal.created_at.isoformat(),
                ),
            )
            await conn.commit()

    # --- Auto Trading Config ---
    async def get_auto_config(self) -> AutoTradingConfig:
        async with self.engine.connect() as conn:
            cursor = await conn.execute("SELECT config_json FROM auto_strategy_config WHERE id = 'active'")
            row = await cursor.fetchone()
            if not row:
                default_cfg = AutoTradingConfig()
                await self.save_auto_config(default_cfg)
                return default_cfg
            return AutoTradingConfig.model_validate_json(row["config_json"])

    async def save_auto_config(self, config: AutoTradingConfig) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO auto_strategy_config (id, config_json, updated_at)
                VALUES ('active', ?, ?)
                """,
                (config.model_dump_json(), utc_now().isoformat()),
            )
            await conn.commit()

    # --- Active & Closed Trades ---
    async def save_trade(self, trade: ActiveTrade) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO auto_trades (
                    trade_id, mode, strategy, direction, option_type,
                    contract_symbol, contract_instrument_id, expiry, strike, quantity, lot_size, lots,
                    entry_time, entry_option_price, entry_spot_price, initial_structural_stop, initial_r_points,
                    current_option_price, current_spot_price, current_trailing_stop, option_hard_stop_price,
                    current_r, peak_r, mfe_points, mae_points, reversal_score, state, unrealized_pnl,
                    exit_time, exit_option_price, exit_spot_price, exit_reason, gross_pnl, net_pnl, realized_r
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_id,
                    trade.mode.value,
                    trade.strategy.value,
                    trade.direction.value,
                    trade.option_type.value,
                    trade.contract_symbol,
                    trade.contract_instrument_id,
                    trade.expiry,
                    trade.strike,
                    trade.quantity,
                    trade.lot_size,
                    trade.lots,
                    trade.entry_time.isoformat(),
                    trade.entry_option_price,
                    trade.entry_spot_price,
                    trade.initial_structural_stop,
                    trade.initial_r_points,
                    trade.current_option_price,
                    trade.current_spot_price,
                    trade.current_trailing_stop,
                    trade.option_hard_stop_price,
                    trade.current_r,
                    trade.peak_r,
                    trade.mfe_points,
                    trade.mae_points,
                    trade.reversal_score,
                    trade.state.value,
                    trade.unrealized_pnl,
                    trade.exit_time.isoformat() if trade.exit_time else None,
                    trade.exit_option_price,
                    trade.exit_spot_price,
                    trade.exit_reason,
                    trade.gross_pnl,
                    trade.net_pnl,
                    trade.realized_r,
                ),
            )
            await conn.commit()

    async def get_active_trades(self) -> list[ActiveTrade]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM auto_trades WHERE state != 'CLOSED' ORDER BY entry_time DESC"
            )
            rows = await cursor.fetchall()
            return [self._row_to_trade(r) for r in rows]

    async def list_trades(self, limit: int = 50) -> list[ActiveTrade]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM auto_trades ORDER BY entry_time DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_trade(r) for r in rows]

    def _row_to_trade(self, r: Any) -> ActiveTrade:
        return ActiveTrade(
            trade_id=r["trade_id"],
            mode=r["mode"],
            strategy=r["strategy"],
            direction=r["direction"],
            option_type=r["option_type"],
            contract_symbol=r["contract_symbol"],
            contract_instrument_id=r["contract_instrument_id"],
            expiry=r["expiry"],
            strike=r["strike"],
            quantity=r["quantity"],
            lot_size=r["lot_size"],
            lots=r["lots"],
            entry_time=datetime.fromisoformat(r["entry_time"]),
            entry_option_price=r["entry_option_price"],
            entry_spot_price=r["entry_spot_price"],
            initial_structural_stop=r["initial_structural_stop"],
            initial_r_points=r["initial_r_points"],
            current_option_price=r["current_option_price"],
            current_spot_price=r["current_spot_price"],
            current_trailing_stop=r["current_trailing_stop"],
            option_hard_stop_price=r["option_hard_stop_price"],
            current_r=r["current_r"],
            peak_r=r["peak_r"],
            mfe_points=r["mfe_points"],
            mae_points=r["mae_points"],
            reversal_score=r["reversal_score"],
            state=r["state"],
            unrealized_pnl=r["unrealized_pnl"],
            exit_time=datetime.fromisoformat(r["exit_time"]) if r["exit_time"] else None,
            exit_option_price=r["exit_option_price"],
            exit_spot_price=r["exit_spot_price"],
            exit_reason=r["exit_reason"],
            gross_pnl=r["gross_pnl"],
            net_pnl=r["net_pnl"],
            realized_r=r["realized_r"],
        )

    # --- Decision Logs ---
    async def save_decision_log(self, entry: DecisionLogEntry) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO decision_logs (id, timestamp, category, strategy, message, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.timestamp.isoformat(),
                    entry.category,
                    entry.strategy,
                    entry.message,
                    json.dumps(entry.details),
                ),
            )
            await conn.commit()

    async def list_decision_logs(self, limit: int = 100) -> list[DecisionLogEntry]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM decision_logs ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                DecisionLogEntry(
                    id=r["id"],
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                    category=r["category"],
                    strategy=r["strategy"],
                    message=r["message"],
                    details=json.loads(r["details"]),
                )
                for r in rows
            ]

    # --- Strategy Signals ---
    async def save_strategy_signal(self, sig: StrategySignal) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_signals (
                    signal_id, strategy, direction, option_type, timestamp, spot_reference_price,
                    structural_stop, r_points, derivatives_score, features_snapshot, block_reason, passed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sig.signal_id,
                    sig.strategy.value,
                    sig.direction.value,
                    sig.option_type.value,
                    sig.timestamp.isoformat(),
                    sig.spot_reference_price,
                    sig.structural_stop,
                    sig.r_points,
                    sig.derivatives_score,
                    json.dumps(sig.features_snapshot),
                    sig.block_reason,
                    1 if sig.passed else 0,
                ),
            )
            await conn.commit()

    async def list_strategy_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM strategy_signals ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "signal_id": r["signal_id"],
                    "strategy": r["strategy"],
                    "direction": r["direction"],
                    "option_type": r["option_type"],
                    "timestamp": r["timestamp"],
                    "spot_reference_price": r["spot_reference_price"],
                    "structural_stop": r["structural_stop"],
                    "r_points": r["r_points"],
                    "derivatives_score": r["derivatives_score"],
                    "features_snapshot": json.loads(r["features_snapshot"]),
                    "block_reason": r["block_reason"],
                    "passed": bool(r["passed"]),
                }
                for r in rows
            ]
