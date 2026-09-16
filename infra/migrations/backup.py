"""Safe online backup and restore utilities for SQLite service databases.

Uses SQLite's online backup API to take consistent point-in-time snapshots
even while WAL mode is active, with automatic integrity checking.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Optional

from libs.config.settings import get_platform_settings

logger = logging.getLogger(__name__)


def verify_database_integrity(db_path: Path) -> tuple[bool, str]:
    """Run SQLite PRAGMA integrity_check on the specified database file.

    Returns:
        (is_ok, message): True and 'ok' if healthy; False and error text if corrupted.
    """
    if not db_path.exists() or db_path.stat().st_size == 0:
        return True, "empty_or_new"

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        if len(rows) == 1 and rows[0][0] == "ok":
            return True, "ok"
        errors = "; ".join(str(r[0]) for r in rows)
        return False, errors
    except Exception as exc:
        return False, f"Integrity check failed with exception: {exc}"
    finally:
        conn.close()


def backup_database(
    service_name: str,
    db_path: Path,
    backup_dir: Optional[Path] = None,
    label: str = "pre_migration",
) -> Optional[Path]:
    """Take an atomic online backup snapshot of a service SQLite database.

    Args:
        service_name: Name of the service owning the database.
        db_path: Path to the SQLite database file.
        backup_dir: Target directory for backup files (defaults to settings.backups_dir / service_name).
        label: Tag included in the backup filename.

    Returns:
        Path to the created backup file, or None if the source database does not exist or is empty.
    """
    if not db_path.exists() or db_path.stat().st_size == 0:
        logger.debug("Database %s does not exist or is empty. Skipping backup.", db_path)
        return None

    target_dir = backup_dir or (get_platform_settings().backups_dir / service_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_filename = f"{service_name}_{timestamp}_{label}.db"
    backup_path = target_dir / backup_filename

    logger.info("Starting online backup of %s -> %s", db_path, backup_path)
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

    # Verify backup integrity immediately
    is_ok, msg = verify_database_integrity(backup_path)
    if not is_ok:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup created for {service_name} failed integrity check: {msg}")

    logger.info("Backup successfully completed and verified for %s at %s", service_name, backup_path)
    return backup_path


def restore_database(db_path: Path, backup_path: Path) -> None:
    """Safely restore a database file from a verified backup snapshot.

    Args:
        db_path: Target SQLite database file to restore to.
        backup_path: Source backup file to restore from.
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    is_ok, msg = verify_database_integrity(backup_path)
    if not is_ok:
        raise ValueError(f"Cannot restore from corrupted backup {backup_path}: {msg}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.warning("Restoring database %s from backup %s", db_path, backup_path)

    src_conn = sqlite3.connect(str(backup_path))
    dst_conn = sqlite3.connect(str(db_path))
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

    # Re-verify destination database integrity
    restored_ok, restored_msg = verify_database_integrity(db_path)
    if not restored_ok:
        raise RuntimeError(f"Database {db_path} corrupted after restore: {restored_msg}")

    logger.info("Database %s successfully restored from %s", db_path, backup_path)

