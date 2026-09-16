# ICICI Direct Options Trading Platform — Microservices Architecture v2
## Latest Stable Python + Modern React UI + SQLite-First Storage

> **Status:** Revised architecture  
> **Supersedes:** Previous microservices architecture document  
> **Technology baseline verified:** 16 September 2026  
> **Primary purpose:** Build a scalable options-trading platform around ICICI Direct Breeze APIs with a modern web UI, durable trading storage, paper/live execution, charts, and future automated strategies.

---

# 1. Important Architecture Decision

We will continue with an **event-driven microservices architecture**, but use **SQLite as the initial persistent database technology**.

There is one important technical constraint:

> **SQLite is excellent for reliable local transactional storage, but a normal SQLite file is not a horizontally distributed multi-node database.**

Therefore we must not design the platform as though multiple Kubernetes pods on different machines can concurrently write to the same `.db` file.

The correct architecture is:

```text
Each state-owning microservice
        ↓
owns its own SQLite database file
        ↓
never shares the database file with another service
```

Example:

```text
OMS Service          → oms.db
Portfolio Service    → portfolio.db
Risk Service         → risk.db
Strategy Service     → strategy.db
Instrument Service   → instruments.db
Session Service      → broker_session.db
Audit Service        → audit.db
```

This keeps service boundaries clean and makes migration to a distributed SQL database straightforward later if the scale requires it.

For large-scale horizontal multi-node deployment, we can later replace a service's SQLite repository with PostgreSQL/libSQL or another server database **without changing the business/service contracts**.

---

# 2. Latest Stable Technology Baseline

Do not use pre-release versions in the live trading stack.

## Backend

Use:

```text
Python                 3.14.x latest stable patch
FastAPI                latest stable 0.141.x+
Pydantic               latest stable 2.13.x+
SQLAlchemy             2.1.x+
Alembic                latest stable
aiosqlite              latest stable
Breeze SDK             latest compatible official release
httpx                  latest stable
WebSockets             native async / FastAPI
uv                     Python project/dependency manager
Ruff                   formatter + linter
Pyright                static type checking
pytest                 testing
pytest-asyncio          async testing
```

At the time of this architecture revision:

```text
Python stable line      3.14
Python 3.15             release candidate — DO NOT use in production yet
FastAPI                 0.141.x stable
Pydantic                2.13.x stable
SQLAlchemy              2.1 documentation/current line
```

## Python Runtime Policy

Use standard CPython.

Do **not** initially use:

```text
Python prereleases
free-threaded Python builds
experimental interpreter modes
```

Trading reliability is more important than adopting experimental runtime features.

Upgrade policy:

```text
patch versions     → automatically tested and routinely adopted
minor versions     → compatibility tested before adoption
major/framework    → explicit migration
prerelease         → never used in LIVE
```

---

# 3. Frontend / UI Technology Baseline

Use a modern React stack.

```text
Next.js                    16.3+
React                      19.3+
TypeScript                 latest stable
Node.js                    24 LTS
Tailwind CSS               4.3+
shadcn/ui                  latest
TanStack Query             v5
TanStack Table             latest stable
Zustand                    latest stable
TradingView
Lightweight Charts         5.2+
React Hook Form            latest stable
Zod                        latest stable
Lucide React               latest stable
```

## Why Node 24 LTS instead of Node 26 Current

For the production UI build/runtime:

```text
Node 24 LTS
```

is preferred over a newer non-LTS/current release.

The platform should use the **latest stable production release**, not simply the newest experimental/current channel.

---

# 4. UI Architecture

Use:

```text
Next.js App Router
React Server Components for shell/static data where useful
Client Components for live trading views
TanStack Query for server/API state
Zustand for transient trading UI state
WebSocket for real-time updates
Tailwind + shadcn/ui for UI system
Lightweight Charts for financial charts
```

Architecture:

```text
Next.js 16 UI
     │
     ├── Server-rendered shell
     │
     ├── Dashboard
     ├── Trading Workspace
     ├── Option Chain
     ├── Orders
     ├── Positions
     ├── P&L
     ├── Strategies
     └── System Health
             │
             ▼
        API Gateway
             │
       REST + WebSocket
```

Do not expose internal microservices directly to the browser.

---

# 5. Recommended UI Experience

The UI should look and behave like a professional trading terminal rather than a generic admin dashboard.

## Global Header

Display continuously:

