# Strategy D — S&R Momentum Breakout V1

## Status

Research/backtest implementation only. Strategy D is intentionally isolated
from the production Strategy A, Strategy B, and Strategy C scheduler until its
Breeze historical results are reviewed.

Strategy ID: `STRATEGY_D_SR_MOMENTUM_BREAKOUT_V1`

## Data contract

Strategy D uses completed real-market candles only (`BREEZE`, `KITE`, or
`LIVE`). NIFTY spot 5-minute candles are authoritative for the structural
breakout, RSI, ATR, EMA9, and previous-session levels. The active NIFTY futures
contract supplies a volume-backed 5-minute session VWAP confirmation.

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

These levels are immutable during the current session and are emitted in the
backtest report for later chart overlays.

## Entry contract

Signals are evaluated only on completed NIFTY spot 5-minute candles inside
09:20–14:45 IST.

### Long CE

All conditions must hold:

1. The previous completed spot candle was at/below PDH or R1 and the current
   completed candle closes strictly above it.
2. Active NIFTY futures price is strictly above its completed-session VWAP.
3. RSI(14) crosses from at/below 60 to strictly above 60.
4. RSI is not in the 45–55 trap zone.

If both PDH and R1 are crossed in one candle, the higher crossed resistance is
the structural trigger.

### Long PE

All conditions must hold:

1. The previous completed spot candle was at/above PDL or S1 and the current
   completed candle closes strictly below it.
2. Active NIFTY futures price is strictly below its completed-session VWAP.
3. RSI(14) crosses from at/above 40 to strictly below 40.
4. RSI is not in the 45–55 trap zone.

If both PDL and S1 are crossed in one candle, the lower crossed support is the
structural trigger.

The crossover requirement prevents repeated entries simply because price
remains above or below a broken level.

## Risk and lifecycle

The completed 5-minute spot ATR(14) sets initial structural risk:

- CE stop: entry - 1.5 × ATR
- PE stop: entry + 1.5 × ATR

The PE stop is deliberately symmetric: a bearish thesis is invalidated by an
upward move in the underlying.

At +1.5R, the research lifecycle realizes 50% and moves the remaining stop to
breakeven. Runtime option sizing reuses the existing PositionManager and reads
the selected contract's actual `lot_size`; 65 is not hard-coded.

Whole-lot execution is preserved. A one-lot live/paper position therefore
cannot literally be halved.

After T1, the runner exits on the first applicable condition:

- breakeven protective stop;
- completed 5-minute spot close across EMA9;
- R2 for CE or S2 for PE, when that pivot lies beyond +1.5R;
- session force exit at 15:20 IST.

With only 5-minute OHLC, a bar containing both a protective stop and favorable
target is treated conservatively as stop-first. A later 1-minute execution
layer can refine intrabar ordering without changing Strategy D signals.

## Backtest

Run against the read-only historical database:

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

`data/strategy_d_sr_momentum_breakout_backtest.json`

V1 reports signal quality and lifecycle in underlying R. It does not fabricate
historical option bid/ask. Option selection, option fills, slippage, transaction
costs, and executable option P&L are intentionally the next validation layer.

The backtest never writes market data, never calls a broker, and does not alter
Strategy A, Strategy B, Strategy C, production thresholds, or runtime state.
