"""Isolated paper runtime for frozen Strategy D V2.

Strategy D remains outside the production OMS until it is explicitly promoted.
This sidecar runs every strategy scheduler cycle, evaluates only completed real
market candles, captures a real option contract/quote for paper execution, and
tracks the frozen underlying stop / T1 / EMA9 / pivot lifecycle.

No broker order is ever created by this monitor.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from typing import Any, Sequence

from libs.contracts.models import Candle, generate_id
from services.historical.strategy_a_data_audit import IST
from services.historical.strategy_d_candidate_manifest import (
    CANDIDATE_ID,
    FREEZE_DATE,
    spec_fingerprint,
    validate_v2_config,
)
from services.strategy.features import FeatureEngine
from services.strategy.models import AutoTradingMode, DecisionLogEntry, TradeDirection
from services.strategy.strategies.sr_momentum_breakout import (
    REAL_SOURCES,
    PivotLevels,
    StrategyDConfig,
    StrategyDPositionManager,
    StrategyDSignal,
    evaluate_strategy_d_signal,
    previous_session_levels,
)


logger = logging.getLogger(__name__)
RUNTIME_KEY = "strategy_d_paper_v2"
MAX_PAPER_ENTRY_LATENCY_SECONDS = 300.0
MAX_PAPER_EVENT_QUOTE_LATENCY_SECONDS = 120.0


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo and value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    return None


def _signal_id(signal: StrategyDSignal) -> str:
    payload = "|".join(
        (
            signal.strategy_id,
            signal.timestamp.isoformat(),
            signal.option_type,
            signal.breakout_level_name,
            f"{signal.entry_price:.6f}",
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"STRAT-D-{digest}"


def _signal_from_payload(payload: dict[str, Any]) -> StrategyDSignal:
    levels_payload = dict(payload["levels"])
    levels = PivotLevels(
        session_date=datetime.fromisoformat(levels_payload["session_date"]).date(),
        source_session_date=datetime.fromisoformat(
            levels_payload["source_session_date"]
        ).date(),
        pdh=float(levels_payload["pdh"]),
        pdl=float(levels_payload["pdl"]),
        pdc=float(levels_payload["pdc"]),
        pivot=float(levels_payload["pivot"]),
        r1=float(levels_payload["r1"]),
        s1=float(levels_payload["s1"]),
        r2=float(levels_payload["r2"]),
        s2=float(levels_payload["s2"]),
    )
    return StrategyDSignal(
        strategy_id=str(payload["strategy_id"]),
        direction=TradeDirection(str(payload["direction"])),
        option_type=str(payload["option_type"]),
        timestamp=datetime.fromisoformat(
            str(payload["timestamp"]).replace("Z", "+00:00")
        ),
        breakout_level_name=str(payload["breakout_level_name"]),
        breakout_level=float(payload["breakout_level"]),
        entry_price=float(payload["entry_price"]),
        initial_stop=float(payload["initial_stop"]),
        risk_points=float(payload["risk_points"]),
        atr_5m=float(payload["atr_5m"]),
        rsi_previous=float(payload["rsi_previous"]),
        rsi_current=float(payload["rsi_current"]),
        rsi_clearance_points=float(payload["rsi_clearance_points"]),
        previous_day_range_atr=float(payload["previous_day_range_atr"]),
        vwap_reference_price=float(payload["vwap_reference_price"]),
        vwap=float(payload["vwap"]),
        vwap_source=str(payload["vwap_source"]),
        next_pivot_name=payload.get("next_pivot_name"),
        next_pivot_price=(
            float(payload["next_pivot_price"])
            if payload.get("next_pivot_price") is not None
            else None
        ),
        levels=levels,
    )


class StrategyDPaperMonitor:
    """Forward-paper observer for the frozen Strategy D V2 candidate."""

    def __init__(
        self,
        *,
        repository: Any,
        historical_service: Any = None,
        option_chain_service: Any = None,
        market_data_service: Any = None,
        contract_selector: Any = None,
        risk_config: Any = None,
        session_config: Any = None,
    ) -> None:
        self.repo = repository
        self.hist_svc = historical_service
        self.chain_svc = option_chain_service
        self.mkt_svc = market_data_service
        self.contract_selector = contract_selector
        self.risk_config = risk_config
        self.session_config = session_config
        self.config = StrategyDConfig.v2_candidate()
        validate_v2_config(self.config)
        self.manager = StrategyDPositionManager(
            risk_config=risk_config,
            session_config=session_config,
            strategy_d_config=self.config,
        )
        self.initialized = False
        self.disabled_reason: str | None = None
        self.runtime: dict[str, Any] = self._empty_runtime()

    @staticmethod
    def _empty_runtime() -> dict[str, Any]:
        return {
            "candidate_id": CANDIDATE_ID,
            "candidate_spec_fingerprint": spec_fingerprint(),
            "seen_signal_ids": [],
            "used_level_keys": [],
            "paper_closed_signal_ids": [],
            "tracked": {},
        }

    def refresh_dependencies(
        self,
        *,
        contract_selector: Any = None,
        risk_config: Any = None,
        session_config: Any = None,
    ) -> None:
        if contract_selector is not None:
            self.contract_selector = contract_selector
        if risk_config is not None:
            self.risk_config = risk_config
        if session_config is not None:
            self.session_config = session_config
        self.manager = StrategyDPositionManager(
            risk_config=self.risk_config,
            session_config=self.session_config,
            strategy_d_config=self.config,
        )

    async def initialize(self) -> None:
        self.initialized = True
        if not self.repo or not hasattr(self.repo, "get_runtime"):
            return
        try:
            stored = await self.repo.get_runtime(RUNTIME_KEY)
        except Exception:
            logger.exception("Strategy D paper runtime restore failed")
            return
        if not stored:
            return
        if stored.get("candidate_spec_fingerprint") != spec_fingerprint():
            self.disabled_reason = "FROZEN_CANDIDATE_FINGERPRINT_MISMATCH"
            return
        self.runtime = {
            **self._empty_runtime(),
            **stored,
            "seen_signal_ids": list(stored.get("seen_signal_ids") or []),
            "used_level_keys": list(stored.get("used_level_keys") or []),
            "paper_closed_signal_ids": list(
                stored.get("paper_closed_signal_ids") or []
            ),
            "tracked": dict(stored.get("tracked") or {}),
        }

    async def _save_runtime(self) -> None:
        if self.repo and hasattr(self.repo, "save_runtime"):
            await self.repo.save_runtime(self.runtime, RUNTIME_KEY)

    async def _log(
        self,
        *,
        timestamp: datetime,
        message: str,
        details: dict[str, Any],
    ) -> None:
        if not self.repo or not hasattr(self.repo, "save_decision_log"):
            return
        entry = DecisionLogEntry(
            id=f"TEL-D-{generate_id()}",
            timestamp=timestamp,
            category="STRATEGY_D_PAPER",
            strategy=CANDIDATE_ID,
            message=message,
            details=details,
        )
        await self.repo.save_decision_log(entry)

    async def _candles(
        self,
        instrument_id: str,
        interval: str,
        *,
        now: datetime,
        limit: int = 700,
    ) -> list[Candle]:
        if not self.hist_svc:
            return []
        candles = await self.hist_svc.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            limit=limit,
            requested_source="MIXED",
            allow_provider_fallback=True,
            allow_synthetic_fallback=False,
        )
        seconds = {"1m": 60, "5m": 300}[interval]
        return sorted(
            {
                candle.start_time: candle
                for candle in candles
                if candle.source in REAL_SOURCES
                and candle.interval == interval
                and candle.end_time <= now
                and abs(
                    (candle.end_time - candle.start_time).total_seconds()
                    - seconds
                )
                <= 5
            }.values(),
            key=lambda item: item.start_time,
        )

    async def _get_chain(self) -> dict[str, Any]:
        if not self.chain_svc:
            return {"source": "UNAVAILABLE", "strikes": []}
        try:
            chain = await self.chain_svc.get_chain(underlying="NIFTY")
        except Exception:
            logger.exception("Strategy D paper option-chain retrieval failed")
            return {"source": "UNAVAILABLE", "strikes": []}
        if str(chain.get("source", "")).upper() not in REAL_SOURCES:
            return {**chain, "source": "UNAVAILABLE"}
        return chain

    async def _quote_for_contract(
        self,
        selected: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        instrument_id = selected.get("instrument_id")
        if not instrument_id:
            return {
                "status": "UNAVAILABLE",
                "reason": "MISSING_INSTRUMENT_ID",
            }
        if self.mkt_svc is not None:
            quote = self.mkt_svc.get_latest_quote(instrument_id)
            if quote is not None:
                quote_time = _aware(getattr(quote, "timestamp", None))
                freshness = (
                    max(0.0, (now - quote_time).total_seconds())
                    if quote_time is not None
                    else None
                )
                result = {
                    "instrument_id": instrument_id,
                    "quote_timestamp": (
                        quote_time.isoformat() if quote_time else None
                    ),
                    "source": str(
                        getattr(quote, "source", "UNKNOWN")
                    ).upper(),
                    "freshness_seconds": freshness,
                    "bid": float(
                        getattr(quote, "best_bid", 0) or 0
                    ),
                    "ask": float(
                        getattr(quote, "best_ask", 0) or 0
                    ),
                    "ltp": float(
                        getattr(quote, "last_price", 0) or 0
                    ),
                }
                if (
                    result["source"] in REAL_SOURCES
                    and freshness is not None
                    and freshness <= 30.0
                    and result["bid"] > 0
                    and result["ask"] >= result["bid"]
                ):
                    return {
                        **result,
                        "status": "VALID",
                        "reason": None,
                    }

        chain = await self._get_chain()
        raw_time = chain.get("captured_at") or chain.get("timestamp")
        quote_time = _aware(raw_time) or now
        freshness = max(0.0, (now - quote_time).total_seconds())
        option_type = str(
            selected.get("option_type")
            or selected.get("right")
            or ""
        ).upper()
        side = "call" if option_type in {"CALL", "CE"} else "put"
        for strike_row in chain.get("strikes", []):
            leg = strike_row.get(side) or {}
            if leg.get("instrument_id") != instrument_id:
                continue
            bid = float(leg.get("bid", 0) or 0)
            ask = float(leg.get("ask", 0) or 0)
            source = str(chain.get("source", "UNKNOWN")).upper()
            valid = (
                source in REAL_SOURCES
                and freshness <= 30.0
                and bid > 0
                and ask >= bid
            )
            return {
                "instrument_id": instrument_id,
                "quote_timestamp": quote_time.isoformat(),
                "source": source,
                "freshness_seconds": freshness,
                "bid": bid,
                "ask": ask,
                "ltp": float(leg.get("ltp", 0) or 0),
                "status": "VALID" if valid else "UNAVAILABLE",
                "reason": None if valid else "INVALID_OR_STALE_QUOTE",
            }
        return {
            "status": "UNAVAILABLE",
            "reason": "SELECTED_CONTRACT_NOT_IN_CHAIN",
        }

    def _paper_costs(
        self,
        *,
        entry_price: float,
        entry_quantity: int,
        sell_legs: Sequence[tuple[float, int]],
    ) -> dict[str, float | str | None]:
        r = self.risk_config
        if r is None or entry_quantity <= 0:
            return {
                "transaction_costs": 0.0,
                "cost_assumption_version": None,
            }
        buy_turnover = entry_price * entry_quantity
        sell_turnover = sum(price * qty for price, qty in sell_legs)
        turnover = buy_turnover + sell_turnover
        brokerage = round(
            (1 + len(sell_legs))
            * float(
                getattr(r, "paper_brokerage_per_order", 0.0) or 0.0
            ),
            2,
        )
        exchange = round(
            turnover
            * float(
                getattr(r, "paper_exchange_charge_rate", 0.0) or 0.0
            ),
            2,
        )
        stt = round(
            sell_turnover
            * float(
                getattr(r, "paper_stt_sell_rate", 0.0) or 0.0
            ),
            2,
        )
        sebi = round(
            turnover
            * float(
                getattr(r, "paper_sebi_charge_rate", 0.0) or 0.0
            ),
            2,
        )
        stamp = round(
            buy_turnover
            * float(
                getattr(r, "paper_stamp_buy_rate", 0.0) or 0.0
            ),
            2,
        )
        gst = round(
            (brokerage + exchange + sebi)
            * float(getattr(r, "paper_gst_rate", 0.0) or 0.0),
            2,
        )
        total = round(brokerage + exchange + stt + sebi + stamp + gst, 2)
        return {
            "brokerage": brokerage,
            "exchange_charges": exchange,
            "stt": stt,
            "sebi_charges": sebi,
            "stamp_duty": stamp,
            "gst": gst,
            "transaction_costs": total,
            "cost_assumption_version": getattr(
                r,
                "paper_cost_assumption_version",
                None,
            ),
        }

    async def _capture_entry(
        self,
        signal: StrategyDSignal,
        *,
        observed_at: datetime,
    ) -> dict[str, Any]:
        signal_id = _signal_id(signal)
        chain = await self._get_chain()
        selected = None
        inspected: list[dict[str, Any]] = []
        reason = "CONTRACT_SELECTOR_UNAVAILABLE"
        if self.contract_selector is not None:
            selected, inspected, reason = self.contract_selector.select_contract(
                direction=signal.direction,
                spot_price=signal.entry_price,
                option_chain=chain,
                strategy_a=False,
                as_of=observed_at,
            )
        latency = max(
            0.0,
            (observed_at - signal.timestamp).total_seconds(),
        )
        selected_payload = (
            selected.model_dump(mode="json") if selected else None
        )
        lots = 0
        quantity = 0
        entry_fill = None
        paper_status = "SKIPPED"
        rejection = reason or "CONTRACT_SELECTION_REJECTED"
        if selected is not None:
            slippage = float(
                getattr(
                    self.risk_config,
                    "paper_slippage_points",
                    0.0,
                )
                or 0.0
            )
            entry_fill = round(float(selected.ask_price) + slippage, 6)
            lots, quantity = self.manager.size_option_position(
                entry_premium=entry_fill,
                lot_size=int(selected.lot_size),
                account_equity=float(
                    getattr(
                        self.risk_config,
                        "account_equity",
                        0.0,
                    )
                    or 0.0
                )
                or None,
            )
            if latency > MAX_PAPER_ENTRY_LATENCY_SECONDS:
                rejection = "STALE_SIGNAL_FOR_PAPER_ENTRY"
            elif lots < 1 or quantity < 1:
                rejection = "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"
            else:
                paper_status = "OPEN"
                rejection = None

        tracked = {
            "signal_id": signal_id,
            "signal": signal.to_dict(),
            "entry_observed_at": observed_at.isoformat(),
            "signal_to_observation_seconds": latency,
            "selected_contract": selected_payload,
            "selector_candidates_checked": len(inspected),
            "selector_rejection_reason": reason,
            "entry_raw_ask": (
                float(selected.ask_price) if selected else None
            ),
            "entry_executable_price": entry_fill,
            "paper_lots": lots,
            "paper_quantity": quantity,
            "paper_initial_quantity": quantity,
            "paper_remaining_quantity": quantity,
            "paper_status": paper_status,
            "paper_rejection_reason": rejection,
            "paper_partial_status": "NOT_REACHED",
            "paper_partial_quantity": 0,
            "paper_partial_fill": None,
            "paper_realized_gross": 0.0,
            "sell_legs": [],
            "current_underlying_stop": signal.initial_stop,
            "current_r": 0.0,
            "lifecycle_state": "OPEN_INITIAL_RISK",
            "ema9_5m": None,
            "last_quote": None,
        }
        await self._log(
            timestamp=observed_at,
            message="CANDIDATE_SIGNAL_OBSERVED",
            details={
                "signal": signal.to_dict(),
                "selected_contract": selected_payload,
                "paper_status": paper_status,
                "paper_rejection_reason": rejection,
                "lots": lots,
                "quantity": quantity,
                "broker_called": False,
            },
        )
        if paper_status == "OPEN":
            if self.repo and hasattr(self.repo, "save_execution_ledger"):
                await self.repo.save_execution_ledger({
                    "ledger_id": f"PAPER-D-BUY-{generate_id()}",
                    "trade_id": f"PAPER-D:{signal_id}",
                    "side": "BUY",
                    "timestamp": observed_at.isoformat(),
                    "raw_bid": float(selected.bid_price),
                    "raw_ask": float(selected.ask_price),
                    "raw_ltp": float(selected.ltp),
                    "executable_price": entry_fill,
                    "slippage_points": float(
                        getattr(
                            self.risk_config,
                            "paper_slippage_points",
                            0.0,
                        )
                        or 0.0
                    ),
                    "quantity": quantity,
                    "source": chain.get("source", "UNKNOWN"),
                    "cost_assumption_version": getattr(
                        self.risk_config,
                        "paper_cost_assumption_version",
                        "unknown",
                    ),
                    "reason": "STRATEGY_D_PAPER_ENTRY",
                })
        return tracked

    async def _apply_partial_if_due(
        self,
        tracked: dict[str, Any],
        *,
        lifecycle: Any,
        quote: dict[str, Any],
        observed_at: datetime,
    ) -> None:
        if lifecycle.scale_out_time is None:
            return
        if tracked.get("paper_partial_status") not in {
            "NOT_REACHED",
            "T1_QUOTE_PENDING",
        }:
            return
        total_lots = int(tracked.get("paper_lots") or 0)
        partial_lots = self.manager.scale_out_lots(total_lots)
        if partial_lots < 1:
            tracked["paper_partial_status"] = "ONE_LOT_NO_PARTIAL"
            return
        scale_time = lifecycle.scale_out_time
        quote_time = _aware(quote.get("quote_timestamp"))
        latency = (
            (quote_time - scale_time).total_seconds()
            if quote_time is not None
            else None
        )
        if (
            quote.get("status") != "VALID"
            or quote_time is None
            or latency is None
            or latency < -5.0
            or latency > MAX_PAPER_EVENT_QUOTE_LATENCY_SECONDS
        ):
            if (
                observed_at - scale_time
            ).total_seconds() > MAX_PAPER_EVENT_QUOTE_LATENCY_SECONDS:
                tracked["paper_partial_status"] = "INCOMPLETE_T1_QUOTE"
            else:
                tracked["paper_partial_status"] = "T1_QUOTE_PENDING"
            return

        slippage = float(
            getattr(self.risk_config, "paper_slippage_points", 0.0)
            or 0.0
        )
        fill = round(max(0.0, float(quote["bid"]) - slippage), 6)
        if fill <= 0:
            tracked["paper_partial_status"] = "INCOMPLETE_T1_QUOTE"
            return
        lot_size = int(
            (tracked.get("selected_contract") or {}).get("lot_size")
            or 0
        )
        quantity = partial_lots * lot_size
        entry_fill = float(tracked["entry_executable_price"])
        realized = round((fill - entry_fill) * quantity, 2)
        tracked["paper_partial_status"] = "FILLED"
        tracked["paper_partial_quantity"] = quantity
        tracked["paper_partial_fill"] = fill
        tracked["paper_partial_time"] = observed_at.isoformat()
        tracked["paper_realized_gross"] = realized
        tracked["paper_remaining_quantity"] = max(
            0,
            int(tracked["paper_initial_quantity"]) - quantity,
        )
        tracked.setdefault("sell_legs", []).append(
            {"price": fill, "quantity": quantity}
        )
        if self.repo and hasattr(self.repo, "save_execution_ledger"):
            await self.repo.save_execution_ledger({
                "ledger_id": f"PAPER-D-T1-{generate_id()}",
                "trade_id": f"PAPER-D:{tracked['signal_id']}",
                "side": "SELL",
                "timestamp": observed_at.isoformat(),
                "raw_bid": float(quote["bid"]),
                "raw_ask": float(quote.get("ask") or 0.0),
                "raw_ltp": float(quote.get("ltp") or 0.0),
                "executable_price": fill,
                "slippage_points": slippage,
                "quantity": quantity,
                "source": quote.get("source", "UNKNOWN"),
                "cost_assumption_version": getattr(
                    self.risk_config,
                    "paper_cost_assumption_version",
                    "unknown",
                ),
                "reason": "STRATEGY_D_T1_PARTIAL",
            })

    async def _close_if_resolved(
        self,
        tracked: dict[str, Any],
        *,
        lifecycle: Any,
        quote: dict[str, Any],
        observed_at: datetime,
    ) -> None:
        if lifecycle.runner_exit_reason == "DATA_END":
            return
        if tracked.get("paper_status") == "CLOSED":
            return
        if (
            tracked.get("paper_partial_status") == "INCOMPLETE_T1_QUOTE"
            and int(tracked.get("paper_lots") or 0) >= 2
        ):
            tracked["paper_status"] = "INCOMPLETE_T1_QUOTE"
            tracked["paper_rejection_reason"] = (
                "T1_PARTIAL_FILL_UNAVAILABLE"
            )
            return
        exit_time = lifecycle.exit_time
        quote_time = _aware(quote.get("quote_timestamp"))
        latency = (
            (quote_time - exit_time).total_seconds()
            if quote_time is not None
            else None
        )
        if (
            quote.get("status") != "VALID"
            or quote_time is None
            or latency is None
            or latency < -5.0
            or latency > MAX_PAPER_EVENT_QUOTE_LATENCY_SECONDS
        ):
            if (
                observed_at - exit_time
            ).total_seconds() > MAX_PAPER_EVENT_QUOTE_LATENCY_SECONDS:
                tracked["paper_status"] = "INCOMPLETE_EXIT_QUOTE"
                tracked["paper_rejection_reason"] = (
                    "NO_TIMELY_EXECUTABLE_EXIT_QUOTE"
                )
            else:
                tracked["paper_status"] = "EXIT_QUOTE_PENDING"
            return

        remaining = int(tracked.get("paper_remaining_quantity") or 0)
        if remaining <= 0:
            tracked["paper_status"] = "CLOSED"
            return
        slippage = float(
            getattr(self.risk_config, "paper_slippage_points", 0.0)
            or 0.0
        )
        fill = round(max(0.0, float(quote["bid"]) - slippage), 6)
        if fill <= 0:
            tracked["paper_status"] = "INCOMPLETE_EXIT_QUOTE"
            tracked["paper_rejection_reason"] = "NON_POSITIVE_EXIT_FILL"
            return
        entry_fill = float(tracked["entry_executable_price"])
        final_gross = round((fill - entry_fill) * remaining, 2)
        total_gross = round(
            float(tracked.get("paper_realized_gross") or 0.0)
            + final_gross,
            2,
        )
        tracked.setdefault("sell_legs", []).append(
            {"price": fill, "quantity": remaining}
        )
        legs = [
            (float(row["price"]), int(row["quantity"]))
            for row in tracked["sell_legs"]
        ]
        costs = self._paper_costs(
            entry_price=entry_fill,
            entry_quantity=int(tracked["paper_initial_quantity"]),
            sell_legs=legs,
        )
        tracked.update({
            "paper_status": "CLOSED",
            "paper_exit_time": observed_at.isoformat(),
            "paper_exit_underlying_time": exit_time.isoformat(),
            "paper_exit_fill": fill,
            "paper_gross_pnl": total_gross,
            "paper_transaction_costs": float(
                costs["transaction_costs"]
            ),
            "paper_net_pnl": round(
                total_gross - float(costs["transaction_costs"]),
                2,
            ),
            "paper_cost_breakdown": costs,
            "paper_underlying_realized_r": lifecycle.realized_r,
            "paper_underlying_exit_reason": lifecycle.runner_exit_reason,
            "paper_remaining_quantity": 0,
            "paper_rejection_reason": None,
        })
        if self.repo and hasattr(self.repo, "save_execution_ledger"):
            await self.repo.save_execution_ledger({
                "ledger_id": f"PAPER-D-SELL-{generate_id()}",
                "trade_id": f"PAPER-D:{tracked['signal_id']}",
                "side": "SELL",
                "timestamp": observed_at.isoformat(),
                "raw_bid": float(quote["bid"]),
                "raw_ask": float(quote.get("ask") or 0.0),
                "raw_ltp": float(quote.get("ltp") or 0.0),
                "executable_price": fill,
                "slippage_points": slippage,
                "quantity": remaining,
                "source": quote.get("source", "UNKNOWN"),
                "cost_assumption_version": costs[
                    "cost_assumption_version"
                ]
                or "unknown",
                "reason": (
                    "STRATEGY_D_"
                    + str(lifecycle.runner_exit_reason)
                ),
            })
        await self._log(
            timestamp=observed_at,
            message="PAPER_TRADE_CLOSED",
            details={
                "signal_id": tracked["signal_id"],
                "paper_trade": tracked,
                "execution_mode": AutoTradingMode.PAPER.value,
                "broker_called": False,
            },
        )

    async def _manage_open_trade(
        self,
        tracked: dict[str, Any],
        *,
        spot_5m: Sequence[Candle],
        spot_1m: Sequence[Candle],
        now: datetime,
    ) -> None:
        signal = _signal_from_payload(dict(tracked["signal"]))
        history = [
            bar for bar in spot_5m
            if bar.end_time <= signal.timestamp
        ]
        future = [
            bar for bar in spot_5m
            if bar.start_time >= signal.timestamp
            and bar.end_time <= now
        ]
        latest_close = (
            float(spot_5m[-1].close)
            if spot_5m
            else signal.entry_price
        )
        current_r = (
            (latest_close - signal.entry_price) / signal.risk_points
            if signal.direction == TradeDirection.BULLISH
            else (signal.entry_price - latest_close) / signal.risk_points
        )
        closes = [float(bar.close) for bar in spot_5m]
        ema9 = (
            FeatureEngine.calculate_ema(closes, 9)
            if closes
            else signal.entry_price
        )
        tracked["current_r"] = round(current_r, 6)
        tracked["ema9_5m"] = round(float(ema9), 6)

        lifecycle = None
        if future:
            lifecycle = self.manager.replay_underlying_lifecycle(
                signal,
                history_through_entry=history,
                future_bars=future,
                one_minute_bars=spot_1m,
            )
            tracked["current_underlying_stop"] = lifecycle.final_stop
            tracked["scale_out_time"] = (
                lifecycle.scale_out_time.isoformat()
                if lifecycle.scale_out_time
                else None
            )
            tracked["lifecycle_state"] = (
                "PROTECTED_BREAKEVEN"
                if lifecycle.scale_out_time is not None
                else "OPEN_INITIAL_RISK"
            )
            tracked["underlying_exit_reason"] = (
                None
                if lifecycle.runner_exit_reason == "DATA_END"
                else lifecycle.runner_exit_reason
            )
            tracked["underlying_exit_time"] = (
                None
                if lifecycle.runner_exit_reason == "DATA_END"
                else lifecycle.exit_time.isoformat()
            )
            tracked["underlying_realized_r"] = (
                None
                if lifecycle.runner_exit_reason == "DATA_END"
                else lifecycle.realized_r
            )
        else:
            tracked["current_underlying_stop"] = signal.initial_stop
            tracked["lifecycle_state"] = "OPEN_INITIAL_RISK"
            tracked["underlying_exit_reason"] = None
            tracked["underlying_exit_time"] = None
            tracked["underlying_realized_r"] = None

        selected = tracked.get("selected_contract") or {}
        quote = await self._quote_for_contract(selected, now=now)
        tracked["last_quote"] = quote
        if quote.get("status") == "VALID":
            current_option = float(
                quote.get("ltp")
                or quote.get("bid")
                or 0.0
            )
            remaining = int(
                tracked.get("paper_remaining_quantity") or 0
            )
            tracked["paper_unrealized_pnl"] = round(
                (
                    current_option
                    - float(tracked["entry_executable_price"])
                )
                * remaining,
                2,
            )

        if lifecycle is not None:
            await self._apply_partial_if_due(
                tracked,
                lifecycle=lifecycle,
                quote=quote,
                observed_at=now,
            )
            await self._close_if_resolved(
                tracked,
                lifecycle=lifecycle,
                quote=quote,
                observed_at=now,
            )

    def _market_snapshot(
        self,
        *,
        spot_5m: Sequence[Candle],
        futures_5m: Sequence[Candle],
        levels: PivotLevels,
    ) -> dict[str, Any]:
        closes = [float(bar.close) for bar in spot_5m]
        atr = (
            FeatureEngine.calculate_atr(list(spot_5m), 14)
            if len(spot_5m) >= 15
            else 0.0
        )
        rsi = (
            FeatureEngine.calculate_rsi(closes, 14)
            if len(closes) >= 15
            else 0.0
        )
        ema9 = (
            FeatureEngine.calculate_ema(closes, 9)
            if closes
            else 0.0
        )
        day = spot_5m[-1].end_time.astimezone(IST).date()
        day_futures = [
            bar for bar in futures_5m
            if bar.end_time.astimezone(IST).date() == day
        ]
        vwap = (
            FeatureEngine.calculate_futures_vwap(day_futures)
            if day_futures
            else 0.0
        )
        return {
            "spot_price": float(spot_5m[-1].close),
            "rsi_5m": round(float(rsi), 4),
            "atr_5m": round(float(atr), 4),
            "ema9_5m": round(float(ema9), 4),
            "futures_price": (
                float(day_futures[-1].close)
                if day_futures
                else None
            ),
            "futures_vwap": round(float(vwap), 4),
            "previous_day_range_atr": (
                round((levels.pdh - levels.pdl) / atr, 4)
                if atr > 0
                else None
            ),
            "levels": levels.to_dict(),
        }

    async def observe(
        self,
        *,
        active_futures_instrument: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if not self.initialized:
            await self.initialize()
        if self.disabled_reason:
            return {
                "status": "DISABLED",
                "reason": self.disabled_reason,
            }
        local_day = now.astimezone(IST).date()
        if local_day < FREEZE_DATE:
            return {
                "status": "WAITING_FOR_FREEZE_DATE",
                "freeze_date": FREEZE_DATE.isoformat(),
            }
        if not active_futures_instrument or not self.hist_svc:
            return {"status": "NO_ACTIVE_FUTURES_DATA"}

        try:
            spot_5m = await self._candles(
                "INST-NIFTY-INDEX",
                "5m",
                now=now,
            )
            spot_1m = await self._candles(
                "INST-NIFTY-INDEX",
                "1m",
                now=now,
            )
            futures_5m = await self._candles(
                active_futures_instrument,
                "5m",
                now=now,
            )
        except Exception:
            logger.exception("Strategy D paper market-data retrieval failed")
            return {"status": "DATA_RETRIEVAL_FAILED"}

        levels = previous_session_levels(spot_5m, local_day)
        if levels is None or not spot_5m or not futures_5m:
            return {
                "status": "MARKET_DATA_UNAVAILABLE",
                "spot_5m": len(spot_5m),
                "spot_1m": len(spot_1m),
                "futures_5m": len(futures_5m),
            }

        tracked_map = self.runtime.setdefault("tracked", {})
        changed = False
        for tracked in tracked_map.values():
            if tracked.get("paper_status") in {
                "OPEN",
                "EXIT_QUOTE_PENDING",
            }:
                before = dict(tracked)
                await self._manage_open_trade(
                    tracked,
                    spot_5m=spot_5m,
                    spot_1m=spot_1m,
                    now=now,
                )
                if tracked != before:
                    changed = True

        open_tracks = [
            row for row in tracked_map.values()
            if row.get("paper_status") in {
                "OPEN",
                "EXIT_QUOTE_PENDING",
            }
        ]
        seen = set(self.runtime.get("seen_signal_ids") or [])
        used_levels = set(self.runtime.get("used_level_keys") or [])
        closed_ids = set(
            self.runtime.get("paper_closed_signal_ids") or []
        )

        day_bars = [
            bar for bar in spot_5m
            if bar.end_time.astimezone(IST).date() == local_day
        ]
        discovered: list[StrategyDSignal] = []
        for bar in day_bars:
            history = [
                item for item in spot_5m
                if item.end_time <= bar.end_time
            ]
            futures_history = [
                item for item in futures_5m
                if item.end_time <= bar.end_time
            ]
            signal = evaluate_strategy_d_signal(
                history,
                futures_history,
                levels,
                self.config,
            )
            if signal is not None:
                discovered.append(signal)

        for signal in discovered:
            signal_id = _signal_id(signal)
            if signal_id in seen:
                continue
            level_key = (
                f"{local_day.isoformat()}|{signal.option_type}|"
                f"{signal.breakout_level_name}"
            )
            latency = max(
                0.0,
                (now - signal.timestamp).total_seconds(),
            )
            if open_tracks:
                seen.add(signal_id)
                changed = True
                continue
            if level_key in used_levels:
                seen.add(signal_id)
                changed = True
                continue
            if latency > MAX_PAPER_ENTRY_LATENCY_SECONDS:
                seen.add(signal_id)
                changed = True
                continue

            tracked = await self._capture_entry(
                signal,
                observed_at=now,
            )
            tracked_map[signal_id] = tracked
            seen.add(signal_id)
            used_levels.add(level_key)
            changed = True
            if tracked.get("paper_status") == "OPEN":
                open_tracks = [tracked]

        for signal_id, tracked in tracked_map.items():
            if tracked.get("paper_status") == "CLOSED":
                closed_ids.add(signal_id)

        if changed:
            self.runtime["seen_signal_ids"] = sorted(seen)
            self.runtime["used_level_keys"] = sorted(used_levels)
            self.runtime["paper_closed_signal_ids"] = sorted(closed_ids)
            await self._save_runtime()

        tracked_values = list(tracked_map.values())
        open_paper = [
            row for row in tracked_values
            if row.get("paper_status") in {
                "OPEN",
                "EXIT_QUOTE_PENDING",
            }
        ]
        closed_paper = [
            row for row in tracked_values
            if row.get("paper_status") == "CLOSED"
        ]
        incomplete = [
            row for row in tracked_values
            if str(row.get("paper_status", "")).startswith(
                "INCOMPLETE"
            )
            or str(
                row.get("paper_partial_status", "")
            ).startswith("INCOMPLETE")
        ]
        latest_signal = (
            discovered[-1].to_dict() if discovered else None
        )
        recent_paper_trades = sorted(
            tracked_values,
            key=lambda row: str(row.get("entry_observed_at") or ""),
            reverse=True,
        )[:10]
        return {
            "status": (
                "PAPER_POSITION_OPEN"
                if open_paper
                else "MONITORING"
            ),
            "candidate_id": CANDIDATE_ID,
            "candidate_spec_fingerprint": spec_fingerprint(),
            "execution_mode": AutoTradingMode.PAPER.value,
            "live_trading_allowed": False,
            "broker_called": False,
            "orders_created": False,
            "freeze_date": FREEZE_DATE.isoformat(),
            "native_spot_5m_candles": len(spot_5m),
            "native_spot_1m_candles": len(spot_1m),
            "native_futures_5m_candles": len(futures_5m),
            "signals_seen": len(seen),
            "paper_open_trades": len(open_paper),
            "paper_closed_trades": len(closed_paper),
            "paper_incomplete_trades": len(incomplete),
            "paper_net_pnl": round(
                sum(
                    float(row.get("paper_net_pnl") or 0.0)
                    for row in closed_paper
                ),
                2,
            ),
            "active_paper_trade": (
                open_paper[0] if open_paper else None
            ),
            "paper_trades": recent_paper_trades,
            "latest_signal": latest_signal,
            "market": self._market_snapshot(
                spot_5m=spot_5m,
                futures_5m=futures_5m,
                levels=levels,
            ),
        }
