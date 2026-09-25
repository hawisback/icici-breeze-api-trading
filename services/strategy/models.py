"""Domain models and configuration schemas for NIFTY Intraday Options Auto-Trading.
Based on implementation/NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from math import isclose
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, field_name: str) -> None:
    """Reject timestamps whose timezone semantics are ambiguous."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class AutoTradingMode(str, Enum):
    PAPER = "PAPER"
    SHADOW_ONLY = "SHADOW_ONLY"
    LIVE = "LIVE"
    DISABLED = "DISABLED"


class StrategyName(str, Enum):
    TREND_PULLBACK = "TREND_PULLBACK"
    VOLATILITY_BREAKOUT = "VOLATILITY_BREAKOUT"
    DI_CONTINUATION = "DI_CONTINUATION"
    SR_MOMENTUM_BREAKOUT = "SR_MOMENTUM_BREAKOUT"
    PIVOT_VWAP_SCALP = "PIVOT_VWAP_SCALP"


STRATEGY_A_REVISION = 5
STRATEGY_A_VERSION_ID = "trend_pullback_r5"
STRATEGY_A_DISPLAY_LABEL = "Strategy A · Trend Pullback R5"


class StrategyState(str, Enum):
    """Deterministic Strategy A lifecycle states.

    Legacy values are accepted by the enum parser through ``_missing_`` for
    old API/runtime payloads, but new serialized values use the five-state
    contract below. ``TRIGGERED`` maps to ``ARMED`` because the old evaluator
    represented a fired/pre-entry condition, not a broker-confirmed fill.
    """

    FLAT = "FLAT"
    SETUP = "SETUP"
    ARMED = "ARMED"
    ENTERED = "ENTERED"
    COOLDOWN = "COOLDOWN"

    @classmethod
    def _missing_(cls, value: object) -> Optional["StrategyState"]:
        # Compatibility for persisted/API state written before the contract
        # was introduced.  These are aliases, not new lifecycle states.
        legacy = {"SEARCHING": cls.FLAT, "TRIGGERED": cls.ARMED, "PAUSED": cls.FLAT}
        return legacy.get(value)


class TradeDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class StrategyDirection(str, Enum):
    """Strategy A direction and its option execution vehicle."""

    CALL = "CALL"
    PUT = "PUT"

    @classmethod
    def from_trade_direction(cls, direction: TradeDirection) -> "StrategyDirection":
        return cls.CALL if direction == TradeDirection.BULLISH else cls.PUT

    @property
    def trade_direction(self) -> TradeDirection:
        return TradeDirection.BULLISH if self is self.CALL else TradeDirection.BEARISH


