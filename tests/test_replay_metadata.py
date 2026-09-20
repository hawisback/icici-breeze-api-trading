import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from libs.contracts.models import Candle
from services.historical.service import HistoricalService
from services.instrument.service import InstrumentService
from services.strategy.models import (
    HistoricalReplaySource,
    SessionTimersConfig,
    StrategyTunablesConfig,
    ThresholdOverrides,
    SimulationRequest,
)
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.replay_metadata import (
    build_configuration_snapshot,
    configuration_fingerprint,
)
from services.strategy.simulation import SimulationEngine


class MixedSourceHistoricalService:
    broker_gateway = None

    async def get_candles(self, **kwargs):
        start = datetime(2026, 9, 18, 3, 45, tzinfo=UTC)
        return [
            Candle(
                instrument_id=kwargs["instrument_id"],
                interval="5m",
                start_time=start,
                end_time=start + timedelta(minutes=5),
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=1,
                source="BREEZE",
            ),
            Candle(
                instrument_id=kwargs["instrument_id"],
                interval="5m",
                start_time=start,
                end_time=start + timedelta(minutes=5),
                open=200,
                high=201,
                low=199,
                close=200.5,
                volume=1,
                source="KITE",
            ),
        ]


def test_replay_source_filter_does_not_mix_providers():
    diagnostics = {}
    warmup, session = asyncio.run(
        SimulationEngine(historical_service=MixedSourceHistoricalService())._fetch_session_candles(
            "2026-09-18",
            "INST-NIFTY-INDEX",
            historical_source=HistoricalReplaySource.BREEZE,
            source_diagnostics=diagnostics,
            role="spot",
        )
    )

    assert len(warmup) == 0
    assert len(session) == 1
    assert {c.source for c in session} == {"BREEZE"}
    assert diagnostics["spot"]["available_before_filter"] == {"BREEZE": 1, "KITE": 1}
    assert diagnostics["spot"]["selected_after_filter"] == {"BREEZE": 1}


def test_replay_configuration_fingerprint_is_deterministic_and_bypass_sensitive():
    kwargs = dict(
        start_date="2026-09-18",
        end_date="2026-09-18",
        instrument_id="INST-NIFTY-INDEX",
        historical_source=HistoricalReplaySource.BREEZE,
        strategy_a_enabled=True,
        overrides=ThresholdOverrides(),
        tunables=StrategyTunablesConfig(),
        session=SessionTimersConfig(),
    )
    baseline = build_configuration_snapshot(bypass_entry_window=False, **kwargs)
    baseline_repeat = build_configuration_snapshot(bypass_entry_window=False, **kwargs)
    bypassed = build_configuration_snapshot(bypass_entry_window=True, **kwargs)

    assert configuration_fingerprint(baseline) == configuration_fingerprint(baseline_repeat)
    assert configuration_fingerprint(baseline) != configuration_fingerprint(bypassed)


def test_manifest_persists_replay_metadata(tmp_path):
    recorder = ReplayManifestRecorder()
    recorder.set_replay_metadata({
        "configuration_fingerprint": "config-hash",
        "data_fingerprint": {"dataset_hash": "data-hash"},
    })
    path = tmp_path / "manifest.json"
    recorder.persist(path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["metadata"]["configuration_fingerprint"] == "config-hash"
    assert payload["metadata"]["data_fingerprint"]["dataset_hash"] == "data-hash"
    assert payload["records"] == []