```text
Broker Session       CONNECTED / EXPIRED
Market Feed          LIVE / STALE / DOWN
Order Feed           LIVE / DOWN
Trading Mode         PAPER / SHADOW / LIVE
Safety Mode          NORMAL / ENTRY BLOCKED / EXIT ONLY / HALTED
NIFTY                current value
Day P&L              current P&L
Open Positions       count
API Health           status
```

## Main Trading Workspace

Recommended layout:

```text
┌─────────────────────────────────────────────────────────────────┐
│ Global Broker / Market / Risk Status                            │
├───────────────┬───────────────────────────┬─────────────────────┤
│ Market Watch  │                           │ Option Chain        │
│               │       Main Chart          │                     │
│ NIFTY         │                           │ CALL / PUT          │
│ BANKNIFTY     │                           │                     │
│ Contracts     │                           │                     │
├───────────────┴───────────────────────────┴─────────────────────┤
│ Positions | Orders | Trades | Strategy Events | Logs            │
└─────────────────────────────────────────────────────────────────┘
```

---

# 6. Financial Charts

Use:

```text
TradingView Lightweight Charts 5.2+
```

Initial capabilities:

```text
Candlestick chart
Volume
1m
5m
15m
30m
1D
Crosshair
Live candle updates
Historical loading
Trade entry markers
Trade exit markers
Order markers
Stop-loss line
Entry-price line
Current-price line
Position average-price line
```

Future indicators:

```text
Heikin Ashi
Supertrend
EMA
VWAP
RSI
MACD
ATR
Bollinger Bands
Option-specific overlays
Strategy signals
```

Indicator calculations used by strategies must live in shared backend strategy/indicator packages, not only in JavaScript.

---

# 7. High-Level Microservices Architecture

```text
                          ┌──────────────────┐
                          │ Next.js 16 UI    │
                          │ React 19         │
                          └────────┬─────────┘
                                   │
                           REST + WebSocket
                                   │
                          ┌────────▼─────────┐
                          │ API Gateway/BFF  │
                          └────────┬─────────┘
                                   │
 ┌─────────────────────────────────┼──────────────────────────────────┐
 │                                 │                                  │
 ▼                                 ▼                                  ▼
Broker Session                Market Services                    Trading Services
Service                       ───────────────                    ────────────────
                              Instrument Service                OMS
Broker Gateway                Market Data Service               Risk
                              Historical Service                Execution
                              Option Chain Service              Portfolio/P&L
                                                                Strategy
                                                                Reconciliation
                                                                Audit
                                   │
                                   ▼
                           Kafka / Redpanda
                                   │
                   ┌───────────────┼───────────────┐
                   ▼               ▼               ▼
                SQLite          Redis         Market Data
             service DBs      live/cache      SQLite files
```

---

# 8. Core Architectural Rule

Strategies never call ICICI Direct APIs.

Correct flow:

```text
Strategy / UI
     ↓
Order Intent
     ↓
Risk Service
     ↓
Execution Service
     ↓
Broker Gateway
     ↓
ICICI Direct Breeze
```

No exception.

---

# 9. Microservices

Initial service set:

```text
api-gateway
broker-session-service
broker-gateway-service
instrument-service
market-data-service
historical-service
option-chain-service
oms-service
portfolio-service
risk-service
execution-service
reconciliation-service
strategy-service
audit-service
notification-service
```

Do not implement all services at once.

Recommended implementation order is defined later in this document.

---

# 10. API Gateway

Technology:

```text
Python 3.14
FastAPI
Pydantic
WebSockets
```

Responsibilities:

```text
UI authentication
REST API
WebSocket gateway
request routing
response aggregation
API versioning
rate limiting
system status
```

External API:

```text
/api/v1/session
/api/v1/account
/api/v1/instruments
/api/v1/market
/api/v1/options
/api/v1/orders
/api/v1/trades
/api/v1/positions
/api/v1/risk
/api/v1/strategies
/api/v1/system
```

Live UI:

```text
/ws/live
```

One application-level WebSocket can multiplex:

```text
quotes
candles
orders
positions
P&L
alerts
strategy state
system state
```

---

# 11. Broker Gateway

The Broker Gateway is the only service that knows ICICI Breeze-specific request/response formats.

Responsibilities:

```text
ICICI Breeze SDK integration
REST calls
market WebSocket
order WebSocket
payload normalization
error normalization
broker API throttling
broker health
```

Future adapters:

```text
BrokerAdapter
├── IciciBreezeAdapter
├── ZerodhaAdapter
├── PaperAdapter
└── FutureBrokerAdapter
```

Internal services always use broker-independent models.

---

# 12. Broker Session Service

SQLite:

```text
data/broker-session/broker_session.db
```

Stores:

```text
broker account metadata
session lifecycle
session activation timestamps
expected expiry
health history
```

Do not store unencrypted broker secrets.

Session runtime credentials should preferably remain in memory or encrypted storage.

---

# 13. Instrument Service

SQLite:

```text
data/instruments/instruments.db
```

Tables:

```text
instruments
instrument_versions
broker_mappings
expiry_calendar
underlyings
```

Instrument model:

```text
instrument_id
broker
exchange
segment
underlying
stock_code
expiry
strike
option_right
lot_size
tick_size
broker_token
tradable
valid_from
valid_to
```

Never hardcode:

```text
lot sizes
expiries
contract tokens
option script names
```

---

# 14. Market Data Service

Responsibilities:

```text
ICICI market WebSocket
subscription manager
quote normalization
OHLC normalization
feed freshness
candle construction
event publication
live quote cache
```

Redis:

```text
latest quote
latest candle
feed health
subscriptions
```

Durable market storage:

```text
SQLite
```

Do not write every raw market tick to the same transactional trading database.

---

# 15. Market Data SQLite Layout

High-frequency market data should be separated from trading/order databases.

Recommended directory:

```text
data/
└── market/
    ├── 2026/
    │   ├── 09/
    │   │   ├── market_2026_09.db
    │   │   └── ...
```

Tables:

```text
candles_1m
candles_5m
candles_15m
market_data_gaps
```

Initially persist:

```text
underlying candles
actively watched instruments
actively traded option contracts
```

Do not persist every tick for the full derivatives universe unless a strategy requires it.

If raw ticks are later required at massive scale:

```text
compressed files / Parquet archive
```

can complement SQLite without changing trading-state storage.

---

# 16. Historical Service

Responsibilities:

```text
ICICI historical API
pagination
gap detection
backfill
candle normalization
query API
```

Storage remains SQLite.

Historical and live candles use the same schema:

```text
instrument_id
interval
start_time
end_time
open
high
low
close
volume
open_interest
source
```

Unique constraint:

```text
(instrument_id, interval, start_time)
```

---

# 17. Option Chain Service

The Option Chain Service should primarily be read/computation-oriented.

It combines:

```text
Instrument Service
Latest quote cache
ICICI option-chain data where needed
```

Output:

```text
CALL                  STRIKE                 PUT
OI
Volume
Bid
Ask
LTP
IV later
Greeks later
```

Short-lived option-chain results may be cached in Redis.

---

# 18. OMS — Order Management Service

SQLite:

```text
data/oms/oms.db
```

The OMS database is one of the most important files in the platform.

Tables:

```text
order_intents
broker_orders
order_events
order_links
reconciliation_records
outbox_events
processed_events
```

State machine:

```text
CREATED
VALIDATING
RISK_REJECTED
APPROVED
SUBMITTING
SUBMISSION_UNKNOWN
ACKNOWLEDGED
OPEN
PARTIALLY_FILLED
FILLED
CANCELLED
REJECTED
EXPIRED
FAILED_SAFE
```

Every state transition is persisted.

---

# 19. Portfolio / P&L Service

SQLite:

```text
data/portfolio/portfolio.db
```

Tables:

```text
executions
positions
position_events
position_snapshots
pnl_snapshots
account_snapshots
fund_snapshots
```

The UI should display:

```text
realized P&L
unrealized P&L
day P&L
position average
LTP
quantity
exposure
```

Live values can be held in Redis, while durable snapshots remain in SQLite.

---

# 20. Risk Service

SQLite:

```text
data/risk/risk.db
```

Tables:

```text
risk_profiles
risk_rules
risk_decisions
system_modes
kill_switch_events
outbox_events
processed_events
```

Risk checks:

```text
broker session
market feed freshness
system mode
kill switch
trading time
instrument validity
expiry
lot size
tick size
position size
order quantity
daily loss
daily trade count
open-order count
premium exposure
funds
price sanity
duplicate order
```

Every risk decision must be stored.

---

# 21. Strategy Service

SQLite:

```text
data/strategy/strategy.db
```

Tables:

```text
strategy_definitions
strategy_versions
strategy_instances
strategy_parameters
strategy_runs
strategy_state
signals
strategy_events
```

Modes:

```text
SHADOW
PAPER
LIVE
```

A strategy produces:

```text
Signal
     ↓
OrderIntent
```

It cannot submit orders.

---

# 22. Audit Service

SQLite:

```text
data/audit/audit.db
```

Audit should be append-only from application logic.

Store:

```text
SESSION_ACTIVATED
SESSION_EXPIRED
SYSTEM_MODE_CHANGED
KILL_SWITCH_ENABLED
RISK_RULE_CHANGED
ORDER_INTENT_CREATED
RISK_APPROVED
RISK_REJECTED
ORDER_SUBMITTED
ORDER_ACKNOWLEDGED
ORDER_FILLED
ORDER_CANCELLED
POSITION_CHANGED
STRATEGY_STARTED
STRATEGY_STOPPED
RECONCILIATION_MISMATCH
```

---

# 23. SQLite Configuration

Every writable SQLite database should be initialized explicitly.

Recommended baseline:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

For **trading-critical databases**:

```text
oms.db
portfolio.db
risk.db
broker_session.db
```

prefer stronger durability settings.

Recommended:

```sql
PRAGMA synchronous = FULL;
```

For less critical/rebuildable market-data stores, benchmark:

```sql
PRAGMA synchronous = NORMAL;
```

Do not change durability settings globally without testing.

---

# 24. SQLite WAL Constraint

WAL improves local read/write concurrency:

```text
readers can continue while a writer writes
```

but normal SQLite WAL requires all processes accessing that database file to be on the **same host**.

Therefore:

```text
DO:
OMS service → owns local oms.db

DO NOT:
OMS pod A ─┐
           ├── shared network filesystem/oms.db
OMS pod B ─┘
```

That would be a bad production architecture.

---

# 25. SQLite Service Scaling Model

Services fall into two classes.

## Stateless / Horizontally Scalable

Examples:

```text
API Gateway
Option Chain calculation workers
some strategy compute workers
notification workers
market event processors
```

These can scale to multiple replicas.

## SQLite State Owners

Examples:

```text
OMS
Portfolio
Risk
Session
Strategy state
Audit writer
```

Initially run:

```text
one active writer per service database
```

Other compute workers communicate through APIs/events.

This is a deliberate trade-off to retain SQLite.

---

# 26. Future Database Migration Boundary

Every service must access persistence through a repository interface.

Example:

```python
class OrderRepository(Protocol):
    async def create_intent(...): ...
    async def save_order(...): ...
    async def append_event(...): ...
    async def get_order(...): ...
```

Implementation:

```text
SQLiteOrderRepository
```

Future:

```text
PostgresOrderRepository
libSQLOrderRepository
```

Business logic must not contain SQLite-specific SQL.

This makes SQLite a deployment/storage choice rather than a permanent architectural constraint.

---

# 27. SQLAlchemy + SQLite

Use:

```text
SQLAlchemy 2.1+
aiosqlite
Alembic
```

Connection:

```text
sqlite+aiosqlite:///data/oms/oms.db
```

Important:

`aiosqlite` gives an asyncio-compatible interface but SQLite itself remains file-based and is not a network-async database.

Do not mistake async syntax for unlimited database write concurrency.

---

# 28. IDs

Never use SQLite auto-increment integers as externally meaningful distributed identifiers.

Use:

```text
UUIDv7
```

for:

```text
event_id
intent_id
order_id
execution_id
position_id
strategy_instance_id
correlation_id
```

Advantages:

```text
globally unique
time sortable
safe across services
```

SQLite may still use internal integer row IDs where useful.

---

# 29. Date/Time

Store timestamps in:

```text
UTC
```

Use ISO 8601 strings or integer microseconds consistently.

UI conversion:

```text
Asia/Kolkata
```

Never use naive Python datetimes.

---

# 30. Event Backbone

Use:

```text
Redpanda / Kafka-compatible event streaming
```

Core topics:

```text
market.quote.v1
market.candle.v1

strategy.signal.v1
order.intent.v1

risk.decision.v1

execution.command.v1
execution.result.v1

broker.order.event.v1
broker.trade.event.v1

order.state.v1

portfolio.position.v1
portfolio.pnl.v1

system.state.v1
audit.event.v1
notification.event.v1
```

Kafka/Redpanda carries messages.

SQLite stores service state.

Redis stores ephemeral/live state.

They serve different purposes.

---

# 31. Transactional Outbox

SQLite works well with the transactional outbox pattern.

Example:

```text
BEGIN

INSERT INTO broker_orders ...
INSERT INTO outbox_events ...

COMMIT
```

A background publisher reads:

```text
outbox_events
```

and publishes to Redpanda.

Then marks the event published.

This avoids:

```text
database write succeeds
but event publication is lost
```

---

# 32. Consumer Inbox / Deduplication

Every critical consumer keeps:

```text
processed_events
```

with:

```text
event_id
consumer_name
processed_at
```

Kafka delivery should be assumed to be:

```text
at least once
```

Therefore all handlers must be idempotent.

---

# 33. Redis Usage

Redis is not the database of record.

Use Redis for:

```text
latest quote
latest candle
live P&L
market-feed health
broker health
distributed locks
temporary subscriptions
rate-limit counters
short-lived caches
UI fanout state
```

Never rely on Redis alone for:

```text
orders
executions
risk decisions
positions
trading history
audit events
```

---

# 34. Order Execution Pipeline

```text
Strategy / Manual UI
        ↓
Order Intent
        ↓
OMS persists intent
        ↓
Risk Service
        ↓
Risk decision persisted
        ↓
Approved event
        ↓
Execution Service
        ↓
Idempotency check
        ↓
Broker rate limit
        ↓
Broker Gateway
        ↓
ICICI Direct
        ↓
Broker acknowledgement
        ↓
OMS state update
        ↓
Trade / fill event
        ↓
Portfolio update
        ↓
UI
```

---

# 35. Never Blind Retry Orders

If:

```text
place_order()
```

times out, the result is not automatically a failure.

State:

```text
SUBMISSION_UNKNOWN
```

Flow:

```text
timeout
  ↓
SUBMISSION_UNKNOWN
  ↓
broker reconciliation
  ↓
order found?
  ├── YES → attach order and continue lifecycle
  └── NO  → prove safe before resubmission
```

This rule is mandatory for all live trading.

---

# 36. Manual and Automated Trading Use the Same Pipeline

Manual UI:

```text
Order Ticket
   ↓
OrderIntent(source=MANUAL)
```

Automated:

```text
Strategy
   ↓
OrderIntent(source=STRATEGY)
```

Both then go through:

```text
Risk
Execution
Broker Gateway
OMS
Portfolio
```

Manual trades never bypass risk/audit.

---

# 37. Trading Modes

Support:

```text
SHADOW
PAPER
LIVE
```

## SHADOW

```text
live data
real strategy logic
signals stored
no orders
```

## PAPER

```text
live data
real strategy logic
simulated execution
simulated positions
```

## LIVE

```text
real broker orders
real portfolio
all safety systems enabled
```

The default must be:

```text
PAPER
```

never LIVE.

---

# 38. Safety Modes

Use:

```text
NORMAL
ENTRY_BLOCKED
EXIT_ONLY
HALTED
```

Execution Service enforces this server-side.

UI state alone is never trusted.

---

# 39. Kill Switch

Provide separate controls:

```text
BLOCK NEW ENTRIES
CANCEL PENDING ENTRY ORDERS
EXIT ALL POSITIONS
HALT AUTOMATION
```

Each action:

```text
requires confirmation
creates an audit event
has a correlation ID
```

---

# 40. Repository Layout

```text
trading-platform/
│
├── services/
│   ├── api-gateway/
│   ├── broker-session/
│   ├── broker-gateway/
│   ├── instrument/
│   ├── market-data/
│   ├── historical/
│   ├── option-chain/
│   ├── oms/
│   ├── portfolio/
│   ├── risk/
│   ├── execution/
│   ├── reconciliation/
│   ├── strategy/
│   ├── audit/
│   └── notification/
│
├── frontend/
│   └── trading-ui/
│
├── libs/
│   ├── contracts/
│   ├── events/
│   ├── observability/
│   ├── broker-models/
│   └── testing/
│
├── data/
│   ├── broker-session/
│   ├── instruments/
│   ├── oms/
│   ├── portfolio/
│   ├── risk/
│   ├── strategy/
│   ├── audit/
│   └── market/
│
├── infra/
│   ├── docker/
│   ├── kubernetes/
│   ├── helm/
│   └── scripts/
│
├── docs/
│
├── pyproject.toml
├── uv.lock
├── pnpm-workspace.yaml
└── docker-compose.yml
```

---

# 41. Python Monorepo Tooling

Use `uv` workspaces.

Example logical structure:

```toml
[tool.uv.workspace]
members = [
    "services/api-gateway",
    "services/broker-session",
    "services/broker-gateway",
    "services/instrument",
    "services/market-data",
    "services/historical",
    "services/oms",
    "services/portfolio",
    "services/risk",
    "services/execution",
    "services/strategy",
    "libs/contracts",
    "libs/events",
    "libs/observability",
]
```

Use:

```text
uv.lock
```

for reproducible Python dependency resolution.

---

# 42. Python Code Quality

