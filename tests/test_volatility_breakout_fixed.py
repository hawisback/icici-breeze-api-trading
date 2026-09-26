from datetime import datetime, timedelta, timezone
from copy import deepcopy
import pytest

from libs.contracts.models import Candle
from services.strategy.features import FeatureEngine
from services.strategy.models import (
    CompressionBox,
    MarketFeatures,
    RiskConfig,
    StrategyTunablesConfig,
    ThresholdOverrides,
    TradeDirection,
)
from services.strategy.strategies.volatility_breakout import VolatilityBreakoutStrategy
from services.strategy.position_manager import PositionManager


def setup():
    start = datetime(2026,9,18,4,0,tzinfo=timezone.utc)
    bars = [Candle(instrument_id="SPOT",interval="5m",source="BREEZE",
                   start_time=start+timedelta(minutes=5*i),end_time=start+timedelta(minutes=5*(i+1)),
                   open=100,high=103,low=97,close=100,volume=0) for i in range(8)]
    f = MarketFeatures(timestamp=bars[-1].end_time,spot_price=100,atr_5m=5,
                       breakout_data_ready=True,bb_width_percentile=20,
                       rvol_5m=0,futures_price=110,futures_vwap=105)
    return f,bars


def advance(f,bars,bear=False,close=None):
    cl = close if close is not None else (96 if bear else 104)
    op = 98 if bear else 102
    hi = max(99 if bear else 104.2, cl + 0.1, op + 0.1)
    lo = min(95.8 if bear else 101, cl - 0.1, op - 0.1)
    c = bars[-1].model_copy(update={"start_time":bars[-1].end_time,
        "end_time":bars[-1].end_time+timedelta(minutes=5),
        "open":op,"high":hi,"low":lo,"close":cl})
    bars.append(c)
    f.timestamp = c.end_time
    f.bb_width_percentile = 95  # expansion must not unlock the valid box
    if bear:
        f.futures_price = 100
    f.spot_price = cl


@pytest.mark.parametrize("bear",[False,True])
def test_completed_breakout_parity_frozen_r_and_restart_dedup(bear):
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    before = strat.export_state()
    assert strat.diagnose(f,bars)[0].phase_state == "BOX_LOCKED"
    assert strat.export_state() == before  # diagnostics are read-only
    assert strat.evaluate(f,bars) is None
    assert strat.locked_box.atr_at_lock == 5  # no floor of 10
    saved = strat.export_state()
    strat = VolatilityBreakoutStrategy()
    strat.restore_state(saved)
    advance(f,bars,bear)
    # A completed breakout candle qualifies on its first evaluation.
    diag = strat.diagnose(f,bars)[int(bear)]
    assert diag.overall_status == "READY_TO_TRIGGER"  # RVOL is not mandatory
    signal = strat.evaluate(f,bars)
    assert signal.direction == (TradeDirection.BEARISH if bear else TradeDirection.BULLISH)
    assert signal.spot_reference_price == bars[-1].close
    assert signal.features_snapshot["entry_reference_spot"] == bars[-1].close
    assert signal.r_points == pytest.approx(2.25)
    assert signal.timestamp == bars[-1].end_time
    assert signal.features_snapshot["confirmation_score"] == 3
    assert signal.features_snapshot["raw_confirmation_score"] == 3
    assert signal.features_snapshot["oi_wall_penalty"] == 0
    assert signal.features_snapshot["effective_confirmation_score"] == 3
    assert strat.breakout_confirm_count == 0
    after_signal = strat.export_state()
    assert strat.evaluate(f,bars) is None
    assert strat.export_state() == after_signal
    restored = VolatilityBreakoutStrategy()
    restored.restore_state(strat.export_state())
    assert restored.evaluate(f,bars) is None


@pytest.mark.parametrize("bear",[False,True])
def test_intrabar_live_quote_cannot_trigger_or_mutate_same_candle(bear):
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars,bear=bear,close=102 if not bear else 98)
    f.spot_price = 110 if not bear else 90

    assert strat.evaluate(f,bars) is None
    after = strat.export_state()
    assert after["box"] is not None
    assert after["box"]["bars_active"] == 1
    assert after["breakout_confirm_count"] == 0
    assert after["last_bar"] == bars[-1].end_time.isoformat()

    # A repeated scheduler poll for the same completed candle is a no-op.
    assert strat.evaluate(f,bars) is None
    assert strat.export_state() == after
    assert strat.evaluate(f,bars) is None
    assert strat.export_state() == after


