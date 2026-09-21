# Strategy A — AI-DLC Lifecycle Specification & Progress Tracker

> **Repository:** `hawisback/icici-breeze-api-trading`  
> **Workstream:** Strategy A — NIFTY Trend-Pullback Confluence  
> **Lifecycle type:** Brownfield strategy replacement / refactor  
> **Tracker version:** 1.0  
> **Last reviewed branch:** `strategy-a-trend-pullback-v2`  
> **Last reviewed head:** `43218b2`  
> **Lifecycle owner:** Human approval required at every phase gate

---

## 1. Purpose

This file is the durable source of truth for the Strategy A refactor.

It converts the Strategy A implementation plan into a phase-gated AI-Driven Development Life Cycle (AI-DLC) that can be resumed across coding sessions without losing:

- the intended architecture;
- the current implementation phase;
- completed work;
- test evidence;
- known limitations;
- decisions and compatibility choices;
- blockers;
- cleanup decisions;
- the exact acceptance gate for moving to the next phase.

The implementation target is one deterministic **NIFTY Trend-Pullback Confluence strategy**.

The strategy must ultimately use:

- NIFTY futures as the authoritative signal/structure instrument;
- completed 15-minute futures bars for setup, indicators, structure and price action;
- options only as the execution vehicle;
- the same deterministic state transitions in replay, paper and runtime paths.

---

## 2. How AI Agents Must Use This File

Every agent working on this workstream MUST:

1. Read this file before modifying Strategy A.
2. Read the **Current Status** section.
3. Work only on the current phase unless a dependency requires a narrowly scoped prerequisite fix.
4. Do not silently implement later-phase behavior.
5. Do not change strategy parameters merely to make tests pass.
6. Run the phase-specific tests.
7. Record exact test commands and results under that phase.
8. Record files added, modified and deleted.
9. Record any migration or compatibility decision.
10. Update the phase status only after its acceptance gate is satisfied.
11. Append an entry to the **Lifecycle Change Log**.
12. Stop progression if a blocker remains.

### Status notation

- `[ ]` Not started
- `[-]` In progress
- `[?]` Awaiting human review/approval
- `[R]` Revision required
- `[x]` Completed and approved
- `[S]` Skipped with explicit reason
- `[!]` Blocked

A phase may move to `[?]` only when implementation and automated evidence are complete.

A phase may move to `[x]` only after human approval.

---

## 3. Non-Negotiable Architecture

Strategy A must converge to the following architecture:

```text
NIFTY FUTURES MARKET DATA
        |
        v
Completed 15m futures candles
        |
        v
Canonical feature engine
EMA20 / EMA50 / ADX14 / +DI / -DI / ATR14 / session VWAP
confirmed non-repainting S/R structure
        |
        v
Deterministic Trend-Pullback state machine
FLAT -> SETUP -> ARMED -> ENTERED -> COOLDOWN
        |
        v
Underlying entry signal
        |
        v
Strategy-aware NIFTY option selection
        |
        v
Sizing from underlying structural risk
        |
        v
OMS / risk / position management
        |
        v
Paper / shadow execution telemetry
        |
        v
Replay-vs-runtime comparison
```

### Explicit prohibitions

The final Strategy A must not generate signals from:

- NIFTY spot structure;
- 5m EMA9 retests;
- the old 5m impulse/pullback state machine;
- generic 8%-70% pullback rules;
- frozen PUT 40%-60% pullback rules;
- confirmation-score voting;
- Supertrend confirmation scoring;
- derivatives/OI confirmation scoring;
- RVOL confirmation scoring;
- volume dry-up scoring;
- "2 of N confirmations";
- spot-price breakout triggers.

Old research may be deleted when it is derived Strategy A output and no longer useful.

Raw/reusable market data must be preserved.

Strategy B must not be changed by this workstream.

---

## 4. Data Retention / Cleanup Policy

We are intentionally starting Strategy A validation afresh.

### Delete when obsolete

Derived artifacts belonging only to superseded Strategy A behavior may be removed:

- old Strategy A replay reports;
- old baseline reports;
- frozen PUT 40%-60% candidate reports;
- old confirmation-variant reports;
- generated JSON/CSV backtest outputs;
- old signal-ID manifests;
- tests asserting exact old Strategy A signal counts;
- analysis scripts whose only purpose is superseded Strategy A research;
- obsolete 5m Strategy A fixtures and tests.

### Preserve

Do not delete:

- reusable raw historical market candles;
- instrument-master data;
- option-chain forward captures useful for later option validation;
- production/runtime audit data;
- shared market-data infrastructure;
- shared historical-data infrastructure;
- OMS/risk tests;
- generic candle tests;
- Strategy B code, tests or Strategy B-specific research;
- unrelated reports.

**Rule:** delete derived legacy Strategy A research results; preserve reusable source data.

---

# 5. Master Lifecycle

| Phase | Name | Status | Gate owner | Primary output |
|---|---|---:|---|---|
| 1 | Contract, Configuration, State Hardening & Clean Reset | [?] | Human | Strict Strategy A contract + clean baseline |
| 2 | Futures-Only Signal Data Path | [?] | Human | Canonical completed-15m futures input path |
| 3 | Deterministic Trend-Pullback State Machine | [?] | Human | New Strategy A trading logic |
| 4 | Service Integration & Runtime/Replay Semantics | [?] | Human | Thin orchestration + deterministic execution semantics |
| 5 | Strategy-Aware Option Selection | [?] | Human | Delta/expiry/liquidity based contract selector |
| 6 | Underlying-R Sizing & Position Management | [?] | Human | Structural-risk sizing and exits |
| 7 | Production-Path Historical Replay | [?] | Human | Same strategy path in replay/runtime |
| 8 | Regression & Acceptance Suite | [?] | Human | Full rule-to-test matrix |
| 9 | Forward Option-Execution Validation | [?] | Human | Executability evidence from captured chains |
| 10 | Paper/Shadow Readiness & Final Safety Gate | [?] | Human | Runtime/replay comparison and readiness report |

---

# 6. Current Status

**Current phase:** Phase 10 — Paper/Shadow Readiness & Final Safety Gate  
**Status:** `[?] Awaiting human review/approval`  
**Next phase:** Human review of the consolidated implementation  
**Progress:** 0/10 phases human-approved; 10/10 phases implementation evidence recorded

### Current Phase 1 review findings

The existing Phase 1 implementation has the correct overall direction, but must be hardened before approval.

Open items:

- [x] Make `StrategySetup` immutable/frozen.
- [x] Make `StrategyStateSnapshot` immutable/frozen.
- [x] Reject unexpected contract fields where practical.
- [x] Require one or more confluence references.
- [x] Enforce ENTERED chronology and setup-state invariants.
- [x] Define ACTIVE/CONSUMED semantics atomically on entry.
- [x] Eliminate remaining duplicated Strategy A compatibility defaults.
- [x] Remove replay metadata literals that duplicate compatibility config.
- [x] Clearly distinguish V2 hypothesis configuration from the frozen legacy evaluator.
- [x] Remove obsolete Strategy A baseline/reproducibility tests.
- [x] Remove obsolete derived Strategy A reports/backtest artifacts.
- [x] Preserve raw/shared data.
- [x] Add configuration-propagation sentinel test.
- [x] Add JSON serialization round-trip tests.
- [x] Add direct-mutation/invariant tests.
- [x] Run and record the relevant test suites.

### Phase 1 reviewed implementation context

Last reviewed Phase 1 delta:

```text
base: c66eccc2
head: 6402a6e0
branch: strategy-a-trend-pullback-v2
```

This identifies the reviewed code snapshot only. Update these values after new implementation work.

---

# PHASE 1 — Contract, Configuration, State Hardening & Clean Reset

**Status:** `[?] Awaiting human review/approval`

## Goal

Leave the repository with:

- one clean Strategy A configuration contract;
- one strict deterministic Strategy A state model;
- no ambiguous Strategy A defaults;
- a small relevant contract test suite;
- obsolete Strategy A tests/reports removed;
- reusable raw market data preserved;
- Strategy B unchanged.

Do not implement the new trading behavior in this phase.

## Authoritative Strategy A V2 defaults

The single authoritative configuration must contain:

| Parameter | Value |
|---|---:|
| EMA fast | 20 |
| EMA slow | 50 |
| ADX period | 14 |
| ADX threshold | 22 |
| ATR period | 14 |
| EMA separation minimum | 0.10 ATR |
| Confluence distance | 0.25 ATR |
| S/R zone | 0.10 ATR |
| Confirmation minimum body ratio | 0.40 |
| Confirmation close location | directional 30% |
| Confirmation maximum range | 1.50 ATR |
| Trigger buffer | 0.05 ATR |
| Trigger validity | 2 completed 15m bars |
| Maximum chase | 0.25 ATR |
| Structural stop buffer | 0.10 ATR |
| Minimum stop distance | 0.80 ATR |
| Maximum stop distance | 1.50 ATR |
| Minimum room to opposing S/R | 1.50R |
| T1 | 1.50R |
| Runner target/reference | 2.50R |
| Trailing activation | +1R |
| Entry session | 09:45–14:45 |
| Forced exit | 15:15 |

These are hypotheses, not proven optimal values.

## Contract requirements

### Strategy states

- `FLAT`
- `SETUP`
- `ARMED`
- `ENTERED`
- `COOLDOWN`

Legacy parsing may safely map:

- `SEARCHING -> FLAT`
- `TRIGGERED -> ARMED`
- `PAUSED -> FLAT`

Never map legacy `TRIGGERED` to `ENTERED`.

### Direction

- CALL / bullish
- PUT / bearish

### StrategySetup

Must contain:

- direction;
- setup timestamp;
- confirmation-bar timestamp;
- confirmation high/low;
- trigger price;
- structural stop;
- initial underlying risk `R`;
- relevant support/resistance level;
- one or more confluence references;
- setup expiry timestamp/bar index;
- invalidation state/reason.

Required invariants:

```text
confirmation_bar_timestamp <= setup_timestamp <= setup_expiry_timestamp
initial_underlying_r == abs(trigger_price - structural_stop)
CALL  -> structural_stop < trigger_price
PUT   -> structural_stop > trigger_price
```

Timestamps must be timezone-aware.

`confluence_references` must not be empty.

### StrategyStateSnapshot

Make it immutable.

`FLAT`:
- cannot carry direction/setup/entry/cooldown state.

`SETUP` and `ARMED`:
- require direction + active setup;
- direction must match setup;
- cannot carry entry/cooldown state.

`ENTERED`:
- requires direction + setup + entry timestamp;
- direction must match setup;
- entry must be timezone-aware;
- entry >= setup timestamp;
- entry <= setup expiry;
- setup must not be INVALIDATED or EXPIRED;
- define and document whether successful entry atomically marks the setup CONSUMED.

`COOLDOWN`:
- requires `cooldown_until`;
- cannot carry stale direction/setup/entry state.

State transitions must return new validated snapshots.

Direct mutation must not bypass invariants.

## Configuration authority

`StrategyTunablesConfig` is the source of truth.

Do not independently redefine Strategy A defaults in:

- `TrendPullbackStrategy`;
- `StrategyService`;
- `SimulationEngine`;
- replay metadata;
- helper functions;
- tests.

If temporary legacy evaluator values remain, they must be named compatibility fields and defined exactly once.

Examples:

- `legacy_strategy_a_adx_threshold`
- `legacy_trigger_buffer_atr`
- `legacy_min_impulse_atr`
- `legacy_retest_tolerance_atr`
- `legacy_min_available_confirmations`

Any remaining legacy stop offset/R limit/etc. must follow the same rule.

Replay metadata must distinguish:

1. Strategy A V2 contract configuration;
2. active evaluator version;
3. temporary legacy evaluator compatibility configuration.

Do not imply V2 values currently drive old evaluator behavior when they do not.

## Cleanup

Remove obsolete Strategy A-specific tests/artifacts tied to:

- old 5m impulse/pullback;
- EMA9 retest;
- old breakout;
- 8%-70% pullback;
- frozen PUT 40%-60%;
- confirmation voting;
- Supertrend;
- derivatives/OI scoring;
- RVOL;
- volume dry-up;
- fixed old signal IDs/counts;
- old Strategy A replay baselines.

Preserve shared/raw data per the retention policy.

## Required tests

- [ ] All planned V2 defaults.
- [ ] EMA fast < EMA slow.
- [ ] Minimum stop <= maximum stop.
- [ ] Entry start < entry end < forced exit.
- [ ] Invalid configuration rejected.
- [ ] CALL setup valid.
- [ ] PUT setup valid.
- [ ] JSON round-trip.
- [ ] Naive timestamps rejected.
- [ ] Confirmation high > low.
- [ ] R equals trigger-stop distance.
- [ ] Directional stop invariant.
- [ ] Confluence references non-empty.
- [ ] Invalidation reason/state consistency.
- [ ] Timestamp ordering.
- [ ] All valid state transitions.
- [ ] Illegal transitions rejected.
- [ ] Direct mutation cannot create illegal state.
- [ ] ENTERED chronology checks.
- [ ] ENTERED rejects invalidated/expired setup.
- [ ] Legacy state aliases.
- [ ] Public compatibility constructor/import.
- [ ] Migration preserves Strategy B.
- [ ] Configuration-propagation sentinel test.

### Configuration-propagation sentinel test

Use unusual values such as:

```text
legacy ADX = 27.0
legacy trigger buffer = 0.031
legacy min impulse = 0.83
legacy retest tolerance = 0.37
legacy min confirmations = 4
```

