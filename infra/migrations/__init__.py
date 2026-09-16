"""Database Migration Framework for Microservice SQLite Databases.
"""

from infra.migrations.backup import (
    backup_database,
    restore_database,
    verify_database_integrity,
)
from infra.migrations.registry import (
    SERVICES,
    ServiceMigrationInfo,
    get_registered_services,
    get_service_info,
    resolve_service_db_path,
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

__all__ = [
    "SERVICES",
    "SchemaCompatibilityError",
    "ServiceMigrationInfo",
    "assert_service_compatibility",
    "backup_database",
    "check_all_services",
    "check_service_compatibility",
    "downgrade_service",
    "get_registered_services",
    "get_service_current_revision",
    "get_service_head_revision",
    "get_service_info",
    "resolve_service_db_path",
    "restore_database",
    "upgrade_all_services",
    "upgrade_service",
    "verify_database_integrity",
]

