# Strategy A — Trend Pullback Continuation (Fixed Implementation Specification)

## 1. Goal

Trade NIFTY intraday options only when:

1. A valid 15-minute trend exists.
2. Price makes a controlled 5-minute pullback.
3. The pullback holds structure.
4. A completed 5-minute candle confirms trend resumption.
5. Basic confirmation and risk checks pass.

The strategy must **not** require every indicator to agree. Slow/lagging indicators are confirmations, not hard blockers.

---

## 2. Mandatory Data Rules

Use only **real market OHLC candles** for all calculations, entries, stops, highs/lows and triggers.

- Do not use Heikin Ashi OHLC for execution logic.
- Use only **completed** 5-minute and 15-minute candles for strategy decisions.
- Use NIFTY futures volume for volume/RVOL calculations. NIFTY spot volume must not be used.
- Futures VWAP must be calculated from NIFTY futures.
- Option-chain/OI data is confirmation data only.
- Lot size and available strikes must come from the current instrument master; never hardcode them.

---

## 3. Strategy State Machine

```text
SEARCH_REGIME
    ↓
TREND_QUALIFIED
    ↓
WAIT_FOR_IMPULSE
    ↓
IMPULSE_FOUND
    ↓
PULLBACK_ACTIVE
    ↓
PULLBACK_QUALIFIED
    ↓
WAIT_FOR_TRIGGER
    ↓
TRIGGERED
    ↓
RISK_AND_CONTRACT_CHECK
    ↓
ENTER_TRADE
    ↓
MANAGE_POSITION
    ↓
EXIT
```

A state must expire/reset when its conditions are no longer valid. Do not keep stale pullback/impulse structures indefinitely.

---

# 4. Indicators

## 4.1 15m EMA

- EMA20
- EMA50

Standard EMA formula.

## 4.2 Normalized EMA20 Slope

```text
ema20_slope_norm = (EMA20_now - EMA20_2bars_ago) / ATR15m
```

Defaults:

```text
Bullish slope: >= +0.10
Bearish slope: <= -0.10
Flat: between -0.10 and +0.10
```

Make the threshold configurable.

## 4.3 ADX / DMI

15-minute ADX(14), +DI(14), -DI(14).

Default:

```text
ADX >= 20
```

## 4.4 RSI

5-minute RSI(14).

Defaults:

```text
Bullish momentum: RSI > 50
Bearish momentum: RSI < 50
```

RSI is confirmation, not a standalone blocker.

## 4.5 ATR

Use 5-minute ATR(14) for entry/stop normalization and 15-minute ATR(14) for normalized macro slope.

Do not artificially floor ATR unless a separate safety rule specifically requires it.

## 4.6 Futures VWAP

Session VWAP from NIFTY futures, reset at session start.

## 4.7 Futures RVOL

Preferred calculation:

```text
RVOL = current 5m futures volume /
       median volume for the same 5m time slot over prior sessions
```

If time-of-day history is not yet available, use rolling intraday median as fallback.

Default useful confirmation threshold:

```text
RVOL >= 1.20
```

---

# 5. Phase 1 — Macro Trend Qualification

Do NOT use 8/8 mandatory conditions.

## 5.1 Bullish Regime

### Mandatory strength condition

```text
ADX15m >= 20
```

### Direction score — require at least 3 of 4

```text
1. EMA20_15m > EMA50_15m
2. normalized EMA20 slope >= +0.10
3. last completed 15m close > EMA20_15m
4. +DI15m > -DI15m
```

Bullish regime is valid when:

```text
ADX_pass = true
AND
bull_direction_score >= 3
```

## 5.2 Bearish Regime

### Mandatory strength condition

```text
ADX15m >= 20
```

### Direction score — require at least 3 of 4

```text
1. EMA20_15m < EMA50_15m
2. normalized EMA20 slope <= -0.10
3. last completed 15m close < EMA20_15m
4. -DI15m > +DI15m
```

Bearish regime is valid when:

```text
ADX_pass = true
AND
bear_direction_score >= 3
```

## 5.3 Macro Confirmations — NOT hard blockers

Score one point for each directional confirmation:

```text
1. 5m Supertrend agrees with direction
2. NIFTY futures is on correct side of futures VWAP
3. Derivatives/OI flow score agrees with direction
4. Futures price/OI behaviour agrees with direction
```

