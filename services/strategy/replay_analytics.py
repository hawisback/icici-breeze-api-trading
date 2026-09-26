"""Canonical portfolio analytics and reproducibility helpers for Day Replay."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from libs.contracts.models import generate_id, utc_now
from services.historical.strategy_c_candidate_manifest import (
    _spec_fingerprint as strategy_c_spec_fingerprint,
)
from services.historical.strategy_d_candidate_manifest import (
    spec_fingerprint as strategy_d_spec_fingerprint,
)
from services.strategy.models import ReplayPortfolioMetrics, SimulationResult
from services.strategy.replay_manifest import ReplayManifestRecord


REPLAY_ENGINE_REVISION = "day_replay_increment_8_v1"


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _drawdown(values: Iterable[float]) -> float:
    peak = 0.0
    cumulative = 0.0
    worst = 0.0
    for value in values:
        cumulative += float(value)
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return round(worst, 4)


def _streaks(values: Iterable[float]) -> tuple[int, int]:
    max_wins = 0
    max_losses = 0
    current_wins = 0
    current_losses = 0
    for value in values:
        if value > 0:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        elif value < 0:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)
        else:
            current_wins = 0
            current_losses = 0
    return max_wins, max_losses


def _merged_exposure_minutes(
    intervals: list[tuple[datetime, datetime]],
) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals, key=lambda item: item[0])
    total = 0.0
    start, end = ordered[0]
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
            continue
        total += (end - start).total_seconds() / 60.0
        start, end = next_start, next_end
    total += (end - start).total_seconds() / 60.0
    return round(total, 3)


def _peak_commitments(
    records: list[ReplayManifestRecord],
    *,
    session_end: datetime,
) -> tuple[int, float, float]:
    events: list[tuple[datetime, int, float, float]] = []
    for record in records:
        entry = record.simulated_entry_timestamp
        exit_time = record.exit_timestamp or session_end
        quantity = int(record.replay_quantity or 0)
        entry_price = (
            record.simulated_entry_fill_price
            or record.sizing_entry_reference_price
            or record.option_entry_price
            or 0.0
        )
        premium = max(0.0, float(entry_price) * quantity)
        risk_budget = max(0.0, float(record.sizing_risk_budget or 0.0))
        events.append((entry, 1, premium, risk_budget))
        # Exit events sort before entries at the same timestamp.
        events.append((exit_time, -1, -premium, -risk_budget))

    concurrent = 0
    premium = 0.0
    risk_budget = 0.0
    peak_concurrent = 0
    peak_premium = 0.0
    peak_risk = 0.0
    for _, direction, premium_delta, risk_delta in sorted(
        events,
        key=lambda item: (item[0], item[1]),
    ):
        concurrent += direction
        premium += premium_delta
        risk_budget += risk_delta
        peak_concurrent = max(peak_concurrent, concurrent)
        peak_premium = max(peak_premium, premium)
        peak_risk = max(peak_risk, risk_budget)
    return (
        peak_concurrent,
        round(peak_premium, 2),
        round(peak_risk, 2),
    )


def build_portfolio_metrics(
    records: Iterable[ReplayManifestRecord],
    *,
    starting_equity: float,
    session_start: datetime,
    session_end: datetime,
    execution_metadata: dict[str, Any],
) -> ReplayPortfolioMetrics:
    """Build chronological account analytics from accepted execution-parity records."""
    accepted = [
        record
        for record in records
        if record.sizing_status == "APPLIED"
        and int(record.replay_quantity or 0) > 0
    ]
    resolved = [
        record
        for record in accepted
        if record.lifecycle_status == "RESOLVED"
        and record.realized_r is not None
    ]
    resolved.sort(
        key=lambda record: (
            record.exit_timestamp or session_end,
            record.simulated_entry_timestamp,
            record.replay_signal_id,
        )
    )

    lifecycle_complete = len(resolved) == len(accepted)
    pnl_complete = lifecycle_complete and all(
        record.simulated_net_pnl is not None
        and record.simulated_gross_pnl is not None
        for record in resolved
    )
    net_values = [
        float(record.simulated_net_pnl)
        for record in resolved
        if record.simulated_net_pnl is not None
    ]
    gross_values = [
        float(record.simulated_gross_pnl)
        for record in resolved
        if record.simulated_gross_pnl is not None
    ]
    r_values = [float(record.realized_r or 0.0) for record in resolved]

    net_total = round(sum(net_values), 2) if pnl_complete else None
    gross_total = round(sum(gross_values), 2) if pnl_complete else None
    ending_equity = (
        round(starting_equity + float(net_total), 2)
        if net_total is not None
        else None
    )

    equity_curve: list[dict[str, Any]] = [{
        "timestamp": session_start.isoformat(),
        "equity": round(starting_equity, 2),
        "cumulative_net_pnl": 0.0,
        "cumulative_r": 0.0,
        "event": "SESSION_START",
    }]
    cumulative_pnl = 0.0
    cumulative_r = 0.0
    if pnl_complete:
        for record in resolved:
            cumulative_pnl += float(record.simulated_net_pnl or 0.0)
            cumulative_r += float(record.realized_r or 0.0)
            equity_curve.append({
                "timestamp": (
                    record.exit_timestamp or session_end
                ).isoformat(),
                "equity": round(starting_equity + cumulative_pnl, 2),
                "cumulative_net_pnl": round(cumulative_pnl, 2),
                "cumulative_r": round(cumulative_r, 4),
                "event": "TRADE_EXIT",
                "strategy": record.strategy_id,
                "signal_id": record.replay_signal_id,
            })

    pnl_drawdown = _drawdown(net_values) if pnl_complete else None
    drawdown_pct = (
        round((float(pnl_drawdown) / starting_equity) * 100, 4)
        if pnl_drawdown is not None and starting_equity > 0
        else None
    )
    gains = sum(value for value in net_values if value > 0)
    losses = abs(sum(value for value in net_values if value < 0))
    profit_factor = (
        round(gains / losses, 4)
        if pnl_complete and losses > 0
        else None
    )
    expectancy_pnl = (
        round(sum(net_values) / len(resolved), 2)
        if pnl_complete and resolved
        else (0.0 if pnl_complete else None)
    )
    expectancy_r = (
        round(sum(r_values) / len(resolved), 4)
        if lifecycle_complete and resolved
        else (0.0 if lifecycle_complete else None)
    )
    max_consecutive_wins, max_consecutive_losses = (
        _streaks(r_values) if lifecycle_complete else (0, 0)
    )

    intervals: list[tuple[datetime, datetime]] = []
    for record in accepted:
        start = max(session_start, record.simulated_entry_timestamp)
        end = min(session_end, record.exit_timestamp or session_end)
        if end > start:
            intervals.append((start, end))
    exposure_minutes = _merged_exposure_minutes(intervals)
    session_minutes = max(
        0.0, (session_end - session_start).total_seconds() / 60.0
    )
    exposure_pct = (
        round(exposure_minutes / session_minutes * 100, 2)
        if session_minutes
        else 0.0
    )

    peak_concurrent, peak_premium, peak_risk = _peak_commitments(
        accepted,
        session_end=session_end,
    )
    strategy_r: dict[str, float] = defaultdict(float)
    for record in resolved:
        strategy_r[record.strategy_id] += float(record.realized_r or 0.0)

    rejected = list(execution_metadata.get("rejected_opportunities") or [])
    block_counts = dict(
        execution_metadata.get("entry_gate_block_counts") or {}
    )
    limitation = None
    if not lifecycle_complete:
        limitation = (
            "One or more accepted positions have unresolved or ambiguous "
            "lifecycles; account equity, portfolio R drawdown and expectancy "
            "are intentionally unavailable rather than partially calculated."
        )
    elif not pnl_complete:
        limitation = (
            "One or more resolved trades lack estimated executable P&L; "
            "account-equity, P&L drawdown, P&L profit factor and P&L expectancy "
            "are intentionally unavailable rather than partially calculated."
        )

    return ReplayPortfolioMetrics(
        price_basis=(
            "ESTIMATED_EXECUTABLE_OPTION_FILLS_WITH_UNDERLYING_R_LIFECYCLES"
        ),
        calculation_basis=(
            "CHRONOLOGICAL_ACCEPTED_ENTRIES_AND_RESOLVED_EXITS"
        ),
        available=True,
        lifecycle_complete=lifecycle_complete,
        pnl_complete=pnl_complete,
        accepted_entries=len(accepted),
        resolved_entries=len(resolved),
        starting_equity=round(starting_equity, 2),
        ending_equity=ending_equity,
        gross_executable_pnl=gross_total,
        net_executable_pnl=net_total,
        max_drawdown_pnl=pnl_drawdown,
        max_drawdown_pct=drawdown_pct,
        max_drawdown_r=(
            _drawdown(r_values) if lifecycle_complete else None
        ),
        profit_factor_pnl=profit_factor,
        expectancy_pnl=expectancy_pnl,
        expectancy_r=expectancy_r,
        max_consecutive_wins=max_consecutive_wins,
        max_consecutive_losses=max_consecutive_losses,
        exposure_minutes=exposure_minutes,
        exposure_pct=exposure_pct,
        max_concurrent_positions=peak_concurrent,
        peak_premium_committed=peak_premium,
        peak_premium_utilization_pct=(
            round(peak_premium / starting_equity * 100, 4)
            if starting_equity > 0
            else 0.0
        ),
        peak_risk_budget_committed=peak_risk,
        peak_risk_budget_utilization_pct=(
            round(peak_risk / starting_equity * 100, 4)
            if starting_equity > 0
            else 0.0
        ),
        rejected_opportunities=len(rejected),
        risk_gate_block_counts={
            str(key): int(value) for key, value in sorted(block_counts.items())
        },
        daily_entries=int(execution_metadata.get("daily_entries") or 0),
        daily_entries_by_strategy={
            str(key): int(value)
            for key, value in sorted(
                (execution_metadata.get("daily_entries_by_strategy") or {}).items()
            )
        },
        strategy_realized_r={
            key: round(value, 4) for key, value in sorted(strategy_r.items())
        },
        equity_curve=equity_curve,
        limitation=limitation,
    )


def build_replay_run_identity(
    *,
    session_date: str,
    replay_mode: str,
    configuration_fingerprint: str,
    data_fingerprint: str,
    configuration_snapshot: dict[str, Any],
    cost_model_version: str,
    contract_selection_policy: str,
    historical_source: str,
    contract_selection_evidence: dict[str, int] | None = None,
    execution_fill_methods: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return a unique run id plus deterministic reproducibility fingerprint."""
    suite = configuration_snapshot.get("strategy_suite") or {}
    candidate_contracts = suite.get("candidate_contracts") or {}
    strategy_a = configuration_snapshot.get("strategy_a") or {}
    strategy_versions = {
        "TREND_PULLBACK": strategy_a.get("evaluator_version"),
        "VOLATILITY_BREAKOUT": "production_volatility_breakout_v1",
        "DI_CONTINUATION": candidate_contracts.get(
            "DI_CONTINUATION",
            "STRATEGY_C_DI_CONTINUATION_V1_CANDIDATE",
        ),
        "SR_MOMENTUM_BREAKOUT": candidate_contracts.get(
            "SR_MOMENTUM_BREAKOUT",
            "STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE",
        ),
        "PIVOT_VWAP_SCALP": "production_pivot_vwap_scalp_v2",
    }
    identity_payload = {
        "replay_engine_revision": REPLAY_ENGINE_REVISION,
        "session_date": session_date,
        "replay_mode": replay_mode,
        "configuration_fingerprint": configuration_fingerprint,
        "data_fingerprint": data_fingerprint,
        "cost_model_version": cost_model_version,
        "contract_selection_policy": contract_selection_policy,
        "historical_source": historical_source,
        "strategy_versions": strategy_versions,
        "frozen_candidate_fingerprints": {
            "DI_CONTINUATION": strategy_c_spec_fingerprint(),
            "SR_MOMENTUM_BREAKOUT": strategy_d_spec_fingerprint(),
        },
        "contract_selection_evidence": dict(
            sorted((contract_selection_evidence or {}).items())
        ),
        "execution_fill_methods": dict(
            sorted((execution_fill_methods or {}).items())
        ),
    }
    canonical = json.dumps(
        identity_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    generated_at = utc_now().astimezone(timezone.utc)
    return {
        **identity_payload,
        "run_id": "RPL-" + generate_id(),
        "run_fingerprint": fingerprint,
        "generated_at": generated_at.isoformat(),
    }


def _metric(
    baseline: float | int | None,
    candidate: float | int | None,
) -> dict[str, float | int | None]:
    delta = (
        round(float(candidate) - float(baseline), 4)
        if baseline is not None and candidate is not None
        else None
    )
    return {
        "baseline": baseline,
        "candidate": candidate,
        "delta": delta,
    }


def compare_replay_results(
    baseline: SimulationResult,
    candidate: SimulationResult,
) -> dict[str, Any]:
    """Compare canonical replay sections without declaring a winner."""
    b_life = baseline.underlying_lifecycle_metrics
    c_life = candidate.underlying_lifecycle_metrics
    b_opt = baseline.option_mark_metrics
    c_opt = candidate.option_mark_metrics
    b_port = baseline.portfolio_metrics
    c_port = candidate.portfolio_metrics
    b_rep = baseline.reproducibility or {}
    c_rep = candidate.reproducibility or {}

    return {
        "baseline_run_id": baseline.run_id,
        "candidate_run_id": candidate.run_id,
        "identity": {
            "same_session_date": baseline.session_date == candidate.session_date,
            "same_replay_mode": baseline.replay_mode == candidate.replay_mode,
            "same_configuration_fingerprint": (
                b_rep.get("configuration_fingerprint")
                == c_rep.get("configuration_fingerprint")
            ),
            "same_data_fingerprint": (
                b_rep.get("data_fingerprint")
                == c_rep.get("data_fingerprint")
            ),
            "same_cost_model_version": (
                b_rep.get("cost_model_version")
                == c_rep.get("cost_model_version")
            ),
            "same_contract_selection_policy": (
                b_rep.get("contract_selection_policy")
                == c_rep.get("contract_selection_policy")
            ),
        },
        "metrics": {
            "qualified_signals": _metric(
                baseline.signal_metrics.qualified_signals,
                candidate.signal_metrics.qualified_signals,
            ),
            "resolved_trades": _metric(
                b_life.resolved_trades,
                c_life.resolved_trades,
            ),
            "win_rate_pct": _metric(
                b_life.win_rate_pct,
                c_life.win_rate_pct,
            ),
            "total_realized_r": _metric(
                b_life.total_realized_r,
                c_life.total_realized_r,
            ),
            "profit_factor_r": _metric(
                b_life.profit_factor_r,
                c_life.profit_factor_r,
            ),
            "max_drawdown_r": _metric(
                b_life.max_drawdown_r,
                c_life.max_drawdown_r,
            ),
            "net_estimated_executable_pnl": _metric(
                b_opt.net_estimated_executable_pnl,
                c_opt.net_estimated_executable_pnl,
            ),
            "portfolio_max_drawdown_pnl": _metric(
                b_port.max_drawdown_pnl,
                c_port.max_drawdown_pnl,
            ),
            "portfolio_expectancy_pnl": _metric(
                b_port.expectancy_pnl,
                c_port.expectancy_pnl,
            ),
            "portfolio_expectancy_r": _metric(
                b_port.expectancy_r,
                c_port.expectancy_r,
            ),
            "portfolio_exposure_pct": _metric(
                b_port.exposure_pct,
                c_port.exposure_pct,
            ),
            "rejected_opportunities": _metric(
                b_port.rejected_opportunities,
                c_port.rejected_opportunities,
            ),
        },
        "strategy_realized_r": {
            strategy: _metric(
                b_port.strategy_realized_r.get(strategy, 0.0),
                c_port.strategy_realized_r.get(strategy, 0.0),
            )
            for strategy in sorted(
                set(b_port.strategy_realized_r)
                | set(c_port.strategy_realized_r)
            )
        },
        "provenance": {
            "baseline": b_rep,
            "candidate": c_rep,
        },
    }
