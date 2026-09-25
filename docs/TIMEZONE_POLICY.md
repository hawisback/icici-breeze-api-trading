# Timezone policy

The trading platform has one exchange/business timezone: **Asia/Kolkata (IST)**.

## Rule

Use IST for anything whose meaning depends on the Indian market calendar or
exchange wall clock:

- trading/session date
- NSE/NFO session windows and candle boundaries
- expiry filtering and active futures selection
- forced exits and entry windows
- broker request wall-clock timestamps
- interpretation of naive Kite/Breeze exchange timestamps
- operator-facing trading timestamps in the UI and logs

Use timezone-aware UTC internally for things whose meaning is an instant rather
than a market-calendar label:

- database persistence timestamps
- audit/event timestamps
- token/challenge expiries
- quote/candle age calculations
- cross-service ordering and reconciliation

Those UTC timestamps must be converted to IST before applying a trading-date or
session-time rule, and the UI must render them with `timeZone: "Asia/Kolkata"`.

## Canonical helpers

Backend code imports `IST`, `ist_today`, `market_date`, and conversion
helpers from `libs.market_time`.

Frontend code imports IST format/session helpers from
`frontend/trading-ui/lib/time.ts`.

Do not use host-local `date.today()`, naive `datetime.now()`, hand-built
`UTC+05:30` objects, or browser-local `toLocaleTimeString()` in trading
paths. CI enforces these rules for runtime trading modules.
