# Codebase Concerns

**Analysis Date:** 2026-05-19

## Tech Debt

**Low Win Rate Strategy:**
- Issue: Current momentum-based strategy shows 31% win rate (16 trades) after filtering out failed pre-entry strategy. Expected threshold to declare strategy viable is 45%+.
- Files: `scripts/alt_monitor.py` (lines 57-112), `docs/STRATEGY.md`
- Impact: Strategy may be fundamentally flawed; continuing to trade risks compounding losses. Daily sessions show -3k to -9k PnL losses.
- Fix approach: Accumulate to 30+ trade samples (currently ~8 trades post-PRE-disable), then declare strategy viable/dead. If still <45% at threshold, pivot to pullback-only or teardown entire entry logic.

**Hard-Coded Critical Parameters:**
- Issue: All trading parameters (volume mult, take profit, stop loss, entry ratios) live as module constants in `scripts/alt_monitor.py` (lines 57-112). Changes require code modification and restart.
- Files: `scripts/alt_monitor.py` (lines 57-112, 82-104)
- Impact: Parameter tuning requires downtime and code redeploy. No A/B testing capability. If a parameter needs emergency adjustment mid-day, requires full bot restart.
- Fix approach: Extract parameters to `config.yaml` with hot-reload capability. Implement parameter versioning in database for audit trail.

**Multiple Thread-Local State Without Synchronization:**
- Issue: Global mutable state (pos, highest, phase, sold_vol, recv_krw, trail) shared across multiple daemon threads with minimal locking. Three threads: outcome_tracker, pullback_monitor, price_tracker.
- Files: `scripts/alt_monitor.py` (lines 122-256, 358-376, 1000-1100)
- Impact: Race conditions on position updates. If price_tracker updates pos while main loop is reading it, inconsistent state possible. No explicit locks protecting active_pos.json reads/writes.
- Fix approach: Introduce threading.RLock() on position state. Use atomic json.dumps() to prevent partial writes.

**Database File Duplication:**
- Issue: Two database files exist: `/c/code/coinbase/data/trades.db` (444K, active) and `/c/code/coinbase/trades.db` (0 bytes, orphaned).
- Files: `trades.db`, `data/trades.db`
- Impact: Possible data loss if wrong db path referenced. Queries may read stale data from wrong location.
- Fix approach: Delete orphaned `/c/code/coinbase/trades.db`. Update all db references to use `Path("data/trades.db")` consistently. Add db path validation on startup.

**Exception Handling Too Broad:**
- Issue: Widespread bare `except Exception:` clauses throughout alt_monitor.py that silently swallow errors. Examples at lines 245, 371, 397, 503, 661, 678, 686.
- Files: `scripts/alt_monitor.py` (40+ occurrences of bare except)
- Impact: Silent failures in indicator calculations, API calls, or order placement. Bugs hide until system crashes from cascading failures.
- Fix approach: Replace bare `except Exception` with specific exception types. Log all exceptions. Add metrics/alerts for exception frequencies.

**Log File Growth Without Rotation:**
- Issue: `/c/code/coinbase/logs/alt_monitor.log` is 30MB after ~8 days of operation. No log rotation configured.
- Files: `logs/alt_monitor.log`, `scripts/alt_monitor.py` (line 52)
- Impact: Disk space will fill. Log queries slow. Crash if disk full during trade execution.
- Fix approach: Implement RotatingFileHandler(maxBytes=100MB, backupCount=5) or TimedRotatingFileHandler(when='midnight', interval=1). Archive old logs.

## Known Bugs

**Pump Dump False Signals - No Pullback Recovery:**
- Symptoms: 5 major losses observed (MOVE -9.7k, RAD -7.5k, YGG -5.1k, ASTR -9.5k). Most trigger pullback filter but fail to recover. 2026-05-18: 100% pullback detection but 0% recovery.
- Files: `scripts/alt_monitor.py` (lines 82-88 PULLBACK parameters), `docs/PERFORMANCE.md` (05-11 to 05-12 trades)
- Trigger: High volume spike (>5x baseline) + price jump +3% within 60s. Bot enters. Market reverses sharply within 2-3 minutes. Pullback below -7% achieved but bounce fails.
- Workaround: Disable pullback entry entirely (set `PULLBACK_ENABLED = False`). Revert to manual entry confirmation only.

