# Codebase Structure

**Analysis Date:** 2026-05-19

## Directory Layout

```
coinbase/
├── bithumb/                 # Core trading library
│   ├── __init__.py
│   ├── client.py            # Bithumb API client (REST + legacy endpoints)
│   ├── db.py                # SQLite trade/signal/pump persistence
│   ├── indicators.py        # Technical indicators (RSI, MACD, BB%B)
│   └── notify.py            # Telegram notification helper
│
├── scripts/                 # Executable trading and utility scripts
│   ├── alt_monitor.py       # Main trading bot (event loop + state machine)
│   ├── watchdog.py          # Process supervisor (keeps bots alive)
│   ├── tg_bot.py            # Telegram command listener
│   ├── session_writer.py    # Daily session log compiler
│   ├── ai_analyze.py        # AI analysis generation (Claude API)
│   ├── signal_stats.py      # Signal performance analysis
│   ├── show_stats.py        # Display account/trade statistics
│   ├── show_pnl.py          # PnL breakdown by coin
│   ├── check_ticker.py      # Manual ticker inspection
│   ├── monitor_listing.py   # New listing detector
│   └── [_*.py, *.py]        # Diagnostic/utility scripts (data cleaning, debugging)
│
├── strategy/                # Strategy module (reserved for future use)
│   └── __init__.py
│
├── data/                    # Runtime state and database
│   ├── trades.db            # SQLite: trades, signal_log, pump_log, daily_params
│   ├── active_pos.json      # Current position state (coin, volume, entry_price, etc.)
│   ├── loss_coins.json      # Cooldown registry (coin → {count, until_timestamp})
│   ├── alt_monitor.pid      # PID file for single-instance enforcement
│   └── bot.lock             # Bot lock file (for parallel bot checks)
│
├── logs/                    # Log files (persistent, manually rotated)
│   ├── alt_monitor.log      # Main trading bot logs
│   ├── watchdog.log         # Supervisor logs
│   └── [others]
│
├── docs/                    # Documentation and analysis
│   ├── STRATEGY.md          # Strategy design + parameter change history
│   ├── PERFORMANCE.md       # Cumulative performance metrics
│   ├── sessions/            # Daily session logs (YYYY-MM-DD.md)
│   │   ├── 2026-05-12.md
│   │   ├── 2026-05-13.md
│   │   └── ...
│   ├── ai_analysis_*.md     # Daily AI analysis reports (Claude-generated)
│   └── [others]
│
├── .planning/               # GSD codebase analysis (generated)
│   └── codebase/
│       ├── ARCHITECTURE.md  # This file: system design & data flows
│       └── STRUCTURE.md     # This file: file layout & naming conventions
│
├── .claude/                 # Claude Code working directory (not tracked)
│   └── [projects/memory]
│
├── config.yaml              # Config file (NOT tracked, contains API keys)
│   .gitignore              # (includes config.yaml, *.db, .env, *.lock)
│
├── CLAUDE.md                # Project charter + work rules
├── PROJECT_STATE.md         # Current bot settings and cooldowns
├── README.md                # Project overview
└── .git/                    # Version control
```

## Directory Purposes

**bithumb/:**
- Purpose: Reusable trading library (API abstraction, data persistence, notifications)
- Contains: Classes and functions for Bithumb integration
- Key files: `client.py` (110+ lines), `db.py` (250 lines), `indicators.py` (78 lines), `notify.py` (50 lines)
- Immutability: Stable interface; changes must not break `alt_monitor.py`

**scripts/:**
- Purpose: Executable trading logic and operational utilities
- Contains: Main bot (`alt_monitor.py` ~1200 lines), supervisor (`watchdog.py`), reporting (`signal_stats.py`), utilities (data diagnosis/repair)
- Key files: `alt_monitor.py` (main event loop), `watchdog.py` (process management), `tg_bot.py` (chat interface)
- Mutability: Frequently modified for strategy tweaks, parameter tuning, signal filtering

**data/:**
- Purpose: Runtime state and persistent database
- Contains: SQLite database (trades, signals, pumps), JSON state files (position, cooldowns), PID lock files
- Gitignored: Yes (except schema, which is in `db.py`)
- Recovery: Bot loads `active_pos.json` on startup to resume mid-trade; `loss_coins.json` to apply cooldowns