def test_box_lock_candle_cannot_trigger_its_own_breakout():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    assert strat.evaluate(f,bars) is None
    assert strat.locked_box is not None
    assert strat.export_state()["last_bar"] == bars[-1].end_time.isoformat()


def test_box_ages_only_on_completed_bars_and_expires_after_eight():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    high = strat.locked_box.box_high
    for i in range(1,10):
        last = bars[-1]
        bars.append(last.model_copy(update={"start_time":last.end_time,"end_time":last.end_time+timedelta(minutes=5)}))
        f.timestamp = bars[-1].end_time
        f.spot_price = 100  # inside consolidation box
        f.bb_width_percentile = 99
        for _ in range(5):
            strat.diagnose(f,bars)
            strat.evaluate(f,bars)
        if i <= 8:
            assert strat.locked_box.bars_active == i
            assert strat.locked_box.box_high == high
        else:
            assert strat.locked_box is None


@pytest.mark.parametrize("fault",["stale","synthetic","incomplete","duplicate","gap","config"])
def test_invalid_data_and_config_reset_box(fault):
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars)
    overrides = None
    if fault == "stale": f.timestamp += timedelta(minutes=5)
    if fault == "synthetic": bars[-1] = bars[-1].model_copy(update={"source":"SIMULATED"})
    if fault == "incomplete": f.timestamp -= timedelta(seconds=1)
    if fault == "duplicate": bars.append(bars[-1])
    if fault == "gap":
        bars[-1] = bars[-1].model_copy(update={"start_time":bars[-1].start_time+timedelta(minutes=5),
                                             "end_time":bars[-1].end_time+timedelta(minutes=5)})
        f.timestamp = bars[-1].end_time
    if fault == "config": overrides = ThresholdOverrides(bb_width_percentile=30)
    assert strat.evaluate(f,bars,overrides=overrides) is None
    assert strat.locked_box is None


def test_confirmation_overrides_do_not_leak_from_strategy_a_and_wall_penalty():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    override = ThresholdOverrides(min_confirmation_score=6)
    strat.evaluate(f,bars,overrides=override)
    advance(f,bars)
    assert strat.evaluate(f,bars,overrides=override) is not None

    # CALL: raw 4 - relevant bullish wall penalty 1 = effective 3, so it passes.
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars)
    f.rvol_5m = 1.20
    f.bullish_oi_wall = True
    diag = strat.diagnose(f,bars)[0]
    confirmation = diag.phase_summary["confirmation"]
    assert confirmation["oi_wall_detected"] is True
    assert confirmation["raw_confirmation_score"] == 4
    assert confirmation["oi_wall_penalty"] == 1
    assert confirmation["effective_confirmation_score"] == 3
    assert confirmation["score"] == 3
    sig = strat.evaluate(f,bars)
    assert sig is not None
    assert sig.features_snapshot["raw_confirmation_score"] == 4
    assert sig.features_snapshot["oi_wall_penalty"] == 1
    assert sig.features_snapshot["effective_confirmation_score"] == 3

    # CALL: raw 3 - relevant bullish wall penalty 1 = effective 2, so it fails.
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars)
    f.bullish_oi_wall = True
    diag = strat.diagnose(f,bars)[0]
    confirmation = diag.phase_summary["confirmation"]
    assert (confirmation["raw_confirmation_score"], confirmation["oi_wall_penalty"], confirmation["effective_confirmation_score"]) == (3, 1, 2)
    assert diag.overall_status == "WAITING"
    assert strat.evaluate(f,bars) is None

    # An opposing-direction wall does not penalize a CALL.
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars)
    f.bearish_oi_wall = True
    assert strat.diagnose(f,bars)[0].phase_summary["confirmation"]["oi_wall_penalty"] == 0
    assert strat.evaluate(f,bars) is not None

    # PUT parity: bearish wall is relevant; bullish wall is irrelevant.
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars,bear=True)
    f.bearish_oi_wall = True
    diag = strat.diagnose(f,bars)[1]
    assert diag.phase_summary["confirmation"]["oi_wall_penalty"] == 1
    assert diag.phase_summary["confirmation"]["effective_confirmation_score"] == 2
    assert strat.evaluate(f,bars) is None

    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    advance(f,bars,bear=True)
    f.bullish_oi_wall = True
    assert strat.diagnose(f,bars)[1].phase_summary["confirmation"]["oi_wall_penalty"] == 0
    assert strat.evaluate(f,bars) is not None
    assert sig.features_snapshot["oi_wall_detected"] is True


