"""Isolated SQLite repository for Auth Service managing users, credentials, tokens, and WS tickets.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Optional

from libs.config.settings import AppEnv, PlatformSettings, get_platform_settings
from libs.contracts.models import UserRole, generate_id, utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine
from services.auth.security import hash_password

logger = logging.getLogger(__name__)


class AuthRepository:
    """Repository managing credentials, refresh tokens, and WebSocket single-use tickets."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        settings: Optional[PlatformSettings] = None,
    ) -> None:
        self.settings = settings or get_platform_settings()
        self.db_path = db_path or self.settings.auth_db_path
        self.engine = SQLiteEngine(SQLiteConfig(db_path=self.db_path, synchronous="FULL"))

    async def initialize(self) -> None:
        """Initialize database tables and indexes."""
        await self.engine.initialize()

        async with self.engine.connect() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);"
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_refresh_token_hash ON refresh_tokens(token_hash);"
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ws_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    ticket_hash TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ws_ticket_hash ON ws_tickets(ticket_hash);"
            )
            await conn.commit()

        logger.info("Initialized auth.db schema.")
        await self.seed_default_users_if_empty()

    async def seed_default_users_if_empty(self) -> None:
        """Seed default roles if the users table is empty."""
        async with self.engine.connect() as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM users;")
            row = await cursor.fetchone()
            count = row[0] if row else 0

        if count > 0:
            return

        if self.settings.app_env == AppEnv.PRODUCTION:
            raise RuntimeError(
                "Production auth.db has no users. Predictable bootstrap credentials are disabled; "
                "provision production users before starting the live platform."
            )

        logger.info("Seeding default bootstrap users into auth.db for non-production use only...")
        defaults = [
            ("admin", "Admin@Trading123!", UserRole.ADMIN),
            ("operator", "Operator@Trading123!", UserRole.OPERATOR),
            ("trader", "Trader@Trading123!", UserRole.TRADER),
            ("viewer", "Viewer@Trading123!", UserRole.READ_ONLY),
        ]

        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            for username, password, role in defaults:
                pw_hash, salt = hash_password(password)
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO users (
                        user_id, username, password_hash, salt, role, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?);
                    """,
                    (generate_id(), username, pw_hash, salt, role.value, now, now),
                )
            await conn.commit()
        logger.info("Successfully seeded default users: admin, operator, trader, viewer.")

    async def create_user(
        self,
        username: str,
        password_hash: str,
        salt: str,
        role: UserRole,
        is_active: bool = True,
    ) -> dict[str, Any]:
        user_id = generate_id()
        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO users (
                    user_id, username, password_hash, salt, role, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    user_id,
                    username.strip().lower(),
                    password_hash,
                    salt,
                    role.value,
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
            await conn.commit()
        return {
            "user_id": user_id,
            "username": username.strip().lower(),
            "role": role.value,
            "is_active": is_active,
            "created_at": now,
        }

    async def get_user_by_username(self, username: str) -> Optional[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT user_id, username, password_hash, salt, role, is_active, created_at, updated_at
                FROM users WHERE username = ?;
                """,
                (username.strip().lower(),),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "user_id": row[0],
                "username": row[1],
                "password_hash": row[2],
                "salt": row[3],
                "role": row[4],
                "is_active": bool(row[5]),
                "created_at": row[6],
                "updated_at": row[7],
            }

    async def get_user_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT user_id, username, password_hash, salt, role, is_active, created_at, updated_at
                FROM users WHERE user_id = ?;
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "user_id": row[0],
                "username": row[1],
                "password_hash": row[2],
                "salt": row[3],
                "role": row[4],
                "is_active": bool(row[5]),
                "created_at": row[6],
                "updated_at": row[7],
            }

    async def store_refresh_token(
        self,
        token_id: str,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO refresh_tokens (
                    token_id, user_id, token_hash, expires_at, revoked_at, created_at
                ) VALUES (?, ?, ?, ?, NULL, ?);
                """,
                (token_id, user_id, token_hash, expires_at.isoformat(), now),
            )
            await conn.commit()

    async def get_active_refresh_token(self, token_hash: str) -> Optional[dict[str, Any]]:
        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT token_id, user_id, token_hash, expires_at, revoked_at
                FROM refresh_tokens
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?;
                """,
                (token_hash, now),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "token_id": row[0],
                "user_id": row[1],
                "token_hash": row[2],
                "expires_at": row[3],
                "revoked_at": row[4],
            }

    async def revoke_refresh_token(self, token_hash: str) -> bool:
        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                UPDATE refresh_tokens
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL;
                """,
                (now, token_hash),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def store_ws_ticket(
        self,
        ticket_id: str,
        ticket_hash: str,
        user_id: str,
        role: str,
        expires_at: datetime,
    ) -> None:
        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO ws_tickets (
                    ticket_id, ticket_hash, user_id, role, expires_at, used_at, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?);
                """,
                (ticket_id, ticket_hash, user_id, role, expires_at.isoformat(), now),
            )
            await conn.commit()

    async def consume_ws_ticket(self, ticket_hash: str) -> Optional[dict[str, Any]]:
        """Atomically claim and mark a single-use WebSocket ticket."""
        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT ticket_id, user_id, role, expires_at, used_at
                FROM ws_tickets
                WHERE ticket_hash = ?;
                """,
                (ticket_hash,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            ticket_id, user_id, role, expires_at, used_at = row
            # Verify not used and not expired
            if used_at is not None or expires_at <= now:
                return None

            # Mark consumed
            await conn.execute(
                "UPDATE ws_tickets SET used_at = ? WHERE ticket_id = ?;",
                (now, ticket_id),
            )
            await conn.commit()

            return {
                "ticket_id": ticket_id,
                "user_id": user_id,
                "role": role,
            }