**logs/:**
- Purpose: Operational audit trail
- Contains: Daily trade logs (`alt_monitor.log`), supervisor events (`watchdog.log`)
- Rotation: Manual (no logrotate configured; operator responsibility)
- Access: Tail for real-time debugging, archive for pattern analysis

**docs/:**
- Purpose: Human-readable strategy documentation and analysis
- Contains: Parameter justification (`STRATEGY.md`), cumulative metrics (`PERFORMANCE.md`), daily summaries (`sessions/`)
- Update pattern: `STRATEGY.md` edited by human when parameters change; `sessions/` auto-generated daily by `session_writer.py`; `ai_analysis_*.md` generated daily by `ai_analyze.py` (Claude API)

**.planning/codebase/:**
- Purpose: GSD automation context (architecture, structure, conventions, testing, concerns)
- Contains: Analysis documents for code generation and phase planning
- Generated by: `/gsd:map-codebase` command
- Consumed by: `/gsd:plan-phase` and `/gsd:execute-phase` commands

**strategy/:**
- Purpose: Reserved for future modular strategy implementations
- Current state: Empty `__init__.py` only (not yet used; all logic in `scripts/alt_monitor.py`)

## Key File Locations

**Entry Points:**
- `scripts/alt_monitor.py`: Main trading bot (run via `python scripts/alt_monitor.py` or watchdog)
- `scripts/watchdog.py`: Process supervisor (run via cron or init system)
- `scripts/tg_bot.py`: Telegram listener (started by watchdog or manually)

**Configuration:**
- `config.yaml`: API keys, capital, Telegram token (external, not committed)
- `PROJECT_STATE.md`: Current parameters, bot status, cooldowns (human-readable reference)
- `CLAUDE.md`: Project charter and work rules (for Claude Code context)

**Core Logic:**
- `bithumb/client.py`: Bithumb API wrapper (authentication, order execution, market data)
- `scripts/alt_monitor.py`: Signal detection + position state machine (lines 500-1200: main loop, entry/exit logic)
- `bithumb/db.py`: Trade/signal/pump persistence (SQLite operations)
- `bithumb/indicators.py`: Technical indicators (RSI, MACD, Bollinger Bands)

**Testing & Analysis:**
- `scripts/signal_stats.py`: Analyze historical signals (entry price vs. 5m/30m outcomes)
- `scripts/ai_analyze.py`: Daily performance analysis via Claude API
- `scripts/session_writer.py`: Compile daily trade log (date, trades, PnL, cooldowns)

**Data Access:**
- `data/trades.db`: Query via `bithumb.db` functions (see `get_trades()`, `get_stats()`)
- `data/active_pos.json`: Load/save via `load_active()` / `save_active()` in `alt_monitor.py`
- `data/loss_coins.json`: Load/save via `load_loss_coins()` / `save_loss_coins()` in `alt_monitor.py`

**Logs:**
- `logs/alt_monitor.log`: Trade events, signal details, errors (tail for debugging)
- `logs/watchdog.log`: Bot restart events, process management

## Naming Conventions

**Files:**
- Python scripts: `snake_case.py` (e.g., `alt_monitor.py`, `session_writer.py`)
- Config: `config.yaml` (external, not committed)
- Data: `lowercase.json`, `lowercase.db`, `lowercase.lock`
- Logs: `name.log` (e.g., `alt_monitor.log`)
- Docs: `UPPERCASE.md` for structured docs (STRATEGY.md, PERFORMANCE.md); `YYYY-MM-DD.md` for dated logs

**Directories:**
- Modules: `lowercase` (e.g., `bithumb`, `scripts`, `docs`)
- Internal state: `data/` (lowercase)

**Variables & Functions:**
- Functions: `snake_case` (e.g., `log_trade()`, `get_signal()`, `do_buy()`)
- Variables: `snake_case` (e.g., `entry_price`, `vol_mult`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `VOLUME_MULT = 5.0`, `TP_HALF = 0.015`)
- Private functions/methods: Prefixed with `_` (e.g., `_make_token()`, `_conn()`)

**Classes:**
- `PascalCase` (e.g., `BithumbClient`, `PriceTracker`)

**Data Keys (dicts):**
- Column names from database: `snake_case` (e.g., `entry_price`, `pnl_krw`, `exit_reason`)
- JSON state keys: `snake_case` (e.g., `active_pos.json`: coin, market, volume, entry_price, entered_at, highest, phase)

