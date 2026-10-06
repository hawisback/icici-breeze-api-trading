from datetime import date

from services.historical.breeze_pe_1m_target_backfill import (
    choose_futures_candidate,
    futures_contract_expiry,
    nearest_tuesday_expiry,
    option_instrument_id,
    strike_band,
)


def test_nearest_tuesday_expiry_tracks_weekly_contract():
    assert nearest_tuesday_expiry(date(2026, 8, 31)) == date(2026, 9, 1)
    assert nearest_tuesday_expiry(date(2026, 9, 1)) == date(2026, 9, 1)
    assert nearest_tuesday_expiry(date(2026, 9, 2)) == date(2026, 9, 8)
    assert nearest_tuesday_expiry(date(2026, 9, 9)) == date(2026, 9, 15)
    assert nearest_tuesday_expiry(date(2026, 9, 16)) == date(2026, 9, 22)


def test_strike_band_is_50_point_aligned_and_conservative():
    strikes = strike_band(23941.0, 24049.0)
    assert strikes[0] == 23600
    assert strikes[-1] == 24850
    assert all(b - a == 50 for a, b in zip(strikes, strikes[1:]))


def test_option_id_matches_existing_repository_convention():
    assert (
        option_instrument_id(date(2026, 9, 8), 24200)
        == "INST-NIFTY-2026-09-08-24200-PE"
    )

def test_futures_contract_expiry_parses_repository_id():
    assert futures_contract_expiry("INST-NIFTY-FUT-2026-09-29") == date(2026, 9, 29)
    assert futures_contract_expiry("INST-NIFTY-INDEX") is None


def test_choose_futures_candidate_prefers_nearest_expiry_then_best_coverage():
    rows = [
        {
            "instrument_id": "INST-NIFTY-FUT-2026-09-29",
            "source": "BREEZE",
            "rows": 9,
            "day_low": 23410.0,
            "day_high": 23456.0,
        },
        {
            "instrument_id": "INST-NIFTY-FUT-2026-09-29",
            "source": "KITE",
            "rows": 770,
            "day_low": 23286.0,
            "day_high": 23477.6,
        },
        {
            "instrument_id": "INST-NIFTY-FUT-2026-10-27",
            "source": "KITE",
            "rows": 1000,
            "day_low": 23350.0,
            "day_high": 23550.0,
        },
    ]
    selected = choose_futures_candidate(rows, date(2026, 9, 22))
    assert selected is not None
    assert selected["instrument_id"] == "INST-NIFTY-FUT-2026-09-29"
    assert selected["source"] == "KITE"
    assert selected["rows"] == 770


def test_choose_futures_candidate_uses_breeze_only_to_break_coverage_tie():
    rows = [
        {
            "instrument_id": "INST-NIFTY-FUT-2026-09-29",
            "source": "KITE",
            "rows": 376,
            "day_low": 23000.0,
            "day_high": 23100.0,
        },
        {
            "instrument_id": "INST-NIFTY-FUT-2026-09-29",
            "source": "BREEZE",
            "rows": 376,
            "day_low": 23001.0,
            "day_high": 23101.0,
        },
    ]
    selected = choose_futures_candidate(rows, date(2026, 9, 25))
    assert selected is not None
    assert selected["source"] == "BREEZE"