Do not reject the trend merely because one or more of these are missing.

---

# 6. Phase 2 — Chronological Impulse Detection

The old `max(high) - min(low)` method must not be used without checking time order.

Use the latest 15 completed 5-minute candles.

## 6.1 Bullish Impulse

Find a swing low `L`, followed later in time by a swing high `H`.

Mandatory:

```text
index(L) < index(H)
impulse_height = H - L
impulse_height >= 1.0 * ATR5m
```

The selected impulse should be the most recent valid impulse, not simply the largest session-wide range.

## 6.2 Bearish Impulse

Find a swing high `H`, followed later in time by a swing low `L`.

Mandatory:

```text
index(H) < index(L)
impulse_height = H - L
impulse_height >= 1.0 * ATR5m
```

## 6.3 Suggested Swing Detection

A simple pivot is enough for V1:

```text
Swing High:
high[i] > high[i-1] AND high[i] >= high[i+1]

Swing Low:
low[i] < low[i-1] AND low[i] <= low[i+1]
```

Use only completed candles.

---

# 7. Phase 3 — Pullback Detection

Pullback begins after the impulse extreme.

## 7.1 Duration

Preferred:

```text
2 to 6 completed 5m bars = ideal
7 to 9 bars = allowed but lower quality
>9 bars = reset setup
```

Minimum is 2 bars.

## 7.2 Bullish Pullback Depth

For bullish impulse `L → H`:

```text
pullback_low = minimum low after H
pullback_depth = (H - pullback_low) / (H - L)
```

Valid:

```text
0.10 <= pullback_depth <= 0.65
```

The pullback must not break the original impulse low.

## 7.3 Bearish Pullback Depth

For bearish impulse `H → L`:

```text
pullback_high = maximum high after L
pullback_depth = (pullback_high - L) / (H - L)
```

Valid:

```text
0.10 <= pullback_depth <= 0.65
```

The pullback must not break the original impulse high.

---

# 8. Pullback Retest Qualification

Do not use fixed 30-point VWAP distance.

A pullback qualifies if it approaches at least one valid reference level.

## 8.1 Bullish

At least one:

```text
abs(pullback_low - EMA9_5m)  <= 0.35 * ATR5m
OR
abs(pullback_low - EMA20_5m) <= 0.35 * ATR5m
OR
futures price during pullback comes within 0.35 * futures_ATR of futures VWAP
OR
pullback retests a previously detected breakout/support level within 0.35 * ATR5m
```

## 8.2 Bearish

At least one:

```text
abs(pullback_high - EMA9_5m)  <= 0.35 * ATR5m
OR
abs(pullback_high - EMA20_5m) <= 0.35 * ATR5m
OR
futures price during pullback comes within 0.35 * futures_ATR of futures VWAP
OR
pullback retests a previously detected breakdown/resistance level within 0.35 * ATR5m
```

Important: do not directly compare NIFTY spot price with futures VWAP without accounting for the futures basis. Prefer futures-vs-futures comparisons.

---

# 9. Pullback Volume Quality — Confirmation Only

Calculate:

```text
pullback_volume_ratio =
average futures volume during pullback /
average futures volume during impulse
```

Interpretation:

```text
< 0.80  = strong/healthy pullback confirmation
0.80-1.00 = neutral
> 1.00 = warning; do not automatically reject
```

This contributes to confirmation score but is not mandatory.

---

# 10. Phase 4 — Resumption Trigger

All trigger calculations must use the latest **completed real 5-minute candle**.

## 10.1 CALL Trigger

### Mandatory trigger

```text
close_5m > previous_real_5m_high
```

### Need at least 1 of 3 momentum confirmations

```text
1. close_5m > EMA9_5m
2. RSI5m > 50
3. bullish directional candle confirmation
```

Bullish directional candle confirmation:

```text
close > open
AND
body_ratio >= 0.40
AND
(close - low) / max(high-low, epsilon) >= 0.65
```

### Anti-exhaustion safety

```text
(high-low) <= 1.85 * ATR5m
```

If larger, reject this trigger candle and keep watching for a later valid trigger while the pullback remains valid.

## 10.2 PUT Trigger

