#!/usr/bin/env python3
"""Back up and clear only local OMS PAPER orders, never LIVE orders.

Run from repository root while the trading backend and strategy workers are
STOPPED. Dry-run is the default; use --apply --backend-stopped for execution.

This deliberately does NOT reset risk.db system mode, LIVE broker state,
strategy trades, or the separate AI PAPER trade journal.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def open_existing(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise ValueError(f"OMS database does not exist: {db_path}")
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def paper_plan(conn: sqlite3.Connection) -> dict:
    """Build a bounded PAPER-only deletion plan; refuse inconsistent modes."""
    for name in ("broker_orders", "order_intents", "order_events", "outbox_events", "processed_events"):
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is None:
            raise ValueError(f"OMS schema missing table {name}; database was not modified")

    mismatches = conn.execute("""
        SELECT b.order_id, b.trading_mode AS order_mode, i.trading_mode AS intent_mode
        FROM broker_orders b LEFT JOIN order_intents i ON b.intent_id=i.intent_id
        WHERE i.intent_id IS NULL OR b.trading_mode != i.trading_mode
    """).fetchall()
    if mismatches:
        raise ValueError(
            f"Found {len(mismatches)} orphan/mismatched OMS order-intent pairs; "
            "cannot reset safely without manual reconciliation"
        )

    orders = [dict(r) for r in conn.execute("""
        SELECT order_id, intent_id, status, created_at, filled_quantity, broker_order_id
        FROM broker_orders WHERE trading_mode='PAPER' ORDER BY created_at, order_id
    """)]
    order_ids = {r["order_id"] for r in orders}
    intent_ids = {
        r["intent_id"] for r in conn.execute(
            "SELECT intent_id FROM order_intents WHERE trading_mode='PAPER'"
        )
    }

    # The OMS outbox contains serialized ORDER_INTENT / ORDER_STATE payloads.
    # Prevent replaying obsolete PAPER intents after the worker restarts.
    # Published and unpublished matching entries are archived by the backup.
    outbox_ids: set[str] = set()
    for event in conn.execute("SELECT event_id, payload FROM outbox_events"):
        try:
            payload = json.loads(event["payload"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Unparseable OMS outbox payload {event['event_id']}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid OMS outbox payload {event['event_id']}")
        associated = (
            payload.get("order_id") in order_ids
            or payload.get("intent_id") in intent_ids
        )
        if associated and payload.get("trading_mode") not in (None, "PAPER"):
            raise ValueError(
                f"Outbox event {event['event_id']} references PAPER rows with a non-PAPER mode"
            )
        if payload.get("trading_mode") == "PAPER" or associated:
            outbox_ids.add(event["event_id"])

    return {
        "orders": orders,
        "order_ids": order_ids,
        "intent_ids": intent_ids,
        "outbox_ids": outbox_ids,
        "status_counts": dict(Counter(r["status"] for r in orders)),
    }


def backup_database(conn: sqlite3.Connection, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f"Backup destination already exists: {destination}")
    try:
        with sqlite3.connect(destination) as backup:
            conn.backup(backup)
            check = backup.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise ValueError(f"OMS backup integrity check failed: {check}")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def remove_paper_rows(conn: sqlite3.Connection, plan: dict) -> dict[str, int]:
    """Delete child events first, then PAPER orders and intents, in one transaction."""
    def delete_ids(table: str, field: str, values: set[str]) -> int:
        if not values:
            return 0
        keys = sorted(values)
        deleted = 0
        # Keep below SQLite's conservative variable-binding limit.
        for start in range(0, len(keys), 400):
            batch = keys[start:start + 400]
            marks = ",".join("?" for _ in batch)
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {field} IN ({marks})",
                batch,
            )
            deleted += cursor.rowcount
        return deleted

    conn.execute("BEGIN IMMEDIATE")
    try:
        # Detect an accidental concurrent change even if the operator ignored
        # the --backend-stopped instruction.
        current = paper_plan(conn)
        if (
            current["order_ids"] != plan["order_ids"]
            or current["intent_ids"] != plan["intent_ids"]
            or current["outbox_ids"] != plan["outbox_ids"]
        ):
            raise ValueError("OMS PAPER records changed during reset; stop backend and retry")
        counts = {
            "order_events": delete_ids("order_events", "order_id", plan["order_ids"]),
            "processed_events": delete_ids("processed_events", "event_id", plan["outbox_ids"]),
            "outbox_events": delete_ids("outbox_events", "event_id", plan["outbox_ids"]),
            "broker_orders": delete_ids("broker_orders", "order_id", plan["order_ids"]),
            "order_intents": delete_ids("order_intents", "intent_id", plan["intent_ids"]),
        }
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("OMS foreign-key check failed after PAPER cleanup")
        # Defend against deleting any LIVE order or crossing an intent boundary.
        if conn.execute("SELECT 1 FROM broker_orders WHERE trading_mode='PAPER' LIMIT 1").fetchone():
            raise ValueError("PAPER orders remain unexpectedly")
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/oms/oms.db"))
    parser.add_argument("--backup-dir", type=Path, default=Path("data/backups"))
    parser.add_argument("--apply", action="store_true", help="Apply PAPER-only cleanup after backup")
    parser.add_argument(
        "--backend-stopped", action="store_true",
        help="Confirm you stopped ALL trading backend/strategy processes",
    )
    args = parser.parse_args()

    if args.apply and not args.backend_stopped:
        parser.error("--apply requires --backend-stopped (stop services before reset)")
    with open_existing(args.db) as conn:
        plan = paper_plan(conn)
        print(f"OMS database: {args.db.resolve()}")
        print(f"PAPER orders: {len(plan['orders'])}")
        print(f"PAPER order statuses: {json.dumps(plan['status_counts'], sort_keys=True)}")
        print(f"PAPER intents: {len(plan['intent_ids'])}")
        print(f"PAPER outbox events: {len(plan['outbox_ids'])}")
        print(f"LIVE/SHADOW orders: {conn.execute('SELECT count(*) FROM broker_orders WHERE trading_mode != ?', ('PAPER',)).fetchone()[0]} (untouched)")
        if not args.apply:
            print("DRY RUN only. Stop the backend, then run with --apply --backend-stopped.")
            return 0
        if not (plan["order_ids"] or plan["intent_ids"] or plan["outbox_ids"]):
            print("No PAPER OMS records to clear.")
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = args.backup_dir / f"oms-before-paper-reset-{stamp}.sqlite3"
        backup_database(conn, backup_path)
        print(f"Verified complete OMS backup: {backup_path.resolve()}")
        counts = remove_paper_rows(conn, plan)
        print(f"Deleted PAPER-only OMS rows: {json.dumps(counts, sort_keys=True)}")
        print("LIVE/SHADOW orders, risk mode, broker accounts, AI journal and strategy ledger are unchanged.")
        print("Restart backend and verify GET /api/v1/ai/account/context and GET /api/v1/risk/status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
