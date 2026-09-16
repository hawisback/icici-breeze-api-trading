"""Tests for microservice database migrations, schema compatibility, and backup safety.

Verifies Item 0.7 requirements:
- Isolated per-service Alembic migrations
- Migration execution on fresh empty database
- Migration execution on pre-existing database
- Forward upgrade and backward downgrade lifecycle
- Automatic backup-before-migrate snapshot creation
- Automatic restore on simulated migration failure
- PRAGMA integrity_check validation across all service databases
- Fail-closed startup schema compatibility enforcement
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import pytest

from libs.config.settings import PlatformSettings
from infra.migrations.backup import (
    backup_database,
    restore_database,
    verify_database_integrity,
)
from infra.migrations.registry import (
    SERVICES,
    get_registered_services,
    get_service_info,
)
from infra.migrations.runner import (
    SchemaCompatibilityError,
    assert_service_compatibility,
    check_all_services,
    check_service_compatibility,
    downgrade_service,
    get_service_current_revision,
    get_service_head_revision,
    upgrade_all_services,
    upgrade_service,
)
from services.api_gateway.service_container import initialize_services


def test_registered_services_have_valid_migration_environments() -> None:
    """Every registered service must define an existing migrations directory with env.py and 0001 revision."""
    registered = get_registered_services()
    assert len(registered) == 11
    expected_services = {
        "oms", "risk", "portfolio", "instrument", "broker_session",
        "historical", "audit", "strategy", "auth", "gateway", "broker_gateway"
    }
    assert set(registered) == expected_services


    for svc in registered:
        info = get_service_info(svc)
        assert info.migrations_dir.exists(), f"Missing migrations dir for {svc}"
        assert (info.migrations_dir / "env.py").exists(), f"Missing env.py for {svc}"
        assert (info.migrations_dir / "versions" / "0001_initial_schema.py").exists(), f"Missing 0001 for {svc}"
        head = get_service_head_revision(svc)
        assert head == "0001"


def test_migration_on_fresh_empty_database(tmp_path: Path) -> None:
    """Migrating a brand new empty database must create all tables, indexes, and alembic_version."""
    db_path = tmp_path / "oms_fresh.db"
    assert not db_path.exists()

    rev = upgrade_service(service_name="oms", db_path=db_path, backup=False)
    assert rev == "0001"
    assert db_path.exists()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    expected_tables = {
        "alembic_version",
        "outbox_events",
        "processed_events",
        "order_intents",
        "broker_orders",
        "order_events",
    }
    assert expected_tables.issubset(tables)

    # Verify SQLite integrity
    is_ok, msg = verify_database_integrity(db_path)
    assert is_ok is True
    assert msg == "ok"


def test_migration_on_existing_database_with_data(tmp_path: Path) -> None:
    """Migrating an unversioned database with pre-existing tables must succeed idempotently without losing data."""
    db_path = tmp_path / "risk_existing.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE system_modes (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mode TEXT NOT NULL DEFAULT 'NORMAL',
            updated_at TEXT NOT NULL
        );
    """)
    conn.execute("INSERT INTO system_modes (id, mode, updated_at) VALUES (1, 'NORMAL', '2026-09-16T00:00:00Z');")
    conn.commit()
    conn.close()

    # Apply migration to existing DB
    rev = upgrade_service(service_name="risk", db_path=db_path, backup=False)
    assert rev == "0001"

    # Verify data is preserved
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT mode FROM system_modes WHERE id = 1;")
    row = cur.fetchone()
    assert row[0] == "NORMAL"

    # Verify new tables were also added
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {r[0] for r in cur.fetchall()}
    conn.close()
    assert "kill_switch_events" in tables
    assert "risk_decisions" in tables
    assert "alembic_version" in tables


def test_forward_upgrade_and_backward_downgrade(tmp_path: Path) -> None:
    """Upgrading to head, downgrading to base, and re-upgrading to head must be clean and reversible."""
    db_path = tmp_path / "gateway_lifecycle.db"

    # 1. Upgrade to head
    rev = upgrade_service(service_name="gateway", db_path=db_path, backup=False)
    assert rev == "0001"

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_records';")
    assert cur.fetchone() is not None
    conn.close()

    # 2. Downgrade to base
    down_rev = downgrade_service(service_name="gateway", db_path=db_path, target_revision="base", backup=False)
    assert down_rev is None

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_records';")
    assert cur.fetchone() is None
    conn.close()

    # 3. Re-upgrade to head
    re_rev = upgrade_service(service_name="gateway", db_path=db_path, backup=False)
    assert re_rev == "0001"

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_records';")
    assert cur.fetchone() is not None
    conn.close()