**WebSocket Connection Silent Drops:**
- Symptoms: Bot continues logging "[스캔] 458개 코인 추적" repeatedly even if WS connection died. No reconnection attempt detected.
- Files: `scripts/alt_monitor.py` (line 58 WS_URL, lines 496+ websocket setup in PriceTracker class)
- Trigger: Bithumb WS server timeout (>30 min idle) or network blip. Bot keeps main loop running but receives stale prices.
- Impact: All signals based on frozen data. Orders placed on outdated prices. Miss real opportunities.
- Workaround: Restart bot manually or via watchdog. No automatic WS reconnect logic exists.

**Off-by-One Error in Indicator Calculation:**
- Symptoms: RSI/MACD sometimes return None when exactly at boundary (e.g., 34 candles when needing 35). Bollinger Bands silently returns None on flat price (std=0).
- Files: `bithumb/indicators.py` (lines 9-24, 43-63)
- Trigger: Asset with very low trading volume (e.g., stablecoin) where all prices identical. Or API returns fewer candles than expected.
- Impact: Filters skip coins that should be evaluated. Incomplete coverage of tradeable assets.
- Workaround: Lower period requirements. Accept None returns as "insufficient data, skip".

**Daily Loss Cooldown Not Properly Enforced:**
- Symptoms: `loss_coins.json` file may grow unbounded. No cleanup of expired entries until runtime access.
- Files: `scripts/alt_monitor.py` (lines 379-391, 1500-1501), `data/loss_coins.json`
- Trigger: Bot runs many days. loss_coins dict holds expired cooldown records indefinitely in memory.
- Impact: Potential memory bloat. File becomes large JSON blob. Performance degradation on large loss_coins dict.
- Workaround: Manually delete old entries from loss_coins.json. Add periodic cleanup task.

**Multiple Same-Coin Entries in Single Minute:**
- Symptoms: 2026-05-17 session shows 3x BOB entry (09:06), 3x UP entry (09:29), 3x META entry (10:25-10:26). Suggests position not properly cleared between signals.
- Files: `scripts/alt_monitor.py` (lines 1350-1430, position exit logic)
- Trigger: Fast signal generation + slow exit confirmation. Next scan iteration detects same coin again before previous position fully closed.
- Impact: Overexposure to single asset. Multiplied losses.
- Workaround: Add explicit coin-level cooldown after exit. Track exited_at timestamp per coin to prevent re-entry within 60s.

## Security Considerations

**API Credentials in config.yaml (Not Committed But Still Risk):**
- Risk: `config.yaml` contains Bithumb API key/secret in plaintext. One-time leaked → account compromise.
- Files: `config.yaml` (present locally, not in git)
- Current mitigation: `.gitignore` prevents commit. File permissions are 644 (world-readable on shared system).
- Recommendations:
  1. Reduce file permissions to 600 (`chmod 600 config.yaml`)
  2. Implement environment variable fallback (allow `BITHUMB_API_KEY` env var override)
  3. Use OS credential store (keyring on Linux/Mac, DPAPI on Windows)
  4. Rotate API keys every 90 days via Bithumb dashboard
  5. Create read-only API keys with IP whitelist if Bithumb supports

**Telegram Bot Token in Config:**
- Risk: Telegram bot token allows anyone with access to `config.yaml` to control the bot and read chat history.
- Files: `config.yaml` (telegram.bot_token)
- Current mitigation: Not in git, file permissions 644
- Recommendations: Apply same credential hardening as API keys. Rotate quarterly.

