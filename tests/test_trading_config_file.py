"""Tests for canonical file-backed strategy configuration."""

from __future__ import annotations

import json

import pytest

from services.strategy.models import AutoTradingConfig, AutoTradingMode
from services.strategy.repository import StrategyRepository


@pytest.mark.asyncio
async def test_existing_database_bootstraps_canonical_file_without_losing_operator_state(
    tmp_path,
):
    db_path = tmp_path / "strategy.db"
    config_path = tmp_path / "trading.json"

    # Simulate an installation created before file-backed config existed.
    legacy_repo = StrategyRepository(db_path=db_path)
    await legacy_repo.initialize()
    legacy = AutoTradingConfig()
    legacy.tunables.pivot_vwap_scalp_enabled = True
    legacy.auto_trade_enabled = False
    await legacy_repo.save_auto_config(legacy)

    # The checked-in seed carries a one-time migration marker.
    config_path.write_text(
        json.dumps(
            {
                "_meta": {
                    "bootstrap_from_database_if_present": True,
                },
                "mode": "PAPER",
                "auto_trade_enabled": True,
                "system_armed": False,
                "kill_switch": False,
                "tunables": {
                    "pivot_vwap_scalp_enabled": False,
                },
            }
        ),
        encoding="utf-8",
    )

    repo = StrategyRepository(
        db_path=db_path,
        config_path=config_path,
    )
    await repo.initialize()
    loaded = await repo.get_auto_config()

    assert loaded.tunables.pivot_vwap_scalp_enabled is True
    assert loaded.auto_trade_enabled is False

    exported = json.loads(config_path.read_text(encoding="utf-8"))
    assert "_meta" not in exported
    assert exported["tunables"]["pivot_vwap_scalp_enabled"] is True
    assert exported["auto_trade_enabled"] is False
    assert exported["system_armed"] is False


@pytest.mark.asyncio
async def test_config_file_is_canonical_after_bootstrap_marker_is_consumed(
    tmp_path,
):
    db_path = tmp_path / "strategy.db"
    config_path = tmp_path / "trading.json"

    repo_without_file = StrategyRepository(db_path=db_path)
    await repo_without_file.initialize()
    await repo_without_file.save_auto_config(AutoTradingConfig())

    config_path.write_text(
        json.dumps(
            {
                "mode": "SHADOW_ONLY",
                "auto_trade_enabled": True,
                "system_armed": True,
                "kill_switch": False,
                "tunables": {
                    "pivot_vwap_scalp_enabled": True,
                },
            }
        ),
        encoding="utf-8",
    )

    repo = StrategyRepository(
        db_path=db_path,
        config_path=config_path,
    )
    await repo.initialize()
    loaded = await repo.get_auto_config()

    assert loaded.mode is AutoTradingMode.SHADOW_ONLY
    assert loaded.tunables.pivot_vwap_scalp_enabled is True
    # Files can select LIVE/PAPER/SHADOW mode, but never restore arm authority.
    assert loaded.system_armed is False
    normalized_file = json.loads(config_path.read_text(encoding="utf-8"))
    assert normalized_file["system_armed"] is False

    async with repo.engine.connect() as conn:
        row = await (
            await conn.execute(
                "SELECT config_json FROM auto_strategy_config WHERE id = 'active'"
            )
        ).fetchone()
    mirrored = json.loads(row["config_json"])
    assert mirrored["mode"] == "SHADOW_ONLY"
    assert mirrored["tunables"]["pivot_vwap_scalp_enabled"] is True
    assert mirrored["system_armed"] is False