Verify the exact values propagate through:

- runtime Strategy A construction;
- simulation Strategy A construction;
- replay/config metadata.

The test must fail if any consumer secretly hardcodes a default.

## Acceptance gate

Phase 1 can move to `[?]` only if:

- [ ] State objects cannot mutate into illegal combinations.
- [ ] ENTERED invariants are complete.
- [ ] Setup confluence references are required.
- [ ] V2 defaults have one authoritative source.
- [ ] Legacy evaluator values have one explicit compatibility source.
- [ ] Replay/runtime/simulation do not independently redefine defaults.
- [ ] Obsolete Strategy A tests/results are removed.
- [ ] Raw/shared data is preserved.
- [ ] Strategy B is unchanged.
- [ ] Relevant automated tests pass.

## Implementation evidence

**Files modified:** _TBD_  
**Files added:** _TBD_  
**Files deleted:** _TBD_  
**Tests run:** _TBD_  
**Result:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 2 — Make NIFTY Futures the Single Signal Data Source

**Status:** `[?] Awaiting human review/approval`

## Goal

Strategy A must use the active NIFTY futures contract as the sole source for signal/structure decisions.

Authoritative futures-derived inputs:

- OHLC;
- EMA20;
- EMA50;
- ATR14;
- ADX14;
- +DI;
- -DI;
- session VWAP;
- swing/support/resistance structure;
- confirmation candles;
- trigger levels;
- structural stops;
- underlying `R`.

NIFTY spot may remain elsewhere in the platform but must not influence Strategy A signals.

Options remain execution instruments only.

## Implementation requirements

- [ ] Deterministic active/nearest futures contract resolver.
- [ ] Explicit contract rollover.
- [ ] Never splice two futures contracts into one candle.
- [ ] Every candle/setup identifies its futures contract.
- [ ] Session VWAP resets each trading session.
- [ ] Reject stale/incomplete candles.
- [ ] Indicators use only completed candles.
- [ ] No future candle can affect current calculations.
- [ ] Live/paper and replay use the same feature-generation API.
- [ ] If deriving 15m from 5m, aggregation must use only completed source candles and be deterministic.
- [ ] Do not delete global spot support.
- [ ] Do not change option selection yet.

## Required tests

- [ ] Strategy A features come from one futures contract.
- [ ] Spot changes do not alter Strategy A signal input.
- [ ] Incomplete 15m candles cannot generate signals.
- [ ] EMA/ATR/ADX/DI have no future leakage.
- [ ] VWAP resets at session boundaries.
- [ ] Rollover never combines contracts into one candle.
- [ ] Replay/live feature calculations match for identical candles.

## Acceptance gate

- [ ] One canonical Strategy A input path exists.
- [ ] Spot is absent from Strategy A signal decisions.
- [ ] Contract identity is explicit.
- [ ] Completion/staleness rules are enforced.
- [ ] Feature parity tests pass.

## Evidence

**Files:** _TBD_  
**Tests:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 3 — Deterministic Trend-Pullback State Machine

**Status:** `[?] Awaiting human review/approval`

## Goal

Replace the old Strategy A internal trading logic with the new completed-15m NIFTY futures Trend-Pullback Confluence state machine.

## Remove from active Strategy A logic

- [ ] old 5m impulse/pullback state machine;
- [ ] 5m EMA9 retests;
- [ ] 5m breakout logic;
- [ ] generic 8%-70% pullback;
- [ ] PUT 40%-60% frozen candidate;
- [ ] confirmation-score voting;
- [ ] Supertrend scoring;
- [ ] derivatives/OI scoring;
- [ ] RVOL scoring;
- [ ] volume dry-up scoring;
- [ ] "2 of N confirmations";
- [ ] spot breakout triggers.

## Trend regime

Bullish:

```text
EMA20 > EMA50
+DI > -DI
ADX >= 22
abs(EMA20 - EMA50) >= 0.10 ATR
```

Bearish is symmetric.

No scoring.

## Support / resistance

- deterministic confirmed price-action pivots;
- current-time information only;
- no repaint/look-ahead;
- S/R zone approximately ±0.10 ATR;
- pivot confirmation rule documented and tested.

## Pullback / confluence

Price must pull back into confirmed S/R with:

- EMA20 and/or
- session VWAP

within 0.25 ATR.

The exact boolean rule must be explicit.

## Confirmation candle

Directional candle must satisfy:

- body >= 40% of total range;
- close in directional 30% of range;
- total range <= 1.50 ATR;
- correct direction;
- safe zero-range handling.

## Trigger

CALL:

```text
confirmation_high + 0.05 * ATR
```

PUT:

```text
confirmation_low - 0.05 * ATR
```

Trigger valid for next 2 completed 15m bars.

Reject chase > 0.25 ATR beyond intended trigger.

## Structural stop

Based on futures structure:

- pullback/structure extreme;
- 0.10 ATR buffer;
- minimum risk distance 0.80 ATR;
- maximum risk distance 1.50 ATR.

Reject the trade if structure cannot support a valid stop.

Do not invent an unrelated stop.

## Room to target

Require at least 1.50R before meaningful opposing confirmed S/R.

## State behavior

Explicitly model:

- setup creation;
- arming;
- triggering;
- expiry;
- invalidation;
- entry;
- cooldown.

Every rejection/invalidation has a machine-readable reason.

Same confirmation candle must not recreate duplicate setups.

## Required tests

- [ ] Bullish and bearish symmetry.
- [ ] Trend boundaries.
- [ ] Pivot confirmation/no look-ahead.
- [ ] EMA confluence.
- [ ] VWAP confluence.
- [ ] No confluence.
- [ ] Confirmation body boundary.
- [ ] Confirmation close-location boundary.
- [ ] Oversized candle.
- [ ] Zero-range candle.
- [ ] Trigger boundaries.
- [ ] Two-bar expiry.
- [ ] Chase rejection.
- [ ] Stop min/max.
- [ ] Room-to-target.
- [ ] Duplicate evaluation/signal prevention.

## Acceptance gate

- [ ] Same ordered candles + same config always produce same transitions/signals.
- [ ] All legacy active Strategy A logic is removed/disabled.
- [ ] All rule boundaries have tests.
- [ ] Rejections are machine-readable.

## Evidence

**Files:** _TBD_  
**Tests:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 4 — Service Integration & Runtime/Replay Execution Semantics

**Status:** `[?] Awaiting human review/approval`

## Goal

`service.py` orchestrates; `trend_pullback.py` owns Strategy A decision rules.

## Service responsibilities

1. Resolve active NIFTY futures contract.
2. Obtain completed 15m futures candles.
3. Compute canonical feature set.
4. Build one well-defined strategy input.
5. Receive deterministic setup/signal decisions.
6. Select option only after underlying entry signal.
7. Route execution info to OMS/risk/position management.
8. Persist diagnostics/rejection reasons.

