"""Regression coverage for the offline PAPER-only OMS reset command."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from scripts.reset_paper_oms import backup_database, open_existing, paper_plan, remove_paper_rows


def populated_db(tmp_path: Path, *, mode_mismatch: bool = False) -> Path:
    path = tmp_path / "oms.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE order_intents (
                intent_id TEXT PRIMARY KEY,
                trading_mode TEXT NOT NULL
            );
            CREATE TABLE broker_orders (
                order_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL REFERENCES order_intents(intent_id),
                trading_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                filled_quantity INTEGER NOT NULL DEFAULT 0,
                broker_order_id TEXT
            );
            CREATE TABLE order_events (
                event_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES broker_orders(order_id)
            );
            CREATE TABLE outbox_events (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE processed_events (
                event_id TEXT NOT NULL,
                consumer_name TEXT NOT NULL,
                PRIMARY KEY (event_id, consumer_name)
            );
        """)
        for name, mode in (
            ("p-old-1", "PAPER"),
            ("p-old-2", "PAPER"),
            ("live", "LIVE"),
            ("shadow", "SHADOW"),
        ):
            conn.execute(
                "INSERT INTO order_intents (intent_id,trading_mode) VALUES (?,?)",
                (name, mode),
            )
            conn.execute(
                """INSERT INTO broker_orders
                   (order_id,intent_id,trading_mode,status,created_at,filled_quantity,broker_order_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    name, name,
                    "LIVE" if mode_mismatch and name == "p-old-1" else mode,
                    "VALIDATING", "2026-09-16T00:00:00+00:00", 0, None,
                ),
            )
            conn.execute(
                "INSERT INTO order_events(event_id,order_id) VALUES (?,?)",
                (name + "-event", name),
            )
            conn.execute(
                "INSERT INTO outbox_events(event_id,payload) VALUES (?,?)",
                (name + "-outbox", json.dumps({
                    "order_id": name, "intent_id": name, "trading_mode": mode
                })),
            )
            conn.execute(
                "INSERT INTO processed_events(event_id,consumer_name) VALUES (?,?)",
                (name + "-outbox", "OMS"),
            )
        conn.execute(
            "INSERT INTO order_intents (intent_id,trading_mode) VALUES ('paper-orphan','PAPER')"
        )
        conn.commit()
    return path


def test_paper_only_reset_preserves_all_other_modes_and_verifiable_backup(tmp_path):
    path = populated_db(tmp_path)
    with open_existing(path) as conn:
        plan = paper_plan(conn)
        assert plan["status_counts"] == {"VALIDATING": 2}
        assert len(plan["orders"]) == 2
        assert len(plan["intent_ids"]) == 3  # includes orphan PAPER intent
        assert len(plan["outbox_ids"]) == 2

        backup_path = tmp_path / "backups" / "oms-before-paper.sqlite3"
        backup_database(conn, backup_path)
        deleted = remove_paper_rows(conn, plan)
        assert deleted == {
            "order_events": 2,
            "processed_events": 2,
            "outbox_events": 2,
            "broker_orders": 2,
            "order_intents": 3,
        }
        assert [r[0] for r in conn.execute("SELECT order_id FROM broker_orders ORDER BY order_id")] == [
            "live", "shadow"
        ]
        assert [r[0] for r in conn.execute("SELECT intent_id FROM order_intents ORDER BY intent_id")] == [
            "live", "shadow"
        ]
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
        assert not paper_plan(conn)["orders"]

    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("SELECT count(*) FROM broker_orders").fetchone()[0] == 4
        assert backup.execute("SELECT count(*) FROM outbox_events").fetchone()[0] == 4


def test_refuses_any_live_paper_mismatch(tmp_path):
    path = populated_db(tmp_path, mode_mismatch=True)
    with open_existing(path) as conn:
        with pytest.raises(ValueError, match="mismatched OMS order-intent"):
            paper_plan(conn)
        assert conn.execute("SELECT count(*) FROM broker_orders").fetchone()[0] == 4


def test_does_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="does not exist"):
        open_existing(path)
    assert not path.exists()


def test_refuses_invalid_outbox_json(tmp_path):
    path = populated_db(tmp_path)
    with open_existing(path) as conn:
        conn.execute(
            "INSERT INTO outbox_events(event_id,payload) VALUES ('bad',?)",
            ("NOT_JSON",),
        )
        conn.commit()
        with pytest.raises(ValueError, match="Unparseable OMS outbox payload"):
            paper_plan(conn)
        assert conn.execute("SELECT count(*) FROM broker_orders").fetchone()[0] == 4


def test_detects_changes_between_planning_and_delete(tmp_path):
    path = populated_db(tmp_path)
    with open_existing(path) as conn:
        plan = paper_plan(conn)
        conn.execute("INSERT INTO order_intents(intent_id,trading_mode) VALUES ('new-paper','PAPER')")
        conn.commit()
        with pytest.raises(ValueError, match="changed during reset"):
            remove_paper_rows(conn, plan)
        assert conn.execute("SELECT count(*) FROM broker_orders").fetchone()[0] == 4


def test_duplicate_backup_is_not_overwritten(tmp_path):
    path = populated_db(tmp_path)
    with open_existing(path) as conn:
        backup_path = tmp_path / "already-there.db"
        backup_path.write_text("do not overwrite", encoding="utf-8")
        with pytest.raises(ValueError, match="already exists"):
            backup_database(conn, backup_path)
        assert backup_path.read_text(encoding="utf-8") == "do not overwrite"