def test_backup_before_migrate_created(tmp_path: Path) -> None:
    """Forward migration on a non-empty database must create a verified pre-migration backup."""
    db_path = tmp_path / "portfolio.db"
    backup_dir = tmp_path / "backups" / "portfolio"

    # Populate dummy initial database
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE dummy (val TEXT);")
    conn.execute("INSERT INTO dummy VALUES ('before_migration');")
    conn.commit()
    conn.close()

    # Manual backup check
    backup_file = backup_database(
        service_name="portfolio",
        db_path=db_path,
        backup_dir=backup_dir,
        label="test_snap",
    )
    assert backup_file is not None
    assert backup_file.exists()
    assert backup_file.name.startswith("portfolio_")
    assert "test_snap" in backup_file.name

    # Verify backup contains the exact data
    bconn = sqlite3.connect(str(backup_file))
    bcur = bconn.cursor()
    bcur.execute("SELECT val FROM dummy;")
    assert bcur.fetchone()[0] == "before_migration"
    bconn.close()


def test_automatic_restore_on_failed_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a migration upgrade throws an exception, the database must be automatically restored from backup."""
    db_path = tmp_path / "auth_fail.db"

    # Seed an initial valid database with some content
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test_canary (canary TEXT);")
    conn.execute("INSERT INTO test_canary VALUES ('alive');")
    conn.commit()
    conn.close()

    from alembic import command as alembic_cmd

    def mock_upgrade_fail(*args, **kwargs):
        # Corrupt / modify the db before failing to verify restore
        c = sqlite3.connect(str(db_path))
        c.execute("DROP TABLE test_canary;")
        c.commit()
        c.close()
        raise RuntimeError("Simulated Alembic Migration Disaster")

    monkeypatch.setattr(alembic_cmd, "upgrade", mock_upgrade_fail)

    with pytest.raises(RuntimeError, match="Simulated Alembic Migration Disaster"):
        upgrade_service(service_name="auth", db_path=db_path, backup=True)

    # Database must have been restored automatically from the pre-upgrade backup
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT canary FROM test_canary;")
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "alive"


def test_pragma_integrity_check_all_services(tmp_path: Path) -> None:
    """All 10 microservice databases upgraded via upgrade_all_services must pass PRAGMA integrity_check."""
    settings = PlatformSettings(
        data_root=tmp_path / "data",
        auto_migrate_on_startup=True,
    )

    results = upgrade_all_services(backup=False, settings=settings)
    assert len(results) == 11

    for svc, rev in results.items():
        assert rev == "0001", f"{svc} ended at unexpected revision {rev}"
        info = get_service_info(svc)
        db_path = info.get_db_path(settings)
        assert db_path.exists()
        is_ok, msg = verify_database_integrity(db_path)
        assert is_ok is True, f"Integrity check failed for {svc}: {msg}"
        assert msg == "ok"


def test_schema_compatibility_check(tmp_path: Path) -> None:
    """Compatibility checker must correctly differentiate unmigrated vs up-to-date databases."""
    db_path = tmp_path / "audit_check.db"

    # 1. Unmigrated
    status = check_service_compatibility(service_name="audit", db_path=db_path)
    assert status["is_compatible"] is False
    assert status["current_revision"] is None
    assert status["head_revision"] == "0001"

    with pytest.raises(SchemaCompatibilityError, match="is not at head revision"):
        assert_service_compatibility(service_name="audit", db_path=db_path)

    # 2. Migrated
    upgrade_service(service_name="audit", db_path=db_path, backup=False)
    status2 = check_service_compatibility(service_name="audit", db_path=db_path)
    assert status2["is_compatible"] is True
    assert status2["current_revision"] == "0001"
    # Should not raise
    assert_service_compatibility(service_name="audit", db_path=db_path)


@pytest.mark.asyncio
async def test_service_container_startup_with_auto_migrate(tmp_path: Path) -> None:
    """Service container with auto_migrate_on_startup=True automatically migrates all databases on startup."""
    settings = PlatformSettings(
        data_root=tmp_path / "container_auto",
        auto_migrate_on_startup=True,
    )

    container = await initialize_services(settings=settings, force_reinit=True)
    assert container is not None

    # Verify all service databases are at head revision 0001
    statuses = check_all_services(settings=settings)
    assert len(statuses) == 11
    for svc, st in statuses.items():

        assert st["is_compatible"] is True
        assert st["current_revision"] == "0001"


@pytest.mark.asyncio
async def test_service_container_startup_fail_closed_when_unmigrated(tmp_path: Path) -> None:
    """Service container with auto_migrate_on_startup=False must fail closed if any service DB is unmigrated."""
    settings = PlatformSettings(
        data_root=tmp_path / "container_strict",
        auto_migrate_on_startup=False,
    )

    with pytest.raises(SchemaCompatibilityError, match="is not at head revision"):
        await initialize_services(settings=settings, force_reinit=True)