**No Input Validation on Order Amounts:**
- Risk: If `get_available_krw()` returns corrupted data or negative value, buy_krw could become negative/huge. No bounds checking before market_buy() call.
- Files: `scripts/alt_monitor.py` (lines 1154-1160, order placement), `bithumb/client.py` (market_buy)
- Current mitigation: Only loose check `if buy_krw >= MIN_KRW`
- Recommendations: Add max order size validation (never >50% portfolio). Assert buy_krw > 0 and < portfolio. Log order size for audit.

## Performance Bottlenecks

**Inefficient Coin List Fetch (O(n) API Call):**
- Problem: `tracker.coins()` in main loop (line 1504) iterates all 458 coins every iteration. No caching of coin list.
- Files: `scripts/alt_monitor.py` (lines 1504-1517)
- Cause: New coin detection requires up-to-date list, but list changed infrequently (<1 new coin/day typically).
- Current capacity: 458 coins, ~1s per full scan. At 10+ signals/min, main loop never pauses.
- Improvement path: Cache coin list in memory, refresh every 5 minutes. Use webhook/polling for new listings instead of repeated API calls.

**WebSocket Buffer Not Limited:**
- Problem: Price updates queued indefinitely in `tracker._data` dict without size caps. Fast market can generate 1000+ updates/min per coin.
- Files: `scripts/alt_monitor.py` (line 441, PriceTracker._data initialization)
- Cause: Each price update appends to deque. No maxlen set on deques.
- Current capacity: Can hold ~50MB of float data before memory warning.
- Improvement path: Set `deque(maxlen=60)` to keep 60-second rolling window. Trim older data.

**Database Queries Not Indexed:**
- Problem: Trades table has 400+ rows but no index on (coin, date). Every trade lookup does full table scan.
- Files: `bithumb/db.py` (CREATE_SQL, no CREATE INDEX statements)
- Cause: Initial MVP didn't expect scale. At 5-10 trades/day, negligible impact. At 30+ trades/day, queries slow.
- Current capacity: Sub-second for <1000 rows. Will degrade at 10k+ trades.
- Improvement path: Add indexes: `CREATE INDEX idx_trades_coin_date ON trades(coin, date)`. Add index on entered_at for time-range queries.

**Session Writer Blocks Main Bot:**
- Problem: `session_writer.py` invoked via subprocess at date boundary (watchdog line 157). If writing takes >2 seconds, main bot loop blocked.
- Files: `scripts/watchdog.py` (lines 157-158), `scripts/session_writer.py`
- Cause: Synchronous subprocess.run() call. No timeout.
- Impact: Risk of missing signals during session write (happens daily at 00:00 KST).
- Improvement path: Run session_writer in background thread/separate cron. Remove from watchdog critical path.

## Fragile Areas

**Position State Serialization (active_pos.json):**
- Files: `scripts/alt_monitor.py` (lines 358-376)
- Why fragile: JSON file written atomically with `json.dumps(data, default=str)`. `default=str` hides type mismatches. If bot crashes mid-trade, partial JSON may corrupt state. No checksum validation.
- Safe modification: Always use write-then-rename pattern. Validate JSON on read. Add schema version field to catch incompatible changes.
- Test coverage: No tests for crash-recovery scenario. No retry logic if file write fails.

**Price Tracker Thread Safety (PriceTracker class):**
- Files: `scripts/alt_monitor.py` (lines 432-496, PriceTracker._lock at line 435)
- Why fragile: Lock exists but only protects `_data` dict access. Main loop reads `highest` and `sold_vol` without locking (lines 1140, 1170). Race condition if price_tracker thread updates during exit logic.
- Safe modification: Wrap all position state access in single RLock. Return snapshot of state instead of individual fields.
- Test coverage: No threading tests. Impossible to detect race without instrumentation.