## Session rules

- no new setup/entry before 09:45;
- no new entry after 14:45;
- forced exit at 15:15;
- exchange-local timestamps consistently.

Old Strategy A 09:20/15:20 behavior must not remain active.

## Trigger semantics

Live/paper/replay must agree.

If tick/event crossing is supported:

- trigger must have been armed from already completed information;
- replay must have an equivalent execution model.

If exact intrabar ordering cannot be reconstructed:

- use conservative deterministic assumptions;
- document the limitation;
- do not use optimistic fills.

## Diagnostics

Persist enough to reconstruct:

- created;
- rejected;
- armed;
- triggered;
- expired;
- invalidated;
- entered;
- exited.

## Required tests

- [ ] Session boundaries.
- [ ] Stale data.
- [ ] Incomplete bars.
- [ ] Duplicate evaluation.
- [ ] Restart/recovery.
- [ ] Option selection not invoked without underlying signal.
- [ ] Paper/live routing restrictions.
- [ ] Existing Strategy A live safety gate remains effective.

## Acceptance gate

- [ ] Service contains orchestration, not duplicated Strategy A rules.
- [ ] Runtime/replay trigger semantics are documented and deterministic.
- [ ] Full lifecycle diagnostics are persisted.

## Evidence

**Files:** _TBD_  
**Tests:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 5 — Strategy-Aware Option Contract Selection

**Status:** `[?] Awaiting human review/approval`

## Goal

Replace Strategy A premium-cap-driven moneyness selection with delta/expiry/liquidity-aware deterministic selection.

## Direction

- bullish underlying signal -> buy CALL;
- bearish underlying signal -> buy PUT.

## Expiry

Select nearest weekly expiry with at least **2 trading sessions remaining**.

Trading-session-aware; do not use simple calendar-day subtraction.

## Delta

Preferred absolute delta:

```text
0.60–0.65
```

Allowed:

```text
0.55–0.70
```

Then rank by execution quality.

## Contract metadata

Carry:

- expiry;
- strike;
- option type;
- bid;
- ask;
- mid;
- spread;
- spread percentage;
- OI;
- volume;
- lot size;
- quote timestamp/freshness;
- delta if available;
- gamma if available;
- Greek timestamp/source if available.

Do not fabricate Greeks.

Greek source must distinguish:

- broker provided;
- locally calculated;
- unavailable/unreliable.

Never treat premium/strike distance as delta.

## Selection order

1. Filter invalid/stale/ineligible contracts.
2. Filter delta eligibility.
3. Rank by closeness to preferred delta band/target.
4. Rank by execution quality/liquidity deterministically.

Old ₹70 premium cap must not determine moneyness.

Insufficient capital is a sizing/capital rejection, not a reason to choose a fundamentally different cheap contract.

Gamma filter only if reliable gamma exists.

## Required tests

- [ ] CALL/PUT.
- [ ] Delta boundaries.
- [ ] Holiday/weekend expiry logic.
- [ ] Stale quote.
- [ ] Wide spread.
- [ ] Missing Greeks.
- [ ] Deterministic ranking.
- [ ] Lot-size metadata.
- [ ] No eligible contract.
- [ ] Capital too small.
- [ ] Premium cap no longer changes directional moneyness.

## Acceptance gate

- [ ] Deterministic selector.
- [ ] Delta provenance is explicit.
- [ ] Expiry uses trading sessions.
- [ ] Missing Greeks never fabricated.
- [ ] Capital rejection separated from contract selection.

## Evidence

**Files:** _TBD_  
**Tests:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 6 — Position Sizing & Position Management Around Underlying R

**Status:** `[?] Awaiting human review/approval`

## Goal

Risk begins with the NIFTY futures structure, not a fixed option-premium stop percentage.

## Entry record

Preserve:

- underlying entry;
- underlying structural stop;
- underlying R;
- option entry bid/ask/fill;
- option delta if available;
- quantity/lots;
- risk budget;
- estimated option loss under underlying-stop scenario.

## Sizing hierarchy

Prefer:

1. option repricing with reliable Greeks/model inputs;
2. conservative delta-based estimate;
3. explicit configured fallback.

Never equate a 25% option premium stop with structural R.

Sizing bounded by:

- account risk;
- available capital;
- lot size;
- max lots;
- portfolio/risk limits.

If risk cannot be estimated reliably, reject or conservatively constrain according to explicit config.

## Normal exits

Underlying futures thesis controls:

- structural stop;
- T1 = +1.5R;
- runner reference = +2.5R;
- trailing activation = +1R;
- forced exit = 15:15.

Partial exits must use whole option lots.

Define one-lot degradation behavior explicitly.

Option-premium emergency stop may remain only as catastrophic execution/risk protection.

Remove/disable legacy adverse-health strategy exit scoring unless it is purely a safety mechanism.

## Required tests

- [ ] Structural stop.
- [ ] Stop min/max.
- [ ] Sizing at multiple deltas/premiums.
- [ ] Missing Greeks.
- [ ] Lot rounding.
- [ ] One-lot position.
- [ ] Partial exits.
- [ ] +1R trailing activation.
- [ ] T1.
- [ ] Runner.
- [ ] Emergency option stop.
- [ ] Forced EOD exit.
- [ ] No unrelated legacy health-score exit.

## Acceptance gate

- [ ] Structural underlying R controls risk.
- [ ] Estimated option loss is explicit.
- [ ] Whole-lot behavior is deterministic.
- [ ] Normal exit logic follows underlying thesis.

## Evidence

**Files:** _TBD_  
**Tests:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 7 — Historical Replay Uses the Exact Production Strategy Path

**Status:** `[?] Awaiting human review/approval`

## Goal

No separate backtest implementation of Strategy A.

Historical replay must use production:

- contract resolution;
- completed 15m futures candles;
- feature engine;
- state machine;
- chronological state transitions.

## Requirements

- [ ] Historical futures contract resolver.
- [ ] Deterministic historical rollover.
- [ ] No contract splicing unless explicitly intentional/documented.
- [ ] Record futures contract for every trade.
- [ ] No future-data leakage.
- [ ] Conservative trigger execution.
- [ ] Event recording for setup/trigger/entry/stop/target/expiry/rejection.

## Fill assumptions must document

- breakout trigger fills;
- gaps through trigger;
- gaps through stop;
- same-bar target/stop ambiguity;
- option fills when historical option quotes are unavailable.

Unknown intrabar ordering must use a conservative deterministic rule.

## Report identity

New reports must be versioned so they cannot be confused with deleted/superseded old Strategy A research.

Report at least:

- strategy version;
- config version/hash;
- data period;
- futures contracts used;
- sessions;
- setups;
- entries;
- rejections by reason;
- CALL/PUT counts;
- R results;
- win/loss distribution;
- profit factor;
- average R/expectancy;
- max drawdown in R;
- exit reasons;
- unresolved trades;
- data-quality warnings.

