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
from services.strategy.replay_execution_model import (
    REPLAY_EXECUTION_MODEL_VERSION,
)
from services.strategy.replay_manifest import ReplayManifestRecord


REPLAY_ENGINE_REVISION = "day_replay_increment_9_v1"


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


def _parse_execution_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _execution_exit_legs(
    record: ReplayManifestRecord,
) -> list[dict[str, Any]]:
    """Return validated quantity-conserving executable exit-leg provenance."""
    provenance = record.simulated_execution_provenance or {}
    raw_legs = provenance.get("exit_legs")
    total_quantity = int(record.replay_quantity or 0)
    if not isinstance(raw_legs, list) or not raw_legs or total_quantity <= 0:
        return []

    legs: list[dict[str, Any]] = []
    for raw in raw_legs:
        if not isinstance(raw, dict):
            return []
        quantity = int(raw.get("quantity") or 0)
        timestamp = _parse_execution_timestamp(raw.get("timestamp"))
        fill = raw.get("fill") or {}
        executable_price = (
            fill.get("executable_price") if isinstance(fill, dict) else None
        )
        if quantity <= 0 or timestamp is None or executable_price is None:
            return []
        if timestamp < record.simulated_entry_timestamp:
            return []
        legs.append({
            "quantity": quantity,
            "timestamp": timestamp,
            "reason": str(raw.get("reason") or "EXIT"),
            "executable_price": float(executable_price),
        })

    legs.sort(key=lambda item: item["timestamp"])
    if sum(int(item["quantity"]) for item in legs) != total_quantity:
        return []
    for index, leg in enumerate(legs):
        leg["is_final"] = index == len(legs) - 1
    return legs


def _position_exit_events(
    record: ReplayManifestRecord,
    *,
    session_end: datetime,
) -> list[dict[str, Any]]:
    """Return quantity releases while keeping the position open until final exit."""
    legs = _execution_exit_legs(record)
    if legs:
        return legs
    quantity = int(record.replay_quantity or 0)
    if quantity <= 0:
        return []
    return [{
        "quantity": quantity,
        "timestamp": record.exit_timestamp or session_end,
        "reason": "FINAL_EXIT",
        "is_final": True,
        "executable_price": record.simulated_exit_fill_price,
    }]


def _allocate_rounded_total(
    total: float,
    weights: list[float],
) -> list[float]:
    """Allocate a rounded total while preserving it exactly."""
    allocations = [0.0 for _ in weights]
    positive = [
        index for index, weight in enumerate(weights)
        if float(weight) > 0
    ]
    if not positive:
        if allocations:
            allocations[0] = round(float(total), 2)
        return allocations

    total_weight = sum(float(weights[index]) for index in positive)
    remaining = round(float(total), 2)
    last_positive = positive[-1]
    for index in positive:
        if index == last_positive:
            allocations[index] = round(remaining, 2)
            break
        allocation = round(
            float(total) * float(weights[index]) / total_weight,
            2,
        )
        allocations[index] = allocation
        remaining = round(remaining - allocation, 2)
    return allocations


def _execution_cost_allocations(
    record: ReplayManifestRecord,
    legs: list[dict[str, Any]],
) -> list[float] | None:
    """Allocate recorded paper costs to entry and exit orders chronologically."""
    aggregate_gross = record.simulated_gross_pnl
    aggregate_net = record.simulated_net_pnl
    entry_fill = record.simulated_entry_fill_price
    quantity = int(record.replay_quantity or 0)
    if (
        aggregate_gross is None
        or aggregate_net is None
        or entry_fill is None
        or quantity <= 0
        or not legs
    ):
        return None

    total_costs = round(float(aggregate_gross) - float(aggregate_net), 2)
    breakdown = record.simulated_cost_breakdown or {}
    component_names = (
        "brokerage",
        "exchange_charges",
        "stt",
        "gst",
        "sebi_charges",
        "stamp_duty",
    )
    if not all(
        isinstance(breakdown.get(name), (int, float))
        for name in component_names
    ):
        return None

    turnovers = [
        float(entry_fill) * quantity,
        *[
            float(leg["executable_price"]) * int(leg["quantity"])
            for leg in legs
        ],
    ]
    order_weights = [1.0 for _ in turnovers]
    sell_turnovers = [0.0, *turnovers[1:]]

    brokerage = _allocate_rounded_total(
        float(breakdown["brokerage"]),
        order_weights,
    )
    exchange = _allocate_rounded_total(
        float(breakdown["exchange_charges"]),
        turnovers,
    )
    sebi = _allocate_rounded_total(
        float(breakdown["sebi_charges"]),
        turnovers,
    )
    stt = _allocate_rounded_total(
        float(breakdown["stt"]),
        sell_turnovers,
    )
    stamp = [0.0 for _ in turnovers]
    stamp[0] = round(float(breakdown["stamp_duty"]), 2)
    gst_weights = [
        brokerage[index] + exchange[index] + sebi[index]
        for index in range(len(turnovers))
    ]
    gst = _allocate_rounded_total(
        float(breakdown["gst"]),
        gst_weights,
    )

    costs = [
        round(
            brokerage[index]
            + exchange[index]
            + stt[index]
            + gst[index]
            + sebi[index]
            + stamp[index],
            2,
        )
        for index in range(len(turnovers))
    ]
    # Component rounding can differ by a paisa from gross-net. Preserve the
    # authoritative aggregate economics by assigning the residual to the final
    # exit order.
    costs[-1] = round(
        costs[-1] + total_costs - sum(costs),
        2,
    )
    return costs


