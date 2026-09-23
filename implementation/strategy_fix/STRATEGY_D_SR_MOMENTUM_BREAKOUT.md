# Strategy D — S&R Momentum Breakout

## Status

Strategy D remains research/backtest-only and isolated from the production
Strategy A, Strategy B, and Strategy C scheduler. The original V1 rules are
preserved as a control. V2 is a frozen candidate derived from the first Breeze
V1 review and must be rerun before paper orchestration is enabled.

Control ID:

`STRATEGY_D_SR_MOMENTUM_BREAKOUT_V1`

Current candidate ID:

`STRATEGY_D_SR_MOMENTUM_BREAKOUT_V2_CANDIDATE`

## V2 amendments

V2 deliberately changes only entry quality. Risk and lifecycle parameters are
left unchanged so the comparison isolates the effect of the filters.

1. **RSI clearance**
   - CE still requires the completed 5m RSI(14) to cross 60, but the trigger
     candle must finish strictly above 62.
   - PE still requires the completed 5m RSI(14) to cross 40, but the trigger
     candle must finish strictly below 38.
   - This is represented as a minimum clearance of more than 2 RSI points.

2. **Previous-day range regime**
   - Previous-day range = PDH - PDL.
   - A new entry is allowed only when
     `previous-day range / current 5m ATR < 8.0`.
   - The ATR is the same completed 5m ATR(14) snapshot used for initial risk.

These two filters were chosen after reviewing the V1 Breeze sample. They are
therefore in-sample research hypotheses, not production-approved thresholds.

The V1 review also found weak R1 performance and a weak 12:00 IST hour, but
those effects were less stable across calendar splits. V2 keeps both as
diagnostics rather than hard filters.

## Data contract

Strategy D uses completed real-market candles only (`BREEZE`, `KITE`, or
`LIVE`).

- NIFTY spot completed 5m candles: structural breakout, RSI, ATR, EMA9, and
  previous-session levels.
- Active NIFTY futures completed 5m candles: volume-backed session VWAP.
- NIFTY spot native 1m candles: intrabar ordering only. They never create the
  signal and never alter the completed-5m entry rules.

No synthetic price, synthetic option premium, or post-entry information is
used to create a signal.

## Pre-market static levels

For session D, the immediately preceding trading session derives:

- PDH: previous-day high
- PDL: previous-day low
- PDC: previous-day close
- P = (PDH + PDL + PDC) / 3
- R1 = 2P - PDL
- S1 = 2P - PDH
- R2 = P + (PDH - PDL)
- S2 = P - (PDH - PDL)

These levels remain immutable during the current session.

## Entry contract

Signals are evaluated only on completed NIFTY spot 5m candles inside
09:20–14:45 IST.

### Long CE

All conditions must hold:

1. The previous completed spot candle was at/below PDH or R1 and the current
   completed candle closes strictly above it.
2. Active NIFTY futures price is strictly above completed-session VWAP.
3. RSI(14) crosses from at/below 60 and the trigger candle closes strictly
   above 62.
4. RSI is not in the 45–55 trap zone.
5. Previous-day range / current completed 5m ATR(14) is strictly below 8.0.

If both PDH and R1 are crossed in one candle, the higher crossed resistance is
the structural trigger.

### Long PE

All conditions must hold:

1. The previous completed spot candle was at/above PDL or S1 and the current
   completed candle closes strictly below it.
2. Active NIFTY futures price is strictly below completed-session VWAP.
3. RSI(14) crosses from at/above 40 and the trigger candle closes strictly
   below 38.
4. RSI is not in the 45–55 trap zone.
5. Previous-day range / current completed 5m ATR(14) is strictly below 8.0.

If both PDL and S1 are crossed in one candle, the lower crossed support is the
structural trigger.

## Risk and lifecycle

The completed 5m spot ATR(14) sets structural risk:

- CE stop: entry - 1.5 × ATR
- PE stop: entry + 1.5 × ATR

At +1.5R the research lifecycle realizes 50% and moves the remaining stop to
entry. Runtime option sizing continues to reuse the existing PositionManager
and reads the selected contract's actual `lot_size`; 65 is not hard-coded.

Whole-lot execution is preserved. A one-lot paper/live position cannot be
literally halved.

After T1, the runner exits on the first applicable condition:

- breakeven protective stop;
- completed 5m spot close across EMA9;
- R2 for CE or S2 for PE when that pivot lies beyond +1.5R;
- session force exit at 15:20 IST.

### Intrabar ordering

If all five native NIFTY spot 1m children exist for a 5m lifecycle candle, V2
uses them to order:

- ATR stop vs +1.5R scale-out;
- post-scale breakeven;
- R2/S2.

A protective stop wins unresolved same-minute ambiguity. If a complete five
minute child set is unavailable, the replay falls back to conservative 5m
ordering and labels activation-bar ambiguity explicitly.

EMA9 remains a completed-5m exit and is never evaluated intrabar.

## Backtest

Run the same command:

```bash
python -m services.historical.strategy_d_sr_momentum_backtest --source BREEZE
```

Optional date bounds:

```bash
python -m services.historical.strategy_d_sr_momentum_backtest \
  --source BREEZE \
  --start-date 2026-06-01 \
  --end-date 2026-09-22
```

Default output:

`data/strategy_d_sr_momentum_breakout_v2_backtest.json`

The root report is V2. It also contains
`comparison_to_corrected_v1`, generated from the same candle set and the same
1m-aware lifecycle engine, so V1/V2 differences are attributable to the V2
entry filters rather than different replay mechanics.

The report also includes segmented metrics by year, entry hour, breakout level,
and direction, plus 1m coverage.

Historical option bid/ask is still not fabricated. Contract selection, option
fills, slippage, transaction costs, and executable option P&L remain the paper
validation layer.

The backtest never writes market data, never calls a broker, and does not alter
Strategy A, Strategy B, Strategy C, production thresholds, or runtime state.