Do not claim option profitability without option execution data.

## Acceptance gate

- [ ] Replay calls production strategy code.
- [ ] Futures rollover is explicit.
- [ ] Fill assumptions are conservative/documented.
- [ ] Reports identify version/config/data.
- [ ] No option profitability claim from underlying-only data.

## Evidence

**Files:** _TBD_  
**Tests/replays:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 8 — Full Regression & Acceptance Test Suite

**Status:** `[?] Awaiting human review/approval`

## Goal

Build deterministic acceptance coverage for every Strategy A rule.

Do not modify parameters merely to make tests pass.

## Required synthetic fixtures

1. valid bullish setup;
2. valid bearish setup;
3. ADX below threshold;
4. incorrect DI direction;
5. insufficient EMA separation;
6. no S/R;
7. S/R not yet confirmed;
8. valid EMA confluence;
9. valid VWAP confluence;
10. no confluence;
11. weak confirmation candle;
12. oversized confirmation candle;
13. valid trigger;
14. expired trigger;
15. chase rejection;
16. insufficient room to opposing S/R;
17. stop below 0.80 ATR;
18. stop above 1.50 ATR;
19. duplicate evaluation;
20. session-boundary rejection.

## Critical look-ahead tests

- [ ] Future candles do not change previously computed historical features.
- [ ] Pivot cannot exist before confirmation bars.
- [ ] Confirmation processed only after candle completion.
- [ ] Trigger uses already-known information only.
- [ ] Replay cannot inspect future outcome to decide entry.

## Parity tests

Given identical event/candle input:

- [ ] feature output identical;
- [ ] state transitions identical;
- [ ] signal fields identical;
- [ ] rejection reasons identical.

## Safety regressions

- [ ] Strategy B unchanged.
- [ ] Spot-data services still work.
- [ ] Option-chain capture works.
- [ ] OMS/risk gates work.
- [ ] Strategy A cannot route LIVE while safety restriction remains.
- [ ] Raw/shared data remains intact.

## Final artifact

Produce a rule-to-test matrix mapping every strategy rule to at least one automated test.

Classify failures/tests as:

- obsolete intentional replacement;
- updated equivalent;
- genuine regression.

## Acceptance gate

- [ ] Every deterministic rule has automated coverage or an explicit documented testing limitation.
- [ ] Look-ahead tests pass.
- [ ] Runtime/replay parity tests pass.
- [ ] Safety regressions pass.

## Evidence

**Test matrix:** _TBD_  
**Commands/results:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 9 — Forward Option-Execution Validation

**Status:** `[?] Awaiting human review/approval`

## Goal

Determine whether valid underlying Strategy A signals are actually executable through NIFTY options using forward-captured chain data.

Do not optimize underlying strategy parameters here.

## For every underlying signal during chain coverage

Reconstruct only information available at that timestamp.

Validate:

- chain freshness;
- eligible expiry;
- delta availability/reliability;
- selected strike;
- bid/ask;
- spread;
- OI;
- volume;
- lot size;
- estimated entry fill;
- slippage;
- position sizing;
- option behavior at underlying stop/T1/runner events where later quotes exist.

Never backfill future chain information into an earlier signal.

Never use EOD option data as an intraday executable quote.

## Validation states

Keep distinct:

- `UNDERLYING_VALID`
- `OPTION_CONTRACT_FOUND`
- `OPTION_EXECUTABLE`
- `OPTION_OUTCOME_OBSERVABLE`

A valid underlying signal may fail option execution validation.

## Report

Include:

- underlying signals;
- signals with usable chain snapshots;
- eligible contracts;
- expiry rejections;
- delta rejections;
- spread/liquidity rejections;
- stale-data rejections;
- sizing/capital rejections;
- executable coverage percentage;
- observed option outcomes where available.

Do not claim profitability from underlying-only results.

Do not tune parameters from a small forward option sample.

## Acceptance gate

- [ ] Time-correct chain reconstruction.
- [ ] Execution states remain separated.
- [ ] No future-chain leakage.
- [ ] Coverage/rejections are measurable.
- [ ] Claims are limited by data availability.

## Evidence

**Coverage period:** _TBD_  
**Signals:** _TBD_  
**Executable coverage:** _TBD_  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# PHASE 10 — Paper/Shadow Readiness, Diagnostics & Final Safety Gate

**Status:** `[?] Awaiting human review/approval`

## Goal

Prepare Strategy A for extended paper/shadow observation.

Do not enable unrestricted LIVE trading.

## Structured diagnostics

For each strategy event/evaluation persist enough to inspect:

- futures contract;
- 15m candle timestamp;
- EMA20;
- EMA50;
- ADX;
- +DI;
- -DI;
- ATR;
- VWAP;
- active S/R;
- trend regime result;
- confluence result;
- confirmation result;
- trigger;
- structural stop;
- initial R;
- room to opposing S/R;
- setup state;
- rejection/invalidation reason;
- selected option;
- expiry;
- delta/gamma source;
- bid/ask/spread;
- position size;
- entry fill;
- exit reason;
- realized R;
- realized option P&L.

Use structured data, not only free-text logs.

## Paper/shadow metrics

Track:

- evaluated sessions;
- setups/session;
- rejection distribution;
- signal timing;
- trigger frequency;
- expired setups;
- option-selection success rate;
- stale-chain rate;
- spread rejection rate;
- sizing rejection rate;
- paper slippage;
- underlying R;
- option P&L;
- expected-vs-observed state discrepancies.

## Runtime/replay comparison

Persist enough data to compare, event by event:

```text
recorded paper decision
vs
offline replay decision
```

for identical market data.

Provide a comparison utility/test that flags differences.

## Safety

- existing Strategy A live-routing safety gate remains;
- LIVE is never automatically enabled.

## Final readiness report

Report separately:

- code/tests ready;
- underlying replay ready;
- option execution validation ready/not ready;
- paper validation ready/not ready;
- live readiness blocked/unblocked.

Live readiness remains blocked until sufficient option-execution and paper/shadow evidence exists.

## Acceptance gate

- [ ] Structured diagnostics complete.
- [ ] Paper metrics available.
- [ ] Offline replay comparison available.
- [ ] Discrepancies detectable.
- [ ] Safety gate remains.
- [ ] Readiness classification is evidence-based.

## Evidence

**Paper observation period:** _TBD_  
**Replay comparison:** _TBD_  
**Known discrepancies:** _TBD_  
**Live status:** `BLOCKED`  
**Commit(s):** _TBD_  
**Reviewer decision:** _TBD_

---

# 7. Cross-Phase Decision Register

Record durable architectural decisions here.