def test_overextension_abandons_box():
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    # Box high is 103, ATR is 5. Max extension 0.75 * 5 = 3.75. 103 + 3.75 = 106.75.
    # Close at 107 is overextended (> 106.75).
    advance(f,bars,close=108)
    bars[-1] = bars[-1].model_copy(update={"high":108.1})
    f.spot_price = 108
    assert strat.evaluate(f,bars) is None
    assert strat.locked_box is None


def test_empirical_bb_rank_and_clean_oi_evidence():
    wide = [100+(-1)**i*5 for i in range(80)]
    narrow = wide+[100+(-1)**i*.01 for i in range(20)]
    assert FeatureEngine.calculate_bb_percentile(narrow) < 25
    assert FeatureEngine.calculate_bb_percentile([100]*50) == 50
    assert FeatureEngine.calculate_bb_percentile([100]*20) == 100
    chain = {"source":"BREEZE","strikes":[{"strike":i,
             "call":{"open_interest":100 if i != 101 else 1000,"oi_change":-10},
             "put":{"open_interest":100,"oi_change":20,"oi_velocity":1}} for i in range(95,106)]}
    bull,bear,wall,_ = FeatureEngine.breakout_oi_features(chain,100.5,"LONG_BUILDUP")
    assert (bull,bear,wall) == (5,0,True)
    chain["source"] = "SIMULATED"
    assert FeatureEngine.breakout_oi_features(chain,100.5,"NEUTRAL") == (0,0,False,False)


def test_maximum_lots_cap_and_missing_lot_rejection():
    pm = PositionManager(RiskConfig(max_lots_per_trade=2))
    assert pm.calculate_position_size(60,500000,25) == (2,50)
    assert pm.calculate_position_size(60,500000) == (0,0)


@pytest.mark.parametrize("bear",[False,True])
def test_false_breakout_counts_distinct_closes_not_polls(bear):
    from services.strategy.models import ActiveTrade, AutoTradingMode, StrategyName, OptionType, SessionTimersConfig
    f,bars = setup()
    t = ActiveTrade(trade_id="B",mode=AutoTradingMode.PAPER,strategy=StrategyName.VOLATILITY_BREAKOUT,
        direction=TradeDirection.BEARISH if bear else TradeDirection.BULLISH,
        option_type=OptionType.PUT if bear else OptionType.CALL,contract_symbol="TEST",contract_instrument_id="TEST",
        expiry="2026-09-24",strike=100,quantity=25,lot_size=25,lots=1,entry_time=f.timestamp,
        entry_option_price=60,entry_spot_price=96 if bear else 104,initial_structural_stop=105 if bear else 95,
        initial_r_points=9,box_high=103,box_low=97,atr_at_lock=5,current_trailing_stop=105 if bear else 95,
        current_option_price=60,current_spot_price=96 if bear else 104,option_hard_stop_price=45)
    pm = PositionManager(session_config=SessionTimersConfig(force_exit_time="23:59"))
    f.spot_price = 97.2 if bear else 102.8
    f.futures_price = f.futures_vwap = 100
    f.ema9_5m = f.ema20_5m = 100
    f.supertrend_direction = "NEUTRAL"
    f.timestamp += timedelta(minutes=5)
    f.closed_5m_time = f.timestamp
    f.closed_5m_price = f.spot_price
    for _ in range(5):
        assert pm.update_position(t,60,f,as_of=f.timestamp)[1] is None
    assert t.consecutive_inside_box_closes == 1
    # Intrabar movement alone does not increment the counter.
    f.spot_price = 97.4 if bear else 102.6
    assert pm.update_position(t,60,f,as_of=f.timestamp)[1] is None
    f.timestamp += timedelta(minutes=5)
    f.closed_5m_time = f.timestamp
    assert "FALSE_BREAKOUT_EXIT" in pm.update_position(t,60,f,as_of=f.timestamp)[1]


@pytest.mark.asyncio
async def test_box_repository_round_trip_is_separate_from_strategy_a(tmp_path):
    from services.strategy.repository import StrategyRepository
    repo = StrategyRepository(db_path=tmp_path/"strategy.db")
    await repo.initialize()
    f,bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f,bars)
    await repo.save_runtime({"BULLISH":{}}, "trend_pullback")
    await repo.save_runtime(strat.export_state(), "volatility_breakout")
    restored = VolatilityBreakoutStrategy()
    restored.restore_state(await repo.get_runtime("volatility_breakout"))
    assert restored.export_state() == strat.export_state()
    assert await repo.get_runtime() == {"BULLISH":{}}


