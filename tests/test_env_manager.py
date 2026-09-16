"""Tests for safe .env management and runtime environment synchronization.
"""

from pathlib import Path

from libs.config import get_platform_settings, update_env_variable


def test_update_existing_env_variable(tmp_path: Path):
    env_file = tmp_path / ".env"
    initial_content = """# Header Comment
APP_ENV=development
BREEZE_API_KEY=test_key_123
BREEZE_SESSION_TOKEN=old_token_value
LOG_LEVEL=INFO
"""
    env_file.write_text(initial_content, encoding="utf-8")

    result = update_env_variable(
        key="BREEZE_SESSION_TOKEN",
        value="new_token_value_999",
        env_path=env_file,
        create_backup=True,
    )

    assert result is True
    updated_content = env_file.read_text(encoding="utf-8")
    assert "BREEZE_SESSION_TOKEN=new_token_value_999" in updated_content
    assert "# Header Comment" in updated_content
    assert "BREEZE_API_KEY=test_key_123" in updated_content
    assert "LOG_LEVEL=INFO" in updated_content
    assert "old_token_value" not in updated_content

    # Backup file should exist
    backup_file = env_file.with_suffix(".env.bak")
    assert backup_file.exists()
    assert "old_token_value" in backup_file.read_text(encoding="utf-8")


def test_append_new_env_variable(tmp_path: Path):
    env_file = tmp_path / ".env"
    initial_content = "APP_ENV=development\n"
    env_file.write_text(initial_content, encoding="utf-8")

    result = update_env_variable(
        key="BREEZE_SESSION_TOKEN",
        value="first_session_token",
        env_path=env_file,
        create_backup=False,
    )

    assert result is True
    updated_content = env_file.read_text(encoding="utf-8")
    assert "APP_ENV=development" in updated_content
    assert "BREEZE_SESSION_TOKEN=first_session_token" in updated_content


def test_runtime_settings_sync(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\n", encoding="utf-8")

    token_val = "session_sync_test_token"
    update_env_variable(
        key="BREEZE_SESSION_TOKEN",
        value=token_val,
        env_path=env_file,
        create_backup=False,
    )

    settings = get_platform_settings()
    assert settings.breeze_session_token is not None
    assert settings.breeze_session_token.get_secret_value() == token_val

