"""Tests for ICICI Breeze OAuth callback, login-url endpoint, and session token persistence.
"""

from pathlib import Path
import pytest
import httpx
from pydantic import SecretStr

from libs.config.settings import PlatformSettings, set_platform_settings
from libs.contracts.models import TradingMode
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services


@pytest.mark.asyncio
async def test_session_login_url_endpoint(tmp_path: Path, monkeypatch):
    test_key = "TEST_API_KEY_BREEZE_XYZ"
    test_env = tmp_path / ".env"
    test_env.write_text(f"APP_ENV=development\nBREEZE_API_KEY={test_key}\n", encoding="utf-8")
    monkeypatch.setenv("BREEZE_API_KEY", test_key)

    settings = PlatformSettings(
        data_root=tmp_path / "data",
        breeze_api_key=test_key,
        default_trading_mode=TradingMode.PAPER,
    )
    set_platform_settings(settings)
    await initialize_services(settings=settings, force_reinit=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/broker/session/login-url")
        assert res.status_code == 200
        data = res.json()
        assert "login_url" in data
        assert f"api_key={test_key}" in data["login_url"]
        assert "redirect_url_hint" in data


@pytest.mark.asyncio
async def test_oauth_callback_missing_token(tmp_path: Path):
    settings = PlatformSettings(
        data_root=tmp_path / "data",
        default_trading_mode=TradingMode.PAPER,
    )
    set_platform_settings(settings)
    await initialize_services(settings=settings, force_reinit=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # JSON request
        res_json = await client.get(
            "/api/v1/broker/session/callback",
            headers={"Accept": "application/json"},
        )
        assert res_json.status_code == 400
        assert res_json.json()["status"] == "ERROR"

        # HTML request
        res_html = await client.get("/api/v1/broker/session/callback")
        assert res_html.status_code == 400
        assert "Authentication Incomplete" in res_html.text


@pytest.mark.asyncio
async def test_oauth_callback_success_json_and_env_update(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\nBREEZE_API_KEY=my_key\n", encoding="utf-8")

    # Point env manager to this test env_file
    import libs.config.env_manager as em
    orig_update = em.update_env_variable

    def mocked_update(key, value, env_path=".env", create_backup=True):
        return orig_update(key, value, env_path=env_file, create_backup=False)

    monkeypatch.setattr("services.api_gateway.main.update_env_variable", mocked_update)

    settings = PlatformSettings(
        data_root=tmp_path / "data",
        breeze_api_key="my_key",
        breeze_secret_key=SecretStr("my_secret"),
        default_trading_mode=TradingMode.PAPER,
    )
    set_platform_settings(settings)
    await initialize_services(settings=settings, force_reinit=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        callback_token = "2948104857201948"
        res = await client.get(
            f"/api/v1/broker/session/callback?apisession={callback_token}",
            headers={"Accept": "application/json"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SUCCESS"
        assert data["token_masked"] == "2948...1948"
        assert data["env_updated"] is True

        # Verify .env file updated on disk
        env_content = env_file.read_text(encoding="utf-8")
        assert f"BREEZE_SESSION_TOKEN={callback_token}" in env_content

        # Verify status endpoint reflects active session
        status_res = await client.get("/api/v1/broker/session/status")
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["connected"] is True
        assert status_data["status"] == "CONNECTED"


@pytest.mark.asyncio
async def test_oauth_callback_html_response_and_aliases(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\n", encoding="utf-8")

    import libs.config.env_manager as em
    orig_update = em.update_env_variable

    def mocked_update(key, value, env_path=".env", create_backup=True):
        return orig_update(key, value, env_path=env_file, create_backup=False)

    monkeypatch.setattr("services.api_gateway.main.update_env_variable", mocked_update)

    settings = PlatformSettings(
        data_root=tmp_path / "data",
        default_trading_mode=TradingMode.PAPER,
    )
    set_platform_settings(settings)
    await initialize_services(settings=settings, force_reinit=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Test root /callback alias
        res = await client.get("/callback?apisession=ROOT_CALLBACK_TOKEN_XYZ")
        assert res.status_code == 200
        assert "ICICI Breeze Session Authenticated" in res.text
        assert "ROOT..._XYZ" in res.text
        assert "postMessage" in res.text
        assert "BREEZE_SESSION_SUCCESS" in res.text