def test_breakout_readiness_does_not_require_strategy_a_macro_warmup():
    f,bars = setup()
    start = bars[0].start_time
    bars = [bars[0].model_copy(update={"start_time":start+timedelta(minutes=i*5),
             "end_time":start+timedelta(minutes=(i+1)*5),"volume":100}) for i in range(40)]
    futures = [c.model_copy(update={"instrument_id":"FUT"}) for c in bars]
    result = FeatureEngine.compute_all_features(bars,[],futures,spot_price=100,as_of=bars[-1].end_time)
    assert result.breakout_data_ready
    assert not result.data_ready


@pytest.mark.asyncio
async def test_unfilled_entry_timeout_cancels_without_assuming_a_fill():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from libs.contracts.models import utc_now
    from services.strategy.models import TradeLifecycleState
    from services.strategy.service import StrategyService
    pending = SimpleNamespace(status=SimpleNamespace(value="OPEN"),filled_quantity=0,
                              broker_order_id="broker-id",trading_mode="LIVE")
    gateway = Mock(cancel_order=AsyncMock())
    oms = Mock(get_order=AsyncMock(return_value=pending))
    svc = StrategyService(oms,historical_service=SimpleNamespace(broker_gateway=gateway))
    trade = SimpleNamespace(state=TradeLifecycleState.ENTRY_PENDING,entry_order_id="order",
                            entry_time=utc_now()-timedelta(seconds=30))
    await svc._evaluate_active_trade(trade,MarketFeatures(spot_price=100))
    gateway.cancel_order.assert_awaited_once_with("broker-id",mode="LIVE")
    assert trade.state == TradeLifecycleState.ENTRY_PENDING


def test_bb_compression_thresholds_and_lookback():
    import inspect
    sig = inspect.signature(FeatureEngine.calculate_bb_percentile)
    assert sig.parameters["history"].default == 60

    strat = VolatilityBreakoutStrategy()
    assert strat.bb_percentile_lookback == 60
    assert strat.bb_width_percentile_threshold == 25.0

    f, bars = setup()
    # 25th percentile: passes compression and locks a box.
    f.bb_width_percentile = 25.0
    diag = strat.diagnose(f, bars)[0]
    assert diag.phase_summary["compression_pass"] is True
    assert diag.phase_state == "BOX_LOCKED"
    assert strat.evaluate(f, bars) is None
    assert strat.locked_box is not None

    # 25.1st percentile: fails compression (> 25).
    f.bb_width_percentile = 25.1
    strat_251 = VolatilityBreakoutStrategy()
    diag = strat_251.diagnose(f, bars)[0]
    assert diag.phase_summary["compression_pass"] is False
    assert diag.phase_summary["primary_blocker"] == "NO_COMPRESSION"
    assert strat_251.evaluate(f, bars) is None
    assert strat_251.locked_box is None


def test_box_height_thresholds():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy()
    assert strat.box_max_height_atr == 1.30

    # ATR is 5.0. Max box height = 1.30 * 5.0 = 6.50.
    # Height 6.50 (1.30 ATR): PASS
    bars_130 = [c.model_copy(update={"high": 102.75, "low": 96.25}) for c in bars]
    strat_130 = VolatilityBreakoutStrategy()
    diag = strat_130.diagnose(f, bars_130)[0]
    assert diag.phase_summary["compression_pass"] is True
    assert diag.phase_state == "BOX_LOCKED"

    # Height 6.55 (>1.30 ATR): FAIL (BOX_TOO_LARGE)
    bars_over = [c.model_copy(update={"high": 102.80, "low": 96.25}) for c in bars]
    strat_over = VolatilityBreakoutStrategy()
    diag = strat_over.diagnose(f, bars_over)[0]
    assert diag.phase_summary["compression_pass"] is False
    assert diag.phase_summary["primary_blocker"] == "BOX_TOO_LARGE"
    assert strat_over.evaluate(f, bars_over) is None
    assert strat_over.locked_box is None