**Indicator Calculations with Insufficient Candles:**
- Files: `bithumb/indicators.py` (all functions), `scripts/alt_monitor.py` (indicator_snapshot call at line 1661)
- Why fragile: Functions return None silently when data insufficient. Callers don't always check for None (e.g., filter logic at line 1706 assumes rsi is numeric).
- Safe modification: Require minimum candle count before returning any value. Raise ValueError instead of returning None so caller must handle explicitly.
- Test coverage: No unit tests. Edge cases untested (flat prices, missing data, etc.).

**Cooldown Tracking (Loss Coins):**
- Files: `scripts/alt_monitor.py` (lines 379-391, 1500-1501, 1026-1027), `data/loss_coins.json`
- Why fragile: loss_coins dict loaded once at startup. If bot runs >7 days, expired entries accumulate in memory. Savepoint only on loss event, not periodic. File JSON can become very large.
- Safe modification: Load loss_coins, filter expired at startup. Add periodic (hourly) cleanup/save. Store as SQLite table instead of JSON for scalability.
- Test coverage: No edge case tests for expiration logic.

**WebSocket Reconnection Logic Missing:**
- Files: `scripts/alt_monitor.py` (lines 496+, PriceTracker.run method), `bithumb/client.py` (no WS client class)
- Why fragile: websocket-client library doesn't auto-reconnect. If connection drops, bot unaware. No heartbeat or connection status check.
- Safe modification: Implement exponential backoff reconnect (1s, 2s, 4s, 8s, max 60s). Add ConnectionMonitor thread to detect dead connections. Emit alert on reconnect.
- Test coverage: No network fault injection tests.

## Scaling Limits

**Concurrent Position Limit - Single Entry Per Minute:**
- Current capacity: Bot enforces one active position at a time (line 1439-1441: if pos is not None, skip signal processing).
- Limit: Can only take one trade per ~60 seconds (SCAN_SEC=1, but full signal processing takes 10-30 seconds).
- At current rate: 1-2 positions/minute max. 30 trades/day limit (line 74: MAX_DAILY_TRADES).
- Scaling path: Remove single-position constraint. Implement portfolio-level max leverage. Track cumulative exposure.

**API Rate Limits Not Tracked:**
- Current capacity: Client makes unlimited API calls per minute (ticker_all, orderbook, candles each counted separately by Bithumb).
- Limit: Bithumb likely enforces 100-1000 req/min per IP. No backoff or token bucket implemented.
- At scale: If processing 20+ coins simultaneously with 458 coins in watchlist, will hit rate limits.
- Scaling path: Implement token bucket rate limiter. Cache all API responses with TTL. Use batch endpoints where available.

**Database File Size Without Archival:**
- Current capacity: 444K for ~100 trades (4.4KB per trade average). At 30 trades/day, will reach 10MB in 1 year.
- Limit: SQLite file grows indefinitely without VACUUM. At 100MB, queries may slow. At 1GB, file lock contention.
- Scaling path: Implement weekly VACUUM. Archive trades >30 days old to separate DB. Implement data retention policy (keep 2 years, archive older).

**Memory Growth - Unbound Deques and Dicts:**
- Current capacity: 458 coins × 60 price samples (seconds) × 8 bytes float ≈ 220KB. At scale with 5000 coins: 2.2MB.
- Limit: If WS deque never clears and app runs 30+ days, memory bloat possible. No monitoring for memory growth.
- Scaling path: Set deque maxlen. Add memory metrics reporting. Implement garbage collection hints.

## Dependencies at Risk

**requests library without timeout:**
- Risk: Multiple requests.post/get calls lack explicit timeout. Network hang can freeze thread indefinitely.
- Files: `bithumb/client.py` (lines 30, 45, 57, 84, 94, 100, 174, 188), `scripts/watchdog.py` (line 49 has timeout=5), `scripts/tg_bot.py` (no timeout)
- Impact: If Bithumb API hangs, bot thread deadlocks. Watchdog can't restart fast enough.
- Migration plan: Add timeout=10 to all requests calls. Implement backoff+retry logic.

