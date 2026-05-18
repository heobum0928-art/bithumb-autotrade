# External Integrations

**Analysis Date:** 2026-05-19

## APIs & External Services

**Bithumb Exchange (South Korean Cryptocurrency Exchange):**
- Trading API for market order execution and balance queries
  - SDK/Client: Custom REST client in `bithumb/client.py`
  - Auth: HMAC-SHA512 + JWT (API 2.0)
  - Env vars: `config.yaml` → `bithumb.api_key`, `bithumb.api_secret`
  - Base URL: https://api.bithumb.com

**Telegram Messaging Service:**
- Real-time alerts and trading notifications
  - SDK/Client: python-telegram-bot 20.7
  - Auth: Bot token via `config.yaml` → `telegram.bot_token`
  - Chat ID: `config.yaml` → `telegram.chat_id`
  - API Endpoint: https://api.telegram.org/bot{TOKEN}/sendMessage
  - Implementation: `bithumb/notify.py` wraps sendMessage API
  - Quiet hours: Configurable via `telegram.quiet_start` and `telegram.quiet_end`

**Claude AI Analysis (Anthropic):**
- Daily trading data analysis and strategy insights
  - SDK/Client: anthropic Python library (installed separately, not in requirements.txt)
  - Auth: API key via `config.yaml` → `anthropic_api_key`
  - Model: claude-haiku-4-5-20251001
  - Usage: `scripts/ai_analyze.py` - analyzes trades, signals, pump logs
  - Input: JSON from SQLite (trades, signal logs, pump tracking)

## Data Storage

**Databases:**
- SQLite (file-based)
  - Path: `data/trades.db`
  - Client: sqlite3 (standard library)
  - ORM: None (raw SQL via `bithumb/db.py`)
  - Schema: Four main tables
    - `trades` - Completed trades with entry/exit prices and PnL
    - `signal_log` - All signals (executed + filtered out) with indicators
    - `pump_log` - Detected pump events and pullback tracking
    - `daily_params` - Parameter snapshots per trading day

**File Storage:**
- Local filesystem only
  - Position state: `data/active_pos.json` (current entry/exit orders)
  - Loss tracking: `data/loss_coins.json` (blocked coins)
  - Database: `data/trades.db`
  - Logs: `logs/alt_monitor.log`, `logs/watchdog.log`

**Caching:**
- In-memory deques in `alt_monitor.py` for real-time price tracking
- No Redis or external cache

## Authentication & Identity

**Auth Provider:**
- Custom (API-key + HMAC-SHA512)

**Implementations:**
- Bithumb API 2.0: JWT (HS256) generated per-request in `bithumb/client.py._make_token()`
  - Payload: access_key, nonce, timestamp, optional query_hash
  - Signing: HMAC-SHA512 with api_secret
- Telegram: Bot token in Authorization header
- Anthropic: Bearer token in Authorization header

**Secrets Management:**
- config.yaml (should be .gitignored but currently checked in with real keys)
- .env file support via python-dotenv
- No vault/secret manager (credentials in plain config)

## Monitoring & Observability

**Error Tracking:**
- None (local logging only)

**Logs:**
- File-based logging to `logs/` directory
- Format: timestamp [SOURCE][LEVEL] message
- Handlers: StreamHandler (stdout) + FileHandler
- Sources:
  - `alt_monitor.py` → `logs/alt_monitor.log`
  - `watchdog.py` → `logs/watchdog.log`
  - `tg_bot.py` → stdout only
  - All modules prefix with source: [ALT], [TG], [WD], [DB]
- No centralized aggregation

## CI/CD & Deployment

**Hosting:**
- Local Windows machine (Windows 11 Pro 10.0.26200)
- Manual startup via `start_bots.bat`

**CI Pipeline:**
- None detected (no GitHub Actions, no Jenkins, no CI config)

**Process Management:**
- Watchdog (`scripts/watchdog.py`) monitors and restarts main bots
  - Restarts `alt_monitor.py` if it crashes
  - Restarts `tg_bot.py` if it crashes
  - Sends Telegram alert on restart
  - Runs session log writer at midnight UTC

## Environment Configuration

**Required env vars:**
- `bithumb.api_key` - Bithumb API key (in config.yaml)
- `bithumb.api_secret` - Bithumb API secret (in config.yaml)
- `telegram.bot_token` - Telegram bot token (in config.yaml)
- `telegram.chat_id` - Telegram chat ID (in config.yaml)
- `anthropic_api_key` - Claude API key (in config.yaml, used by ai_analyze.py)

**Secrets location:**
- `config.yaml` - Contains all secrets (should use .env instead)
- `.env` - Optional (loaded by python-dotenv but not currently used)
- No .gitignore protection for config.yaml keys currently

## Webhooks & Callbacks

**Incoming:**
- Telegram webhook-like: `tg_bot.py` polls for incoming messages
  - Commands: /status, /trades, /pnl
  - Not a true webhook (uses polling with getUpdates)

**Outgoing:**
- Telegram notifications:
  - Entry signals: notify_buy(), notify_sell()
  - Listing alerts: notify_detected()
  - Daily reports: notify_daily()
  - Error alerts: notify_error()
- No other outgoing webhooks

## Real-Time Data Feeds

**WebSocket Subscriptions:**
- Bithumb ticker WebSocket for real-time price updates
  - Subscription type: "ticker" with symbols and "24H" tickTypes
  - Implementation: `alt_monitor.py` via websocket-client library
  - Purpose: Real-time position monitoring and pullback detection
  - Format: JSON messages with price, volume, timestamp

**REST Polling:**
- Bithumb `GET /public/ticker/ALL` - Full market scan for new listings
  - Interval: `monitor.poll_interval_sec` (typically 2 seconds)
  - No caching (fetches full list each poll)

## API Rate Limits & Constraints

**Bithumb:**
- Public API: 1 call/sec per IP (not enforced in code)
- Private API: Rate limits not explicitly documented but requests batched where possible
- Polling interval: 2 seconds (200 calls/day per market)

**Telegram:**
- 30 messages/sec soft limit (rarely hit)
- Sendable from any source with bot token

**Anthropic:**
- Standard Claude API rate limits (1000 req/day typical)
- Used only by ai_analyze.py (once per day)

---

*Integration audit: 2026-05-19*