class SetupInvalidationState(str, Enum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class HistoricalReplaySource(str, Enum):
    """Explicit provider selection for historical replay inputs."""

    BREEZE = "BREEZE"
    KITE = "KITE"
    LIVE = "LIVE"
    MIXED = "MIXED"


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class TradeLifecycleState(str, Enum):
    ENTRY_PENDING = "ENTRY_PENDING"
    EXIT_PENDING = "EXIT_PENDING"
    OPEN_INITIAL_RISK = "OPEN_INITIAL_RISK"
    PROTECTED_BREAKEVEN = "PROTECTED_BREAKEVEN"
    PROFIT_LOCKED = "PROFIT_LOCKED"
    RUNNER_MODE = "RUNNER_MODE"
    CLOSED = "CLOSED"


class OptionSelectionConfig(BaseModel):
    """Strategy-aware option execution constraints.

    Premium fields remain only as compatibility fields for Strategy B/UI
    callers.  Strategy A moneyness is determined by delta and never by a
    premium cap.
    """
    model_config = ConfigDict(extra="forbid")
    max_option_premium: float = Field(default=70.00, ge=5.0, le=500.0, description="Upper ceiling for option premium purchase")
    min_option_premium: float = Field(default=15.00, ge=1.0, le=100.0, description="Lower floor to avoid ultra-low delta lotto options")
    max_otm_strikes: int = Field(default=4, ge=0, le=10, description="Maximum number of strikes out-of-the-money")
    min_open_interest: int = Field(default=10000, ge=1000, description="Minimum contract open interest for liquidity")
    max_bid_ask_spread_pct: float = Field(default=3.0, ge=0.5, le=10.0, description="Maximum acceptable bid-ask spread %")
    prefer_premium_closest_to_cap: bool = Field(default=True, description="Prefer the eligible contract closest to max_option_premium")
    use_current_expiry_on_0dte: bool = Field(default=False, description="Whether to trade 0DTE on expiry day or roll to next weekly")
    preferred_delta_min: float = Field(default=0.60, gt=0.0, lt=1.0)
    preferred_delta_max: float = Field(default=0.65, gt=0.0, lt=1.0)
    allowed_delta_min: float = Field(default=0.55, gt=0.0, lt=1.0)
    allowed_delta_max: float = Field(default=0.70, gt=0.0, lt=1.0)
    minimum_expiry_sessions_remaining: int = Field(default=2, ge=0)
    max_quote_age_seconds: float = Field(default=30.0, gt=0.0)
    minimum_volume: int = Field(default=0, ge=0)
    exchange_holidays: tuple[date, ...] = Field(default=(), description="NSE holidays required for exact expiry-session counting")

    @model_validator(mode="after")
    def validate_delta_ranges(self) -> "OptionSelectionConfig":
        if self.preferred_delta_min > self.preferred_delta_max:
            raise ValueError("preferred delta range is inverted")
        if self.allowed_delta_min > self.allowed_delta_max:
            raise ValueError("allowed delta range is inverted")
        if not (self.allowed_delta_min <= self.preferred_delta_min <= self.preferred_delta_max <= self.allowed_delta_max):
            raise ValueError("preferred delta range must be inside allowed delta range")
        return self


class RiskConfig(BaseModel):
    """Risk management guardrails and sizing controls."""
    max_lots_per_trade: int = Field(default=100, ge=1)
    entry_order_timeout_sec: int = Field(default=10, ge=1, le=120)
    breakeven_buffer_points: float = Field(default=2.0, ge=0)
    max_trades_per_strategy_per_day: int = Field(default=5, ge=1, le=20)
    max_trade_capital: float = Field(default=50000.0, ge=5000.0, description="Maximum total capital per single trade")
    risk_per_trade_pct_of_account: float = Field(default=0.50, ge=0.1, le=5.0, description="Account % risk per trade")
    max_daily_loss_r: float = Field(default=2.0, ge=0.5, le=10.0, description="Daily loss limit in R multiples")
    max_daily_loss_pct: float = Field(default=1.5, ge=0.5, le=5.0, description="Daily loss limit as % of account")
    max_failed_trades_per_strategy: int = Field(default=2, ge=1, le=5, description="Max losing trades per strategy per day")
    max_trades_per_day: int = Field(default=5, ge=1, le=20, description="Total allowed trades per session")
    max_concurrent_positions: int = Field(default=1, ge=1, le=3, description="Maximum simultaneous open positions")
    cooldown_after_loss_min: int = Field(default=10, ge=0, le=60, description="Cooldown wait in minutes after a losing exit")
    option_hard_stop_pct: float = Field(default=25.0, ge=10.0, le=50.0, description="Emergency option premium loss stop %")
    broker_protective_stop_limit_buffer_pct: float = Field(
        default=10.0,
        ge=1.0,
        le=25.0,
        description="SELL SL-limit price buffer below the emergency option trigger",
    )
    broker_protective_stop_max_failures: int = Field(default=2, ge=1, le=5)
    broker_protective_stop_cancel_timeout_sec: float = Field(
        default=5.0, ge=1.0, le=30.0
    )
    broker_protective_stop_cancel_max_attempts: int = Field(
        default=3, ge=1, le=5
    )
    account_equity: float = Field(default=500000.0, gt=0)
    # Forward option-validation assumptions.  These do not alter signal,
    # selector, PositionManager, or exit rules.
    paper_slippage_points: float = Field(default=0.0, ge=0.0)
    paper_brokerage_per_order: float = Field(default=20.0, ge=0.0)
    paper_exchange_charge_rate: float = Field(default=0.0003503, ge=0.0)
    paper_stt_sell_rate: float = Field(default=0.001, ge=0.0)
    paper_gst_rate: float = Field(default=0.18, ge=0.0)
    paper_sebi_charge_rate: float = Field(default=0.000001, ge=0.0)
    paper_stamp_buy_rate: float = Field(default=0.00003, ge=0.0)
    paper_cost_assumption_version: str = "paper_options_costs_v1"


class SessionTimersConfig(BaseModel):
    """Intraday trading window schedules in IST."""
    strategy_b_no_new_trade_before: str = Field(default="09:25", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    no_new_trade_before: str = Field(default="09:20", description="No entries before HH:MM IST")
    no_new_trade_after: str = Field(default="14:45", description="No new entries after HH:MM IST")
    force_exit_time: str = Field(default="15:20", description="Intraday square-off time HH:MM IST")


class StrategyTunablesConfig(BaseModel):
    """Single configuration source for strategy hypotheses.

    The Strategy A fields are deliberately kept here, alongside the existing
    Strategy B fields, so existing ``config.tunables`` callers continue to
    work.  Strategy B's ADX threshold is explicit and remains at its prior
    value; ``adx_threshold`` is now the authoritative Strategy A default.
    """
    bb_width_percentile_threshold: float = Field(default=25.0, ge=15, le=50)
    compression_lookback_bars: int = Field(default=8, ge=6, le=10)
    box_max_age_bars: int = Field(default=8, ge=1, le=20)
    breakout_buffer_atr: float = Field(default=0.05, ge=0.01, le=0.10)
    breakout_max_extension_atr: float = Field(default=0.75, ge=0.60, le=1.5)
    evaluation_interval_sec: int = Field(default=2, ge=1, le=10, description="Scheduler loop interval in seconds")
    trend_pullback_enabled: bool = Field(default=True)
    volatility_breakout_enabled: bool = Field(default=True)
    di_continuation_enabled: bool = Field(
        default=True,
        description="Enable Strategy C DI Continuation signal evaluation and execution",
    )
    sr_momentum_breakout_enabled: bool = Field(
        default=True,
        description="Enable Strategy D S&R Momentum signal evaluation and execution",
    )
    pivot_vwap_scalp_enabled: bool = Field(
        default=False,
        description="Enable Strategy E Pivot/VWAP 5-minute scalp evaluation and execution",
    )
    strategy_e_countertrend_enabled: bool = Field(default=True)
    strategy_e_swing_lookback: int = Field(default=2, ge=1, le=5)
    strategy_e_volume_lookback: int = Field(default=20, ge=5, le=100)
    strategy_e_rvol_confirmation: float = Field(default=1.20, ge=0.5, le=5.0)
    strategy_e_sr_lookback_bars: int = Field(default=30, ge=10, le=100)
    strategy_e_sr_buffer_points: float = Field(default=2.0, ge=0.0, le=25.0)
    strategy_e_counter_zone_points: float = Field(default=6.0, ge=0.0, le=50.0)
    strategy_e_stop_buffer_points: float = Field(default=2.0, ge=0.0, le=25.0)
    strategy_e_max_stop_points: float = Field(default=30.0, gt=0.0, le=200.0)
    strategy_e_trend_target_points: float = Field(default=20.0, gt=0.0, le=200.0)
    strategy_e_counter_target_points: float = Field(default=12.0, gt=0.0, le=100.0)
    strategy_e_min_reward_risk: float = Field(default=1.0, gt=0.0, le=5.0)
    strategy_e_min_room_to_level_points: float = Field(default=6.0, ge=0.0, le=100.0)
    strategy_e_chop_lookback_bars: int = Field(default=6, ge=4, le=20)
    strategy_e_chop_cross_threshold: int = Field(default=2, ge=1, le=10)
    strategy_e_flat_vwap_lookback_bars: int = Field(default=3, ge=1, le=10)
    strategy_e_flat_vwap_threshold_points: float = Field(default=3.0, ge=0.0, le=50.0)
    strategy_e_lots: int = Field(default=1, ge=1, le=20)
    strategy_e_max_signal_age_seconds: float = Field(default=180.0, gt=0.0, le=300.0)
    strategy_e_entry_start: str = Field(default="09:25", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    strategy_e_entry_end: str = Field(default="14:45", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    strategy_e_forced_exit_time: str = Field(default="15:15", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    # Strategy A authoritative defaults.
    ema_fast_period: int = Field(default=20, ge=1, description="Fast EMA period on completed 15m bars")
    ema_slow_period: int = Field(default=50, ge=2, description="Slow EMA period on completed 15m bars")
    adx_period: int = Field(default=14, ge=1)
    adx_threshold: float = Field(
        default=22.0,
        ge=0.0,
        le=100.0,
        description="Legacy hard-ADX compatibility value; Strategy A R5 does not use a hard ADX floor",
    )
    momentum_adx_min_delta_2bars: float = Field(
        default=-2.0,
        ge=-100.0,
        le=100.0,
        description="Minimum allowed ADX14 change versus two completed 15m bars earlier",
    )
    momentum_ema20_slope_min_atr: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum directional EMA20 one-bar slope in current ATR units",
    )
    momentum_ema20_slope_max_atr: float = Field(
        default=0.15,
        gt=0.0,
        le=1.0,
        description="Exclusive maximum directional EMA20 one-bar slope in current ATR units",
    )
    atr_period: int = Field(default=14, ge=1)
    ema_separation_min_atr: float = Field(default=0.10, ge=0.0)
    confluence_distance_atr: float = Field(default=0.25, ge=0.0)
    sr_zone_atr: float = Field(default=0.10, ge=0.0)
    confirmation_min_body_ratio: float = Field(default=0.40, ge=0.0, le=1.0)
    confirmation_close_location_pct: float = Field(default=0.30, ge=0.0, le=0.5)
    confirmation_max_range_atr: float = Field(default=1.50, gt=0.0)
    trigger_buffer_atr: float = Field(default=0.05, ge=0.0)
    trigger_validity_bars: int = Field(default=2, ge=1)
    maximum_chase_atr: float = Field(default=0.25, ge=0.0)
    structural_stop_buffer_atr: float = Field(default=0.10, ge=0.0)
    minimum_stop_distance_atr: float = Field(default=0.80, gt=0.0)
    maximum_stop_distance_atr: float = Field(default=1.50, gt=0.0)
    minimum_room_to_opposing_sr_r: float = Field(default=1.50, gt=0.0)
    t1_r: float = Field(default=1.50, gt=0.0)
    runner_target_reference_r: float = Field(default=2.50, gt=0.0)
    trailing_activation_r: float = Field(default=1.0, gt=0.0)
    entry_session_start: str = Field(default="09:45", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    entry_session_end: str = Field(default="14:45", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    forced_exit_time: str = Field(default="15:15", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    # The existing evaluator still consumes these legacy constructor knobs.
    # They remain injectable until the behavior refactor maps the evaluator
    # to the completed-15m contract in the next prompt.
    legacy_breakout_confirm_polls: int = Field(default=1, ge=1)
    legacy_strategy_a_adx_threshold: float = Field(
        default=20.0,
        ge=0.0,
        le=100.0,
        description="Temporary evaluator compatibility value; remove when the new evaluator is wired",
    )
    legacy_trigger_buffer_atr: float = Field(
        default=0.02,
        ge=0.0,
        description="Temporary evaluator compatibility value; new contract hypothesis is trigger_buffer_atr",
    )
    legacy_min_impulse_atr: float = Field(default=0.70, gt=0.0)
    legacy_retest_tolerance_atr: float = Field(default=0.45, ge=0.0)
    legacy_min_available_confirmations: int = Field(default=2, ge=1)
    strategy_b_adx_threshold: float = Field(default=20.0, ge=0.0, le=100.0)

    rvol_threshold: float = Field(default=1.20, ge=1.0, le=3.0)
    ema_slope_threshold: float = Field(default=0.10, gt=0, le=1.0)
    strat_b_min_confirmation: int = Field(default=3, ge=1, le=6, description="Minimum confirmation points for Strategy B")
    box_max_height_atr: float = Field(default=1.30, ge=1.0, le=2.5, description="Max compression box height in ATR")
    supertrend_period: int = Field(default=10)
    supertrend_multiplier: float = Field(default=3.0)

    @model_validator(mode="after")
    def validate_pullback_bands(self) -> "StrategyTunablesConfig":
        if self.ema_fast_period >= self.ema_slow_period:
            raise ValueError("ema_fast_period must be less than ema_slow_period")
        if self.minimum_stop_distance_atr > self.maximum_stop_distance_atr:
            raise ValueError("minimum_stop_distance_atr must not exceed maximum_stop_distance_atr")
        if self.momentum_ema20_slope_min_atr >= self.momentum_ema20_slope_max_atr:
            raise ValueError("momentum EMA20 slope band must satisfy min < max")
        if not (self.entry_session_start < self.entry_session_end < self.forced_exit_time):
            raise ValueError("Strategy A session must satisfy start < end < forced exit")
        if not (
            self.strategy_e_entry_start
            < self.strategy_e_entry_end
            < self.strategy_e_forced_exit_time
        ):
            raise ValueError("Strategy E session must satisfy start < end < forced exit")
        if self.strategy_e_counter_target_points > self.strategy_e_trend_target_points:
            raise ValueError("Strategy E countertrend target must not exceed trend target")
        return self


class StrategySetup(BaseModel):
    """Immutable setup snapshot using completed 15-minute bar end timestamps."""

    direction: StrategyDirection
    setup_timestamp: datetime = Field(description="Timezone-aware setup event timestamp")
    confirmation_bar_timestamp: datetime = Field(
        description="Timezone-aware end timestamp of the completed confirmation bar"
    )
    confirmation_high: float = Field(gt=0)
    confirmation_low: float = Field(gt=0)
    trigger_price: float = Field(gt=0)
    structural_stop: float = Field(gt=0)
    initial_underlying_r: float = Field(gt=0)
    relevant_support_resistance_level: float = Field(gt=0)
    confluence_references: tuple[str, ...] = Field(min_length=1)
    setup_expiry_timestamp: datetime = Field(description="Timezone-aware setup expiry timestamp")
    setup_expiry_bar_index: int = Field(ge=0)
    invalidation_state: SetupInvalidationState = SetupInvalidationState.ACTIVE
    invalidation_reason: Optional[str] = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_setup_integrity(self) -> "StrategySetup":
        _require_aware(self.setup_timestamp, "setup_timestamp")
        _require_aware(self.confirmation_bar_timestamp, "confirmation_bar_timestamp")
        _require_aware(self.setup_expiry_timestamp, "setup_expiry_timestamp")
        if self.confirmation_high <= self.confirmation_low:
            raise ValueError("confirmation_high must be greater than confirmation_low")
        if any(not ref or not ref.strip() for ref in self.confluence_references):
            raise ValueError("confluence_references must contain non-empty values")
        if self.setup_timestamp < self.confirmation_bar_timestamp:
            raise ValueError("setup_timestamp cannot precede confirmation_bar_timestamp")
        if self.setup_expiry_timestamp < self.setup_timestamp:
            raise ValueError("setup_expiry_timestamp cannot precede setup_timestamp")
        if not isclose(
            self.initial_underlying_r,
            abs(self.trigger_price - self.structural_stop),
            rel_tol=1e-6,
            abs_tol=1e-6,
        ):
            raise ValueError("initial_underlying_r must equal trigger/structural-stop distance")
        if self.direction == StrategyDirection.CALL and self.structural_stop >= self.trigger_price:
            raise ValueError("CALL structural_stop must be below trigger_price")
        if self.direction == StrategyDirection.PUT and self.structural_stop <= self.trigger_price:
            raise ValueError("PUT structural_stop must be above trigger_price")
        if self.invalidation_state == SetupInvalidationState.ACTIVE and self.invalidation_reason is not None:
            raise ValueError("active setup cannot have an invalidation_reason")
        if self.invalidation_state != SetupInvalidationState.ACTIVE and not self.invalidation_reason:
            raise ValueError("invalidated, expired, or consumed setup requires invalidation_reason")
        return self


class StrategyStateSnapshot(BaseModel):
    """Serializable state-machine snapshot with impossible combinations rejected.

    ``entry_timestamp`` and ``cooldown_until`` are timezone-aware event
    timestamps. An old ``TRIGGERED`` value is therefore never enough to
    construct an ``ENTERED`` snapshot without explicit setup and entry data.
    """

    state: StrategyState = StrategyState.FLAT
    direction: Optional[StrategyDirection] = None
    setup: Optional[StrategySetup] = None
    entry_timestamp: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def validate_state_combination(self) -> "StrategyStateSnapshot":
        if self.entry_timestamp is not None:
            _require_aware(self.entry_timestamp, "entry_timestamp")
        if self.cooldown_until is not None:
            _require_aware(self.cooldown_until, "cooldown_until")
        if self.state == StrategyState.FLAT:
            if any(value is not None for value in (self.direction, self.setup, self.entry_timestamp, self.cooldown_until)):
                raise ValueError("FLAT state cannot carry setup, direction, entry, or cooldown data")
        elif self.state in (StrategyState.SETUP, StrategyState.ARMED):
            if self.setup is None or self.direction is None:
                raise ValueError(f"{self.state.value} state requires direction and setup")
            if self.direction != self.setup.direction:
                raise ValueError("state direction must match setup direction")
            if self.setup.invalidation_state != SetupInvalidationState.ACTIVE:
                raise ValueError(f"{self.state.value} state requires an active setup")
            if self.entry_timestamp is not None or self.cooldown_until is not None:
                raise ValueError(f"{self.state.value} state cannot carry entry or cooldown data")
        elif self.state == StrategyState.ENTERED:
            if self.setup is None or self.direction is None or self.entry_timestamp is None:
                raise ValueError("ENTERED state requires direction, setup, and entry_timestamp")
            if self.direction != self.setup.direction:
                raise ValueError("state direction must match setup direction")
            if self.cooldown_until is not None:
                raise ValueError("ENTERED state cannot carry cooldown data")
            if self.entry_timestamp < self.setup.setup_timestamp:
                raise ValueError("entry_timestamp cannot precede setup_timestamp")
            if self.entry_timestamp > self.setup.setup_expiry_timestamp:
                raise ValueError("entry_timestamp cannot exceed setup expiry")
            if self.setup.invalidation_state in (SetupInvalidationState.INVALIDATED, SetupInvalidationState.EXPIRED):
                raise ValueError("ENTERED state cannot use invalidated or expired setup")
        elif self.state == StrategyState.COOLDOWN:
            if self.direction is not None or self.setup is not None or self.entry_timestamp is not None:
                raise ValueError("COOLDOWN state cannot carry direction, setup, or entry data")
            if self.cooldown_until is None:
                raise ValueError("COOLDOWN state requires cooldown_until")
        return self

    def transition(
        self,
        target: StrategyState,
        *,
        setup: Optional[StrategySetup] = None,
        entry_timestamp: Optional[datetime] = None,
        cooldown_until: Optional[datetime] = None,
    ) -> "StrategyStateSnapshot":
        """Return a validated next state or reject an illegal transition."""
        allowed = {
            StrategyState.FLAT: {StrategyState.SETUP, StrategyState.COOLDOWN},
            StrategyState.SETUP: {StrategyState.ARMED, StrategyState.FLAT, StrategyState.COOLDOWN},
            StrategyState.ARMED: {StrategyState.ENTERED, StrategyState.FLAT, StrategyState.COOLDOWN},
            StrategyState.ENTERED: {StrategyState.FLAT, StrategyState.COOLDOWN},
            StrategyState.COOLDOWN: {StrategyState.FLAT},
        }
        if target not in allowed[self.state]:
            raise ValueError(f"invalid strategy state transition: {self.state.value} -> {target.value}")
        if target == StrategyState.FLAT:
            return StrategyStateSnapshot()
        if target == StrategyState.COOLDOWN:
            return StrategyStateSnapshot(state=target, cooldown_until=cooldown_until)
        next_setup = setup or self.setup
        next_direction = next_setup.direction if next_setup else self.direction
        if target == StrategyState.ENTERED:
            if next_setup is None:
                raise ValueError("ENTERED transition requires a setup")
            # Entry consumes the setup atomically.  This prevents a restart or
            # duplicate evaluation from recreating the same confirmation.
            next_setup = StrategySetup.model_validate(next_setup.model_dump(mode="json") | {
                "invalidation_state": SetupInvalidationState.CONSUMED.value,
                "invalidation_reason": "ENTRY_CONSUMED",
            })
        return StrategyStateSnapshot(
            state=target,
            direction=next_direction,
            setup=next_setup,
            entry_timestamp=entry_timestamp if target == StrategyState.ENTERED else None,
        )


class CompressionBox(BaseModel):
    """Consolidation box locked during volatility compression (Strategy B)."""
    box_high: float
    box_low: float
    box_height: float
    atr_at_lock: float
    bb_width_at_lock: float
    locked_at: datetime = Field(default_factory=utc_now)
    created_bar_time: str = ""
    bars_active: int = 0
    max_bars: int = 8
    is_locked: bool = False


class AutoTradingConfig(BaseModel):
    """Aggregated operational and strategy configuration."""
    mode: AutoTradingMode = Field(default=AutoTradingMode.PAPER)
    auto_trade_enabled: bool = Field(default=True, description="Master automated execution switch")
    system_armed: bool = Field(default=False, description="Must be true for live order routing")
    kill_switch: bool = Field(default=False, description="Emergency kill switch halting all trades")
    option_selection: OptionSelectionConfig = Field(default_factory=OptionSelectionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    session: SessionTimersConfig = Field(default_factory=SessionTimersConfig)
    tunables: StrategyTunablesConfig = Field(default_factory=StrategyTunablesConfig)
    strategy_a_revision: int = STRATEGY_A_REVISION
    strategy_e_revision: int = 2


class MarketFeatures(BaseModel):
    """Calculated technical, derivatives, and expected move feature vector."""
    breakout_data_ready: bool = False
    breakout_bull_derivatives_score: float = 0.0
    breakout_bear_derivatives_score: float = 0.0
    bullish_oi_wall: bool = False
    bearish_oi_wall: bool = False
    timestamp: datetime = Field(default_factory=utc_now)
    spot_price: float
    closed_5m_price: Optional[float] = None
    closed_5m_time: Optional[datetime] = None
    plus_di_5m: float = 0.0
    minus_di_5m: float = 0.0
    swing_low_5m: Optional[float] = None
    swing_high_5m: Optional[float] = None
    futures_atr_5m: float = 0.0
    data_ready: bool = False
    data_reason: str = "Awaiting real completed candles"
    spot_change_pct: float = 0.0
    # 15m Indicators
    ema9_15m: float = 0.0
    ema20_15m: float = 0.0
    ema50_15m: float = 0.0
    ema20_slope_15m: float = 0.0
    ema20_slope_norm_15m: float = 0.0
    adx_15m: float = 0.0
    plus_di_15m: float = 0.0
    minus_di_15m: float = 0.0
    atr_15m: float = 25.0
    # 5m Indicators
    ema9_5m: float = 0.0
    ema20_5m: float = 0.0
    rsi_5m: float = 50.0
    atr_5m: float = 25.0
    daily_atr: float = 160.0
    supertrend_direction: str = "BULLISH"
    bb_width_percentile: float = 50.0
    # Futures & Volume
    futures_price: float = 0.0
    futures_vwap: float = 0.0
    rvol_5m: float = 1.0
    futures_buildup: str = "NEUTRAL"
    # Derivatives Confirmation Scores
    bull_derivatives_score: float = 0.0
    bear_derivatives_score: float = 0.0
    derivatives_score_components: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Expected Move Context
    expected_daily_points: float = 150.0
    remaining_session_points: float = 100.0
    atm_straddle_price: float = 220.0
    # Trend classification
    trend_regime: str = "NEUTRAL"


class SelectedContract(BaseModel):
    """Option contract chosen by ContractSelector."""
    instrument_id: str
    symbol: str
    expiry: str
    strike: float
    option_type: OptionType
    ask_price: float
    bid_price: float
    open_interest: int
    volume: int
    spread_pct: float
    lot_size: int = Field(gt=0)
    ltp: float = 0.0
    instrument_token: Optional[str] = None
    premium: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    greek_source: str = "UNAVAILABLE"
    greek_timestamp: Optional[datetime] = None
    quote_timestamp: Optional[datetime] = None
    quote_freshness_seconds: Optional[float] = None
    mid_price: Optional[float] = None
    spread_points: Optional[float] = None
    selection_metadata: dict[str, Any] = Field(default_factory=dict)


class StrategySignal(BaseModel):
    """Directional setup signal emitted by Strategy A or B."""
    signal_id: str
    strategy: StrategyName
    direction: TradeDirection
    option_type: OptionType
    timestamp: datetime = Field(default_factory=utc_now)
    spot_reference_price: float
    underlying_entry_price: Optional[float] = Field(default=None, description="Authoritative Strategy A futures trigger/open fill")
    structural_stop: float
    r_points: float
    derivatives_score: float
    features_snapshot: dict[str, Any] = Field(default_factory=dict)
    block_reason: Optional[str] = None
    passed: bool = True


class ActiveTrade(BaseModel):
    """Live or paper tracking state of an active auto-trade position."""
    trade_id: str
    mode: AutoTradingMode
    strategy: StrategyName
    direction: TradeDirection
    option_type: OptionType
    contract_symbol: str
    contract_instrument_id: str
    expiry: str
    strike: float
    quantity: int
    lot_size: int
    lots: int
    # Entry snapshot
    entry_time: datetime = Field(default_factory=utc_now)
    entry_option_price: float
    entry_spot_price: float
    initial_structural_stop: float
    initial_r_points: float
    strategy_signal_type: Optional[str] = None
    strategy_target_price: Optional[float] = None
    strategy_entry_context: dict[str, Any] = Field(default_factory=dict)
    pullback_swing_low: Optional[float] = None
    pullback_swing_high: Optional[float] = None
    box_high: Optional[float] = None
    box_low: Optional[float] = None
    atr_at_lock: Optional[float] = None
    consecutive_inside_box_closes: int = 0
    highest_close_since_entry: Optional[float] = None
    lowest_close_since_entry: Optional[float] = None
    last_managed_bar: Optional[datetime] = None
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    filled_quantity: int = 0
    pending_exit_reason: Optional[str] = None
    exit_filled_quantity: int = 0
    exit_proceeds: float = 0.0
    exit_order_accounted_filled_quantity: int = 0
    exit_order_accounted_proceeds: float = 0.0
    # Dynamic live tracking
    current_option_price: float
    current_spot_price: float
    current_trailing_stop: float
    option_hard_stop_price: float
    protective_stop_order_id: Optional[str] = None
    protective_stop_status: str = "NOT_REQUIRED"
    protective_stop_trigger_price: Optional[float] = None
    protective_stop_limit_price: Optional[float] = None
    protective_stop_filled_quantity: int = 0
    protective_stop_filled_proceeds: float = 0.0
    protective_stop_cancel_for_exit: bool = False
    protective_stop_failures: int = 0
    protective_stop_last_failure_reason: Optional[str] = None
    protective_stop_last_failure_at: Optional[datetime] = None
    protective_stop_cancel_attempts: int = 0
    protective_stop_cancel_requested_at: Optional[datetime] = None
    current_r: float = 0.0
    peak_r: float = 0.0
    mfe_points: float = 0.0
    mae_points: float = 0.0
    reversal_score: int = 0
    state: TradeLifecycleState = TradeLifecycleState.OPEN_INITIAL_RISK
    unrealized_pnl: float = 0.0
    # Exit snapshot
    exit_time: Optional[datetime] = None
    exit_option_price: Optional[float] = None
    exit_spot_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_pnl: Optional[float] = None
    net_pnl: Optional[float] = None
    realized_r: Optional[float] = None
    # Immutable entry-selection/audit snapshot.  Quote updates never modify
    # these contract identity fields.
    signal_id: Optional[str] = None
    selector_timestamp: Optional[datetime] = None
    selected_contract_snapshot: dict[str, Any] = Field(default_factory=dict)
    entry_bid: Optional[float] = None
    entry_ask: Optional[float] = None
    entry_ltp: Optional[float] = None
    entry_quote_source: Optional[str] = None
    entry_quote_timestamp: Optional[datetime] = None
    entry_quote_freshness_seconds: Optional[float] = None
    entry_slippage_points: float = 0.0
    entry_raw_ask: Optional[float] = None
    entry_executable_price: Optional[float] = None
    # Latest selected-contract quote, persisted for the UI and audit trail.
    current_bid: Optional[float] = None
    current_ask: Optional[float] = None
    current_ltp: Optional[float] = None
    current_quote_source: Optional[str] = None
    current_quote_timestamp: Optional[datetime] = None
    current_quote_freshness_seconds: Optional[float] = None
    current_quote_volume: Optional[int] = None
    current_quote_open_interest: Optional[int] = None
    option_data_status: str = "ENTRY_CAPTURED"
    option_data_quality_reasons: list[str] = Field(default_factory=list)
    # Underlying lifecycle and option execution are intentionally separate.
    underlying_exit_reason: Optional[str] = None
    underlying_exit_time: Optional[datetime] = None
    underlying_outcome_status: Optional[str] = None
    option_exit_reason: Optional[str] = None
    option_exit_time: Optional[datetime] = None
    # Explicit cost ledger values.
    raw_gross_option_pnl: Optional[float] = None
    brokerage: Optional[float] = None
    exchange_charges: Optional[float] = None
    stt: Optional[float] = None
    gst: Optional[float] = None
    sebi_charges: Optional[float] = None
    stamp_duty: Optional[float] = None
    slippage_cost: Optional[float] = None
    transaction_costs: Optional[float] = None
    return_on_premium_pct: Optional[float] = None
    cost_assumption_version: Optional[str] = None
    cost_assumptions: dict[str, Any] = Field(default_factory=dict)
    futures_contract_id: Optional[str] = None
    underlying_entry_price: Optional[float] = None
    underlying_current_price: Optional[float] = None
    underlying_exit_price: Optional[float] = None
    pending_underlying_exit_time: Optional[datetime] = None
    underlying_structural_stop: Optional[float] = None
    underlying_r: Optional[float] = None
    initial_quantity: Optional[int] = None
    remaining_quantity: Optional[int] = None
    t1_reached: bool = False
    t1_exit_quantity: int = 0
    t1_exit_pending: bool = False
    t1_decision_underlying_price: Optional[float] = None
    t1_decision_r: Optional[float] = None
    t1_realized_r: Optional[float] = None
    runner_realized_r: Optional[float] = None
    partial_exit_filled_quantity: int = 0
    partial_exit_proceeds: float = 0.0
    partial_exit_order_id: Optional[str] = None
    partial_exit_order_accounted_filled_quantity: int = 0
    partial_exit_order_accounted_proceeds: float = 0.0
    partial_exit_price: Optional[float] = None
    partial_exit_raw_bid: Optional[float] = None
    partial_exit_slippage_points: Optional[float] = None
    partial_exit_time: Optional[datetime] = None
    partial_exit_reason: Optional[str] = None
    final_exit_quantity: Optional[int] = None
    execution_order_count: int = 0
    selected_option_delta: Optional[float] = None
    selected_option_delta_source: str = "UNAVAILABLE"
    selected_option_gamma: Optional[float] = None
    selected_option_gamma_source: str = "UNAVAILABLE"
    risk_budget: Optional[float] = None
    estimated_option_loss_at_structural_stop: Optional[float] = None


class DecisionLogEntry(BaseModel):
    """Audit log entry explaining algorithmic choices."""
    id: str
    timestamp: datetime = Field(default_factory=utc_now)
    category: str  # SETUP, FILTER, CONTRACT_SELECTION, ORDER, RISK, TRAIL_UPDATE, EXIT
    strategy: Optional[str] = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TriggerCondition(BaseModel):
    """Specific condition evaluation within a strategy setup."""
    id: str
    name: str
    current_value: str
    target_threshold: str
    unit: str = ""
    status: str  # "PASSED" | "PENDING" | "N/A"
    gap_description: str


class StrategyTriggerDiagnostics(BaseModel):
    """Diagnostic state of a strategy setup explaining what it is waiting for."""
    strategy: StrategyName
    strategy_label: str
    direction: TradeDirection
    option_type: OptionType
    overall_status: str  # "READY_TO_TRIGGER" | "WAITING"
    passed_count: int
    total_count: int
    ready_pct: float
    key_blocker: str
    target_entry_level: Optional[float] = None
    current_spot: float
    distance_pts: Optional[float] = None
    phase_state: str = "SEARCH_REGIME"
    phase_summary: dict[str, Any] = Field(default_factory=dict)
    conditions: list[TriggerCondition] = Field(default_factory=list)


class ThresholdOverrides(BaseModel):
    """Dynamic parameter overrides allowed on the fly."""
    max_option_premium: Optional[float] = None
    max_option_premium_cap: Optional[float] = None
    min_option_premium: Optional[float] = None
    min_option_premium_floor: Optional[float] = None
    adx_threshold: Optional[float] = None
    rvol_threshold: Optional[float] = None
    ema_slope_threshold: Optional[float] = Field(default=None, gt=0, le=1.0)
    min_confirmation_score: Optional[int] = None
    strat_b_min_confirmation: Optional[int] = Field(default=None, ge=1, le=6)
    box_max_height_atr: Optional[float] = None
    bb_width_percentile: Optional[float] = None
    bull_derivatives_score: Optional[float] = None
    bear_derivatives_score: Optional[float] = None
    breakout_confirm_polls: Optional[int] = None
    breakout_buffer_atr: Optional[float] = None
    min_impulse_atr: Optional[float] = None
    retest_tolerance_atr: Optional[float] = None
    min_pullback_depth: Optional[float] = None
    max_pullback_depth: Optional[float] = None
    min_available_confirmations: Optional[int] = None
    strat_b_max_extension_atr: Optional[float] = None
    strat_b_breakout_buffer_atr: Optional[float] = None
    strat_b_breakout_confirm_polls: Optional[int] = None
    strat_b_box_max_age_bars: Optional[int] = None
    strat_b_min_available_confirmations: Optional[int] = None
    bypass_entry_window: bool = False
    active: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.max_option_premium_cap is not None and self.max_option_premium is None:
            self.max_option_premium = self.max_option_premium_cap
        elif self.max_option_premium is not None and self.max_option_premium_cap is None:
            self.max_option_premium_cap = self.max_option_premium
        if self.min_option_premium_floor is not None and self.min_option_premium is None:
            self.min_option_premium = self.min_option_premium_floor
        elif self.min_option_premium is not None and self.min_option_premium_floor is None:
            self.min_option_premium_floor = self.min_option_premium


class GateBlockers(BaseModel):
    """Session and risk gates that block trade execution."""
    trading_window_open: bool = True
    trading_window_text: str = "Active"
    within_trading_window: bool = True
    system_armed: bool = False
    kill_switch_active: bool = False
    auto_trade_enabled: bool = True
    market_data_ready: bool = True
    market_data_reason: Optional[str] = None
    max_positions_reached: bool = False
    in_cooldown: bool = False
    primary_blocker: Optional[str] = None
    cooldown_active: bool = False
    cooldown_remaining_min: float = 0.0
    concurrent_positions_count: int = 0
    max_concurrent_positions: int = 1
    daily_trades_count: int = 0
    max_trades_per_day: int = 3
    daily_trades_max: int = 3
    can_enter_new_trades: bool = True
    primary_gate_reason: str = "Ready"


class TriggerDiagnosticsResponse(BaseModel):
    """Complete diagnostic payload for UI trigger radar."""
    timestamp: datetime = Field(default_factory=utc_now)
    system_time: Optional[datetime] = None
    gate_blockers: Optional[GateBlockers] = None
    gates: Optional[GateBlockers] = None
    active_overrides: ThresholdOverrides = Field(default_factory=ThresholdOverrides)
    diagnostics: list[StrategyTriggerDiagnostics] = Field(default_factory=list)
    strategies: list[StrategyTriggerDiagnostics] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if self.system_time is None:
            self.system_time = self.timestamp
        if self.gates is None and self.gate_blockers is not None:
            self.gates = self.gate_blockers
        elif self.gate_blockers is None and self.gates is not None:
            self.gate_blockers = self.gates
        if not self.strategies and self.diagnostics:
            self.strategies = self.diagnostics
        elif not self.diagnostics and self.strategies:
            self.diagnostics = self.strategies


class SimulationRequest(BaseModel):
    date: Optional[str] = None  # YYYY-MM-DD or None for today/latest
    instrument_id: str = "INST-NIFTY-INDEX"
    overrides: Optional[ThresholdOverrides] = None
    capital: float = Field(
        default=500000.0,
        description="Compatibility-only in current Day Replay; historical sizing parity is not applied yet.",
    )
    bypass_window: bool = False
    # Kept separate from ``bypass_window`` so replay metadata uses the
    # canonical name without breaking existing API/debug callers.
    bypass_entry_window: Optional[bool] = None
    historical_source: HistoricalReplaySource = HistoricalReplaySource.BREEZE
    max_trades_per_day: int = Field(
        default=5,
        description="Compatibility-only in current Day Replay; chronological daily trade gating is not applied yet.",
    )


class SimulationBarSnapshot(BaseModel):
    bar_index: int
    timestamp: str
    ist_time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    spot: float
    ema9_5m: float
    ema20_5m: float
    supertrend: str
    adx_15m: float
    rvol_5m: float
    bb_width_percentile: float
    strategy_a_phase: str
    strategy_b_phase: str
    active_trade_id: Optional[str] = None
    event: Optional[str] = None
    event_details: Optional[str] = None


class SimulatedTradeRecord(BaseModel):
    trade_id: str
    strategy: str
    direction: str
    option_type: str
    strike: float
    contract_symbol: str
    entry_time: str
    entry_spot: float
    entry_premium: float | None = None
    exit_time: Optional[str] = None
    exit_spot: Optional[float] = None
    exit_premium: Optional[float] = None
    exit_reason: Optional[str] = None
    initial_stop: float
    initial_r_points: float
    peak_r: float = 0.0
    realized_r: float = 0.0
    quantity: int
    lots: int
    gross_pnl: float | None = None
    net_pnl: float | None = None
    hold_duration_mins: float = 0.0


class ReplaySignalMetrics(BaseModel):
    """Signal-discovery metrics, separated from lifecycle and option economics."""

    price_basis: str = "COMPLETED_UNDERLYING_SPOT_FUTURES_CANDLES"
    calculation_basis: str = "STRATEGY_SIGNAL_DISCOVERY_ON_COMPLETED_HISTORICAL_BARS"
    total_bars_evaluated: int
    qualified_signals: int
    ambiguous_signals: int
    unresolved_signals: int


class ReplayUnderlyingLifecycleMetrics(BaseModel):
    """Underlying lifecycle outcomes expressed only in structural-R terms."""

    price_basis: str = "UNDERLYING_SPOT_OR_FUTURES_REPLAY_EVENT_PRICES"
    calculation_basis: str = "POSITION_MANAGER_RESOLVED_LIFECYCLES_USING_INITIAL_STRUCTURAL_RISK"
    resolved_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    total_realized_r: float
    average_realized_r: float
    median_realized_r: float
    average_winner_r: float
    average_loser_r: float
    profit_factor_r: float | None
    max_drawdown_r: float
    max_consecutive_losses: int


class ReplayOptionMarkMetrics(BaseModel):
    """Historical option mark economics; these values are not executable fills."""

    price_basis: str = "HISTORICAL_OPTION_COMPLETED_CANDLE_CLOSE_MARKS"
    calculation_basis: str = (
        "ONE_RECONSTRUCTED_LOT_USING_REPLAY_CONTRACT_APPROXIMATION_AND_PAPER_COST_SCHEDULE"
    )
    priced_trades: int
    unpriced_trades: int
    all_resolved_trades_priced: bool
    gross_mark_pnl: float | None
    estimated_transaction_costs: float | None
    net_mark_pnl: float | None


class ReplayPortfolioMetrics(BaseModel):
    """Portfolio metrics are explicit even when chronological execution is unavailable."""

    price_basis: str = "NOT_AVAILABLE"
    calculation_basis: str = "CHRONOLOGICAL_PORTFOLIO_EXECUTION_NOT_IMPLEMENTED"
    available: bool = False
    max_drawdown_pnl: float | None = None
    limitation: str = (
        "Current Day Replay resolves signals independently after discovery; "
        "portfolio-level chronological equity and risk-gate metrics are unavailable."
    )


class ReplayDataQuality(BaseModel):
    """Data availability/provenance facts used to qualify the replay result."""

    price_basis: str = "HISTORICAL_SOURCE_AND_REPLAY_PROVENANCE"
    calculation_basis: str = "SOURCE_DIAGNOSTICS_PLUS_REPLAY_RECORD_AVAILABILITY_COUNTS"
    historical_source: str
    missing_data: list[str] = Field(default_factory=list)
    underlying_issue_counts: dict[str, int] = Field(default_factory=dict)
    option_mark_available_trades: int = 0
    option_mark_unavailable_trades: int = 0
    option_mark_quality_reasons: dict[str, int] = Field(default_factory=dict)


class SimulationResult(BaseModel):
    replay_mode: str = "SIGNALS_ONLY"
    limitation: str = "Real completed spot/futures candles only. Historical executable option quotes are unavailable; option-dependent rules are unavailable."
    session_date: str

    # Canonical replay result model. Compatibility fields below are projections
    # of these sections and must not be calculated independently.
    signal_metrics: ReplaySignalMetrics
    underlying_lifecycle_metrics: ReplayUnderlyingLifecycleMetrics
    option_mark_metrics: ReplayOptionMarkMetrics
    portfolio_metrics: ReplayPortfolioMetrics
    data_quality: ReplayDataQuality

    # Temporary compatibility projection for the existing frontend/API.
    total_bars_evaluated: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate_pct: float = 0.0
    total_pnl: float | None = None
    net_pnl: float | None = None
    total_realized_r: float = 0.0
    max_drawdown_pnl: float | None = None
    profit_factor: float | None = None
    max_drawdown_r: float | None = None
    trades: list[SimulatedTradeRecord] = Field(default_factory=list)
    timeline: list[SimulationBarSnapshot] = Field(default_factory=list)
    decision_logs: list[DecisionLogEntry] = Field(default_factory=list)
    replay_trigger_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    replay_manifests: list[dict[str, Any]] = Field(default_factory=list)
    replay_metadata: dict[str, Any] = Field(default_factory=dict)
    replay_lifecycle: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def derive_compatibility_metrics(self) -> "SimulationResult":
        signal = self.signal_metrics
        lifecycle = self.underlying_lifecycle_metrics
        option = self.option_mark_metrics
        portfolio = self.portfolio_metrics
        self.total_bars_evaluated = signal.total_bars_evaluated
        self.total_trades = lifecycle.resolved_trades
        self.winning_trades = lifecycle.winning_trades
        self.losing_trades = lifecycle.losing_trades
        self.win_rate_pct = lifecycle.win_rate_pct
        self.total_pnl = option.gross_mark_pnl
        self.net_pnl = option.net_mark_pnl
        self.total_realized_r = lifecycle.total_realized_r
        self.max_drawdown_pnl = portfolio.max_drawdown_pnl
        self.profit_factor = lifecycle.profit_factor_r
        self.max_drawdown_r = lifecycle.max_drawdown_r
        return self