def _execution_pnl_events(
    record: ReplayManifestRecord,
    *,
    session_end: datetime,
) -> list[dict[str, Any]]:
    """Split aggregate executable P&L across historical order timestamps.

    Aggregate trade economics remain authoritative. When the replay carries the
    production paper-cost breakdown, entry-side costs are booked at entry and
    exit-side costs at each partial/final exit. Older records without that
    breakdown retain the legacy final-exit projection.
    """
    aggregate_gross = record.simulated_gross_pnl
    aggregate_net = record.simulated_net_pnl
    entry_fill = record.simulated_entry_fill_price
    legs = _execution_exit_legs(record)
    if (
        aggregate_gross is None
        or aggregate_net is None
        or entry_fill is None
        or not legs
    ):
        if aggregate_net is None:
            return []
        return [{
            "timestamp": record.exit_timestamp or session_end,
            "event": "TRADE_EXIT",
            "quantity": int(record.replay_quantity or 0),
            "gross_pnl": aggregate_gross,
            "allocated_transaction_costs": (
                round(float(aggregate_gross) - float(aggregate_net), 2)
                if aggregate_gross is not None
                else None
            ),
            "net_pnl": float(aggregate_net),
            "realized_r_delta": float(record.realized_r or 0.0),
            "reason": record.exit_reason or "FINAL_EXIT",
        }]

    gross_values = [
        round(
            (float(leg["executable_price"]) - float(entry_fill))
            * int(leg["quantity"]),
            2,
        )
        for leg in legs
    ]
    gross_values[-1] = round(
        gross_values[-1]
        + float(aggregate_gross)
        - sum(gross_values),
        2,
    )

    order_costs = _execution_cost_allocations(record, legs)
    if order_costs is None:
        total_costs = round(float(aggregate_gross) - float(aggregate_net), 2)
        exit_costs = _allocate_rounded_total(
            total_costs,
            [float(leg["quantity"]) for leg in legs],
        )
        entry_cost = 0.0
    else:
        entry_cost = float(order_costs[0])
        exit_costs = order_costs[1:]

    events: list[dict[str, Any]] = []
    if entry_cost:
        events.append({
            "timestamp": record.simulated_entry_timestamp,
            "event": "ENTRY_COST",
            "quantity": int(record.replay_quantity or 0),
            "gross_pnl": 0.0,
            "allocated_transaction_costs": entry_cost,
            "net_pnl": round(-entry_cost, 2),
            "realized_r_delta": 0.0,
            "reason": "ENTRY_TRANSACTION_COSTS",
        })

    events.extend(
        {
            "timestamp": leg["timestamp"],
            "event": "TRADE_EXIT" if leg["is_final"] else "PARTIAL_EXIT",
            "quantity": int(leg["quantity"]),
            "gross_pnl": gross,
            "allocated_transaction_costs": costs,
            "net_pnl": round(gross - costs, 2),
            "realized_r_delta": (
                float(record.realized_r or 0.0) if leg["is_final"] else 0.0
            ),
            "reason": leg["reason"],
        }
        for leg, gross, costs in zip(legs, gross_values, exit_costs)
    )
    return events


def _peak_commitments(
    records: list[ReplayManifestRecord],
    *,
    session_end: datetime,
) -> tuple[int, float, float]:
    events: list[tuple[datetime, int, float, float]] = []
    for record in records:
        entry = record.simulated_entry_timestamp
        quantity = int(record.replay_quantity or 0)
        entry_price = (
            record.simulated_entry_fill_price
            or record.sizing_entry_reference_price
            or record.option_entry_price
            or 0.0
        )
        risk_budget = max(0.0, float(record.sizing_risk_budget or 0.0))
        events.append(
            (entry, 1, max(0.0, float(entry_price) * quantity), risk_budget)
        )
        for exit_event in _position_exit_events(
            record,
            session_end=session_end,
        ):
            release = max(
                0.0,
                float(entry_price) * int(exit_event["quantity"]),
            )
            is_final = bool(exit_event["is_final"])
            # Partial exits release premium without closing the position slot.
            events.append((
                exit_event["timestamp"],
                -1 if is_final else 0,
                -release,
                -risk_budget if is_final else 0.0,
            ))

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