@pytest.mark.asyncio
async def test_normal_config_save_updates_file_but_forces_file_armed_false(
    tmp_path,
):
    repo = StrategyRepository(
        db_path=tmp_path / "strategy.db",
        config_path=tmp_path / "trading.json",
    )
    await repo.initialize()

    config = AutoTradingConfig(
        mode=AutoTradingMode.LIVE,
        system_armed=True,
    )
    config.tunables.pivot_vwap_scalp_enabled = True
    await repo.save_auto_config(config)

    payload = json.loads(repo.config_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "LIVE"
    assert payload["tunables"]["pivot_vwap_scalp_enabled"] is True
    assert payload["system_armed"] is False


@pytest.mark.asyncio
async def test_runtime_only_arm_persistence_does_not_modify_operator_file(
    tmp_path,
):
    repo = StrategyRepository(
        db_path=tmp_path / "strategy.db",
        config_path=tmp_path / "trading.json",
    )
    await repo.initialize()

    initial = AutoTradingConfig(mode=AutoTradingMode.LIVE)
    await repo.save_auto_config(initial)
    before = repo.config_path.read_text(encoding="utf-8")

    armed = initial.model_copy(update={"system_armed": True})
    await repo.save_auto_config(armed, persist_file=False)

    assert repo.config_path.read_text(encoding="utf-8") == before

    async with repo.engine.connect() as conn:
        row = await (
            await conn.execute(
                "SELECT config_json FROM auto_strategy_config WHERE id = 'active'"
            )
        ).fetchone()
    assert json.loads(row["config_json"])["system_armed"] is True


@pytest.mark.asyncio
async def test_invalid_operator_config_file_fails_closed(tmp_path):
    config_path = tmp_path / "trading.json"
    config_path.write_text("{not-json", encoding="utf-8")
    repo = StrategyRepository(
        db_path=tmp_path / "strategy.db",
        config_path=config_path,
    )
    await repo.initialize()

    with pytest.raises(RuntimeError, match="Unable to read trading config file"):
        await repo.get_auto_config()


@pytest.mark.asyncio
async def test_strategy_e_old_90_second_default_migrates_to_180(tmp_path):
    config_path = tmp_path / "trading.json"
    payload = AutoTradingConfig().model_dump(mode="json")
    payload["strategy_e_revision"] = 1
    payload["tunables"]["strategy_e_max_signal_age_seconds"] = 90.0
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    repo = StrategyRepository(
        db_path=tmp_path / "strategy.db",
        config_path=config_path,
    )
    await repo.initialize()
    loaded = await repo.get_auto_config()

    assert loaded.strategy_e_revision == 2
    assert loaded.tunables.strategy_e_max_signal_age_seconds == 180.0

    normalized = json.loads(config_path.read_text(encoding="utf-8"))
    assert normalized["strategy_e_revision"] == 2
    assert normalized["tunables"]["strategy_e_max_signal_age_seconds"] == 180.0


@pytest.mark.asyncio
async def test_strategy_e_custom_signal_age_is_preserved_during_revision_migration(
    tmp_path,
):
    config_path = tmp_path / "trading.json"
    payload = AutoTradingConfig().model_dump(mode="json")
    payload["strategy_e_revision"] = 1
    payload["tunables"]["strategy_e_max_signal_age_seconds"] = 120.0
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    repo = StrategyRepository(
        db_path=tmp_path / "strategy.db",
        config_path=config_path,
    )
    await repo.initialize()
    loaded = await repo.get_auto_config()

    assert loaded.strategy_e_revision == 2
    assert loaded.tunables.strategy_e_max_signal_age_seconds == 120.0


@pytest.mark.asyncio
async def test_descriptive_meta_survives_normal_config_save(tmp_path):
    config_path = tmp_path / "trading.json"
    payload = AutoTradingConfig().model_dump(mode="json")
    payload = {
        "_meta": {
            "purpose": "operator documentation",
            "routing_note": "Kite frequent; Breeze reference",
        },
        **payload,
    }
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    repo = StrategyRepository(
        db_path=tmp_path / "strategy.db",
        config_path=config_path,
    )
    await repo.initialize()
    loaded = await repo.get_auto_config()
    loaded.auto_trade_enabled = False
    await repo.save_auto_config(loaded)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["_meta"]["purpose"] == "operator documentation"
    assert saved["_meta"]["routing_note"] == "Kite frequent; Breeze reference"
    assert saved["auto_trade_enabled"] is False
    assert saved["system_armed"] is False