def test_completed_close_breakout_requires_no_polling_and_supports_put_parity():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f, bars)
    assert strat.locked_box is not None

    advance(f, bars)
    # The live quote deliberately disagrees with the completed candle close.
    f.spot_price = 110.0
    diag2 = strat.diagnose(f, bars)[0]
    assert diag2.overall_status == "READY_TO_TRIGGER"
    sig = strat.evaluate(f, bars)
    assert sig is not None
    assert sig.direction == TradeDirection.BULLISH
    assert sig.spot_reference_price == bars[-1].close
    assert sig.features_snapshot["entry_reference_spot"] == bars[-1].close

    # Test PUT trigger on its first completed-candle evaluation.
    f_bear, bars_bear = setup()
    strat_bear = VolatilityBreakoutStrategy()
    strat_bear.evaluate(f_bear, bars_bear)
    advance(f_bear, bars_bear, bear=True)
    f_bear.spot_price = 90.0
    sig_bear = strat_bear.evaluate(f_bear, bars_bear)
    assert sig_bear is not None
    assert sig_bear.direction == TradeDirection.BEARISH
    assert sig_bear.spot_reference_price == bars_bear[-1].close


def test_anti_chase_extension_thresholds():
    # 0.75 ATR extension: 103 + 0.75 * 5 = 106.75 (PASS - boundary).
    f, bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f, bars)
    advance(f, bars, close=106.75)
    diag = strat.diagnose(f, bars)[0]
    assert diag.phase_summary["extension"]["passed"] is True
    assert diag.overall_status == "READY_TO_TRIGGER"

    # Just over 0.75 ATR extension (106.76) is rejected.
    f, bars = setup()
    strat = VolatilityBreakoutStrategy()
    strat.evaluate(f, bars)
    advance(f, bars, close=106.76)
    diag = strat.diagnose(f, bars)[0]
    assert diag.phase_summary["primary_blocker"] == "BREAKOUT_OVEREXTENDED"
    assert strat.evaluate(f, bars) is None
    assert strat.locked_box is None


def test_ternary_confirmation_scoring():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat.evaluate(f, bars)
    advance(f, bars, close=104)
    f.spot_price = 104.0

    # Case A: 3 pass, 1 fail, 2 None -> 3 of 4 available (PASS at R1 minimum).
    f.futures_price = 110.0
    f.futures_vwap = 115.0  # VWAP fails
    f.rvol_5m = 1.20        # Passes the frozen R1 RVOL threshold.
    f.breakout_bull_derivatives_score = None  # None
    f.futures_buildup = None                 # None

    diag = strat.diagnose(f, bars)[0]
    assert diag.phase_summary["confirmation"]["score"] == 3
    assert diag.phase_summary["confirmation"]["raw_confirmation_score"] == 3
    assert diag.phase_summary["confirmation"]["oi_wall_penalty"] == 0
    assert diag.phase_summary["confirmation"]["available"] == 4
    assert diag.overall_status == "READY_TO_TRIGGER"
    assert strat.evaluate(f, bars) is not None

    # Case B: 2 pass, 1 fail, 3 None -> 2 of 3 available (FAIL at R1 minimum 3).
    f2, bars2 = setup()
    strat2 = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat2.evaluate(f2, bars2)
    c_weak = bars2[-1].model_copy(update={
        "start_time": bars2[-1].end_time, "end_time": bars2[-1].end_time + timedelta(minutes=5),
        "open": 103.5, "high": 104.0, "low": 101.0, "close": 104.0,
    })
    bars2.append(c_weak)
    f2.timestamp = c_weak.end_time
    f2.spot_price = 104.0
    f2.futures_price = 110.0
    f2.futures_vwap = 105.0  # VWAP passes
    f2.rvol_5m = 0.0         # None
    f2.breakout_bull_derivatives_score = None  # None
    f2.futures_buildup = None                 # None

    diag2 = strat2.diagnose(f2, bars2)[0]
    assert diag2.phase_summary["confirmation"]["score"] == 2
    assert diag2.phase_summary["confirmation"]["effective_confirmation_score"] == 2
    assert diag2.phase_summary["confirmation"]["available"] == 3
    assert diag2.phase_summary["primary_blocker"] == "CONFIRMATION_SCORE_LOW"

    # Case C: 2 pass, 0 fail, 4 None -> 2 of 2 available (FAIL: INSUFFICIENT_CONFIRMATION_DATA < 3)
    f3, bars3 = setup()
    strat3 = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat3.evaluate(f3, bars3)
    advance(f3, bars3, close=104)
    f3.spot_price = 104.0
    f3.futures_vwap = 0.0   # None
    f3.futures_price = 0.0  # None
    f3.rvol_5m = 0.0        # None
    f3.breakout_bull_derivatives_score = None  # None
    f3.futures_buildup = None                 # None

    diag3 = strat3.diagnose(f3, bars3)[0]
    assert diag3.phase_summary["confirmation"]["score"] == 2
    assert diag3.phase_summary["confirmation"]["available"] == 2
    assert diag3.phase_summary["primary_blocker"] == "INSUFFICIENT_CONFIRMATION_DATA"