| ID | Decision | Phase | Status | Rationale |
|---|---|---:|---|---|
| D-001 | NIFTY futures is the sole Strategy A signal/structure instrument | 2 | Approved by plan | Prevent spot/futures structure mixing |
| D-002 | Completed 15m bars drive Strategy A decisions | 2/3 | Approved by plan | Determinism and replay parity |
| D-003 | Options are execution vehicles only | 2/5 | Approved by plan | Separate underlying thesis from option contract |
| D-004 | State machine = FLAT/SETUP/ARMED/ENTERED/COOLDOWN | 1/3 | Approved by plan | Explicit lifecycle |
| D-005 | Old Strategy A derived backtests/tests may be removed | 1 | Approved by user | Clean validation baseline |
| D-006 | Reusable raw historical and forward option-chain data is preserved | 1 | Approved by user | Needed for future validation |
| D-007 | Strategy B is outside this workstream | All | Approved by plan | Avoid regressions / scope creep |
| D-008 | LIVE remains blocked until option + paper evidence | 10 | Approved by plan | Safety |

Add new decisions; do not rewrite past decisions without recording a superseding decision.

---

# 8. Known Limitations Register

| ID | Limitation | First observed | Status | Resolution phase |
|---|---|---:|---|---:|
| L-001 | Existing Strategy A evaluator is still legacy during Phase 1 | 1 | Resolved | Review-fix runtime/replay parity |
| L-002 | Existing signal path mixes old spot/futures responsibilities | 1 | Resolved | Review-fix futures-entry invariant |
| L-003 | Historical option execution may lack reliable intraday quotes | Plan | Open | 7/9 |
| L-004 | Exact intrabar event order may be unknowable from 15m OHLC | Plan | Open | 4/7 |
| L-005 | Reliable delta/gamma may not always be broker-provided | Plan | Open | 5/6 |
| L-006 | Exact exchange holiday calendar is not stored in the repository | Review | Accepted | Injected `OptionSelectionConfig.exchange_holidays`; production calendar remains an operational input |
| L-007 | Paper/shadow observation period and sufficient option-outcome sample are not complete | Review | Open | 10 |

Update status to `Resolved`, `Accepted`, or `Open` with evidence.

---

# 9. Test Evidence Ledger

Append one row per meaningful test run.

| Timestamp | Phase | Commit | Command | Result | Notes |
|---|---:|---|---|---|---|
| _TBD_ | 1 | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

Never replace old rows. Add new evidence.

---

# 10. Artifact Ledger

Track generated implementation evidence.

| Phase | Artifact | Path | Version/commit | Status |
|---:|---|---|---|---|
| 1 | Phase 1 contract test suite | `tests/test_strategy_contracts.py` | _TBD_ | Existing / revise |
| 7 | Replay report | _TBD_ | _TBD_ | Not created |
| 8 | Strategy rule-to-test matrix | _TBD_ | _TBD_ | Not created |
| 9 | Option execution validation report | _TBD_ | _TBD_ | Not created |
| 10 | Paper/replay discrepancy report | _TBD_ | _TBD_ | Not created |

Only track current/new Strategy A artifacts here.

---

# 11. Phase Handoff Contract

Before moving from phase N to N+1, the agent must write:

```text
PHASE HANDOFF

Phase:
Status:
Implementation commit:
Files changed:
Files deleted:
Tests run:
Pass/fail counts:
Acceptance gate:
Known limitations:
Deferred items:
Next phase may assume:
Next phase must NOT assume:
Human approval:
```

If any acceptance item is not satisfied, status is `[!]` or `[R]`, not complete.

---

# 12. Lifecycle Change Log

Append-only.

## 2026-09-20 — Tracker created

- Converted the 10-step Strategy A implementation plan into one phase-gated AI-DLC tracker.
- Integrated the Phase 1 hardening review.
- Marked Phase 1 as in progress.
- Added user-approved clean-reset policy for obsolete Strategy A tests/backtest artifacts.
- Preserved reusable raw historical and option-chain data by policy.
- Kept Strategy B explicitly outside this workstream.
- Added acceptance gates, evidence ledger, decisions, limitations and phase handoff contract.

---

# 13. Resume Instruction for Any Future Coding Agent

When starting a new session:

1. Read this file.
2. Find the first phase not `[x]`.
3. Read that phase's Goal, Requirements, Tests and Acceptance Gate.
4. Inspect the current repository before assuming the tracker is still accurate.
5. If repo reality differs from this file, update **Current Status** and append a change-log entry before coding.
6. Implement only that phase.
7. Run the phase tests.
8. Update evidence.
9. Move phase to `[?]` for human review.
10. Do not begin the next phase without approval.

---

# 14. Batch implementation evidence — 2026-09-20

The user explicitly authorized one continuous implementation run through all
ten phases.  Every implementation phase is therefore marked `[?] Awaiting
human review/approval`; none is marked `[x]` because human approval has not
occurred.

## Phase handoffs

### Phase 1

Status: `[?] Awaiting human review/approval`  
Files modified: `services/strategy/models.py`, `services/strategy/replay_metadata.py`, `services/strategy/service.py`, `services/strategy/simulation.py`  
Files added: `tests/test_strategy_a_v2.py`  
Files deleted: obsolete Strategy A tests and derived baselines listed in the Cleanup section  
Implementation decisions: frozen setup/snapshot contracts; strict extra-field rejection; non-empty confluence references; ENTERED consumes setup atomically; V2 configuration is the active Strategy A source and legacy fields are compatibility metadata only.  
Tests: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_contracts.py tests/test_replay_metadata.py` — `20 passed`.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: legacy compatibility fields remain in the shared configuration model for migration/API compatibility.

### Phase 2

Status: `[?] Awaiting human review/approval`  
Files added: `services/strategy/futures_signal.py`  
Implementation decisions: completed 15m futures bars are canonical; exact three contiguous 5m bars are required for aggregation; indicators and pivots are bounded by `as_of`; one contract is permitted per feature series; session VWAP resets by IST date.  
Tests: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_v2.py` — `10 passed` (including future-data, aggregation, rollover, and configuration-propagation tests).  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: broker instrument-master rollover resolution is represented by deterministic expiry parsing and remains subject to live instrument metadata validation.

### Phase 3

Status: `[?] Awaiting human review/approval`  
Files modified: `services/strategy/strategies/trend_pullback.py`, `services/strategy/models.py`  
Implementation decisions: replaced the active Strategy A evaluator with FLAT/SETUP/ARMED/ENTERED/COOLDOWN; confirmed pivots use two bars on each side; all rejection/invalidation reasons are structured strings; runtime and replay use the same evaluator; no score-based confirmations remain in Strategy A.  
Tests: `tests/test_strategy_a_v2.py`, `tests/test_strategy_contracts.py` — `30 passed` in the combined focused run.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: additional synthetic fixtures can be expanded during human review without changing the authoritative parameters.

### Phase 4

