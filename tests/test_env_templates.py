"""Coverage for tracked environment templates and LIVE routing defaults."""

from __future__ import annotations

from pathlib import Path

from libs.config.settings import PlatformSettings


ROOT = Path(__file__).resolve().parents[1]


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.split("  #", 1)[0].strip()
    return values


def _primary_env_keys() -> set[str]:
    keys: set[str] = set()
    for field in PlatformSettings.model_fields.values():
        alias = field.alias
        serialization_alias = field.serialization_alias
        if isinstance(alias, str):
            keys.add(alias)
        elif isinstance(serialization_alias, str):
            keys.add(serialization_alias)
    return keys


def test_env_templates_cover_all_typed_platform_settings():
    expected = _primary_env_keys()
    for name in (".env.example", ".env.local-live.example"):
        values = _parse_env(ROOT / name)
        missing = expected - set(values)
        assert missing == set(), f"{name} missing typed settings: {sorted(missing)}"


def test_live_env_template_is_kite_first_hybrid_and_live_capable():
    values = _parse_env(ROOT / ".env.local-live.example")

    assert values["LIVE_TRADING_ENABLED"] == "true"
    assert values["LIVE_EXECUTION_BROKER"] == "kite"
    assert values["MARKET_DATA_BACKEND"] == "hybrid"
    assert values["FREQUENT_DATA_BROKER"] == "kite"
    assert values["REFERENCE_DATA_BROKER"] == "breeze"

    # Strategy mode is intentionally separate from the platform capability gate.
    assert values["DEFAULT_TRADING_MODE"] == "PAPER"
    assert values["LOCAL_SINGLE_USER_MODE"] == "false"
    assert "ZERODHA_PRIMARY" in values["LIVE_ALLOWED_ACCOUNTS"]
    assert values["RATE_LIMIT_ENABLED"] == "true"