### Mandatory trigger

```text
close_5m < previous_real_5m_low
```

### Need at least 1 of 3 momentum confirmations

```text
1. close_5m < EMA9_5m
2. RSI5m < 50
3. bearish directional candle confirmation
```

Bearish directional candle confirmation:

```text
close < open
AND
body_ratio >= 0.40
AND
(high - close) / max(high-low, epsilon) >= 0.65
```

### Anti-exhaustion safety

```text
(high-low) <= 1.85 * ATR5m
```

---

# 11. Phase 5 — Entry Confirmation Score

After a valid trigger, calculate confirmation points.

For CALL, +1 each:

```text
1. 5m Supertrend bullish
2. NIFTY futures > futures VWAP
3. Bullish derivatives/OI flow
4. Futures price/OI indicates bullish participation
5. Trigger futures RVOL >= 1.20
6. Pullback volume ratio < 0.80
```

For PUT, mirror all conditions.

Default requirement:

```text
confirmation_score >= 2
```

Do not require every confirmation.

Make the threshold configurable from UI.

---

# 12. Phase 6 — Structural Stop and Initial R

Freeze the entry reference at the completed trigger candle close.

```text
entry_reference_spot = trigger_candle_close
```

## 12.1 CALL

```text
raw_stop = pullback_swing_low - 0.15 * ATR5m
raw_R = entry_reference_spot - raw_stop
```

If:

```text
raw_R < 0.45 * ATR5m
```

widen the stop to:

```text
initial_stop = entry_reference_spot - 0.45 * ATR5m
```

Otherwise:

```text
initial_stop = raw_stop
```

Reject if:

```text
entry_reference_spot - initial_stop > 1.60 * ATR5m
```

## 12.2 PUT

```text
raw_stop = pullback_swing_high + 0.15 * ATR5m
raw_R = raw_stop - entry_reference_spot
```

If:

```text
raw_R < 0.45 * ATR5m
```

widen the stop to:

```text
initial_stop = entry_reference_spot + 0.45 * ATR5m
```

Otherwise:

```text
initial_stop = raw_stop
```

Reject if:

```text
initial_stop - entry_reference_spot > 1.60 * ATR5m
```

Freeze:

```text
R_initial = absolute(entry_reference_spot - initial_stop)
```

Do not recalculate initial R from a moving live spot after signal creation.

---

# 13. Phase 7 — Option Contract Selection

After the underlying signal is accepted, select an option.

## 13.1 Candidate universe

CALL:

```text
1 ITM through maximum 4 OTM available strikes
```

PUT:

```text
1 ITM through maximum 4 OTM available strikes
```

Use actual strikes from the option chain/instrument master. Never assume a fixed strike interval.

## 13.2 Premium rules

```text
ask <= UI Max Option Premium
ask >= UI Min Option Premium
```

Defaults can remain:

```text
Max = ₹70
Min = ₹15
```

## 13.3 Liquidity rules

```text
spread_pct <= 3.5%
OI >= 10,000
```

If option delta is available:

```text
delta_abs >= configured minimum
```

Suggested initial minimum:

```text
0.25
```

If delta is unavailable, enforce the maximum 4-OTM distance.

## 13.4 Ranking

Among eligible contracts:

1. Prefer contract closest to ATM / highest usable delta.
2. Then prefer executable ask closest to, but not above, the premium cap.
3. Prefer tighter spread if otherwise equal.

If no contract passes all execution rules:

```text
NO TRADE
```

Never keep moving farther OTM merely to satisfy the premium cap.

---

# 14. Phase 8 — Correct Position Sizing

All values must be configurable from UI.

Inputs:

```text
max_trade_capital
risk_per_trade_pct
account_equity
selected_option_ask
selected_contract_lot_size
option_emergency_stop_pct
```

Capital limit:

```text
capital_per_lot = option_ask * lot_size
capital_limited_lots = floor(max_trade_capital / capital_per_lot)
```

Emergency premium-risk estimate:

```text
risk_per_lot = option_ask * option_emergency_stop_pct * lot_size
risk_budget = account_equity * risk_per_trade_pct
risk_limited_lots = floor(risk_budget / risk_per_lot)
```

Final:

```text
final_lots = min(capital_limited_lots, risk_limited_lots)
```

