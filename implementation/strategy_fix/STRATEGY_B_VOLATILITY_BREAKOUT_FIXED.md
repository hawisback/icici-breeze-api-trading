# Strategy B — Volatility Compression Breakout (Fixed Implementation)

## Implementation status (2026-09-18)

The core Strategy B implementation now follows the completed-bar box lifecycle
below. Signal evaluation and diagnostics share one read-only-preview decision
path. Box state and consumed-bar/signal identifiers survive restarts; rejection,
expiry, stale data, configuration changes and platform entry gates reset setup
state. A fresh box requires a new contiguous compression window after reset.

Key implementation details:

- BB percentile is an empirical mid-rank against up to 252 prior BB(20, 2)
  observations, with at least 20 prior observations required. It is no longer a
  scaled absolute band width. ATR uses Wilder smoothing without a 10-point floor.
- Defaults: 8-bar compression, 25th percentile, 1.30 ATR maximum box height,
  8-bar lifetime, 0.05 ATR breakout buffer, 0.75 ATR anti-chase limit, 3 of 6
  confirmations and Strategy B start at 09:25 IST. Persistent UI controls expose
  these values. Shared platform entry-window and risk limits still apply.
- Compression need not remain squeezed after lock. A breakout attempt consumes
  the box even if confirmation or risk checks reject it; it cannot become a late
  entry from that same box. Candle body direction qualifies the body point only,
  consistent with the final mandatory-vs-confirmation rules in section 32.
- Strategy B derivatives scoring is separate from Strategy A and excludes VWAP
  and RVOL. Missing change-in-OI/velocity evidence earns no point. The live adapter
  supplies change-in-OI when reported; no OI velocity is fabricated when absent.
  Relative nearby OI walls subtract one confirmation, not a hard rejection.
- False-breakout counts advance once per distinct completed close. B health
  scoring uses adverse futures OI behavior instead of A's pullback-swing factor.
  Shared lot caps, configurable breakeven buffer, per-strategy daily trade/loss
  counts and an entry cancel timeout are enforced. Pending orders remain pending
  until broker reconciliation; timed-out entries are cancelled, not chased.

Operational requirements and limitations:

- The local instrument database checked during Strategy A work has no futures
  contracts. Load verified current broker futures/option metadata and sufficient
  real spot/futures history before expecting signals. Seeded option lots are not
  authoritative. This change does not import the live instrument master.
- Replay remains signals-only: historical option-chain snapshots/executable
  bid/ask are unavailable, so historical option confirmations, fills and PnL are
  not a live-equivalent backtest. Fees and additional paper slippage modeling are
  not implemented here; paper fills use executable ask/bid.
- Runtime state protects the single strategy-service instance across restarts.
  Multi-worker distributed order idempotency and recovery of a submission whose
  order ID was never saved still require OMS/operator reconciliation.
- Restart the backend for the new settings/runtime handling, then validate with
  real data in paper mode. No live orders were placed during implementation.

Regression coverage: `tests/test_volatility_breakout_fixed.py`, plus the shared
Strategy A, position-manager, contract-selector and API tests.

Verification: full isolated suite passed 130 tests; the final focused strategy
run passed 52 tests, including two additional warm-up/entry-timeout tests.
Frontend `npx tsc --noEmit` passed. These checks do not establish trading
performance or live-broker readiness.

## Purpose

This document replaces the current Strategy B implementation.

The goal is to detect a **real volatility squeeze**, lock a valid consolidation box, and automatically buy a NIFTY CE or PE only when price breaks that box with sufficient participation.

The strategy must **not** require every confirmation indicator to be true at the same time.

---

# 1. Strategy Flow

Implement Strategy B as a state machine:

```text
SEARCH_COMPRESSION
      ↓
COMPRESSION_FOUND
      ↓
BOX_LOCKED
      ↓
WAITING_FOR_BREAKOUT
      ↓
BREAKOUT_TRIGGERED
      ↓
CONFIRMATION_CHECK
      ↓
RISK_AND_OPTION_CHECK
      ↓
AUTO_TRADE
      ↓
MANAGE_POSITION
```

If the setup becomes invalid at any point:

```text
RESET → SEARCH_COMPRESSION
```

---

# 2. Data Rules

Use:

- Real NIFTY 5-minute OHLC candles for breakout levels.
- NIFTY Futures for volume, VWAP and futures OI.
- NIFTY option-chain data for option OI / OI change.
- Only **completed 5-minute candles** for setup and breakout decisions.

Do not use:

- Heikin Ashi OHLC for breakout, stop or box calculations.
- NIFTY spot volume.
- Current incomplete 5-minute candle for trigger confirmation.

The current live candle may be displayed in UI, but it must not trigger Strategy B.

---

# 3. Required Indicators

## 3.1 ATR

Use 5-minute ATR(14), Wilder smoothing.

```text
TR = max(
    High - Low,
    abs(High - PreviousClose),
    abs(Low - PreviousClose)
)

ATR = Wilder ATR(14)
```

Do not artificially floor ATR at 10 points unless this is a configurable safety option.

---

## 3.2 Bollinger Band Width

Use 5-minute close:

```text
Period = 20
StdDev = 2

Middle = SMA20
Upper = SMA20 + 2 * StdDev
Lower = SMA20 - 2 * StdDev

BBWidth = (Upper - Lower) / Middle
```

Convert current BBWidth to percentile versus historical observations.

Recommended initial rule:

```text
BBWidth Percentile <= 25%
```

UI configurable range:

```text
20% to 35%
```

---

## 3.3 Futures VWAP

Calculate using NIFTY Futures.

Reset daily at market open.

```text
TypicalPrice = (High + Low + Close) / 3

VWAP =
sum(TypicalPrice * Volume)
/
sum(Volume)
```

VWAP is a confirmation.

It must NOT block an otherwise valid breakout by itself.

---

## 3.4 Relative Volume

Preferred calculation:

```text
RVOL =
Current completed 5m futures volume
/
Median volume for the same 5m time slot
over previous 20 sessions
```

Example:

```text
10:35-10:40 current futures volume
/
median 10:35-10:40 volume from previous 20 sessions
```

Fallback if historical time-of-day data is unavailable:

```text
RVOL =
latest completed 5m futures volume
/
median volume of previous 10-20 intraday bars
```

Initial confirmation threshold:

```text
RVOL >= 1.20
```

Strong participation:

```text
RVOL >= 1.50
```

---

# 4. Phase 1 — Detect Compression

Search for compression using the most recent completed 5-minute candles.

Recommended starting window:

```text
6 to 10 completed bars
```

Default:

```text
8 bars
```

A compression candidate exists when:

```text
BBWidth Percentile <= configured threshold
```

AND the recent price range is sufficiently tight.

Calculate:

```text
CandidateHigh = max(high of compression-window candles)
CandidateLow  = min(low of compression-window candles)

CandidateHeight = CandidateHigh - CandidateLow
```

Initial box-height rule:

```text
CandidateHeight <= 1.30 * ATR5m
```

UI configurable:

```text
1.10 ATR to 1.60 ATR
```

Do NOT increase this simply to make more trades.

If the range is too wide:

```text
NO COMPRESSION
```

Continue scanning for a fresh compression window.

---

# 5. Abnormal Candle / Data Protection

Before a candle can define the compression box, validate it.

Initial abnormal-candle rule:

```text
CandleRange > 4.0 * ATR5m
```

If true:

```text
mark candle as suspicious
```

A suspicious candle must not define the compression box until validated.

Also reject obviously stale or invalid market data.

Suggested checks:

```text
quote age <= configured maximum
bid <= ask
prices > 0
timestamp increasing
no duplicate stale candles
```

---

# 6. Phase 2 — Lock the Consolidation Box

When compression qualifies, freeze:

```text
BoxHigh
BoxLow
BoxHeight
ATRAtLock
BBWidthAtLock
BoxCreatedAt
BoxCreatedBarIndex
```

Important:

**Do not continuously recalculate BoxHigh and BoxLow after the box is locked.**

The breakout target must remain stable.

State becomes:

```text
BOX_LOCKED
```

---

# 7. Box Expiry and Reset Rules

A box cannot remain active forever.

Default maximum life after lock:

```text
8 completed 5-minute candles
```

Reset the box if any of these happen:

```text
1. Box age > 8 completed candles
2. Market data becomes stale
3. Session moves into no-new-entry window
4. Structural expansion invalidates the compression
5. Strategy configuration changes
6. A false breakout is completed and setup is abandoned
```

After reset:

```text
SEARCH_COMPRESSION
```

