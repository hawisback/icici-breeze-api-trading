from datetime import datetime, timedelta, timezone

from services.historical.option_macd_rsi_backtest import (
    Bar,
    Config,
    aggregate_3m,
    cross_up,
    pnl,
    rsi_wilder,
)


def bar(index: int, close: float) -> Bar:
    start = datetime(2026, 9, 21, 3, 45, tzinfo=timezone.utc) + timedelta(minutes=index)
    return Bar(
        "X",
        start,
        start + timedelta(minutes=1),
        close,
        close + 1,
        close - 1,
        close,
        1,
        1,
    )


def test_three_minute_aggregation():
    aggregated = aggregate_3m([
        bar(0, 399),
        bar(1, 401),
        bar(2, 402),
        bar(3, 403),
    ])
    assert len(aggregated) == 1
    assert aggregated[0].close == 402


def test_entry_requires_fresh_bottom_up_cross():
    macd = [-1.0, 0.2, 0.3]
    signal = [-0.5, 0.1, 0.2]
    rsi = [55.0, 50.1, 60.0]
    assert cross_up(macd, signal, rsi, 1)
    assert not cross_up(macd, signal, rsi, 2)


def test_rsi_must_be_strictly_above_50():
    assert not cross_up([-1.0, 0.2], [-0.5, 0.1], [55.0, 50.0], 1)


def test_one_lot_65_unit_pnl():
    assert pnl(400.0, 410.0, 65) == 650.0
    assert pnl(400.0, 390.0, 65) == -650.0


def test_flat_rsi_is_neutral():
    assert rsi_wilder([400.0] * 20, 14)[14] == 50.0


def test_defaults_match_requested_strategy():
    config = Config()
    assert (
        config.fast,
        config.slow,
        config.signal,
        config.rsi,
        config.quantity,
        config.end_date,
    ) == (3, 10, 16, 14, 65, None)