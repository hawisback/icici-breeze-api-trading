import json

import services.historical.independent_cohort3_source_manifest as manifest_mod
from services.historical.independent_cohort3_source_manifest import build_manifest


def _write(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_source_manifest_hashes_exact_passed_artifacts_without_scoring(
    tmp_path, monkeypatch
):
    expected = {
        "sessions": 80,
        "five_minute_rows": 6000,
        "one_minute_rows": 30000,
        "five_minute_bars_per_session": 75,
        "one_minute_bars_per_session": 375,
        "block_size_sessions": 10,
        "blocks": 8,
    }
    monkeypatch.setattr(manifest_mod, "EXPECTED", expected)
    monkeypatch.setattr(manifest_mod, "validate_market", lambda _x: None)
    monkeypatch.setattr(
        manifest_mod, "validate_auxiliary", lambda _x, _kind: None
    )
    monkeypatch.setattr(manifest_mod, "validate_options", lambda _x: None)
    monkeypatch.setattr(manifest_mod, "validate_intrabar", lambda _x: None)

    audit = {
        "protocol_version": manifest_mod.PROTOCOL_VERSION,
        "cohort_role": manifest_mod.COHORT_ROLE,
        "blind_data_used": False,
        "implementation_allowed": False,
        "expected": expected,
        "validation": "PASSED",
        "artifacts": {
            "market": "PASSED",
            "vix": "PASSED",
            "options": "PASSED",
            "spot": "PASSED",
            "intrabar": "PASSED",
        },
    }
    market = {"session_dates": ["d"], "canonical_market_rows": [{"x": 1}]}
    vix = {"session_dates": ["d"], "vix_rows": [{"x": 2}]}
    options = {"session_dates": ["d"], "option_candles": [{"x": 3}]}
    spot = {"session_dates": ["d"], "spot_rows": [{"x": 4}]}
    intrabar = {"session_dates": ["d"], "rows": [{"x": 5}]}

    paths = {}
    for name, payload in {
        "audit": audit,
        "market": market,
        "vix": vix,
        "options": options,
        "spot": spot,
        "intrabar": intrabar,
    }.items():
        path = tmp_path / f"{name}.json"
        _write(path, payload)
        paths[name] = path

    report = build_manifest(
        audit_path=paths["audit"],
        market_path=paths["market"],
        vix_path=paths["vix"],
        options_path=paths["options"],
        spot_path=paths["spot"],
        intrabar_path=paths["intrabar"],
    )

    assert report["research_only"] is True
    assert report["candidate_frozen"] is False
    assert report["blind_data_used"] is False
    assert report["implementation_allowed"] is False
    assert set(report["sources"]) == {
        "market", "vix", "options", "spot", "intrabar"
    }
    assert report["sources"]["market"]["rows"] == 1
    assert report["sources"]["intrabar"]["rows"] == 1
    assert all(len(item["sha256"]) == 64 for item in report["sources"].values())
    assert len(report["audit"]["sha256"]) == 64
    assert report["manifest_policy"] == {
        "hashes_frozen_before_event_feature_construction": True,
        "hashes_frozen_before_event_outcome_construction": True,
        "strategy_scoring_performed": False,
        "threshold_search_performed": False,
        "blind_validation_performed": False,
    }
