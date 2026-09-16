"""Safe environment file management and runtime configuration synchronization.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
from typing import Optional, Union

from pydantic import SecretStr

from libs.config.settings import get_platform_settings

logger = logging.getLogger(__name__)


def update_env_variable(
    key: str,
    value: str,
    env_path: Union[str, Path] = ".env",
    create_backup: bool = True,
) -> bool:
    """Safely updates or appends a key-value pair in a .env file.

    Preserves surrounding comments, whitespace, and untouched configuration keys.
    Also synchronizes `os.environ` and updates the active runtime `PlatformSettings` singleton.

    Args:
        key: Environment variable name (e.g. 'BREEZE_SESSION_TOKEN')
        value: New string value to set
        env_path: Path to the target .env file
        create_backup: If True, saves a backup copy of .env to .env.bak before modifying

    Returns:
        bool: True if the file and runtime were successfully updated.
    """
    target_path = Path(env_path).resolve()
    key_clean = key.strip()
    val_clean = value.strip()

    # Read existing content if file exists
    if target_path.exists():
        if create_backup:
            try:
                backup_path = target_path.with_suffix(".env.bak")
                shutil.copyfile(target_path, backup_path)
            except Exception as exc:
                logger.warning("Failed to create .env backup at %s: %s", target_path, exc)

        try:
            content = target_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to read %s: %s", target_path, exc)
            return False
    else:
        content = ""

    pattern = re.compile(rf"^[ \t]*{re.escape(key_clean)}[ \t]*=.*$", flags=re.MULTILINE)

    new_line = f"{key_clean}={val_clean}"

    if pattern.search(content):
        # In-place replace
        updated_content = pattern.sub(new_line, content)
    else:
        # Append with newline guarantee
        if content and not content.endswith("\n"):
            content += "\n"
        updated_content = content + f"{new_line}\n"

    try:
        target_path.write_text(updated_content, encoding="utf-8")
        logger.info("Successfully persisted %s to %s", key_clean, target_path)
    except Exception as exc:
        logger.error("Failed to write updated .env to %s: %s", target_path, exc)
        return False

    # Synchronize process environment
    os.environ[key_clean] = val_clean

    # Synchronize singleton PlatformSettings
    try:
        settings = get_platform_settings()
        if key_clean == "BREEZE_SESSION_TOKEN":
            settings.breeze_session_token = SecretStr(val_clean)
        elif key_clean == "BREEZE_API_KEY":
            settings.breeze_api_key = val_clean
        elif key_clean == "BREEZE_SECRET_KEY":
            settings.breeze_secret_key = SecretStr(val_clean)
        elif hasattr(settings, key_clean.lower()):
            setattr(settings, key_clean.lower(), val_clean)
    except Exception as exc:
        logger.warning("Could not synchronize runtime PlatformSettings for %s: %s", key_clean, exc)

    return True

