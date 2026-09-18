import pytest
from httpx import ASGITransport, AsyncClient

from libs.contracts.models import Candle
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services
from services.strategy.models import (
    SimulationRequest,
    ThresholdOverrides,
)
from services.strategy.simulation import SimulationEngine


def test_simulation_engine_missing_data():
    """Missing history must never be replaced by a fabricated trading session."""
    engine = SimulationEngine()
    req = SimulationRequest(
        date="2026-09-17",
        instrument_id="INST-NIFTY-INDEX",
        bypass_window=True,
    )

    import asyncio
    result = asyncio.run(engine.run_day_simulation(req))

    assert result is not None
    assert result.session_date == "2026-09-17"
    assert result.total_bars_evaluated == 0
    assert result.timeline == []
    assert result.replay_mode == "SIGNALS_ONLY"

    # Summary performance metrics
    assert result.win_rate_pct >= 0.0
    assert result.total_trades == len(result.trades)
    assert result.winning_trades + result.losing_trades == result.total_trades


def test_simulation_overrides_cannot_bypass_missing_real_data():
    """Relaxing thresholds cannot generate trades from missing history."""
    engine = SimulationEngine()
    import asyncio

    # Strict overrides
    strict_req = SimulationRequest(
        date="2026-09-17",
        overrides=ThresholdOverrides(
            adx_threshold=35.0,
            rvol_threshold=2.5,
            min_confirmation_score=5,
            strat_b_min_confirmation=5,
        ),
        bypass_window=True,
    )
    res_strict = asyncio.run(engine.run_day_simulation(strict_req))

    # Relaxed overrides
    relaxed_req = SimulationRequest(
        date="2026-09-17",
        overrides=ThresholdOverrides(
            adx_threshold=10.0,
            rvol_threshold=0.8,
            min_confirmation_score=1,
            strat_b_min_confirmation=1,
        ),
        bypass_window=True,
    )
    res_relaxed = asyncio.run(engine.run_day_simulation(relaxed_req))

    assert res_relaxed.total_trades == res_strict.total_trades == 0
    assert res_relaxed.total_bars_evaluated == res_strict.total_bars_evaluated == 0


def test_simulation_available_dates():
    """Validates discovery of available trading session dates."""
    engine = SimulationEngine()
    import asyncio
    dates = asyncio.run(engine.get_available_dates())

    assert dates == []
    for d in dates:
        assert len(d) == 10
        assert d[4] == "-" and d[7] == "-"


@pytest.mark.asyncio
async def test_simulation_rest_endpoints():
    """Tests the REST API simulation endpoints."""
    container = await initialize_services()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Available dates
        res_dates = await client.get("/api/v1/strategies/simulate/available-dates")
        assert res_dates.status_code == 200
        dates_json = res_dates.json()
        assert "dates" in dates_json
        assert isinstance(dates_json["dates"], list)

        # 2. Run simulation
        payload = {
            "date": "2026-09-17",
            "instrument_id": "INST-NIFTY-INDEX",
            "bypass_window": True,
        }
        res_sim = await client.post("/api/v1/strategies/simulate", json=payload)
        assert res_sim.status_code == 200
        sim_json = res_sim.json()

        assert "total_bars_evaluated" in sim_json
        assert "total_trades" in sim_json
        assert "win_rate_pct" in sim_json
        assert "timeline" in sim_json
        assert "trades" in sim_json
        assert sim_json["replay_mode"] == "SIGNALS_ONLY"
        assert sim_json["total_trades"] == 0

    await container.strategy_svc.stop()