def test_structural_risk_gate():
    f, bars = setup()
    strat = VolatilityBreakoutStrategy(breakout_confirm_polls=1)
    strat.evaluate(f, bars)
    advance(f, bars, close=104)

    # 1. Normal risk: completed close = 104.0. Stop = 103 - 1.25 = 101.75.
    # Risk = 2.25 (0.45 ATR <= 1.20 ATR) -> PASS, despite a different live quote.
    f.spot_price = 108.0
    diag_pass = strat.diagnose(f, bars)[0]
    assert diag_pass.phase_summary["risk"]["initial_risk_atr"] == 0.45
    assert diag_pass.overall_status == "READY_TO_TRIGGER"

    # 2. Risk too high: completed close = 108.0 with max_extension relaxed to 1.5 ATR
    f_risk, bars_risk = setup()
    strat_risk = VolatilityBreakoutStrategy(breakout_confirm_polls=1, max_extension_atr=1.5)
    strat_risk.evaluate(f_risk, bars_risk)
    advance(f_risk, bars_risk, close=108)
    f_risk.spot_price = 104.0
    diag_risk = strat_risk.diagnose(f_risk, bars_risk)[0]
    assert diag_risk.phase_summary["primary_blocker"] == "RISK_TOO_HIGH"


def test_strategy_b_r1_defaults_agree_across_construction_paths(monkeypatch):
    from unittest.mock import Mock

    from services.strategy.service import StrategyService
    from services.strategy.simulation import SimulationEngine

    expected = {
        "rvol_threshold": 1.20,
        "min_confirmation_score": 3,
        "bb_width_percentile_threshold": 25.0,
        "box_max_height_atr": 1.30,
        "lookback_bars": 8,
        "max_age_bars": 8,
        "breakout_buffer_atr": 0.05,
        "max_extension_atr": 0.75,
    }

    direct = VolatilityBreakoutStrategy()
    assert {name: getattr(direct, name) for name in expected} == expected

    config = StrategyTunablesConfig()
    assert config.bb_width_percentile_threshold == 25.0
    assert config.compression_lookback_bars == 8
    assert config.box_max_age_bars == 8
    assert config.breakout_buffer_atr == 0.05
    assert config.breakout_max_extension_atr == 0.75
    assert config.strat_b_min_confirmation == 3
    assert config.box_max_height_atr == 1.30
    assert config.rvol_threshold == 1.20

    box = CompressionBox(
        box_high=103, box_low=97, box_height=6, atr_at_lock=5,
        bb_width_at_lock=25,
    )
    assert box.max_bars == 8

    service = StrategyService(Mock())
    assert {name: getattr(service.strategy_b, name) for name in expected} == expected

    engine = SimulationEngine(tunables=config)
    assert engine.tunables is config

    # Exercise the production replay-registry construction path without
    # running a historical replay. Empty data is sufficient to reach adapter
    # construction.
    import services.strategy.replay_registry as replay_registry_module
    captured = {}

    class SpyVolatilityBreakout:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def reset(self, at=None):
            return None

    async def empty_fetch(date_str, instrument_id, historical_source, source_diagnostics, role):
        source_diagnostics[role] = {
            "requested_source": historical_source.value,
            "available_before_filter": {},
            "selected_after_filter": {},
            "selected_count": 0,
            "missing_selected_source": True,
        }
        return [], []

    monkeypatch.setattr(
        replay_registry_module,
        "VolatilityBreakoutStrategy",
        SpyVolatilityBreakout,
    )
    monkeypatch.setattr(engine, "_fetch_session_candles", empty_fetch)
    from services.strategy.models import SimulationRequest
    import asyncio
    asyncio.run(engine.run_day_simulation(SimulationRequest(date="2026-09-18")))
    assert captured == {
        "rvol_threshold": 1.20,
        "adx_threshold": 20.0,
        "min_confirmation_score": 3,
        "box_max_height_atr": 1.30,
        "bb_width_percentile_threshold": 25.0,
        "lookback_bars": 8,
        "max_age_bars": 8,
        "breakout_buffer_atr": 0.05,
        "max_extension_atr": 0.75,
        "entry_start": "09:25",
        "entry_end": "14:45",
    }

