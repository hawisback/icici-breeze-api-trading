import json

import pytest

from services.historical import independent_cohort2_options_research as module
from services.historical.independent_cohort2_protocol import OPTION_EXPIRIES, SESSION_DATES


def test_cohort2_options_collector_uses_frozen_expiry_schedule(monkeypatch, tmp_path):
    market = {
        "session_dates": SESSION_DATES,
        "canonical_market_rows": [],
    }
    market_path = tmp_path / "market.json"
    output_path = tmp_path / "options.json"
    market_path.write_text(json.dumps(market), encoding="utf-8")

    captured = {}

    monkeypatch.setattr(module, "validate_market", lambda payload: None)
    monkeypatch.setattr(module, "_load_local_env", lambda: None)
    monkeypatch.setattr(module, "_usable_secret", lambda name: "x")
    monkeypatch.setattr(
        module,
        "BreezeOptionsClient",
        lambda *args, **kwargs: object(),
    )

    def fake_build(payload, expiries, *, client):
        captured["expiries"] = list(expiries)
        return {
            "session_dates": SESSION_DATES,
            "quality": {},
        }

    monkeypatch.setattr(module, "build_options_dataset", fake_build)
    monkeypatch.setattr(module, "validate_options", lambda payload: None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "independent_cohort2_options_research",
            "--market-input",
            str(market_path),
            "--output",
            str(output_path),
        ],
    )

    module.main()

    assert captured["expiries"] == OPTION_EXPIRIES
    assert json.loads(output_path.read_text(encoding="utf-8"))["session_dates"] == SESSION_DATES


def test_cohort2_options_collector_rejects_session_drift(monkeypatch, tmp_path):
    market = {"session_dates": SESSION_DATES[:-1], "canonical_market_rows": []}
    market_path = tmp_path / "market.json"
    output_path = tmp_path / "options.json"
    market_path.write_text(json.dumps(market), encoding="utf-8")
    monkeypatch.setattr(module, "validate_market", lambda payload: None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "independent_cohort2_options_research",
            "--market-input",
            str(market_path),
            "--output",
            str(output_path),
        ],
    )

    with pytest.raises(ValueError, match="frozen Cohort 2 protocol"):
        module.main()