The system must never remain indefinitely in:

```text
AWAITING MARKET CONDITIONS
```

using an old or oversized box.

---

# 8. Phase 3 — Breakout Trigger

Use the latest **completed real 5-minute candle**.

Use frozen:

```text
BoxHigh
BoxLow
ATRAtLock
```

## CALL Trigger

Mandatory:

```text
Close5m > BoxHigh + BreakoutBuffer
```

Where:

```text
BreakoutBuffer = 0.05 * ATRAtLock
```

Recommended configurable range:

```text
0.05 to 0.10 ATR
```

## PUT Trigger

Mandatory:

```text
Close5m < BoxLow - BreakoutBuffer
```

---

# 9. Anti-Chase Rule

After a valid breakout close, reject excessively extended entries.

## CALL

```text
Extension = Close5m - BoxHigh
```

## PUT

```text
Extension = BoxLow - Close5m
```

Default:

```text
Extension <= 0.75 * ATRAtLock
```

Configurable:

```text
0.60 to 1.00 ATR
```

If exceeded:

```text
REJECT TRADE
```

Reason:

```text
BREAKOUT_OVEREXTENDED
```

---

# 10. Breakout Candle Quality

Do not make candle body a mandatory standalone blocker.

Calculate:

```text
Range = max(High - Low, small_epsilon)
Body  = abs(Close - Open)

BodyRatio = Body / Range
```

Directional requirement:

### CALL

```text
Close > Open
```

### PUT

```text
Close < Open
```

Body confirmation:

```text
BodyRatio >= 0.45
```

Also calculate close-location quality.

### CALL

```text
CloseLocation =
(Close - Low) / Range
```

Bullish confirmation:

```text
CloseLocation >= 0.70
```

### PUT

```text
CloseLocation =
(High - Close) / Range
```

Bearish confirmation:

```text
CloseLocation >= 0.70
```

---

# 11. Derivatives Confirmation

Do not include VWAP and RVOL inside the derivatives score because they are already evaluated separately.

Create a clean 0-5 derivatives score.

## Bull Score

Add +1 for each:

```text
1. Futures price rising AND futures OI rising
2. Put OI / Put change-in-OI support developing below ATM
3. Call OI unwinding near or above ATM
4. Near-ATM change-in-OI balance is bullish
5. OI velocity is bullish
```

## Bear Score

Add +1 for each:

```text
1. Futures price falling AND futures OI rising
2. Call OI / Call change-in-OI resistance developing above ATM
3. Put OI unwinding near or below ATM
4. Near-ATM change-in-OI balance is bearish
5. OI velocity is bearish
```

Use:

```text
0-1 = weak
2   = useful confirmation
3+  = strong confirmation
```

This score is a confirmation, not a mandatory standalone blocker.

---

# 12. OI Wall Logic

Do not use a fixed value such as:

```text
OI > 500000
```

Instead calculate OI wall strength relative to nearby strikes.

Use:

```text
ATM ± 5 strikes
```

Example:

```text
WallStrength =
StrikeOI /
MedianOINearbyStrikes
```

A strong wall may initially be defined as:

```text
>= 90th percentile
```

If a strong wall is directly ahead of the breakout:

```text
confirmation score -= 1
```

Do not automatically reject unless total confirmation becomes insufficient.

---

# 13. Phase 4 — Confirmation Score

After the mandatory breakout trigger passes, calculate confirmation score.

Add +1 for each:

```text
1. RVOL >= 1.20
2. BodyRatio >= 0.45
3. CloseLocation >= 0.70
4. Futures on correct side of VWAP
5. Derivatives Score >= 2
6. Futures OI behaviour supports breakout direction
```

Default rule:

```text
ConfirmationScore >= 3
```

Optional UI modes:

```text
Aggressive       >= 2
Balanced         >= 3
High Confidence  >= 4
```

Important:

The mandatory conditions are only:

```text
valid box
valid breakout
not overextended
risk checks
```

VWAP, body, derivatives and RVOL are confidence inputs.

---

# 14. Structural Stop

Freeze the structural stop at entry.

## CALL

```text
StructuralStop =
BoxHigh - 0.25 * ATRAtLock
```

## PUT

```text
StructuralStop =
BoxLow + 0.25 * ATRAtLock
```

Freeze entry reference:

```text
EntryReferenceSpot = TriggerCandleClose
```

