"""Domain models and configuration schemas for NIFTY Intraday Options Auto-Trading.
Based on implementation/NIFTY_INTRADAY_OPTIONS_AUTO_TRADING_STRATEGIES.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


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


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class TradeLifecycleState(str, Enum):
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
    max_trade_capital: float = Field(default=50000.0, ge=5000.0, description="Maximum total capital per single trade")
    risk_per_trade_pct_of_account: float = Field(default=0.50, ge=0.1, le=5.0, description="Account % risk per trade")
    max_daily_loss_r: float = Field(default=2.0, ge=0.5, le=10.0, description="Daily loss limit in R multiples")
    max_daily_loss_pct: float = Field(default=1.5, ge=0.5, le=5.0, description="Daily loss limit as % of account")
    max_failed_trades_per_strategy: int = Field(default=2, ge=1, le=5, description="Max consecutive failed trades before strategy pause")
    max_trades_per_day: int = Field(default=5, ge=1, le=20, description="Total allowed trades per session")
    max_concurrent_positions: int = Field(default=1, ge=1, le=3, description="Maximum simultaneous open positions")
    cooldown_after_loss_min: int = Field(default=10, ge=0, le=60, description="Cooldown wait in minutes after a losing exit")
    option_hard_stop_pct: float = Field(default=25.0, ge=10.0, le=50.0, description="Emergency option premium loss stop %")


class SessionTimersConfig(BaseModel):
    """Intraday trading window schedules in IST."""
    no_new_trade_before: str = Field(default="09:30", description="No entries before HH:MM IST")
    no_new_trade_after: str = Field(default="14:45", description="No new entries after HH:MM IST")
    force_exit_time: str = Field(default="15:20", description="Intraday square-off time HH:MM IST")


class StrategyTunablesConfig(BaseModel):
    """Algorithmic tuning parameters."""
    evaluation_interval_sec: int = Field(default=2, ge=1, le=10, description="Scheduler loop interval in seconds")
    trend_pullback_enabled: bool = Field(default=True)
    volatility_breakout_enabled: bool = Field(default=True)
    adx_threshold: float = Field(default=20.0, ge=10.0, le=40.0)
    rvol_threshold: float = Field(default=1.30, ge=1.0, le=3.0)
    supertrend_period: int = Field(default=10)
    supertrend_multiplier: float = Field(default=3.0)


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


class MarketFeatures(BaseModel):
    """Calculated technical, derivatives, and expected move feature vector."""
    timestamp: datetime = Field(default_factory=utc_now)
    spot_price: float
    spot_change_pct: float = 0.0
    # 15m Indicators
    ema9_15m: float = 0.0
    ema20_15m: float = 0.0
    ema50_15m: float = 0.0
    ema20_slope_15m: float = 0.0
    adx_15m: float = 0.0
    plus_di_15m: float = 0.0
    minus_di_15m: float = 0.0
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
    lot_size: int = 25


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
    status: str  # "PASSED" | "PENDING"
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
    conditions: list[TriggerCondition] = Field(default_factory=list)


class ThresholdOverrides(BaseModel):
    """Dynamic parameter overrides allowed on the fly."""
    max_option_premium: Optional[float] = None
    max_option_premium_cap: Optional[float] = None
    min_option_premium: Optional[float] = None
    min_option_premium_floor: Optional[float] = None
    adx_threshold: Optional[float] = None
    rvol_threshold: Optional[float] = None
    bb_width_percentile: Optional[float] = None
    bull_derivatives_score: Optional[float] = None
    bear_derivatives_score: Optional[float] = None
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


