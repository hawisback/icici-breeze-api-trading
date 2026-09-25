"""Rotate known development bootstrap passwords before enabling LIVE trading.

Run from the repository root:

    python scripts/rotate_live_auth_passwords.py

The script only changes active users whose current password still matches one
of the known development bootstrap passwords. Passwords are prompted with
getpass and never accepted on the command line.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.auth.security import hash_password, verify_password  # noqa: E402


KNOWN_DEFAULTS = {
    "admin": "Admin@Trading123!",
    "operator": "Operator@Trading123!",
    "trader": "Trader@Trading123!",
    "viewer": "Viewer@Trading123!",
}


def _compromised_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT username, password_hash, salt
        FROM users
        WHERE is_active = 1
        ORDER BY username
        """
    ).fetchall()
    return [
        row
        for row in rows
        if row["username"] in KNOWN_DEFAULTS
        and verify_password(
            KNOWN_DEFAULTS[row["username"]],
            row["password_hash"],
            row["salt"],
        )
    ]


def _prompt_password(username: str) -> str:
    while True:
        password = getpass(
            f"New password for {username} (minimum 12 characters): "
        )
        if len(password) < 12:
            print("Password must contain at least 12 characters.")
            continue
        if password == KNOWN_DEFAULTS.get(username):
            print("Password must not be the development bootstrap password.")
            continue
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords do not match.")
            continue
        return password


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rotate active auth users that still use known development "
            "bootstrap passwords."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/auth/auth.db"),
        help="Path to auth SQLite database (default: data/auth/auth.db)",
    )
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.exists():
        print(f"Auth database not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        compromised = _compromised_rows(conn)
        if not compromised:
            print("No active users are using known development bootstrap passwords.")
            return 0

        print(
            "The following active users must be rotated before LIVE startup: "
            + ", ".join(row["username"] for row in compromised)
        )

        for row in compromised:
            username = row["username"]
            new_password = _prompt_password(username)
            password_hash, salt = hash_password(new_password)
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, salt = ?, updated_at = ?
                WHERE username = ? AND is_active = 1
                """,
                (
                    password_hash,
                    salt,
                    datetime.now(timezone.utc).isoformat(),
                    username,
                ),
            )
        conn.commit()

        remaining = _compromised_rows(conn)
        if remaining:
            print(
                "Rotation incomplete; known defaults remain active for: "
                + ", ".join(row["username"] for row in remaining),
                file=sys.stderr,
            )
            return 1

        print(
            "Password rotation complete. No known development bootstrap "
            "passwords remain active."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