Do not use continuously updating spot for initial risk.

---

# 15. Initial R

## CALL

```text
InitialR =
EntryReferenceSpot - StructuralStop
```

## PUT

```text
InitialR =
StructuralStop - EntryReferenceSpot
```

Reject if:

```text
InitialR <= 0
```

Recommended maximum:

```text
InitialR <= 1.20 * ATRAtLock
```

There is no need for a separate minimum `0.35 ATR` check if breakout-buffer and stop formulas already imply it.

---

# 16. Option Contract Selection

After the signal is valid, select the option.

## Direction

```text
CALL breakout → CE
PUT breakout  → PE
```

## Premium Rules

UI configured:

```text
MaxOptionPremium
MinOptionPremium
```

Example:

```text
MaxOptionPremium = ₹70
MinOptionPremium = ₹15
```

Eligible:

```text
Ask <= MaxOptionPremium
Ask >= MinOptionPremium
```

## Strike Search

Do not hardcode 50-point strike spacing.

Read available strikes from the option chain / instrument master.

Search around ATM:

```text
1 ITM
ATM
up to 4 OTM
```

for the required direction.

## Liquidity

Require:

```text
SpreadPercent <= 3.5%
OI >= configured minimum
valid bid
valid ask
fresh quote
```

Spread:

```text
Mid = (Bid + Ask) / 2

SpreadPercent =
((Ask - Bid) / Mid) * 100
```

## Moneyness / Delta Protection

Do not select a very far OTM contract merely because it is cheap.

Require at least one:

```text
minimum delta threshold
```

OR

```text
maximum allowed OTM distance
```

If no suitable option exists below premium cap:

```text
NO TRADE
```

Do not force a trade.

## Ranking

Among valid contracts:

```text
select highest-quality contract
closest to MaxOptionPremium
```

Prefer:

```text
higher delta
better liquidity
smaller spread
premium closer to cap
```

---

# 17. Position Sizing

Never hardcode lot size.

Use:

```text
LotSize = selected_contract.lot_size
```

from broker / instrument master.

## Capital Limit

```text
CapitalLimitedLots =
floor(
    MaxTradeCapital /
    (OptionAsk * LotSize)
)
```

## Risk Limit

Use UI configurable:

```text
RiskPerTradePct
```

Example:

```text
0.50%
```

Calculate option emergency-loss estimate:

```text
OptionRiskPerUnit =
OptionAsk * EmergencyStopPct
```

Example:

```text
EmergencyStopPct = 25%
```

Then:

```text
RiskLimitedLots =
floor(
    (AccountEquity * RiskPerTradePct)
    /
    (OptionRiskPerUnit * LotSize)
)
```

## Final Lots

```text
FinalLots =
min(
    CapitalLimitedLots,
    RiskLimitedLots,
    MaxLotsPerTrade
)
```

Critical:

```text
if FinalLots < 1:
    NO TRADE
```

Never use:

```text
max(1, calculated_lots)
```

---

# 18. Auto Execution

When the full signal passes:

```text
SIGNAL_VALID
      ↓
SELECT_OPTION
      ↓
CALCULATE_QUANTITY
      ↓
PLACE_ORDER
```

Paper mode and live mode must use the same signal logic.

Only the execution adapter changes.

## Paper Mode

Simulate:

```text
entry at executable ask
exit at executable bid
include configured slippage
include fees if desired
```

## Live Mode

Use aggressive limit execution.

For BUY:

```text
1. Read current best ask
2. Submit limit order near executable ask
3. Wait configured short timeout
4. Check fill
5. If not filled:
   cancel/modify/reprice
6. Stop repricing beyond maximum allowed slippage
```

For SELL EXIT:

```text
1. Read current best bid
2. Submit aggressive sell limit
3. Reprice if necessary
4. Confirm fill
```

---

# 19. Duplicate Trade Protection

Before placing an order, create a unique signal key:

```text
strategy
direction
box_created_time
trigger_candle_time
```

Example:

```text
VOL_BREAKOUT_CALL_20260917_1120_1150
```

Do not place another order for the same signal key.

---

# 20. Phase 5 — Position Management

Use underlying NIFTY movement for the strategy stop/trailing logic.

Keep option premium emergency stop separately.

## Emergency Option Stop

Default:

```text
Option premium loss = -25%
```

