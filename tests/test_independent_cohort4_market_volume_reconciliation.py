import copy

import pytest

import services.historical.independent_cohort4_market_volume_reconciliation as mod


def _one_minute_rows():
    rows = []
    start = mod.datetime.fromisoformat(f"{mod.TARGET_DAY}T09:15:00+05:30")
    target_start = mod.datetime.fromisoformat(mod.TARGET_TIMESTAMP)
    target_indices = {
        int((target_start - start).total_seconds() // 60) + offset
        for offset in range(5)
    }
    # Make target five-minute volume sum exactly 826950.
    target_volumes = [160000.0, 165000.0, 170000.0, 166000.0, 165950.0]
    target_map = {
        index: target_volumes[pos]
        for pos, index in enumerate(sorted(target_indices))
    }

    for index in range(375):
        ts = start + mod.timedelta(minutes=index)
        base = 25000.0 + index * 0.1
        volume = target_map.get(index, 1000.0 + index)
        rows.append({
            "timestamp": ts.isoformat(),
            "open": base,
            "high": base + 0.4,
            "low": base - 0.3,
            "close": base + 0.1,
            "volume": volume,
            "open_interest": 10_000_000.0 + index,
            "instrument": f"NIFTY FUT {mod.TARGET_EXPIRY}",
            "source": "BREEZE",
        })
    return rows


def _payload(rows):
    normalized = mod._normalize_one_minute_rows(rows)
    aggregated = mod._aggregate_five_minute(normalized)
    futures = []
    canonical = []
    for row in aggregated:
        volume = row["volume"]
        if row["timestamp"] == mod.TARGET_TIMESTAMP:
            volume = mod.TARGET_STORED_VOLUME
        future = {
            "timestamp": row["timestamp"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": volume,
            "open_interest": row["open_interest"],
            "instrument": f"NIFTY FUT {mod.TARGET_EXPIRY}",
            "source": "BREEZE",
            "instrument_type": "Futures",
        }
        futures.append(future)
        canonical.append({
            "timestamp": row["timestamp"],
            "spot_open": None,
            "spot_high": None,
            "spot_low": None,
            "spot_close": None,
            "spot_source": None,
            "futures_open": row["open"],
            "futures_high": row["high"],
            "futures_low": row["low"],
            "futures_close": row["close"],
            "futures_volume": volume,
            "futures_open_interest": row["open_interest"],
            "futures_instrument": f"NIFTY FUT {mod.TARGET_EXPIRY}",
            "futures_basis_points": None,
            "futures_source": "BREEZE",
            "vix_open": None,
            "vix_high": None,
            "vix_low": None,
            "vix_close": None,
            "vix_source": None,
            "niftybees_volume": None,
        })
    return {
        "research_type": "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_REPAIRED_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "nifty_futures": copy.deepcopy(futures),
        "canonical_market_rows": canonical,
        "provider_series": {
            "BREEZE": {"NIFTY_FUTURES": copy.deepcopy(futures)}
        },
        "quality": {"nonpositive_volume_rows": 1},
        "provider_quality": {
            "nifty_futures": {
                "providers": {
                    "BREEZE": {"nonpositive_volume_rows": 1}
                }
            }
        },
    }


def test_reconciliation_frozen_to_exact_failed_repair_and_bar():
    assert mod.INPUT_SHA256 == (
        "e87ae99cd56d26e3ddd39469f9cda32cfa7efc60ac9802c3e62d99a8d11a9803"
    )
    assert mod.TARGET_DAY == "2025-06-27"
    assert mod.TARGET_EXPIRY == "2025-07-31"
    assert mod.TARGET_TIMESTAMP == "2025-06-27T14:30:00+05:30"
    assert mod.TARGET_STORED_VOLUME == -826950.0


def test_reconciliation_changes_only_target_volume_when_full_session_matches(monkeypatch):
    rows = _one_minute_rows()
    payload = _payload(rows)
    before = copy.deepcopy(payload)
    monkeypatch.setattr(mod, "validate_market", lambda _payload: None)

    report = mod.reconcile_payload(
        payload,
        rows,
        input_sha256=mod.INPUT_SHA256,
    )

    assert report["reconciliation"]["replacement_volume"] == 826950.0
    assert report["reconciliation"]["all_session_ohlc_matches"] is True
    assert report["reconciliation"]["all_other_session_volume_matches"] is True
    assert report["quality"]["nonpositive_volume_rows"] == 0
    assert (
        report["provider_quality"]["nifty_futures"]["providers"]["BREEZE"][
            "nonpositive_volume_rows"
        ]
        == 0
    )

    target = [
        row for row in report["nifty_futures"]
        if row["timestamp"] == mod.TARGET_TIMESTAMP
    ][0]
    assert target["volume"] == 826950.0

    non_target_before = [
        row for row in before["nifty_futures"]
        if row["timestamp"] != mod.TARGET_TIMESTAMP
    ]
    non_target_after = [
        row for row in report["nifty_futures"]
        if row["timestamp"] != mod.TARGET_TIMESTAMP
    ]
    assert non_target_after == non_target_before
    assert report["reconciliation"]["policy"]["abs_applied_as_data_transform"] is False
    assert report["reconciliation"]["policy"]["synthetic_fill"] is False


def test_reconciliation_rejects_any_other_volume_disagreement(monkeypatch):
    rows = _one_minute_rows()
    payload = _payload(rows)
    monkeypatch.setattr(mod, "validate_market", lambda _payload: None)

    # Change a non-target stored five-minute volume; reconciliation must fail.
    payload["nifty_futures"][0]["volume"] += 1.0
    with pytest.raises(ValueError, match="aggregated 1m volume"):
        mod.reconcile_payload(
            payload,
            rows,
            input_sha256=mod.INPUT_SHA256,
        )


def test_reconciliation_rejects_target_volume_not_exact_absolute_match(monkeypatch):
    rows = _one_minute_rows()
    payload = _payload(rows)
    monkeypatch.setattr(mod, "validate_market", lambda _payload: None)

    # Alter one target 1-minute volume so the target sum is no longer 826950.
    target = [
        row for row in rows
        if row["timestamp"] == mod.TARGET_TIMESTAMP
    ][0]
    target["volume"] += 1.0

    with pytest.raises(ValueError, match="does not equal absolute"):
        mod.reconcile_payload(
            payload,
            rows,
            input_sha256=mod.INPUT_SHA256,
        )


def test_reconciliation_rejects_nonpositive_one_minute_volume(monkeypatch):
    rows = _one_minute_rows()
    payload = _payload(rows)
    monkeypatch.setattr(mod, "validate_market", lambda _payload: None)
    rows[0]["volume"] = -1.0

    with pytest.raises(ValueError, match="nonpositive volume"):
        mod.reconcile_payload(
            payload,
            rows,
            input_sha256=mod.INPUT_SHA256,
        )