Status: `[?] Awaiting human review/approval`  
Files modified: `services/strategy/service.py`, `services/strategy/simulation.py`, `services/strategy/position_manager.py`  
Implementation decisions: service passes the authoritative config into Strategy A; simulation no longer uses the removed replay-only trigger bookkeeping; Strategy A management is futures-R based; the existing LIVE gate remains unchanged.  
Tests: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_simulation.py tests/test_strategy_b_replay_lifecycle.py tests/test_volatility_breakout_fixed.py` — `48 passed, 1 warning`; the warning is an upstream Breeze SDK deprecation warning.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: exact intrabar order remains unknowable from 15m OHLC and is conservatively represented by the state machine.

### Phase 5

Status: `[?] Awaiting human review/approval`  
Files modified: `services/strategy/contract_selector.py`, `services/strategy/models.py`  
Implementation decisions: Strategy A filters nearest expiry with two remaining trading sessions, requires usable absolute delta in 0.55–0.70, ranks toward 0.60–0.65, and carries Greek provenance; premium is not a moneyness filter.  
Tests: selector, expiry, missing-delta, freshness, liquidity, and provenance coverage in `tests/test_strategy_a_v2.py` — passed.  
Acceptance-gate result: implementation evidence present; awaiting human review.  

---

# 15. Review-fix reconciliation — 2026-09-21

Review base: `861306bfc48c5e652d72e42a517facd5e00da251`.

All phase headings remain `[?] Awaiting human review/approval`; automated
evidence does not grant human approval. Relevant acceptance items are
reconciled below with `[x]` for implemented and tested, `[S]` for an explicit
testing limitation, and `[?]` where operational human evidence is still
required.

| Acceptance item | Status | Evidence / limitation |
|---|---|---|
| Strategy A forced exit is configurable at 15:15; Strategy B remains 15:20 | [x] | `PositionManager` boundary tests |
| Actual futures trigger/open entry is authoritative for R, sizing, trade, telemetry, and replay | [x] | `StrategySignal.underlying_entry_price`, service/sizer/replay paths and normal/gap tests |
| +1R protective stop is enforced and monotonic for CALL/PUT | [x] | Position-manager regression test |
| T1 +1.5R is whole-lot deterministic; one-lot behavior is explicit | [x] | 1/2/3/4-lot parameterized test and paper partial-exit path |
| Canonical futures resolver and rollover reset | [x] | Runtime feature path, replay path, `FUTURES_ROLLOVER_RESET` test |
| Replay lifecycle, conservative OHLC, metrics, exit reasons, unresolved trades | [x] | `replay_lifecycle.py`, `replay_strategy_a.py`, simulation and replay tests |
| Phase 8 deterministic acceptance matrix and genuine runtime/replay parity | [x] | Concrete mapping in `tests/STRATEGY_A_RULE_TEST_MATRIX.md` |
| Preferred delta band 0.60–0.65 ranked before outside-band quality | [x] | Selector ranking test |
| Explicit quote freshness and timestamp validation; Greek provenance carried | [x] | Missing/naive/future/stale quote tests; selected Greek timestamp uses same-snapshot quote provenance when no separate Greek timestamp exists |
| Phase 9 inspected-status rejection accounting | [x] | Expiry, delta, stale, spread/liquidity and sizing bucket tests |
| Telemetry lifecycle and restart persistence | [x] | Structured decision-log persistence and restart restoration test |
| Strategy A service entry window 09:45–14:45; Strategy B legacy window unchanged | [x] | Service/runtime window code and boundary tests |
| Structural stop uses confirmed pullback structure extreme | [x] | `_build_setup` test and implementation comments |
| Holiday semantics | [S] | Calendar is injected/configured; no repository-owned exchange calendar exists |
| Strategy B unchanged | [x] | Focused Strategy B/volatility regression suites and origin diff check |
| Legacy reference classification and tracker reconciliation | [x] | Matrix, this section, Decision Register, Limitations, Evidence and Change Log |
| LIVE safety | [x] | Existing gate retained; Strategy A order routing remains blocked |

## Review-fix decisions

| ID | Decision | Status |
|---|---|---|
| D-009 | `StrategySignal.underlying_entry_price` is the authoritative Strategy A futures trigger/open fill; compatibility spot fields are not risk inputs | Implemented |
| D-010 | Strategy A uses `StrategyTunablesConfig.forced_exit_time` (default 15:15) and 09:45–14:45 entry window; Strategy B retains shared legacy timers | Implemented |
| D-011 | Position management enforces a monotonic +1R protective stop and T1 +1.5R whole-lot partial exit with explicit one-lot degradation | Implemented |
| D-012 | Runtime, simulation, replay and feature generation resolve one canonical nearest non-expired futures contract | Implemented |
| D-013 | Replay uses conservative stop-first OHLC ambiguity handling and records unresolved session-end trades | Implemented |
| D-014 | Exchange holidays are injected through configuration/chain metadata; a complete repository calendar is outside this change | Accepted limitation |

## Review-fix artifact and evidence ledger

| Artifact | Path | Status |
|---|---|---|
| Review-fix acceptance/regression suite | `tests/test_strategy_a_review_fixes.py` | Added |
| Rule-to-test matrix | `tests/STRATEGY_A_RULE_TEST_MATRIX.md` | Reconciled with concrete test names |
| Runtime/replay telemetry comparison | `services/strategy/telemetry.py` | Exact field comparison; persisted decision-log records |
| Strategy A replay lifecycle report | `services/strategy/replay_strategy_a.py`, `services/strategy/replay_lifecycle.py` | Underlying lifecycle metrics and conservative exits |

## Review-fix test evidence

Final command results:

- Review-fix acceptance suite: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_review_fixes.py` — `39 passed`.
- Required focused suites: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_v2.py tests/test_strategy_contracts.py tests/test_forward_option_execution_validation.py tests/test_strategy_simulation.py tests/test_strategy_b_forward_validation.py tests/test_strategy_b_replay_lifecycle.py tests/test_volatility_breakout_fixed.py tests/test_live_gate.py` — `94 passed`.
- Full suite: `.venv\Scripts\python.exe -m pytest -q tests` — `218 passed, 6 warnings`.
- Compile check: `.venv\Scripts\python.exe -m compileall -q services libs tests` — passed.
- Baseline smoke against `861306b`: Strategy A 15:15 was not a dedicated baseline gate, `StrategySignal` had no declared authoritative underlying-entry field, and the baseline manager had no T1 partial-exit implementation; the new regression paths therefore exercise baseline gaps.

## Review-fix handoff

Implementation is paper/shadow scoped. Strategy A LIVE routing remains
blocked by the existing safety gate and no LIVE order path was enabled.
Remaining operational work is the paper/shadow observation period, sufficient
forward option-outcome coverage, and human approval of the ten `[?]` phases.

---

## 2026-09-21 — Review-fix lifecycle change

- Audited the pushed review base `861306b` against all listed findings.
- Added futures-authoritative entry/R propagation, explicit Strategy A timing,
  protective-stop enforcement, whole-lot T1 management, canonical rollover
  handling, replay lifecycle metrics, selector timestamp/ranking checks,
  Phase 9 accounting, and persisted telemetry restoration.
- Added concrete review-fix regression tests and reconciled the rule matrix.
- Preserved Strategy B compatibility behavior and kept all phase headings at
  `[?]` pending human review.
Known limitation: locally calculated Greeks are accepted only when explicitly supplied and labelled; this repository does not fabricate a local model.

### Phase 6

Status: `[?] Awaiting human review/approval`  
Files modified: `services/strategy/position_manager.py`, `services/strategy/service.py`, `services/strategy/models.py`  
Implementation decisions: `UnderlyingRiskSizer` estimates option loss from futures structural R using reliable delta or rejects without an explicit fallback; whole-lot sizing is deterministic; T1/runner/trailing values come from V2 configuration; option premium stop remains emergency-only.  
Tests: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_v2.py` — `10 passed`.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: live reliable repricing requires broker Greeks/volatility inputs not guaranteed by captured data.

