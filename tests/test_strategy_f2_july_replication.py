from datetime import date

from services.historical.strategy_f2_july_replication_market import _nearest_expiry
from services.historical.strategy_f2_july_replication_protocol import (
    BACKTEST_WINDOW,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    VARIANTS,
)


def test_f2_july_replication_protocol_is_frozen_unchanged():
    assert PROTOCOL_VERSION == "STRATEGY_F2_JULY_REPLICATION_V1"
    assert BACKTEST_WINDOW["start"] == "2026-07-01"
    assert BACKTEST_WINDOW["end"] == "2026-07-31"
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert VARIANTS["F2_1BAR"]["confirmation_bars"] == 1
    assert VARIANTS["F2_2BAR"]["confirmation_bars"] == 2
    assert GUARDRAILS["both_august_variants_must_be_carried_forward"] is True
    assert GUARDRAILS["no_confirmation_depth_selection_before_july_scoring"] is True
    assert GUARDRAILS["no_option_side_filter"] is True
    assert GUARDRAILS["no_extra_filters"] is True


def test_f2_july_weekly_expiry_schedule():
    assert OPTION_SELECTION["weekly_expiries"] == [
        "2026-07-07",
        "2026-07-14",
        "2026-07-21",
        "2026-07-28",
        "2026-08-04",
    ]
    assert _nearest_expiry(date(2026, 7, 1)) == date(2026, 7, 7)
    assert _nearest_expiry(date(2026, 7, 7)) == date(2026, 7, 7)
    assert _nearest_expiry(date(2026, 7, 31)) == date(2026, 8, 4)