def _strategy_r_statistics(
    records: list[ReplayManifestRecord],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in sorted(
        records,
        key=lambda item: (
            item.exit_timestamp or item.simulated_entry_timestamp,
            item.replay_signal_id,
        ),
    ):
        if record.realized_r is not None:
            grouped[record.strategy_id].append(float(record.realized_r))

    stats: dict[str, dict[str, Any]] = {}
    for strategy, values in sorted(grouped.items()):
        gains = sum(value for value in values if value > 0)
        losses = abs(sum(value for value in values if value < 0))
        wins, consecutive_losses = _streaks(values)
        stats[strategy] = {
            "resolved_trades": len(values),
            "total_realized_r": round(sum(values), 4),
            "expectancy_r": round(sum(values) / len(values), 4) if values else 0.0,
            "profit_factor_r": round(gains / losses, 4) if losses > 0 else None,
            "max_drawdown_r": _drawdown(values),
            "max_consecutive_wins": wins,
            "max_consecutive_losses": consecutive_losses,
        }
    return stats


def _capital_utilization(
    records: list[ReplayManifestRecord],
    *,
    starting_equity: float,
    session_start: datetime,
    session_end: datetime,
) -> tuple[list[dict[str, Any]], float]:
    events: list[tuple[datetime, int, float, str, str]] = []
    for record in records:
        entry = max(session_start, record.simulated_entry_timestamp)
        quantity = int(record.replay_quantity or 0)
        entry_price = (
            record.simulated_entry_fill_price
            or record.sizing_entry_reference_price
            or record.option_entry_price
            or 0.0
        )
        premium = max(0.0, float(entry_price) * quantity)
        if quantity <= 0:
            continue
        events.append((
            entry,
            1,
            premium,
            record.replay_signal_id,
            "ENTRY",
        ))
        for exit_event in _position_exit_events(
            record,
            session_end=session_end,
        ):
            timestamp = min(
                session_end,
                max(session_start, exit_event["timestamp"]),
            )
            if timestamp < entry:
                continue
            release = max(
                0.0,
                float(entry_price) * int(exit_event["quantity"]),
            )
            is_final = bool(exit_event["is_final"])
            events.append((
                timestamp,
                -1 if is_final else 0,
                -release,
                record.replay_signal_id,
                "EXIT" if is_final else "PARTIAL_EXIT",
            ))

    ordered = sorted(events, key=lambda item: (item[0], item[1], item[3]))
    curve: list[dict[str, Any]] = [{
        "timestamp": session_start.isoformat(),
        "premium_committed": 0.0,
        "utilization_pct": 0.0,
        "active_positions": 0,
        "event": "SESSION_START",
    }]
    committed = 0.0
    active = 0
    weighted_pct_minutes = 0.0
    previous = session_start
    for timestamp, direction, delta, signal_id, event_name in ordered:
        bounded = min(max(timestamp, session_start), session_end)
        minutes = max(0.0, (bounded - previous).total_seconds() / 60.0)
        current_pct = (
            committed / starting_equity * 100 if starting_equity > 0 else 0.0
        )
        weighted_pct_minutes += current_pct * minutes
        committed += delta
        active += direction
        curve.append({
            "timestamp": bounded.isoformat(),
            "premium_committed": round(max(0.0, committed), 2),
            "utilization_pct": round(
                max(0.0, committed) / starting_equity * 100, 4
            ) if starting_equity > 0 else 0.0,
            "active_positions": max(0, active),
            "event": event_name,
            "signal_id": signal_id,
        })
        previous = bounded

    tail_minutes = max(0.0, (session_end - previous).total_seconds() / 60.0)
    tail_pct = committed / starting_equity * 100 if starting_equity > 0 else 0.0
    weighted_pct_minutes += tail_pct * tail_minutes
    session_minutes = max(
        0.0, (session_end - session_start).total_seconds() / 60.0
    )
    average = (
        round(weighted_pct_minutes / session_minutes, 4)
        if session_minutes > 0 else 0.0
    )
    return curve, average


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
    chronological_pnl_events: list[dict[str, Any]] = []
    if pnl_complete:
        for record in resolved:
            for event in _execution_pnl_events(
                record,
                session_end=session_end,
            ):
                chronological_pnl_events.append({
                    **event,
                    "strategy": record.strategy_id,
                    "signal_id": record.replay_signal_id,
                })
        chronological_pnl_events.sort(
            key=lambda item: (
                item["timestamp"],
                item["signal_id"],
                item["event"],
            )
        )

    cumulative_pnl = 0.0
    cumulative_r = 0.0
    for event in chronological_pnl_events:
        cumulative_pnl += float(event["net_pnl"])
        cumulative_r += float(event["realized_r_delta"])
        equity_curve.append({
            "timestamp": event["timestamp"].isoformat(),
            "equity": round(starting_equity + cumulative_pnl, 2),
            "cumulative_net_pnl": round(cumulative_pnl, 2),
            "cumulative_r": round(cumulative_r, 4),
            "event": event["event"],
            "strategy": event["strategy"],
            "signal_id": event["signal_id"],
            "quantity": event["quantity"],
            "gross_pnl": event["gross_pnl"],
            "allocated_transaction_costs": (
                event["allocated_transaction_costs"]
            ),
            "net_pnl": event["net_pnl"],
            "reason": event["reason"],
        })

    pnl_drawdown = (
        _drawdown(
            float(event["net_pnl"])
            for event in chronological_pnl_events
        )
        if pnl_complete
        else None
    )
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
    strategy_r_statistics = _strategy_r_statistics(resolved)
    capital_utilization_curve, average_premium_utilization_pct = (
        _capital_utilization(
            accepted,
            starting_equity=starting_equity,
            session_start=session_start,
            session_end=session_end,
        )
    )

    rejected = list(execution_metadata.get("rejected_opportunities") or [])
    daily_loss_trigger_events = list(
        execution_metadata.get("daily_loss_trigger_events") or []
    )
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
            "CHRONOLOGICAL_ACCEPTED_ENTRIES_AND_EXECUTION_EXIT_LEGS"
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
        average_premium_utilization_pct=average_premium_utilization_pct,
        capital_utilization_curve=capital_utilization_curve,
        rejected_opportunities=len(rejected),
        rejected_opportunity_details=rejected,
        risk_gate_block_counts={
            str(key): int(value) for key, value in sorted(block_counts.items())
        },
        daily_loss_trigger_events=daily_loss_trigger_events,
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
        strategy_r_statistics=strategy_r_statistics,
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
    execution_model_version: str = REPLAY_EXECUTION_MODEL_VERSION,
    contract_selection_policy: str,
    historical_source: str,
    data_provenance: dict[str, Any] | None = None,
    strategy_manifest: dict[str, Any] | None = None,
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
    strategy_manifest_payload = strategy_manifest or {
        strategy: {
            "revision": version,
            "fingerprint": (
                strategy_c_spec_fingerprint()
                if strategy == "DI_CONTINUATION"
                else strategy_d_spec_fingerprint()
                if strategy == "SR_MOMENTUM_BREAKOUT"
                else hashlib.sha256(
                    json.dumps(
                        {
                            "strategy": strategy,
                            "version": version,
                            "configuration_fingerprint": configuration_fingerprint,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            ),
        }
        for strategy, version in strategy_versions.items()
    }
    identity_payload = {
        "replay_engine_revision": REPLAY_ENGINE_REVISION,
        "session_date": session_date,
        "replay_mode": replay_mode,
        "configuration_fingerprint": configuration_fingerprint,
        "data_fingerprint": data_fingerprint,
        "cost_model_version": cost_model_version,
        "execution_model_version": execution_model_version,
        "contract_selection_policy": contract_selection_policy,
        "historical_source": historical_source,
        "strategy_versions": strategy_versions,
        "strategy_manifest": strategy_manifest_payload,
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
        "data_provenance": data_provenance or {},
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

    identity_checks = {
        "same_session_date": baseline.session_date == candidate.session_date,
        "same_replay_mode": baseline.replay_mode == candidate.replay_mode,
        "same_configuration_fingerprint": b_rep.get("configuration_fingerprint") == c_rep.get("configuration_fingerprint"),
        "same_data_fingerprint": b_rep.get("data_fingerprint") == c_rep.get("data_fingerprint"),
        "same_cost_model_version": b_rep.get("cost_model_version") == c_rep.get("cost_model_version"),
        "same_execution_model_version": b_rep.get("execution_model_version") == c_rep.get("execution_model_version"),
        "same_contract_selection_policy": b_rep.get("contract_selection_policy") == c_rep.get("contract_selection_policy"),
        "same_replay_engine_revision": b_rep.get("replay_engine_revision") == c_rep.get("replay_engine_revision"),
        "same_strategy_manifest": b_rep.get("strategy_manifest") == c_rep.get("strategy_manifest"),
        "same_data_provenance": b_rep.get("data_provenance") == c_rep.get("data_provenance"),
    }
    incompatible = sorted(
        name.removeprefix("same_")
        for name, matches in identity_checks.items()
        if not matches
    )
    return {
        "baseline_run_id": baseline.run_id,
        "candidate_run_id": candidate.run_id,
        "identity": identity_checks,
        "configuration_compatible": not incompatible,
        "configuration_mismatches": incompatible,
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