### Phase 7

Status: `[?] Awaiting human review/approval`  
Files added: `services/strategy/replay_strategy_a.py`  
Files modified: `services/strategy/simulation.py`, `services/strategy/replay_metadata.py`  
Implementation decisions: replay calls the production `TrendPullbackStrategy`; reports carry version/config fingerprint/data range/contracts and explicitly disclaim option profitability; contract changes reset the state machine rather than splice bars.  
Tests: replay report and event-level comparison in `tests/test_strategy_a_v2.py`; simulation/replay lifecycle suites passed.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: historical option fills and exact intrabar sequences remain data-limited.

### Phase 8

Status: `[?] Awaiting human review/approval`  
Files added: `tests/STRATEGY_A_RULE_TEST_MATRIX.md`, `tests/test_strategy_a_v2.py`  
Implementation decisions: boundary-focused tests cover contracts, look-ahead, aggregation, rollover, expiry, delta, sizing, replay comparison, and shared regressions; obsolete signal-count baselines were removed.  
Tests: focused suites and retained shared/Strategy B suites passed; the latest complete suite recorded `179 passed, 7 warnings`.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: the full suite is green, but its dependency warnings remain environment/library-version dependent.  

### Phase 9

Status: `[?] Awaiting human review/approval`  
Files added: `services/strategy/option_execution_validation.py`  
Implementation decisions: validation states distinguish underlying validity, contract selection, executability, and observable outcomes; each result carries a state history; chain data is looked up only at signal timestamp; no EOD quote is treated as an executable intraday quote.  
Tests: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_v2.py tests/test_forward_option_execution_validation.py` — `15 passed`; explicit coverage includes all four validation states.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: no claim of executable coverage is made without a captured-chain coverage run; the utility reports zero/available coverage explicitly.

### Phase 10

Status: `[?] Awaiting human review/approval`  
Files added: `services/strategy/telemetry.py`  
Files modified: `services/strategy/service.py`  
Implementation decisions: structured Strategy A evaluation records capture futures features, state, rejection, option metadata, sizing, management and outcome fields; event-level runtime/replay comparison reports mismatched decisions; LIVE remains blocked.  
Tests: telemetry/replay comparison surfaces are exercised by the focused Strategy A suite and service regressions.  
Acceptance-gate result: implementation evidence present; awaiting human review.  
Known limitation: paper/shadow observation-period metrics require future runtime observations and are not represented as historical proof.

## Consolidated review section

Repository state before commit: branch `strategy-a-trend-pullback-v2`, starting
commit `6402a6e`; implementation commit `43218b2`.

Architecture now flows through completed NIFTY futures candles → canonical
features/pivots → deterministic state machine → delta-aware option selection →
underlying-R sizing/management → existing OMS/risk gates → structured
telemetry and production-path replay comparison.

Final authoritative Strategy A values remain in
`StrategyTunablesConfig`: EMA 20/50, ADX 14/22, ATR 14, EMA separation 0.10
ATR, confluence 0.25 ATR, S/R zone 0.10 ATR, body 0.40, directional close 30%,
maximum confirmation range 1.50 ATR, trigger 0.05 ATR, two bars, chase 0.25
ATR, structural buffer 0.10 ATR, stop range 0.80–1.50 ATR, room 1.50R, T1
1.50R, runner 2.50R, trailing activation 1R, entry 09:45–14:45 and forced
exit 15:15.

LIVE readiness remains `BLOCKED`.  Code/test readiness and underlying replay
readiness are evidenced; option execution validation and paper/shadow
readiness remain observation/data-coverage dependent.  No unrestricted LIVE
Strategy A path was enabled.

## Cleanup ledger

Deleted obsolete derived Strategy A tests/reports: the old fixed trend-pullback
test, the old mixed Strategy A baseline test, the root baseline report, and
legacy confirmation/PUT-depth/frozen-candidate replay artifacts.  Preserved
raw/shared market data, instrument data, option-chain captures, Strategy B,
OMS/risk tests, and generic replay infrastructure.

## Test evidence ledger additions

| Timestamp | Phase | Command | Result |
|---|---:|---|---|
| 2026-09-20 | 1 | `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_contracts.py tests/test_replay_metadata.py` | 20 passed |
| 2026-09-20 | 2–6 | `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_v2.py` | 10 passed |
| 2026-09-20 | 3–7 | `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_v2.py tests/test_strategy_contracts.py tests/test_replay_metadata.py` | 30 passed |
| 2026-09-20 | 4, 7, 8 | `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_simulation.py tests/test_strategy_b_replay_lifecycle.py tests/test_volatility_breakout_fixed.py` | 48 passed, 1 warning |
| 2026-09-20 | all | `python -m compileall -q services libs tests` | passed |
| 2026-09-20 | all | `.venv\Scripts\python.exe -m pytest -q tests` | 179 passed, 24 warnings |
| 2026-09-21 | 9, all | `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_v2.py tests/test_forward_option_execution_validation.py`; `.venv\Scripts\python.exe -m pytest -q tests` | 15 passed; 179 passed, 7 warnings |
| 2026-09-21 | review-fix, all | `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_a_review_fixes.py`; focused matrix; `.venv\Scripts\python.exe -m pytest -q tests`; `.venv\Scripts\python.exe -m compileall -q services libs tests` | 39 passed; 94 passed; 218 passed, 6 warnings; compile passed |

## Final handoff

Phase: 1–10  
Status: `[?] Awaiting human review/approval`  
Implementation commit: `43218b2`  
Acceptance gate: automated implementation evidence recorded; human review required  
Known limitations: intrabar ordering, historical option quote coverage, and paper/shadow observation period  
Human approval: pending

The tracker, repository and automated test evidence together determine progress. No phase is complete merely because code was written.
