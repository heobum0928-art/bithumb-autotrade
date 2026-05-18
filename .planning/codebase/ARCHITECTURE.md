# Architecture

**Analysis Date:** 2026-05-19

## Pattern Overview

**Overall:** Event-driven real-time trading engine with threaded background tracking and state persistence.

**Key Characteristics:**
- Non-blocking single-loop main event processor (avoids blocking on long operations)
- WebSocket-based real-time price streaming (Bithumb API 2.0)
- Multi-threaded background trackers for outcome analysis and pullback detection
- State machine phases for position lifecycle management (entry → trailing → exit)
- Learning system based on accumulated losses and pattern detection

## Layers

**API Layer:**
- Purpose: Abstracts Bithumb REST and WebSocket APIs
- Location: `bithumb/client.py`
- Contains: Authentication (JWT HS256), market data (tickers, candles, orderbook), trading operations (buy/sell/cancel), account balance queries
- Depends on: `requests`, `jwt`, `yaml` (config)
- Used by: Main monitor loop, all trading scripts

**Data Layer:**
- Purpose: Persistent trade history, signal logs, pump detection tracking, daily parameter audits
- Location: `bithumb/db.py`
- Contains: SQLite tables (trades, signal_log, pump_log, daily_params), read/write operations with transaction safety
- Depends on: `sqlite3`
- Used by: Main monitor, outcome tracking, analysis scripts

**Signal Detection & Indicators:**
- Purpose: Real-time price momentum detection and technical indicator calculation
- Location: `bithumb/indicators.py` (RSI, Bollinger Bands, MACD), main loop signal logic
- Contains: RSI (14-period), BB%B (20-period), MACD (12/26/9), volume multiplier checks, BTC correlation filtering
- Depends on: Candle data from API layer
- Used by: Entry decision logic in main monitor

**Notification Layer:**
- Purpose: Telegram alerts (entry, exit, errors, state changes)
- Location: `bithumb/notify.py`
- Contains: Message formatting, quiet hours enforcement, retry logic
- Depends on: `requests`, `config.yaml`
- Used by: Main monitor on trade events

**Main Trading Engine:**
- Purpose: Event loop for position monitoring, signal detection, entry/exit execution
- Location: `scripts/alt_monitor.py`
- Contains: PriceTracker (WebSocket subscription + history buffer), position state machine, signal filtering pipeline, loss learning, pullback queue, outcome tracking queue
- Depends on: All layers above
- Used by: Watchdog process keeps it alive

**Process Manager:**
- Purpose: Daemon supervisor keeping alt_monitor.py and tg_bot.py alive
- Location: `scripts/watchdog.py`
- Contains: PID-based liveness checks, automatic restart with Telegram alerts, session log writing, AI analysis triggering
- Depends on: `psutil`, `subprocess`, Telegram config
- Used by: Manual startup or init system

**Analysis & Reporting:**
- Purpose: Historical performance analysis, signal effectiveness, pump pattern analysis
- Location: `scripts/signal_stats.py`, `scripts/ai_analyze.py`, `scripts/session_writer.py`
- Contains: Win/loss rate calculation, outcome distribution, pump path statistics, session summaries
- Depends on: Database queries
- Used by: Manual review, watchdog triggers

## Data Flow

**Real-time Entry Signal Detection:**

1. WebSocket receives ticker updates for all subscribed coins (price, volume)
2. PriceTracker buffers 60 snapshots (timestamp, price, accumulated_value) per coin
3. Main loop calls `tracker.get_signal(coin)` every 1 second
4. Signal logic checks: price change (+5%+), volume multiplier (5x baseline), RSI (45-75), MACD (bullish), BB%B (≤1.0)
5. Candidate signal logged to `signal_log` table with indicators snapshot
6. If all filters pass → entry attempt via `do_buy()` (market or limit order)
7. Order tracked until completion or 1-minute timeout (auto-cancel if unfilled)

**Position Lifecycle (Phase State Machine):**

1. **Entry Phase (phase=0):** Position acquired, monitoring begins, 30-second confirmation window
2. **Trailing Phase (phase=1):** Price tracking for highest point, trailing stop activated at +1.5% (TP_HALF)
3. **Exit Phase (phase=2):** Position closed via `do_sell()`, reason recorded (익절/손절/시간초과)

**Background Outcome Tracking:**

1. Signal logged with entry price/time → queued to `_outcome_queue`
2. Outcome tracker thread monitors 5-minute and 30-minute post-entry prices
3. Price deltas recorded back to `signal_log` for performance analysis

**Pullback Strategy (Experimental):**

1. Pump detected (price +10%+, volume surge) → queued to `_pullback_queue`
2. Pullback tracker monitors for -7% drop from peak
3. When achieved → entry_ready signal sent to main loop
4. Entry attempt with smaller capital (30,000 KRW) to test pullback bounces
5. Pump path tracked for 5 minutes (peak, drops, bounce patterns) → stored in `pump_log`

**Loss Learning System:**

1. Trade exits with loss → `record_loss_coin()` categorizes by pattern
2. Accumulation: 1 loss = 4h cooldown, 2 losses = 24h, 3+ = 72h
3. Big losses (-5%+) trigger 48h cooldown; pump-dumps trigger 72h
4. Cooldown tracked in `data/loss_coins.json` with expiration timestamps
5. Main loop checks before entry: if coin on cooldown, skip signal

