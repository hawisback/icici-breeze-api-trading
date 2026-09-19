"""Domain models and configuration schemas for NIFTY Intraday Options Auto-Trading.
Based on implementation/NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AutoTradingMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    DISABLED = "DISABLED"


class StrategyName(str, Enum):
    TREND_PULLBACK = "TREND_PULLBACK"
    VOLATILITY_BREAKOUT = "VOLATILITY_BREAKOUT"


class StrategyState(str, Enum):
    SEARCHING = "SEARCHING"
    SETUP = "SETUP"
    TRIGGERED = "TRIGGERED"
    COOLDOWN = "COOLDOWN"
    PAUSED = "PAUSED"


class TradeDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


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
    """Configuration for automated contract selection with maximum premium cap."""
    max_option_premium: float = Field(default=70.00, ge=5.0, le=500.0, description="Upper ceiling for option premium purchase")
    min_option_premium: float = Field(default=15.00, ge=1.0, le=100.0, description="Lower floor to avoid ultra-low delta lotto options")
    max_otm_strikes: int = Field(default=4, ge=0, le=10, description="Maximum number of strikes out-of-the-money")
    min_open_interest: int = Field(default=10000, ge=1000, description="Minimum contract open interest for liquidity")
    max_bid_ask_spread_pct: float = Field(default=3.0, ge=0.5, le=10.0, description="Maximum acceptable bid-ask spread %")
    prefer_premium_closest_to_cap: bool = Field(default=True, description="Prefer the eligible contract closest to max_option_premium")
    use_current_expiry_on_0dte: bool = Field(default=False, description="Whether to trade 0DTE on expiry day or roll to next weekly")


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
    account_equity: float = Field(default=500000.0, gt=0)


class SessionTimersConfig(BaseModel):
    """Intraday trading window schedules in IST."""
    strategy_b_no_new_trade_before: str = Field(default="09:25", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    no_new_trade_before: str = Field(default="09:20", description="No entries before HH:MM IST")
    no_new_trade_after: str = Field(default="14:45", description="No new entries after HH:MM IST")
    force_exit_time: str = Field(default="15:20", description="Intraday square-off time HH:MM IST")


class StrategyTunablesConfig(BaseModel):
    """Algorithmic tuning parameters."""
    bb_width_percentile_threshold: float = Field(default=35.0, ge=15, le=50)
    compression_lookback_bars: int = Field(default=8, ge=6, le=10)
    box_max_age_bars: int = Field(default=12, ge=1, le=20)
    breakout_buffer_atr: float = Field(default=0.03, ge=0.01, le=0.10)
    breakout_max_extension_atr: float = Field(default=0.90, ge=0.60, le=1.5)
    evaluation_interval_sec: int = Field(default=2, ge=1, le=10, description="Scheduler loop interval in seconds")
    trend_pullback_enabled: bool = Field(default=True)
    volatility_breakout_enabled: bool = Field(default=True)
    adx_threshold: float = Field(default=20.0, ge=10.0, le=40.0)
    rvol_threshold: float = Field(default=1.20, ge=1.0, le=3.0)
    ema_slope_threshold: float = Field(default=0.10, gt=0, le=1.0)
    # Strategy A uses independent directional pullback bands.  CALL retains
    # the legacy 8%-70% inclusive range; PUT is the frozen validated candidate
    # with an inclusive lower and exclusive upper boundary.
    call_pullback_min_depth: float = Field(default=0.08, ge=0.0, lt=1.0)
    call_pullback_max_depth: float = Field(default=0.70, gt=0.0, le=1.0)
    put_pullback_min_depth: float = Field(default=0.40, ge=0.0, lt=1.0)
    put_pullback_max_depth: float = Field(default=0.60, gt=0.0, le=1.0)
    min_confirmation_score: int = Field(default=2, ge=1, le=6, description="Minimum confirmation points for Strategy A")
    strat_b_min_confirmation: int = Field(default=2, ge=1, le=6, description="Minimum confirmation points for Strategy B")
    box_max_height_atr: float = Field(default=1.50, ge=1.0, le=2.5, description="Max compression box height in ATR")
    supertrend_period: int = Field(default=10)
    supertrend_multiplier: float = Field(default=3.0)

    @model_validator(mode="after")
    def validate_pullback_bands(self) -> "StrategyTunablesConfig":
        if self.call_pullback_min_depth > self.call_pullback_max_depth:
            raise ValueError("call_pullback_min_depth must not exceed call_pullback_max_depth")
        if self.put_pullback_min_depth >= self.put_pullback_max_depth:
            raise ValueError("put_pullback_min_depth must be less than put_pullback_max_depth")
        return self


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
    max_bars: int = 12
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
    strategy_a_revision: int = 3


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


class StrategySignal(BaseModel):
    """Directional setup signal emitted by Strategy A or B."""
    signal_id: str
    strategy: StrategyName
    direction: TradeDirection
    option_type: OptionType
    timestamp: datetime = Field(default_factory=utc_now)
    spot_reference_price: float
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
    # Dynamic live tracking
    current_option_price: float
    current_spot_price: float
    current_trailing_stop: float
    option_hard_stop_price: float
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
    capital: float = 500000.0
    bypass_window: bool = False
    # Kept separate from ``bypass_window`` so replay metadata uses the
    # canonical name without breaking existing API/debug callers.
    bypass_entry_window: Optional[bool] = None
    historical_source: HistoricalReplaySource = HistoricalReplaySource.BREEZE
    max_trades_per_day: int = 5


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


class SimulationResult(BaseModel):
    replay_mode: str = "SIGNALS_ONLY"
    limitation: str = "Real completed spot/futures candles only. Historical executable option quotes are unavailable; option-dependent rules are unavailable."
    session_date: str
    total_bars_evaluated: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_pnl: float | None
    net_pnl: float | None
    total_realized_r: float
    max_drawdown_pnl: float | None
    profit_factor: float
    trades: list[SimulatedTradeRecord] = Field(default_factory=list)
    timeline: list[SimulationBarSnapshot] = Field(default_factory=list)
    decision_logs: list[DecisionLogEntry] = Field(default_factory=list)
    replay_trigger_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    replay_manifests: list[dict[str, Any]] = Field(default_factory=list)
    replay_metadata: dict[str, Any] = Field(default_factory=dict)
    replay_lifecycle: dict[str, Any] = Field(default_factory=dict)
