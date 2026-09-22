from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from libs.contracts.models import Candle
from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    StrategyTunablesConfig,
    TradeDirection,
)
from services.strategy.position_manager import PositionManager
from services.strategy.replay_lifecycle import HistoricalPositionManagerReplayer
from services.strategy.replay_manifest import ReplayManifestRecorder


UTC = timezone.utc


def _candle(
    instrument_id: str,
    start: datetime,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> Candle:
    return Candle(
        instrument_id=instrument_id,
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
        open_interest=1000,
        source="BREEZE",
    )


def _replayer(
    *,
    session: list[Candle],
    futures: list[Candle] | None = None,
) -> HistoricalPositionManagerReplayer:
    return HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        strategy_config=StrategyTunablesConfig(),
        recorder=ReplayManifestRecorder(),
        instrument_id="INST-NIFTY-INDEX",
        warmup_candles=[],
        session_candles=session,
        futures_candles=futures or [],
        one_minute_candles=[],
    )


def test_strategy_a_replay_entry_timestamp_matches_completed_bar_end_first():
    start = datetime(2026, 9, 18, 4, 15, tzinfo=UTC)
    first = _candle("INST-NIFTY-INDEX", start, open_=100, high=101, low=99, close=100)
    second = _candle("INST-NIFTY-INDEX", start + timedelta(minutes=5), open_=100, high=102, low=99, close=101)
    third = _candle("INST-NIFTY-INDEX", start + timedelta(minutes=10), open_=101, high=103, low=100, close=102)

    record = SimpleNamespace(entry_5m_candle_timestamp=first.end_time)
    entry, bars = _replayer(session=[first, second, third])._bars_after(record)

    assert entry == first
    assert bars == [second, third]


def test_strategy_a_replay_uses_matching_futures_ohlc_not_spot_ohlc():
    start = datetime(2026, 9, 18, 6, 30, tzinfo=UTC)
    spot = _candle("INST-NIFTY-INDEX", start, open_=23300, high=23310, low=23290, close=23305)
    futures = _candle("INST-NIFTY-FUT-2026-09-29", start, open_=23340, high=23360, low=23325, close=23350)
    replayer = _replayer(session=[spot], futures=[futures])

    strategy_a = SimpleNamespace(
        strategy_id=StrategyName.TREND_PULLBACK.value,
        entry_features={"futures_contract": "INST-NIFTY-FUT-2026-09-29"},
    )
    strategy_b = SimpleNamespace(
        strategy_id=StrategyName.VOLATILITY_BREAKOUT.value,
        entry_features={},
    )

    assert replayer._underlying_bar(strategy_a, spot) == futures
    assert replayer._underlying_bar(strategy_b, spot) == spot


def test_strategy_a_position_manager_tracks_underlying_mfe_and_mae():
    trade = ActiveTrade(
        trade_id="T",
        mode=AutoTradingMode.PAPER,
        strategy=StrategyName.TREND_PULLBACK,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        contract_symbol="HIST",
        contract_instrument_id="INST-NIFTY-FUT-2026-09-29",
        expiry="2026-09-29",
        strike=0.0,
        quantity=1,
        lot_size=1,
        lots=1,
        entry_option_price=0.0,
        entry_spot_price=100.0,
        initial_structural_stop=90.0,
        initial_r_points=10.0,
        current_option_price=0.0,
        current_spot_price=100.0,
        current_trailing_stop=90.0,
        option_hard_stop_price=0.0,
        underlying_entry_price=100.0,
        underlying_current_price=100.0,
        underlying_structural_stop=90.0,
        underlying_r=10.0,
    )
    manager = PositionManager(
        RiskConfig(),
        SessionTimersConfig(),
        strategy_config=StrategyTunablesConfig(),
    )
    morning = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)

    trade, _ = manager.update_strategy_a_position(
        trade,
        115.0,
        None,
        as_of=morning,
    )
    assert trade.current_r == 1.5
    assert trade.mfe_points == 15.0
    assert trade.mae_points == 0.0

    trade, _ = manager.update_strategy_a_position(
        trade,
        95.0,
        None,
        as_of=morning + timedelta(minutes=5),
    )
    assert trade.mfe_points == 15.0
    assert trade.mae_points == -5.0