This is emergency protection.

The structural / thesis exit should normally trigger earlier.

---

# 21. R-Multiple Tracking

## CALL

```text
CurrentR =
(CurrentSpot - EntryReferenceSpot)
/
InitialR
```

## PUT

```text
CurrentR =
(EntryReferenceSpot - CurrentSpot)
/
InitialR
```

---

# 22. Stop Ladder

## Before +1R

Use:

```text
StructuralStop
```

## At +1R

Protect approximately entry.

### CALL

```text
Stop = EntryReferenceSpot + configured buffer
```

### PUT

```text
Stop = EntryReferenceSpot - configured buffer
```

Default buffer:

```text
2 points
```

This is underlying break-even protection, not guaranteed option-P&L break-even.

## At +1.5R

### CALL

```text
Stop =
EntryReferenceSpot + 0.50 * InitialR
```

### PUT

```text
Stop =
EntryReferenceSpot - 0.50 * InitialR
```

## At +2R

Enter runner mode.

### CALL Candidate Stop

```text
max(
    latest confirmed 5m swing low,
    EMA9 - 0.25 * ATR5m,
    highest completed 5m close since entry - 1.0 * ATR5m
)
```

Ratchet:

```text
NewStop =
max(PreviousStop, CandidateStop)
```

### PUT Candidate Stop

```text
min(
    latest confirmed 5m swing high,
    EMA9 + 0.25 * ATR5m,
    lowest completed 5m close since entry + 1.0 * ATR5m
)
```

Ratchet:

```text
NewStop =
min(PreviousStop, CandidateStop)
```

Stops never loosen.

---

# 23. False Breakout Exit

Do not exit merely because price barely closes inside the box once.

Use a small failure buffer.

## CALL

Warning:

```text
Close5m < BoxHigh
```

Immediate failure:

```text
Close5m < BoxHigh - 0.10 * ATRAtLock
```

OR:

```text
2 consecutive completed closes inside box
```

Then:

```text
EXIT
```

## PUT

Warning:

```text
Close5m > BoxLow
```

Immediate failure:

```text
Close5m > BoxLow + 0.10 * ATRAtLock
```

OR:

```text
2 consecutive completed closes inside box
```

Then exit.

---

# 24. Early Reversal Health Score

Evaluate while position is open.

## CALL Adverse Factors

Add +1 for each:

```text
1. Close < EMA9
2. Close < EMA20
3. Futures < VWAP
4. RSI < 48
5. -DI > +DI
6. 5m Supertrend bearish
7. Futures OI behaviour turns bearish
8. Bear derivatives score >= 2
```

## PUT Adverse Factors

Mirror the conditions:

```text
1. Close > EMA9
2. Close > EMA20
3. Futures > VWAP
4. RSI > 52
5. +DI > -DI
6. 5m Supertrend bullish
7. Futures OI behaviour turns bullish
8. Bull derivatives score >= 2
```

Actions:

```text
Score 0-1 → hold
Score 2   → tighten stop
Score >=3 AND CurrentR < 1.0 → early exit
Score >=4 → exit regardless of CurrentR unless already protected by a tighter stop
```

---

# 25. Trading Session Rules

Recommended defaults:

```text
No new trades before: 09:25
No new trades after: 14:45
Mandatory square-off: 15:20
```

All UI configurable.

---

# 26. Daily Risk Controls

Apply shared platform limits:

```text
Max trades per strategy per day
Max failed trades per strategy
Daily loss limit
Max concurrent directional exposure
Cooldown after stop-out
Kill switch
```

Recommended initial values:

```text
Max failed breakout trades = 2 per day
Daily loss limit = 1.5R to 2R
```

These should be UI configurable.

---

# 27. Polling / Evaluation

Market-data transport:

```text
WebSocket preferred for live quotes
```

UI configured interval:

```text
StrategyEvaluationIntervalSeconds
```

Example:

```text
1 to 5 seconds
```

Important:

5-minute breakout decisions are still evaluated only on a **new completed 5-minute candle**.

The fast polling interval is used for:

```text
live position management
stop monitoring
option prices
OI refresh
trade-health monitoring
UI refresh
```

---

# 28. UI State Display

Do not show only:

```text
5 / 8 conditions passed
```

Show strategy phase.

Example:

```text
VOLATILITY BREAKOUT CALL

State: WAITING_FOR_BREAKOUT

Compression:       PASSED
Box:               LOCKED
Box High:          23238.9
Box Low:           23210.4
Box Height:        28.5
ATR at Lock:       23.1
Box Age:           3 / 8 bars

Breakout Trigger:  WAITING
Confirmation:      2 / 6
Trade Status:      NO TRADE
```

Possible states:

```text
SEARCHING_COMPRESSION
COMPRESSION_FOUND
BOX_LOCKED
WAITING_FOR_BREAKOUT
BREAKOUT_DETECTED
CONFIRMATION_FAILED
RISK_REJECTED
OPTION_NOT_FOUND
ORDER_PENDING
TRADE_ACTIVE
EXITED
RESET
```

---

# 29. Reset Logic Summary

Reset immediately if:

```text
box too old
box invalid
data stale
session limit reached
false breakout setup abandoned
configuration changed
```

Never allow a stale 99-point range to remain active while the current market is tightly compressed.

---

# 30. Final CALL Logic

```text
IF
    valid locked compression box exists

AND
    box age <= max age

AND
    completed 5m close >
        BoxHigh + breakout buffer

AND
    extension <= anti-chase limit

AND
    ConfirmationScore >= configured minimum

AND
    initial risk acceptable

AND
    eligible CE contract exists

AND
    position sizing >= 1 lot

AND
    daily risk rules allow trade

THEN
    AUTO BUY CE
```

---

# 31. Final PUT Logic

```text
IF
    valid locked compression box exists

AND
    box age <= max age

AND
    completed 5m close <
        BoxLow - breakout buffer

AND
    extension <= anti-chase limit

AND
    ConfirmationScore >= configured minimum

AND
    initial risk acceptable

AND
    eligible PE contract exists

AND
    position sizing >= 1 lot

AND
    daily risk rules allow trade

THEN
    AUTO BUY PE
```

---

# 32. Mandatory vs Confirmation Rules

## Mandatory

These may block a trade:

```text
Valid compression box
Box not expired
Completed breakout close
Anti-chase limit
Valid initial R
Option found
Liquidity acceptable
Position sizing >= 1 lot
Daily risk limits
Fresh market data
```

## Confirmation Only

These must NOT individually block a trade:

```text
VWAP
RVOL
Candle body ratio
Close location
Derivatives score
Futures OI confirmation
OI wall
```

Instead combine them into:

```text
ConfirmationScore
```

---

# 33. Implementation Priority

Implement in this order:

```text
1. Real completed 5m candle handling
2. Compression detection
3. Box locking
4. Box expiry/reset
5. Breakout trigger
6. Confirmation score
7. Structural stop / Initial R
8. Contract selection
9. Position sizing
10. Paper execution
11. Position management
12. Early reversal exits
13. Live execution
14. UI/debug states
```

---

# 34. Critical Fixes Compared With Old Strategy

```text
OLD:
All 8 conditions must pass

NEW:
Mandatory breakout structure + confirmation score
```

```text
OLD:
Range can remain stale

NEW:
Lock box, expire box, reset invalid box
```

```text
OLD:
Box boundaries can effectively move

NEW:
Freeze BoxHigh / BoxLow when box is locked
```

```text
OLD:
55% candle body is mandatory

NEW:
45% body is a confirmation
```

```text
OLD:
VWAP mandatory

NEW:
VWAP confirmation
```

```text
OLD:
Derivatives score mandatory

NEW:
Derivatives score confirmation
```

```text
OLD:
Absolute OI wall > fixed threshold

NEW:
Relative OI wall strength
```

```text
OLD:
RVOL versus generic intraday median

NEW:
Prefer time-of-day futures RVOL
```

```text
OLD:
max(1, calculated lots)

NEW:
If calculated lots < 1 → NO TRADE
```

```text
OLD:
Hardcoded lot size

NEW:
Read contract lot size dynamically
```

---

# 35. Expected Behavior

The strategy should remain selective.

The objective is not to generate a trade every day.

However, when a genuine compression occurs followed by a strong directional expansion, the system should not reject the trade merely because one lagging confirmation such as VWAP, OI score or candle-body threshold is imperfect.

The key philosophy is:

```text
STRUCTURE decides whether a trade exists.

CONFIRMATIONS decide whether the trade is strong enough.

RISK rules decide whether the system is allowed to execute it.
```
