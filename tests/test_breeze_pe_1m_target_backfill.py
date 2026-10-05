from datetime import date

from services.historical.breeze_pe_1m_target_backfill import (
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