Mandatory CI:

```text
ruff check
ruff format --check
pyright
pytest
```

Use strict typing for trading-critical packages.

Do not use:

```text
Any
```

widely in:

```text
orders
risk
execution
broker mapping
portfolio math
```

Broker payloads are validated at the adapter boundary.

---

# 43. Frontend Repository Tooling

Use:

```text
pnpm
Next.js
TypeScript strict mode
ESLint
Prettier if required
Vitest
React Testing Library
Playwright
```

Example:

```text
frontend/trading-ui/
├── app/
├── components/
├── features/
├── hooks/
├── lib/
├── stores/
├── types/
├── tests/
└── public/
```

---

# 44. UI Feature Structure

```text
features/
├── dashboard/
├── market-watch/
├── charts/
├── option-chain/
├── order-ticket/
├── orders/
├── trades/
├── positions/
├── pnl/
├── risk/
├── strategies/
├── broker-session/
└── system-health/
```

Avoid one giant `components` folder.

---

# 45. UI Design System

Use:

```text
Tailwind CSS 4.3+
shadcn/ui
CSS variables/design tokens
dark mode
responsive grid
keyboard navigation
accessible components
```

Trading terminal default:

```text
dark theme
dense information layout
high contrast status indicators
minimal animation
```

Do not make trading actions depend only on color.

Use:

```text
icon + text + color
```

for important states.

---

# 46. Real-Time UI State

Use:

```text
TanStack Query
```

for:

```text
orders queries
positions queries
history
settings
instruments
option chain requests
```

Use:

```text
Zustand
```

for:

```text
selected symbol
selected expiry
chart interval
workspace layout
order ticket draft
temporary UI selections
```

Use application WebSocket for:

```text
quotes
candles
order updates
position updates
P&L
alerts
system status
```

Do not store every live quote in React Query cache if it creates unnecessary rendering pressure.

---

# 47. SQLite Database Tables — Trading Core

## `oms.db`

```text
order_intents
broker_orders
order_events
order_commands
broker_order_links
reconciliation_records
processed_events
outbox_events
```

## `portfolio.db`

```text
executions
positions
position_events
position_snapshots
pnl_snapshots
account_snapshots
fund_snapshots
processed_events
outbox_events
```

## `risk.db`

```text
risk_profiles
risk_rules
risk_decisions
system_modes
kill_switch_events
processed_events
outbox_events
```

## `strategy.db`

```text
strategy_definitions
strategy_versions
strategy_instances
strategy_parameters
strategy_runs
strategy_state
signals
processed_events
outbox_events
```

## `audit.db`

```text
audit_events
```

---

# 48. SQLite Indexes

Critical indexes:

```text
order_intents(intent_id)
order_intents(created_at)

broker_orders(broker_order_id)
broker_orders(client_order_id)
broker_orders(status)
broker_orders(updated_at)

order_events(order_id, occurred_at)

executions(order_id)
executions(instrument_id, execution_time)

positions(instrument_id)
positions(status)

risk_decisions(intent_id)

strategy_runs(strategy_instance_id, started_at)

audit_events(occurred_at)
audit_events(correlation_id)
```

Do not add indexes blindly.

Measure write/read patterns.

---

# 49. Database Migrations

Use Alembic per database-owning service.

Example:

```text
services/oms/migrations/
services/risk/migrations/
services/portfolio/migrations/
```

A service deploy is responsible only for its own schema.

Never create one global migration project touching every service database.

---

# 50. SQLite Backup Strategy

Trading databases require backups.

Minimum:

```text
scheduled SQLite online backup
timestamped backup files
backup verification
retention policy
```

Example:

```text
data/backups/
├── oms/
├── portfolio/
├── risk/
├── strategy/
└── audit/
```

Never copy a live SQLite database file arbitrarily while assuming the copy is consistent.

Use SQLite-supported backup mechanisms.

---

# 51. Event / Trade Recovery

The system should be reconstructable from:

```text
SQLite state
+
broker reconciliation
+
event stream
```

Broker state remains authoritative for actual live broker orders/executions when discrepancies occur.

Local data must preserve:

```text
why the order happened
risk decision
strategy/manual source
broker acknowledgement
every observed state transition
```

---

# 52. API Versioning

Start:

```text
/api/v1
```

Event contracts:

```text
OrderIntentV1
RiskDecisionV1
BrokerOrderEventV1
ExecutionV1
PositionUpdatedV1
CandleV1
QuoteV1
```

Never change an event schema incompatibly without a version change.

---

# 53. Observability

Backend:

```text
OpenTelemetry
Prometheus
Grafana
Loki
Tempo
```

Every service propagates:

```text
trace_id
correlation_id
event_id
intent_id
order_id
strategy_instance_id
```

One trade should be traceable from:

```text
signal
→ intent
→ risk
→ execution
→ ICICI
→ acknowledgement
→ fill
→ position
→ P&L
```

---

# 54. Docker Development

Development runs using Docker Compose.

```text
frontend
api-gateway
broker-session
broker-gateway
instrument-service
market-data-service
historical-service
oms-service
portfolio-service
risk-service
execution-service
strategy-service

redpanda
redis
```

SQLite files are mounted as persistent volumes.

Example:

```text
./data:/app/data
```

Use separate service subdirectories.

---

# 55. Production Deployment with SQLite

If deployment remains on one powerful server:

```text
Docker Compose / systemd + containers
```

is perfectly reasonable and often simpler than Kubernetes.

A single high-performance machine can still run microservices as separate processes/containers.

Advantages:

```text
simple SQLite locality
fixed public IP
lower operational complexity
low latency between services
easy backups
```

For ICICI integration this can actually be an excellent first production topology.

---

# 56. Kubernetes Warning with SQLite

Do not adopt Kubernetes merely because the codebase uses microservices.

With local SQLite databases, Kubernetes introduces storage/placement complexity.

If Kubernetes is later required:

```text
stateful SQLite-owning services
→ StatefulSet
→ persistent volume
→ one active writer
```

and must remain bound to suitable storage.

If we reach a scale requiring many active replicas of the same database-writing service across nodes, that is the point to migrate that service's repository away from local SQLite.

---

# 57. Recommended Initial Production Topology

For the first production version:

```text
High-performance Linux server
Static public IP
Docker Compose

             ┌───────────────────────┐
Browser ────►│ Next.js UI            │
             ├───────────────────────┤
             │ API Gateway           │
             │ Broker Services       │
             │ Market Services       │
             │ Trading Services      │
             │ Strategy Workers      │
             ├───────────────────────┤
             │ Redpanda              │
             │ Redis                 │
             │ SQLite DB files       │
             └──────────┬────────────┘
                        │
                 Static Public IP
                        │
                        ▼
                    ICICI Direct
```

This gives us:

```text
microservice boundaries
event-driven scalability
simple database model
static broker IP
local SQLite performance
```

without pretending SQLite is a distributed database.

---

# 58. Scaling Path

## Stage 1

```text
one server
microservices
SQLite
Redis
Redpanda
```

## Stage 2

Scale compute-heavy workloads:

```text
more strategy workers
more market processors
more backtest workers
```

while database-owning services remain single writers.

## Stage 3

If a state service becomes bottlenecked:

```text
replace only that service's SQLite repository
```

Example:

```text
OMS:
SQLiteOrderRepository
        ↓
DistributedOrderRepository
```

No strategy/UI/broker contract changes.

---

# 59. Implementation Phases

## Phase 0 — Toolchain + Foundation

Build:

```text
Python 3.14
uv workspace
FastAPI services
React 19 / Next.js 16
Node 24 LTS
TypeScript
Tailwind 4
shadcn/ui
Docker Compose
Redpanda
Redis
SQLite foundation
OpenTelemetry
```

Acceptance:

```text
all services start
all health endpoints work
frontend displays service health
SQLite persistence survives restart
```

---

## Phase 1 — ICICI Authentication

Build:

```text
Broker Gateway
Broker Session Service
Breeze SDK integration
daily session activation
session validation
session health
funds
margins
account information
```

SQLite:

```text
broker_session.db
```

No order writes.

---

## Phase 2 — Instruments

Build:

```text
Instrument Service
contract master
option resolver
expiry resolver
lot size
tick size
broker identifiers
```

SQLite:

```text
instruments.db
```

---

## Phase 3 — Historical Data

Build:

```text
Historical Service
ICICI historical API
pagination
candle normalization
gap detection
SQLite candle storage
```

---

## Phase 4 — Live Market Data

Build:

```text
market WebSocket
subscription manager
Redis latest quote
Redpanda market events
candle builder
SQLite candle persistence
UI live updates
```

---

## Phase 5 — Trading UI

Build:

```text
Dashboard
Trading Workspace
Market Watch
TradingView Lightweight Charts
Option Chain
Session status
API health
```

No live execution yet.

---

## Phase 6 — Read-Only Trading State

Build:

```text
OMS
Portfolio
broker orders
broker trades
broker positions
local normalized state
reconciliation
```