## Where to Add New Code

**New Trading Feature (signal filter, entry strategy, exit condition):**
- Primary code: `scripts/alt_monitor.py` (main loop, filter functions)
- Indicator calc: `bithumb/indicators.py` if new technical indicator
- Database: `bithumb/db.py` if new data to persist (new table or column)
- Example: To add a new momentum indicator (e.g., STOCH), add `calc_stoch()` to `indicators.py`, call from `indicator_snapshot()`, then reference in entry filter logic in `alt_monitor.py`

**New Utility Script (analysis, diagnostics, repair):**
- Location: `scripts/[name].py`
- Pattern: Import `from pathlib import Path`, `sys.path.insert(0, ...)` to access bithumb module, load config from `config.yaml`, query DB via `bithumb.db` functions
- Examples: `scripts/signal_stats.py`, `scripts/show_pnl.py`

**New Background Tracker (like outcome_tracker, pump_tracker):**
- Initialize in main() before `run()` call: `start_[name]_tracker(tracker)`
- Implement as `def _run():` nested function launched via `threading.Thread(target=_run, daemon=True).start()`
- Communicate with main loop via `queue.Queue` (e.g., `_outcome_queue.put()`)
- Pattern: Drain queue with `try: while True: queue.get_nowait()` loop, process pending items with sleep(10), update database as needed

**New Notification Type:**
- Implement in `bithumb/notify.py`: Add function like `notify_sell()`, `notify_error()`
- Config: Add Telegram token/chat_id to `config.yaml`
- Call from `alt_monitor.py` on relevant events

**Database Schema Extension:**
- Edit `bithumb/db.py`: Add table to `CREATE_SQL` or add column via `ALTER TABLE` in `init_db()`
- Implement read/write functions (e.g., `log_[name]()`, `get_[name]()`)
- Update DB init on bot startup to auto-migrate schema

**Testing/Validation Script:**
- Location: `scripts/test_*.py` or `scripts/check_*.py`
- Pattern: Direct API calls or DB queries to validate state
- Examples: `scripts/check_ticker.py`, `scripts/check_status.py`

## Special Directories

**data/:**
- Purpose: Runtime state (position, cooldowns) and database
- Generated: Yes (created at runtime by bot)
- Committed: No (gitignored except for schema definition in `db.py`)
- Cleanup: `data/trades.db` grows indefinitely; periodic archive/cleanup recommended

**logs/:**
- Purpose: Operational logs
- Generated: Yes (created by logging handlers)
- Committed: No (gitignored, manually managed)
- Rotation: None (manual operator responsibility; consider adding logrotate config)

**.planning/codebase/:**
- Purpose: GSD automation context (generated by `/gsd:map-codebase`)
- Generated: Yes (by codebase analysis tool)
- Committed: Yes (tracked in git as reference for automation)
- Usage: Read by `/gsd:plan-phase` and `/gsd:execute-phase` to inform code generation

**.claude/:**
- Purpose: Claude Code workspace (project memory, context)
- Generated: Yes (by Claude)
- Committed: No (gitignored)
- Lifecycle: Temporary, resets between conversations

**config.yaml (external):**
- Purpose: API keys, trading parameters, Telegram config
- Generated: No (manually created by operator)
- Committed: No (in .gitignore)
- Required keys: `bithumb.api_key`, `bithumb.api_secret`, `telegram.bot_token`, `telegram.chat_id`, `trading.capital_krw`
- Structure: YAML with sections [bithumb], [telegram], [trading]

---

**Key Principles for Modifications:**

1. **Preserve module boundaries:** Changes to `bithumb/` should not break `scripts/alt_monitor.py` API contracts.
2. **Centralize constants:** Tuneable parameters (thresholds, timeouts) should live at top of `alt_monitor.py` for easy discovery.
3. **Immutable data flow:** Trade state (`active_pos.json`) and cooldowns (`loss_coins.json`) must serialize/deserialize identically.
4. **Database-first for history:** All trade/signal events must be persisted to `data/trades.db` before being forgotten (for later analysis).
5. **Log-first for debugging:** Major events (entry, exit, error, restart) should be logged to `logs/alt_monitor.log` and Telegram simultaneously.