**websocket-client library (no auto-reconnect):**
- Risk: No built-in reconnection. If WS drops, bot silently uses stale data.
- Files: `scripts/alt_monitor.py` (no websocket client visible, but WS_URL at line 58 suggests websocket-client expected)
- Impact: Critical for real-time data. Loss of WS = loss of trading capability.
- Migration plan: Wrap websocket-client with reconnect decorator. Or migrate to websockets library (async/await, better error handling).

**PyYAML without explicit Loader:**
- Risk: `yaml.safe_load()` is safe, but any place using `yaml.load()` is vulnerable to code injection.
- Files: Codebase appears to use safe_load consistently (checked all yaml.* calls).
- Impact: Low risk if adhered to. High risk if anyone adds unsafe_load().
- Migration plan: Add linter rule to forbid non-safe_load. Use json/toml for new config files.

## Missing Critical Features

**No Order Confirmation Before Execution:**
- Problem: Bot places market orders with zero human review. Single signal bug = instant loss.
- Impact: Typo in parameters or indicator bug → automatic $5-30k loss. No kill switch in code.
- Blocks: Can't trust bot for unattended operation.
- Priority: HIGH — Add human approval workflow (e.g., "order placed, awaiting manual /confirm via Telegram before actually submitting")

**No Position-Level Risk Controls:**
- Problem: No absolute max loss per position (only -3% relative). If volatility spikes, can lose 10%+ on single trade.
- Impact: Accumulated losses from 3-4 bad trades in a row can exceed daily risk limit.
- Blocks: Can't scale to larger capital.
- Priority: HIGH — Implement absolute max loss per position (e.g., max -5% in KRW terms, cancel if hit).

**No Trade Audit / Compliance Log:**
- Problem: No record of why each trade was placed/exited. Only DB row with pnl.
- Impact: Can't debug strategy decisions. Can't prove/disprove if bug occurred.
- Blocks: Impossible to verify if bugs like double-entry actually occurred.
- Priority: MEDIUM — Add trade_audit table with full signal snapshot at entry time. Log all filter evaluations.

**No Graceful Shutdown / Position Close-Out:**
- Problem: If bot crashes during trade, position left open with no recovery instructions.
- Impact: Manual intervention required. Risk of leaving position open overnight.
- Blocks: Can't do planned maintenance.
- Priority: MEDIUM — Implement graceful_shutdown() that closes all positions and waits for confirmation.

## Test Coverage Gaps

**No Unit Tests for Core Trading Logic:**
- Untested area: Signal generation (VOLUME_MULT detection, RSI/MACD filtering)
- Files: `scripts/alt_monitor.py` (lines 1550-1700 signal detection), `bithumb/indicators.py`
- Risk: Logic bugs hide until real trade loss occurs.
- Priority: HIGH — Add test cases for:
  - Volume spike detection with edge prices
  - Indicator calculations with boundary data
  - Filter evaluation (BTC bullish, orderbook ratio, etc.)

**No Integration Tests for Order Flow:**
- Untested area: Complete buy → monitor → exit flow
- Files: All of `scripts/alt_monitor.py`, `bithumb/client.py` order methods
- Risk: Race conditions between threads only appear at runtime.
- Priority: HIGH — Add test scenarios:
  - Buy order fails, position not created
  - Exit fires twice (early trail + loss)
  - Price data arrives out-of-order

**No Chaos Testing for Network Faults:**
- Untested area: API timeouts, WS drops, partial responses
- Files: `bithumb/client.py` (all HTTP methods), `scripts/alt_monitor.py` (WS initialization)
- Risk: Rare network issues cause cascading failures.
- Priority: MEDIUM — Add fault injection tests:
  - API timeouts mid-order
  - WS reconnection scenarios
  - Corrupted JSON responses

**No Backtest Suite:**
- Untested area: Strategy viability on historical data
- Files: No backtest script exists
- Risk: Parameter changes deployed untested.
- Priority: MEDIUM — Create backtest.py using historical candle data. Simulate 30-day period before parameter change deployment.
