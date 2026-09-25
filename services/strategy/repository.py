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

    def __init__(
        self,
        db_path: Optional[Path] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        path = db_path or Path("data/strategy/strategy.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))
        self.config_path = config_path

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
            columns = await (await conn.execute("PRAGMA table_info(auto_trades)")).fetchall()
            if "management_json" not in {r["name"] for r in columns}:
                await conn.execute("ALTER TABLE auto_trades ADD COLUMN management_json TEXT NOT NULL DEFAULT '{}'")
            await conn.execute("CREATE TABLE IF NOT EXISTS strategy_runtime (id TEXT PRIMARY KEY, state_json TEXT NOT NULL)")

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
                CREATE TABLE IF NOT EXISTS strategy_option_chain_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    strategy_signal_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    spot_price REAL NOT NULL,
                    expiry TEXT,
                    source TEXT NOT NULL,
                    selector_candidates_json TEXT NOT NULL,
                    chain_candidates_json TEXT NOT NULL,
                    selected_contract_json TEXT,
                    rejection_reason TEXT,
                    strategy TEXT,
                    execution_mode TEXT,
                    chain_snapshot_timestamp TEXT
                );
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_option_chain_snapshots_signal ON strategy_option_chain_snapshots(strategy_signal_id);"
            )
            snapshot_columns = await (await conn.execute("PRAGMA table_info(strategy_option_chain_snapshots)")).fetchall()
            existing_snapshot_columns = {r["name"] for r in snapshot_columns}
            for column_name, column_type in (
                ("selector_timestamp", "TEXT"), ("signal_timestamp", "TEXT"),
                ("direction", "TEXT"), ("selector_result", "TEXT"),
                ("strategy", "TEXT"), ("execution_mode", "TEXT"),
                ("chain_snapshot_timestamp", "TEXT"),
            ):
                if column_name not in existing_snapshot_columns:
                    await conn.execute(f"ALTER TABLE strategy_option_chain_snapshots ADD COLUMN {column_name} {column_type}")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_option_quotes (
                    quote_id TEXT PRIMARY KEY,
                    trade_id TEXT,
                    strategy_signal_id TEXT,
                    instrument_id TEXT NOT NULL,
                    quote_timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    freshness_seconds REAL,
                    bid REAL,
                    ask REAL,
                    ltp REAL,
                    volume INTEGER,
                    open_interest INTEGER,
                    status TEXT NOT NULL,
                    reason TEXT,
                    raw_json TEXT NOT NULL
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_option_quotes_trade ON strategy_option_quotes(trade_id, quote_timestamp);")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_execution_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    raw_bid REAL,
                    raw_ask REAL,
                    raw_ltp REAL,
                    executable_price REAL,
                    slippage_points REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    cost_assumption_version TEXT NOT NULL,
                    reason TEXT
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_execution_trade ON strategy_execution_ledger(trade_id, timestamp);")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_eod_reports (
                    session_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    PRIMARY KEY (session_date, direction)
                );
            """)

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
            signal_columns = await (await conn.execute("PRAGMA table_info(strategy_signals)")).fetchall()
            if "underlying_entry_price" not in {r["name"] for r in signal_columns}:
                await conn.execute("ALTER TABLE strategy_signals ADD COLUMN underlying_entry_price REAL")
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
    @staticmethod
    def _migrate_auto_config_data(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Apply historical schema/default migrations to file or DB payloads."""
        tunables = data.setdefault("tunables", {})
        old_shared_adx = tunables.get("adx_threshold", 20.0)
        changed = False
        if "strategy_b_adx_threshold" not in tunables:
            tunables["strategy_b_adx_threshold"] = old_shared_adx
            changed = True
        if "legacy_strategy_a_adx_threshold" not in tunables:
            tunables["legacy_strategy_a_adx_threshold"] = old_shared_adx
            changed = True

        if data.get("strategy_a_revision", 1) < 3:
            session = data.setdefault("session", {})
            if tunables.get("rvol_threshold") == 1.30:
                tunables["rvol_threshold"] = 1.20
                changed = True
            if session.get("no_new_trade_before") == "09:30":
                session["no_new_trade_before"] = "09:20"
                changed = True
            data["strategy_a_revision"] = 3
            changed = True
        if data.get("strategy_a_revision", 1) < 4:
            if tunables.get("adx_threshold") == 20.0:
                tunables["adx_threshold"] = 22.0
                changed = True
            data["strategy_a_revision"] = 4
            changed = True
        if data.get("strategy_a_revision", 1) < 5:
            tunables.setdefault("momentum_adx_min_delta_2bars", -2.0)
            tunables.setdefault("momentum_ema20_slope_min_atr", 0.0)
            tunables.setdefault("momentum_ema20_slope_max_atr", 0.15)
            data["strategy_a_revision"] = 5
            changed = True
        return data, changed

    def _read_config_file(self) -> AutoTradingConfig | None:
        if self.config_path is None or not self.config_path.exists():
            return None
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Unable to read trading config file {self.config_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Trading config file {self.config_path} must contain a JSON object"
            )
        payload, _ = self._migrate_auto_config_data(payload)
        # Arming is intentionally process-local. A file can choose mode but
        # can never grant live order authority after restart.
        payload["system_armed"] = False
        try:
            return AutoTradingConfig.model_validate(payload)
        except Exception as exc:
            raise RuntimeError(
                f"Invalid trading config file {self.config_path}: {exc}"
            ) from exc

    def _write_config_file(self, config: AutoTradingConfig) -> None:
        if self.config_path is None:
            return
        payload = config.model_dump(mode="json")
        payload["system_armed"] = False
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.config_path.with_suffix(
            self.config_path.suffix + ".tmp"
        )
        temp_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self.config_path)

    async def get_auto_config(self) -> AutoTradingConfig:
        # The JSON file is the canonical non-secret operator configuration
        # whenever configured. SQLite remains the runtime/audit copy.
        file_config = self._read_config_file()
        if file_config is not None:
            await self.save_auto_config(file_config, persist_file=False)
            return file_config

        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT config_json FROM auto_strategy_config WHERE id = 'active'"
            )
            row = await cursor.fetchone()

        if row:
            data = json.loads(row["config_json"])
            data, changed = self._migrate_auto_config_data(data)
            config = AutoTradingConfig.model_validate(data)
            # Existing SQLite installations are migrated into the visible
            # file on first startup after this feature is deployed.
            if self.config_path is not None:
                self._write_config_file(config)
            if changed:
                await self.save_auto_config(config, persist_file=False)
            return config

        default_cfg = AutoTradingConfig()
        await self.save_auto_config(default_cfg)
        return default_cfg

    async def save_runtime(self, state: dict, strategy: str = "trend_pullback") -> None:
        async with self.engine.connect() as conn:
            await conn.execute("INSERT OR REPLACE INTO strategy_runtime VALUES (?, ?)", (strategy, json.dumps(state)))
            await conn.commit()

    async def get_runtime(self, strategy: str = "trend_pullback") -> dict:
        async with self.engine.connect() as conn:
            row = await (await conn.execute("SELECT state_json FROM strategy_runtime WHERE id = ?", (strategy,))).fetchone()
            return json.loads(row["state_json"]) if row else {}

    async def save_auto_config(
        self,
        config: AutoTradingConfig,
        *,
        persist_file: bool = True,
    ) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO auto_strategy_config (id, config_json, updated_at)
                VALUES ('active', ?, ?)
                """,
                (config.model_dump_json(), utc_now().isoformat()),
            )
            await conn.commit()
        if persist_file:
            self._write_config_file(config)

    async def save_option_chain_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Persist passive selector-input capture without affecting execution."""
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_option_chain_snapshots (
                    snapshot_id, strategy_signal_id, captured_at, spot_price, expiry,
                    source, selector_candidates_json, chain_candidates_json,
                    selected_contract_json, rejection_reason, selector_timestamp,
                    signal_timestamp, direction, selector_result, strategy,
                    execution_mode, chain_snapshot_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["snapshot_id"],
                    snapshot["strategy_signal_id"],
                    snapshot["captured_at"],
                    snapshot["spot_price"],
                    snapshot.get("expiry"),
                    snapshot.get("source", "UNAVAILABLE"),
                    json.dumps(snapshot.get("selector_candidates", []), sort_keys=True),
                    json.dumps(snapshot.get("chain_candidates", []), sort_keys=True),
                    json.dumps(snapshot["selected_contract"], sort_keys=True) if snapshot.get("selected_contract") else None,
                    snapshot.get("rejection_reason"),
                    snapshot.get("selector_timestamp", snapshot.get("captured_at")),
                    snapshot.get("signal_timestamp"), snapshot.get("direction"), snapshot.get("selector_result"),
                    snapshot.get("strategy"), snapshot.get("execution_mode"),
                    snapshot.get("chain_snapshot_timestamp", snapshot.get("captured_at")),
                ),
            )
            await conn.commit()

    async def list_option_chain_snapshots(self, session_date: Optional[str] = None) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            query = "SELECT * FROM strategy_option_chain_snapshots"
            params: tuple[Any, ...] = ()
            if session_date:
                query += " WHERE substr(captured_at, 1, 10) = ?"
                params = (session_date,)
            query += " ORDER BY captured_at ASC"
            rows = await (await conn.execute(query, params)).fetchall()
            return [
                {
                    "snapshot_id": r["snapshot_id"], "strategy_signal_id": r["strategy_signal_id"],
                    "captured_at": r["captured_at"], "spot_price": r["spot_price"], "expiry": r["expiry"],
                    "source": r["source"], "selector_candidates": json.loads(r["selector_candidates_json"]),
                    "chain_candidates": json.loads(r["chain_candidates_json"]),
                    "selected_contract": json.loads(r["selected_contract_json"]) if r["selected_contract_json"] else None,
                    "rejection_reason": r["rejection_reason"], "strategy": r["strategy"],
                    "execution_mode": r["execution_mode"], "chain_snapshot_timestamp": r["chain_snapshot_timestamp"],
                }
                for r in rows
            ]

    async def save_option_quote(self, quote: dict[str, Any]) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_option_quotes (
                    quote_id, trade_id, strategy_signal_id, instrument_id,
                    quote_timestamp, source, freshness_seconds, bid, ask, ltp,
                    volume, open_interest, status, reason, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote.get("quote_id", generate_id()), quote.get("trade_id"), quote.get("strategy_signal_id"),
                    quote["instrument_id"], quote["quote_timestamp"], quote.get("source", "UNKNOWN"),
                    quote.get("freshness_seconds"), quote.get("bid"), quote.get("ask"), quote.get("ltp"),
                    quote.get("volume"), quote.get("open_interest"), quote.get("status", "UNAVAILABLE"),
                    quote.get("reason"), json.dumps(quote, sort_keys=True, default=str),
                ),
            )
            await conn.commit()

    async def list_option_quotes(self, session_date: Optional[str] = None) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            query = "SELECT * FROM strategy_option_quotes"
            params: tuple[Any, ...] = ()
            if session_date:
                query += " WHERE substr(quote_timestamp, 1, 10) = ?"
                params = (session_date,)
            query += " ORDER BY quote_timestamp ASC"
            rows = await (await conn.execute(query, params)).fetchall()
            return [dict(r) for r in rows]

    async def save_execution_ledger(self, entry: dict[str, Any]) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_execution_ledger (
                    ledger_id, trade_id, side, timestamp, raw_bid, raw_ask, raw_ltp,
                    executable_price, slippage_points, quantity, source,
                    cost_assumption_version, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("ledger_id", generate_id()), entry["trade_id"], entry["side"], entry["timestamp"],
                    entry.get("raw_bid"), entry.get("raw_ask"), entry.get("raw_ltp"), entry.get("executable_price"),
                    entry.get("slippage_points", 0.0), entry["quantity"], entry.get("source", "UNKNOWN"),
                    entry.get("cost_assumption_version", "unknown"), entry.get("reason"),
                ),
            )
            await conn.commit()

    async def save_eod_report(self, session_date: str, direction: str, report: dict[str, Any]) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_eod_reports
                (session_date, direction, generated_at, report_json) VALUES (?, ?, ?, ?)
                """,
                (session_date, direction, utc_now().isoformat(), json.dumps(report, sort_keys=True, default=str)),
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
            await conn.execute("UPDATE auto_trades SET management_json = ? WHERE trade_id = ?",
                               (trade.model_dump_json(), trade.trade_id))
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
        if "management_json" in r.keys() and r["management_json"] not in (None, "{}"):
            return ActiveTrade.model_validate_json(r["management_json"])
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
                    underlying_entry_price, structural_stop, r_points, derivatives_score, features_snapshot, block_reason, passed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sig.signal_id,
                    sig.strategy.value,
                    sig.direction.value,
                    sig.option_type.value,
                    sig.timestamp.isoformat(),
                    sig.spot_reference_price,
                    sig.underlying_entry_price,
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
                    "underlying_entry_price": r["underlying_entry_price"] if "underlying_entry_price" in r.keys() else None,
                    "structural_stop": r["structural_stop"],
                    "r_points": r["r_points"],
                    "derivatives_score": r["derivatives_score"],
                    "features_snapshot": json.loads(r["features_snapshot"]),
                    "block_reason": r["block_reason"],
                    "passed": bool(r["passed"]),
                }
                for r in rows
            ]