SQLite:

```text
oms.db
portfolio.db
```

---

## Phase 7 — Order Notification Stream

Build:

```text
ICICI order WebSocket
normalized order events
OMS lifecycle
real-time UI order updates
```

---

## Phase 8 — Risk

Build:

```text
Risk Service
OrderIntent
safety modes
kill switch
risk decisions
```

SQLite:

```text
risk.db
```

---

## Phase 9 — Paper Execution

Build:

```text
Paper Broker
paper fills
paper positions
paper P&L
manual order ticket
```

All trades use the same production pipeline except the final broker adapter.

---

## Phase 10 — Live Execution

Build:

```text
Execution Service
limit-order placement
modify
cancel
square-off
idempotency
rate limiting
unknown submission recovery
```

Only after acceptance tests pass.

---

## Phase 11 — Strategies

Build:

```text
strategy framework
strategy workers
strategy state
SHADOW
PAPER
LIVE
strategy dashboard
```

SQLite:

```text
strategy.db
```

Actual trading strategies come after this foundation.

---

# 60. Acceptance Gate Before Strategies

Before implementing real automated options strategies:

```text
[ ] ICICI authentication reliable
[ ] session expiry handled
[ ] instruments reliable
[ ] option contract resolution reliable
[ ] historical data reliable
[ ] live market data reliable
[ ] chart functioning
[ ] option chain functioning
[ ] orders imported/tracked
[ ] trades imported/tracked
[ ] positions imported/tracked
[ ] order WebSocket working
[ ] OMS state machine working
[ ] SQLite WAL configured
[ ] SQLite backups tested
[ ] risk engine active
[ ] kill switch tested
[ ] paper trading tested
[ ] duplicate protection tested
[ ] reconciliation tested
[ ] unknown-order recovery tested
[ ] live execution manually tested
[ ] audit trail working
[ ] service health visible in UI
```

---

# 61. Final Technology Stack

## Python

```text
CPython 3.14.x
uv
FastAPI 0.141.x+
Pydantic 2.13.x+
SQLAlchemy 2.1+
Alembic
aiosqlite
httpx
pytest
Ruff
Pyright
OpenTelemetry
```

## UI

```text
Next.js 16.3+
React 19.3+
TypeScript
Node.js 24 LTS
pnpm
Tailwind CSS 4.3+
shadcn/ui
TanStack Query v5
TanStack Table
Zustand
TradingView Lightweight Charts 5.2+
React Hook Form
Zod
Playwright
Vitest
```

## Storage & Messaging

```text
SQLite                  durable service-owned state
Redis                   live/cache/locks
Redpanda/Kafka           event backbone
SQLite market partitions candle history
```

## Infrastructure

```text
Docker Compose initially
Linux production server
fixed public IP
OpenTelemetry
Prometheus
Grafana
Loki
Tempo
```

---

# 62. Key Architecture Rules

```text
1. Use latest STABLE technology, not prereleases.
2. Python 3.14 is the production baseline until Python 3.15 final is tested.
3. React 19 + Next.js 16 are the UI baseline.
4. SQLite is the initial durable database technology.
5. Each microservice owns its SQLite database.
6. Never share one SQLite file across different hosts.
7. Never let services query another service's SQLite file.
8. Broker-specific code lives only in Broker Gateway.
9. Strategies never call ICICI directly.
10. Execution Service is the only normal live-order writer.
11. Every order goes through Risk.
12. Never blind-retry a broker order.
13. Every business event is idempotent.
14. Use transactional outbox for state + event consistency.
15. Redis is never the durable source of trading truth.
16. Market data storage is separated from trading-state storage.
17. Default mode is PAPER.
18. LIVE requires explicit activation.
19. The UI never enforces safety by itself; backend does.
20. Build repository interfaces so SQLite can be migrated per service if scale requires it.
```

---

# 63. Conclusion

The target platform is:

```text
Modern React trading terminal
        ↓
FastAPI microservices
        ↓
Event-driven trading architecture
        ↓
Service-owned SQLite databases
        ↓
Redis live state + Redpanda event stream
        ↓
ICICI Direct Broker Gateway
```

SQLite will be used deliberately for:

```text
orders
trades
executions
positions
P&L snapshots
risk decisions
strategy state
audit events
instruments
session metadata
historical candles
```

while maintaining a clean persistence abstraction so that individual services can move to a distributed database later if their workload outgrows local SQLite.

This gives us the simplicity and reliability of SQLite today without coupling the trading architecture permanently to SQLite's single-host concurrency model.