**State Management:**

- **Active Position:** `data/active_pos.json` — current coin, volume, entry price, phase, highest price, trailing stop level
  - Persisted after every trade event for recovery on bot restart
- **Loss Coins:** `data/loss_coins.json` — cooldown registry with expiration times
- **Bot Lock:** `data/bot.lock` — single-instance enforcement via PID file

## Key Abstractions

**PriceTracker:**
- Purpose: Real-time price buffering and signal extraction from WebSocket stream
- Examples: `scripts/alt_monitor.py` lines 429-549
- Pattern: Deque-based circular buffer (MAXLEN=60), thread-safe locking, lookback window sliding to detect momentum

**Position Object:**
- Purpose: Represents active trade state (coin, volume, entry price, timestamps)
- Pattern: Dict with keys: coin, market, volume, entry_price, cost, entered_at, highest, phase, sold_vol, recv_krw, trail
- Persisted to JSON for restart recovery

**Signal Tuple:**
- Purpose: Encapsulates detection result (price change %, volume multiplier, indicator snapshots)
- Pattern: Dict with keys: price_chg, vol_mult, rsi, bb_pct, macd_bull, entry_type ("신호감지" or "눌림목")

**Outcome Tracker Queue:**
- Purpose: Deferred tracking of entry signal effectiveness post-execution
- Pattern: Background thread drains `_outcome_queue` items, sleeps 10s, updates signal_log with price deltas at T+5m and T+30m

## Entry Points

**alt_monitor.py:**
- Location: `scripts/alt_monitor.py`
- Triggers: Manual `python scripts/alt_monitor.py` or from `scripts/watchdog.py`
- Responsibilities:
  - Initialize DB, WebSocket subscription, lock file
  - Load active position (if bot restarted mid-trade)
  - Load loss coin cooldowns
  - Start background threads: outcome_tracker, pump_tracker, pullback_tracker
  - Main loop: every 1 second, check position monitoring + signal detection
  - Persist position state after every action

**watchdog.py:**
- Location: `scripts/watchdog.py`
- Triggers: Manual startup (e.g., cron, system init) or operator command
- Responsibilities:
  - Monitor alt_monitor.py and tg_bot.py PIDs every 30 seconds
  - Auto-restart either if dead with Telegram alert
  - Trigger session_writer.py daily at configured time (KST)
  - Trigger ai_analyze.py daily if not already run

**tg_bot.py:**
- Location: `scripts/tg_bot.py` (not fully shown, but integrated)
- Triggers: Started by watchdog
- Responsibilities: Telegram command listener (manual entry/exit, parameter tweaks, balance query)

## Error Handling

**Strategy:** Graceful degradation with logging and Telegram alerts.

**Patterns:**

1. **Order Execution Errors:**
   - Try market buy, if no UUID log error → skip that signal
   - Limit buy fails → log and fall back to market or skip
   - Unfilled order after timeout → auto-cancel, log, continue
   - Retry on sell: up to 5 attempts every 10 seconds before giving up

2. **API Errors:**
   - `get_ticker()`, `get_markets()` → RuntimeError on status != "0000"
   - Indicator calculation (`calc_rsi`, `calc_bb_pct`) → return None if data insufficient
   - WebSocket disconnection → auto-reconnect every 5 seconds

3. **Database Errors:**
   - Transactions rolled back automatically (SQLite)
   - Failed inserts logged but don't halt main loop
   - Missing columns during migration caught with try/except

4. **Position Recovery:**
   - On bot startup: load `data/active_pos.json`
   - If valid, resume monitoring from saved state
   - If corrupted/missing, start fresh with no position

## Cross-Cutting Concerns

**Logging:**
- Framework: Python `logging` module
- Format: `"%(asctime)s [ALT][%(levelname)s] %(message)s"` (ISO timestamp, tag, level)
- Sinks: stdout + `logs/alt_monitor.log` (rotating not configured, manual cleanup expected)
- Levels: DEBUG (signal details), INFO (trade events, entry/exit), WARNING (failures, anomalies), ERROR (critical operations)

**Validation:**

- **Entry Filters (order matters):**
  1. Coin on loss cooldown? Skip.
  2. BTC down >1.5%? Skip (macro filter).
  3. Signal exist (price, volume, time)? If not, skip.
  4. Indicators (RSI, MACD, BB%B)? Log reason if filtered.
  5. Order book (bid/ask ratio)? Skip if >1.5 imbalance.
  6. Volume power (tick buy %)? Skip if <60%.

- **Exit Conditions:**
  1. Price hit TP_HALF (+1.5%)? Sell half, enable tight trail.
  2. Price hit initial stop (-1.5%)? Cancel (if <10 min old).
  3. Time > HOLD_MIN_SEC (600s)? Activate trailing stop.
  4. Trailing stop breach? Sell remaining.

**Authentication:**
- Method: JWT HS256 (API 2.0 private endpoints)
- Implementation: `BithumbClient._make_token()` encodes nonce + timestamp + optional query_hash
- Config: API key/secret read from `config.yaml` (external, not committed)

**Concurrency:**
- WebSocket updates via background thread write to `PriceTracker._hist` (protected by `_lock`)
- Main loop reads lock-protected snapshots for signal detection
- Outcome/pump/pullback trackers are separate daemon threads, non-blocking
- No explicit thread coordination beyond queue.Queue() for inter-thread communication