Critical rule:

```text
if final_lots < 1:
    NO TRADE
```

Never use `max(1, ...)`.

Quantity:

```text
quantity = final_lots * lot_size
```

Lot size must come from instrument metadata.

---

# 15. Phase 9 — Automatic Entry

When all of the following are true:

```text
regime qualified
impulse valid
pullback valid
retest valid
trigger valid
confirmation score >= threshold
R/risk valid
eligible contract found
position size >= 1 lot
daily/system risk limits pass
```

create the trade automatically.

No human confirmation is required when Auto Trade is enabled.

Use the configured execution adapter:

```text
PAPER -> simulated fill engine
LIVE  -> broker order engine
```

Both modes must use exactly the same strategy signal and position-management logic.

---

# 16. Phase 10 — Position Management

The underlying NIFTY structure is the primary trade-management reference.

The option premium has a separate emergency stop.

Default emergency premium stop:

```text
25% below actual option entry price
```

Use executable option price/bid when evaluating an exit, not an unrealistic last traded price.

---

## 16.1 CALL R Multiples

```text
R_current = (current_spot - entry_reference_spot) / R_initial
```

## 16.2 PUT R Multiples

```text
R_current = (entry_reference_spot - current_spot) / R_initial
```

---

# 17. Trailing Stop Ladder

## Before +1R

Use initial structural stop.

## At +1R

CALL:

```text
stop = max(previous_stop, entry_reference_spot + 2)
```

PUT:

```text
stop = min(previous_stop, entry_reference_spot - 2)
```

This is underlying breakeven protection; it does not guarantee exact option-P&L breakeven.

## At +1.5R

CALL:

```text
stop = max(previous_stop,
           entry_reference_spot + 0.50 * R_initial)
```

PUT:

```text
stop = min(previous_stop,
           entry_reference_spot - 0.50 * R_initial)
```

## At +2R — Runner Mode

CALL candidate trail:

```text
candidate = max(
    latest_confirmed_5m_swing_low,
    EMA9_5m - 0.25 * ATR5m,
    highest_close_since_entry - 1.0 * ATR5m
)

new_stop = max(previous_stop, candidate)
```

PUT candidate trail:

```text
candidate = min(
    latest_confirmed_5m_swing_high,
    EMA9_5m + 0.25 * ATR5m,
    lowest_close_since_entry + 1.0 * ATR5m
)

new_stop = min(previous_stop, candidate)
```

Stops never loosen.

---

# 18. Explicit Reversal / Trade Health Score

Replace the undefined "10-factor" score with this exact 8-factor implementation.

## 18.1 CALL adverse-health score

Add +1 for each:

```text
1. completed 5m close < EMA9_5m
2. completed 5m close < EMA20_5m
3. NIFTY futures < futures VWAP
4. RSI5m < 48
5. -DI5m > +DI5m
6. 5m Supertrend becomes bearish
7. bearish derivatives/flow score reaches configured threshold
8. completed 5m close breaks latest confirmed 5m swing low
```

## 18.2 PUT adverse-health score

Mirror the conditions:

```text
1. completed 5m close > EMA9_5m
2. completed 5m close > EMA20_5m
3. NIFTY futures > futures VWAP
4. RSI5m > 52
5. +DI5m > -DI5m
6. 5m Supertrend becomes bullish
7. bullish derivatives/flow score reaches configured threshold
8. completed 5m close breaks latest confirmed 5m swing high
```

## 18.3 Action

```text
score 0-1: HOLD
score 2: tighten stop; do not exit solely from score
score >=3 AND R_current < 1.0: EXIT EARLY
score >=3 AND R_current >= 1.0: tighten to latest valid structural/EMA trail
score >=4: EXIT
```

Structural invalidation always overrides the score.

---

# 19. Immediate Thesis Invalidation

CALL:

```text
completed 5m close < pullback_swing_low
=> urgent exit
```

PUT:

```text
completed 5m close > pullback_swing_high
=> urgent exit
```

Do not wait for the option to hit the 25% emergency premium stop after the underlying thesis has failed.

"Urgent exit" means the execution engine should use its fastest supported aggressive-limit/repricing process.

---

# 20. Session / Reset Rules

Recommended defaults:

```text
No new entries before: 09:20
No new entries after: 14:45
Forced intraday exit: 15:20
```

Also reset the current setup when:

```text
- macro regime becomes invalid
- pullback exceeds 9 bars
- pullback depth > 65%
- original impulse structure breaks
- trigger candle is stale
- data is stale/incomplete
- system/broker risk guard blocks trading
```

After a stopped/failed trade, require at least one completed 5-minute candle before considering re-entry in the same direction.

---

# 21. UI / Debug Display

Do not display a flat `7/11` checklist as the primary strategy status.

Display the state instead:

```text
REGIME
Bearish: QUALIFIED (4/4 direction + ADX)

IMPULSE
Found: YES
Height: 1.42 ATR

PULLBACK
State: QUALIFIED
Bars: 4
Depth: 38%
Retest: EMA20

TRIGGER
Waiting for: close below previous 5m low
Gap: 7.2 points

CONFIRMATION
Score: 2/6
Required: 2

RISK
Initial R: 0.82 ATR
Contract: Not selected until trigger
```

This makes it immediately obvious why a trade has or has not triggered.

---

# 22. Simplified Decision Logic

## CALL

```python
if not bullish_macro_regime():
    reset_setup()
    return NO_TRADE

impulse = find_latest_chronological_bullish_impulse()
if not impulse:
    return WAIT

pullback = evaluate_bullish_pullback(impulse)
if not pullback.valid:
    return WAIT_OR_RESET

if not bullish_resumption_trigger_on_closed_5m():
    return WAIT

if confirmation_score(CALL) < configured_min_confirmation:
    return WAIT_OR_SKIP_TRIGGER

risk = build_structural_risk(CALL, pullback)
if not risk.valid:
    return NO_TRADE

contract = select_option_contract(CALL)
if not contract:
    return NO_TRADE

size = calculate_position_size(contract)
if size.lots < 1:
    return NO_TRADE

return AUTO_ENTER_CALL
```

## PUT

Mirror CALL logic exactly.

---

# 23. Main Changes From Old Strategy

Remove these behaviours:

```text
- 8/8 macro conditions mandatory
- Supertrend as a macro blocker
- VWAP as a macro blocker
- derivatives score as a macro blocker
- raw max(high)-min(low) impulse without chronology
- fixed 30-point VWAP tolerance
- all three momentum indicators mandatory on trigger
- moving live spot used to recalculate initial R
- hardcoded lot size
- max(1, calculated_lots)
- undefined 10-factor reversal score
```

Replace them with:

```text
- ADX + 3/4 directional macro qualification
- chronological impulse detection
- explicit pullback state
- ATR-normalized retest distances
- one mandatory price trigger + confirmation score
- frozen trigger-close entry reference
- dynamic lot size / strikes
- deterministic position sizing
- explicit 8-factor trade-health score
- state-based UI/debugging
```

---

# 24. Acceptance Criteria

Strategy A implementation is complete only when all are true:

- [ ] Uses completed real 5m/15m candles only.
- [ ] Uses futures volume, not NIFTY spot volume.
- [ ] Macro regime does not require Supertrend/VWAP/OI as hard blockers.
- [ ] Macro direction requires ADX + 3/4 directional conditions.
- [ ] EMA slope is ATR-normalized.
- [ ] Impulse low/high chronology is validated.
- [ ] Bullish and bearish pullback-depth formulas are separate and correct.
- [ ] Pullback has state, age and reset rules.
- [ ] Retest proximity uses ATR, not fixed 30 points.
- [ ] Entry trigger uses previous real 5m high/low.
- [ ] Momentum uses 1-of-3 confirmation, not 3-of-3.
- [ ] Entry confirmation uses a score instead of all conditions mandatory.
- [ ] Initial R is frozen from the trigger close.
- [ ] Option strike/lot size come from current instrument metadata.
- [ ] Position sizing returns NO TRADE when calculated lots < 1.
- [ ] Premium-cap selection cannot drift excessively far OTM.
- [ ] CALL and PUT trailing formulas are explicitly mirrored.
- [ ] Reversal score is explicitly defined.
- [ ] Paper and live modes use identical strategy logic.
- [ ] Auto-trade can execute without human confirmation when enabled.
- [ ] UI displays strategy state rather than only an N/M condition count.

