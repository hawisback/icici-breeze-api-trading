from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from services.historical.independent_current_regime_magnitude_findings import (
    _build_events,
    _spearman,
)
from services.historical.independent_current_regime_magnitude_protocol import (
    BLOCKS,
    EXPECTED_BARS_PER_SESSION,
    EXPECTED_ROWS,
    EXPECTED_SCORABLE_EVENTS,
    FUTURES_EXPIRY,
    SESSION_DATES,
)

IST = ZoneInfo("Asia/Kolkata")


def test_protocol_is_exact_current_regime_non_directional_pilot():
    assert len(SESSION_DATES) == 13
    assert SESSION_DATES[0] == "2026-09-10"
    assert SESSION_DATES[-1] == "2026-09-29"
    assert "2026-09-14" not in SESSION_DATES
    assert FUTURES_EXPIRY == "2026-09-29"
    assert EXPECTED_BARS_PER_SESSION == 77
    assert EXPECTED_ROWS == 1001
    assert EXPECTED_SCORABLE_EVENTS == 858
    assert [len(BLOCKS[name]) for name in ("block1", "block2", "block3")] == [5, 4, 4]


def test_spearman_helper():
    assert abs(_spearman(np.array([1, 2, 3]), np.array([2, 4, 6])) - 1.0) < 1e-12
    assert abs(_spearman(np.array([1, 2, 3]), np.array([6, 4, 2])) + 1.0) < 1e-12


def test_each_77_bar_session_contributes_66_events():
    import pandas as pd

    records = []
    for session_index, day in enumerate(SESSION_DATES):
        start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
        for i in range(77):
            base = 25000.0 + 10 * session_index + i * 0.2
            width = 1.0 + (i % 7) * 0.2
            records.append({
                "timestamp": start + timedelta(minutes=5 * i),
                "date": day,
                "open": base,
                "high": base + width,
                "low": base - width,
                "close": base + 0.05,
            })
    frame = pd.DataFrame(records)
    events = _build_events(frame)
    assert len(events) == 13 * 66 == EXPECTED_SCORABLE_EVENTS
    assert events.groupby("date").size().eq(66).all()
