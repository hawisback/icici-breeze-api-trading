"""Shared Migration Runner for microservice SQLite databases.

Enforces:
- Service database isolation: each service migrates only its own SQLite database.
- Safe backup-before-migrate: creates an online backup before any schema upgrade.
- Automatic rollback: restores from backup if an upgrade fails.
- PRAGMA integrity_check: validates SQLite file integrity post-migration.
- Schema compatibility verification: fail-closed startup check.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any, Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from libs.config.settings import PlatformSettings, get_platform_settings
from infra.migrations.backup import (
    backup_database,
    restore_database,
    verify_database_integrity,
)
from infra.migrations.registry import (
    SERVICES,
    get_registered_services,
    get_service_info,
    resolve_service_db_path,
)

logger = logging.getLogger("infra.migrations")


class SchemaCompatibilityError(RuntimeError):
    """Raised when a service database schema does not match the required revision."""
    pass


def get_alembic_config(service_name: str, db_path: Path) -> Config:
    """Create a configured Alembic Config object for a specific service."""
    info = get_service_info(service_name)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    config = Config()
    config.set_main_option("script_location", str(info.migrations_dir))
    # SQLite URL format with forward slashes
    posix_path = db_path.resolve().as_posix()
    config.set_main_option("sqlalchemy.url", f"sqlite:///{posix_path}")
    return config


def get_service_current_revision(
    service_name: str,
    db_path: Optional[Path] = None,
    settings: Optional[PlatformSettings] = None,
) -> Optional[str]:
    """Inspect the alembic_version table in the service database."""
    resolved_path = resolve_service_db_path(service_name, settings, db_path)
    if not resolved_path.exists() or resolved_path.stat().st_size == 0:
        return None

    conn = sqlite3.connect(str(resolved_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version';"
        )
        if not cur.fetchone():
            return None

        cur.execute("SELECT version_num FROM alembic_version LIMIT 1;")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_service_head_revision(service_name: str) -> str:
    """Retrieve the latest head revision identifier defined in the service migrations."""
    info = get_service_info(service_name)
    config = Config()
    config.set_main_option("script_location", str(info.migrations_dir))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError(f"Service '{service_name}' has no migration revisions.")
    return head


def check_service_compatibility(
    service_name: str,
    db_path: Optional[Path] = None,
    settings: Optional[PlatformSettings] = None,
) -> dict[str, Any]:
    """Check whether the service database is compatible with the latest migration head."""
    resolved_path = resolve_service_db_path(service_name, settings, db_path)
    current_rev = get_service_current_revision(service_name, resolved_path, settings)
    head_rev = get_service_head_revision(service_name)
    is_compatible = current_rev == head_rev

    return {
        "service": service_name,
        "db_path": str(resolved_path),
        "current_revision": current_rev,
        "head_revision": head_rev,
        "is_compatible": is_compatible,
        "is_uninitialized": current_rev is None,
    }


def assert_service_compatibility(
    service_name: str,
    db_path: Optional[Path] = None,
    settings: Optional[PlatformSettings] = None,
) -> None:
    """Fail-closed assertion: raises SchemaCompatibilityError if DB is not at head."""
    status = check_service_compatibility(service_name, db_path, settings)
    if not status["is_compatible"]:
        raise SchemaCompatibilityError(
            f"Service '{service_name}' database at {status['db_path']} is not at head revision! "
            f"Current: {status['current_revision']!r}, Expected: {status['head_revision']!r}. "
            f"Run migrations before starting service or enable AUTO_MIGRATE_ON_STARTUP."
        )


def upgrade_service(
    service_name: str,
    db_path: Optional[Path] = None,
    target_revision: str = "head",
    backup: bool = True,
    settings: Optional[PlatformSettings] = None,
) -> str:
    """Apply versioned forward migrations to a service's isolated database.

    Args:
        service_name: The microservice name.
        db_path: Explicit DB path override (useful in tests).
        target_revision: Revision to upgrade to (default 'head').
        backup: If True and the database file exists, takes an online backup before migrating.
        settings: PlatformSettings instance.

    Returns:
        The resulting current revision.
    """
    resolved_path = resolve_service_db_path(service_name, settings, db_path)
    backup_file: Optional[Path] = None

    if backup and resolved_path.exists() and resolved_path.stat().st_size > 0:
        backup_file = backup_database(
            service_name=service_name,
            db_path=resolved_path,
            label="pre_upgrade",
        )

    cfg = get_alembic_config(service_name, resolved_path)

    try:
        logger.info(
            "Upgrading service '%s' (DB: %s) to revision '%s'",
            service_name,
            resolved_path,
            target_revision,
        )
        command.upgrade(cfg, target_revision)

        # Verify integrity immediately
        is_ok, msg = verify_database_integrity(resolved_path)
        if not is_ok:
            raise RuntimeError(f"Database integrity check failed post-upgrade: {msg}")

        current = get_service_current_revision(service_name, resolved_path, settings)
        logger.info(
            "Service '%s' successfully migrated to revision: %s",
            service_name,
            current,
        )
        return current or target_revision

    except Exception as exc:
        logger.error(
            "Migration upgrade failed for service '%s' on %s: %s",
            service_name,
            resolved_path,
            exc,
        )
        if backup_file and backup_file.exists():
            logger.warning("Rolling back service '%s' from backup %s", service_name, backup_file)
            try:
                restore_database(resolved_path, backup_file)
                logger.info("Automatic rollback succeeded for %s", service_name)
            except Exception as restore_err:
                logger.critical(
                    "Automatic rollback FAILED for %s: %s",
                    service_name,
                    restore_err,
                )
        raise


def downgrade_service(
    service_name: str,
    db_path: Optional[Path] = None,
    target_revision: str = "-1",
    backup: bool = True,
    settings: Optional[PlatformSettings] = None,
) -> Optional[str]:
    """Downgrade a service database to a previous revision."""
    resolved_path = resolve_service_db_path(service_name, settings, db_path)
    backup_file: Optional[Path] = None

    if backup and resolved_path.exists() and resolved_path.stat().st_size > 0:
        backup_file = backup_database(
            service_name=service_name,
            db_path=resolved_path,
            label="pre_downgrade",
        )

    cfg = get_alembic_config(service_name, resolved_path)

    try:
        logger.info(
            "Downgrading service '%s' (DB: %s) to revision '%s'",
            service_name,
            resolved_path,
            target_revision,
        )
        command.downgrade(cfg, target_revision)

        is_ok, msg = verify_database_integrity(resolved_path)
        if not is_ok:
            raise RuntimeError(f"Database integrity check failed post-downgrade: {msg}")

        current = get_service_current_revision(service_name, resolved_path, settings)
        return current

    except Exception as exc:
        logger.error("Downgrade failed for service '%s': %s", service_name, exc)
        if backup_file and backup_file.exists():
            restore_database(resolved_path, backup_file)
        raise


def upgrade_all_services(
    backup: bool = True,
    settings: Optional[PlatformSettings] = None,
) -> dict[str, str]:
    """Upgrade all registered services to their respective head revisions."""
    results: dict[str, str] = {}
    for service_name in get_registered_services():
        rev = upgrade_service(
            service_name=service_name,
            target_revision="head",
            backup=backup,
            settings=settings,
        )
        results[service_name] = rev
    return results


def check_all_services(
    settings: Optional[PlatformSettings] = None,
) -> dict[str, dict[str, Any]]:
    """Check migration compatibility for all registered services."""
    statuses: dict[str, dict[str, Any]] = {}
    for service_name in get_registered_services():
        statuses[service_name] = check_service_compatibility(service_name, settings=settings)
    return statuses


# ==============================================================================
# CLI Entrypoint
# ==============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m infra.migrations.runner",
        description="Unified database migration runner for isolated SQLite microservices.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Upgrade command
    up_parser = subparsers.add_parser("upgrade", help="Apply migrations up to target revision.")
    up_parser.add_argument(
        "--service",
        "-s",
        choices=get_registered_services(),
        help="Target a specific service (default: all services).",
    )
    up_parser.add_argument(
        "--revision",
        "-r",
        default="head",
        help="Target revision to upgrade to (default: 'head').",
    )
    up_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip automatic pre-migration backup.",
    )

    # Downgrade command
    down_parser = subparsers.add_parser("downgrade", help="Revert migrations.")
    down_parser.add_argument(
        "--service",
        "-s",
        required=True,
        choices=get_registered_services(),
        help="Service to downgrade.",
    )
    down_parser.add_argument(
        "--revision",
        "-r",
        default="-1",
        help="Target revision to downgrade to (default: '-1').",
    )
    down_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip automatic pre-migration backup.",
    )

    # Status / Check command
    status_parser = subparsers.add_parser("status", help="Show migration status across services.")
    status_parser.add_argument(
        "--service",
        "-s",
        choices=get_registered_services(),
        help="Filter status to a specific service.",
    )

    check_parser = subparsers.add_parser(
        "check", help="Fail-closed check asserting all services are at head."
    )
    check_parser.add_argument(
        "--service",
        "-s",
        choices=get_registered_services(),
        help="Filter check to a specific service.",
    )

    # Backup command
    backup_parser = subparsers.add_parser("backup", help="Create an online backup of a service DB.")
    backup_parser.add_argument(
        "--service",
        "-s",
        required=True,
        choices=get_registered_services(),
        help="Service to backup.",
    )

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore a service DB from backup.")
    restore_parser.add_argument(
        "--service",
        "-s",
        required=True,
        choices=get_registered_services(),
        help="Service to restore.",
    )
    restore_parser.add_argument(
        "--backup-file",
        required=True,
        type=Path,
        help="Path to the backup database file.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.command == "upgrade":
        services_to_run = [args.service] if args.service else get_registered_services()
        print(f"Applying upgrade (revision: {args.revision}) for: {', '.join(services_to_run)}")
        for svc in services_to_run:
            rev = upgrade_service(
                service_name=svc,
                target_revision=args.revision,
                backup=not args.no_backup,
            )
            print(f"  [OK] {svc:<16} -> revision {rev}")
        print("Upgrade complete.")

    elif args.command == "downgrade":
        rev = downgrade_service(
            service_name=args.service,
            target_revision=args.revision,
            backup=not args.no_backup,
        )
        print(f"  [OK] {args.service:<16} downgraded to revision: {rev or 'base'}")

    elif args.command == "status":
        services_to_check = [args.service] if args.service else get_registered_services()
        print(f"{'SERVICE':<16} {'CURRENT':<10} {'HEAD':<10} {'STATUS':<15} {'DATABASE PATH'}")
        print("-" * 80)
        for svc in services_to_check:
            st = check_service_compatibility(svc)
            cur = st["current_revision"] or "None"
            head = st["head_revision"]
            status_label = "UP TO DATE" if st["is_compatible"] else "PENDING"
            print(f"{svc:<16} {cur:<10} {head:<10} {status_label:<15} {st['db_path']}")

    elif args.command == "check":
        services_to_check = [args.service] if args.service else get_registered_services()
        failed = []
        for svc in services_to_check:
            st = check_service_compatibility(svc)
            if not st["is_compatible"]:
                failed.append((svc, st["current_revision"], st["head_revision"]))
        if failed:
            print("SCHEMA CHECK FAILED: Following services are not at head:")
            for svc, cur, head in failed:
                print(f"  - {svc}: current={cur!r}, head={head!r}")
            sys.exit(1)
        else:
            print("SCHEMA CHECK PASSED: All checked services are at head revision.")

    elif args.command == "backup":
        info = get_service_info(args.service)
        db_path = info.get_db_path()
        backup_path = backup_database(args.service, db_path, label="manual")
        if backup_path:
            print(f"Backup created: {backup_path}")
        else:
            print(f"Database {db_path} does not exist or is empty. No backup created.")

    elif args.command == "restore":
        info = get_service_info(args.service)
        db_path = info.get_db_path()
        restore_database(db_path, args.backup_file)
        print(f"Database {db_path} restored from {args.backup_file}")


if __name__ == "__main__":
    main()
