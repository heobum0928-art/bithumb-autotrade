# 빗썸 신규 코인 단타 자동매매 프로젝트

## 대화 시작 시 필독
**새 대화가 시작되면 반드시 `PROJECT_STATE.md`를 먼저 읽을 것.**

## 헛소리 방지 체크리스트
답변 전 아래 상황에 맞는 파일을 반드시 확인할 것:

| 상황 | 확인 파일 |
|------|---------|
| 봇 상태 / 포지션 / 쿨다운 | `data/active_pos.json` + `logs/alt_monitor.log` |
| 파라미터 변경 제안 | `docs/STRATEGY.md` (변경 이력 및 이유 확인) |
| 성과 / 승률 / PnL 언급 | `docs/PERFORMANCE.md` |
| 오늘 뭐 했는지 | `docs/sessions/YYYY-MM-DD.md` |
| 이미 논의한 내용 재제안 방지 | `docs/sessions/` 최근 파일 검색 |

**추측 금지**: 파일 확인 없이 봇 상태나 수치를 말하지 말 것.

## 프로젝트 목표
빗썸(Bithumb) 거래소에서 신규 상장 코인의 첫 거래 시작 시점에 진입하여 단기 수익을 얻는 자동매매 시스템.

## 사용자 작업 스타일 (절대 준수)
- 머신비전 엔지니어 (C#, C++ 주력, Python 학습 중)
- **합의 우선**: 코드 수정 전 항상 의견 먼저 제시 → 동의 받고 진행
- **한 번에 한 단계만**: 여러 단계 묶어서 진행 금지
- **자동 git 백업**: 작업 마무리 시 add/commit/push 자동 수행 (커밋 메시지는 사용자 검토)

## 핵심 전략
```
[빗썸 전체 마켓 폴링] → [신규 코인 감지] → [첫 거래 시작 감지] → [즉시 시장가 매수] → [익절/손절 자동 청산]
```

### 리스크 룰 (초안, 백테스트 후 조정)
- 진입 금액: 자본의 20~30% (1회)
- 익절: +5~10%
- 손절: -3%
- 첫 1분 내 미체결 시 취소
- 일일 손실 한도: -5% 도달 시 당일 중단

## 현재 상태 (2026-05-09 기준)

### 완료
- (없음, 프로젝트 시작)

### 진행 중
- Step 1: 프로젝트 뼈대 + 빗썸 REST API 클라이언트

### 미진행
- Step 2: 잔고 조회 API
- Step 3: 전체 마켓 목록 + 신규 상장 감지
- Step 4: 시장가 매수/매도 주문
- Step 5: 자동 진입 + 익절/손절 전략

## 빗썸 API 정보
- Base URL: `https://api.bithumb.com`
- Public API: 인증 불필요
  - `GET /public/ticker/ALL` - 전체 코인 시세 (신규 상장 감지 핵심)
  - `GET /public/ticker/{coin}` - 개별 코인 시세
  - `GET /public/orderbook/{coin}` - 호가 정보
- Private API: HMAC-SHA512 인증 필요
  - `POST /info/balance` - 보유 자산 조회
  - `POST /trade/market_buy` - 시장가 매수
  - `POST /trade/market_sell` - 시장가 매도
  - `POST /trade/cancel` - 주문 취소

## 거래 비용
- 빗썸 수수료: 0.25% (기본), VIP 등급에 따라 감소
- 왕복 비용: ~0.5%
- 슬리피지: 신규 상장 초기 변동성 매우 큼 (0.5~2% 추가 고려)
- 신호 생성 시 항상 1% 이상 수익 목표로 설계

## 보안 원칙 (절대 위반 금지)
- config.yaml, .env 는 절대 git 커밋 금지 (.gitignore 등록)
- App Key/Secret 코드 하드코딩 금지
- 매매 관련 코드 변경 시 반드시 사용자 검토 후 커밋

## GitHub 운영
- 브랜치: main
- 커밋 단위: 기능 1개 단위
- 커밋 메시지: 영어 권장

## Claude Code 작업 규칙 (중요)
1. 코드 수정/생성 전 무엇을 어떻게 할지 먼저 설명하고 동의 구할 것
2. 동의 받기 전 파일 직접 수정 금지
3. 한 번에 한 단계씩만 진행
4. 작업 완료 시 git 백업 자동 수행
5. config.yaml 은 절대 git에 추가 금지
6. 모르거나 애매한 부분은 추측하지 말고 사용자에게 질문

<!-- GSD:project-start source:PROJECT.md -->
## Project

**빗썸 펌핑 단타봇 — 검증 체계 전환**

빗썸 거래소에서 펌핑하는 신규/알트코인을 단타하는 자동매매 봇 프로젝트다. 지금까지는 전략을 검증 없이 즉흥적으로 운영해 손실(-65,000원)이 누적됐다. 이번 마일스톤은 봇을 **"검증 우선" 체계**로 전환하는 것 — 실거래 없이 데이터를 수집하고, 백테스트로 전략을 검증한 뒤에만 실거래를 허용한다. 머신비전 엔지니어가 Python·퀀트를 학습하며 장기적으로 발전시키는 프로젝트다.

**Core Value:** **검증되지 않은 전략에는 실제 돈을 넣지 않는다.** 데이터 → 백테스트 → 검증을 통과한 것만 실거래로 간다.

### Constraints

- **Tech stack**: Python 3.13, 빗썸 API 2.0 (JWT HS256), SQLite, websocket-client, 단일 봇 프로세스 + watchdog
- **Timeline**: 틱 데이터 축적에 실세계 2~3주 소요 — 백테스트 엔진 단계는 그 기간 동안 병행 가능
- **Budget**: 데이터 수집·백테스트 단계 실거래 손실 0원 (실거래 OFF)
- **Performance**: 틱 기록은 기존 WS 시세 재사용 — API 추가 호출 없음
- **Security**: config.yaml(API 키) git 커밋 금지. 매매 코드 변경은 사용자 검토 후
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.x - Entire bot system, trading automation, monitoring, analysis
- YAML - Configuration files (config.yaml)
- SQL - SQLite database queries and schema
## Runtime
- Python 3.7+ (based on f-strings and type hints)
- pip
- Lockfile: `requirements.txt` present
## Frameworks
- Custom REST API client (`bithumb/client.py`) - Bithumb exchange integration via requests
- None currently in use (no test framework detected)
- yaml==6.0.1 - YAML parsing for config management
- schedule==1.2.1 - Job scheduling for watchdog and periodic tasks
- sqlite3 (stdlib) - Trade logging, signal tracking, pump event recording
## Key Dependencies
- requests==2.31.0 - HTTP client for all API calls (Bithumb, Telegram)
- pyyaml==6.0.1 - Configuration parsing
- PyJWT==2.8.1 - JWT token generation for Bithumb API 2.0 authentication
- python-dotenv==1.0.0 - Environment variable loading (.env files)
- python-telegram-bot==20.7 - Telegram bot for command interface and alerts
- websocket-client==1.7.0 - WebSocket client for Bithumb real-time ticker feeds
- psutil (imported in alt_monitor.py) - Process monitoring for single-instance enforcement and watchdog
- anthropic (optional/installed separately) - Claude API for trading data analysis
## Configuration
- Loads from `config.yaml` (checked into repo but should use .gitignore)
- `.env` support via python-dotenv (for sensitive credentials)
- Environment variables for Bithumb API keys, Telegram tokens, Anthropic API keys
- bithumb.api_key - Bithumb REST API key
- bithumb.api_secret - Bithumb REST API secret (HMAC-SHA512)
- telegram.bot_token - Telegram bot token
- telegram.chat_id - Target Telegram chat ID
- anthropic_api_key - Claude API key for analysis
- trading.capital_krw - Operating capital in Korean Won
- monitor.poll_interval_sec - Polling frequency for new listings
- start_bots.bat - Windows batch file for starting bot system
## Platform Requirements
- Windows 11 Pro 10.0.26200 (current environment)
- PowerShell and Bash available
- Python 3.7+ with pip
- Bithumb exchange account with API credentials
- Telegram bot token and chat ID for notifications
- Anthropic API key for Claude analysis (optional)
- Internet connection for REST API and WebSocket connections
- SQLite (local file-based): `data/trades.db`
- Auto-creates schema via `bithumb.db.init_db()`
## Architecture Notes
- Bithumb API 2.0 uses JWT (HS256) authentication
- Private API: Authorization header with Bearer token
- Public API: No authentication required
- Base URL: https://api.bithumb.com
- `alt_monitor.py` - Main trading bot (REST API polling + WebSocket real-time)
- `tg_bot.py` - Telegram command interface
- `watchdog.py` - Process supervisor (keeps bots alive, manages restarts)
- `session_writer.py` - Daily log generator (reads DB, writes markdown)
- `ai_analyze.py` - Claude-powered analysis script
- `alt_monitor.py` uses threading for non-blocking operations:
- Rotating file handlers to `logs/` directory
- Structured JSON where applicable
- All timestamps use UTC or KST (UTC+9)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Lowercase with underscores: `client.py`, `auto_trade.py`, `alt_monitor.py`
- Script files in `/scripts/` directory use descriptive names with verbs: `check_status.py`, `monitor_listing.py`, `session_writer.py`
- Test/check scripts prefixed with underscore: `_api_test.py`, `_status_check.py`, `_pump_check.py`
- Modules in `bithumb/` package grouped by function: `client.py`, `db.py`, `indicators.py`, `notify.py`
- Lowercase with underscores (snake_case): `get_ticker()`, `wait_for_first_price()`, `log_trade()`
- Getter functions prefixed with `get_`: `get_balance()`, `get_accounts()`, `get_all_coins()`
- Helper functions prefixed with underscore for internal use: `_make_token()`, `_closes()`, `_get_cfg()`, `_is_quiet_hours()`
- Boolean functions often prefixed with `is_`, `has_`, or plain verb: `_is_quiet_hours()`, `calc_macd_bull()` (returns bool)
- Lowercase with underscores: `coin`, `market`, `entry_price`, `total_pnl`, `win_rate`
- Constants in UPPERCASE: `WS_URL`, `CHECK_INTERVAL`, `WINDOW_SEC`, `MIN_KRW`, `PULLBACK_TARGET_PCT`
- Abbreviations allowed for clarity: `krw`, `pnl`, `sl`, `tp`, `vol`, `sec`, `pct`
- Prefixed with underscore for class-level private: `self._api_key`, `self._api_secret`, `self._session`
- Config and path variables: `cfg`, `log`, `ROOT`, `DB_PATH`, `SESS_DIR`, `LOCK_FILE`
- Type hints used extensively in function signatures (Python 3.9+ style with `|` for unions)
- Union types: `dict | list`, `float | None`, `bool | None`
- Generic types with brackets: `list[dict]`, `list[float]`, `set[str]`
- Argument types: `str`, `int`, `float`, `dict`, `bool`, `datetime`
- Return types always specified: `-> dict`, `-> list[dict]`, `-> float | None`, `-> int`
## Code Style
- No explicit formatter configured (no .prettierrc or black config found)
- Consistent indentation: 4 spaces
- Line length: appears to be ~100 characters (based on observed wrapping in files like `client.py`)
- Docstrings used for module and function documentation
- Inline comments sparse but used where strategy logic is complex
- No .eslintrc or .pylintrc found
- No automated linting tool configured in repo
- Code style appears hand-maintained with consistent patterns
- Module-level docstrings explaining purpose: `"""Telegram notification helper."""`, `"""Trade database — SQLite, one row per completed trade."""`
- Function docstrings with Return descriptions: `"""Return current price info. coin='ALL' returns every listed coin."""`
- Docstring format: single-line or multi-line with clear purpose
- Example from `indicators.py`: `"""Bollinger Band %B: 0=lower band, 1=upper band, >1=above upper."""`
## Import Organization
- No aliases configured (.gitignore shows no path aliasing setup)
- Relative imports via `sys.path.insert(0, ...)` pattern: `sys.path.insert(0, str(Path(__file__).parent.parent))`
- Used consistently in scripts to allow imports from parent directory: `from bithumb.client import BithumbClient`
## Error Handling
- API errors: `resp.raise_for_status()` with custom error messages on API response check
- Silent failures in indicator snapshot (never raises):
- Notification failures caught but logged: `bithumb/notify.py`
- Database operations with soft error handling in migrations:
- Graceful fallback for missing data: `get_price()` returns 0.0 on any error
## Logging
- Module-level logger: `log = logging.getLogger(__name__)`
- Configured per script with file and stdout handlers:
- Contextual prefixes in logs for readability:
- Log levels used:
- Korean language used consistently in log messages for trading domain
- Format strings with financial metrics: `{price:,.0f}원`, `{pnl_pct:+.2f}%`, `{vol:.6f}`
## Comments
- Strategy section header markers used with dashes: `# ── 로깅 ────────────────`
- API section headers: `# ------ Public API (no auth) — legacy endpoints ------`
- Complex logic explained: HMAC-SHA512 token generation, indicator calculations
- Parameter comments inline for strategy tunables: `VOLUME_MULT = 5.0   # 7→5, RSI/MACD 복합...`
- Trade flow comments showing step-by-step process
- Not used (Python project without TypeScript)
- Function docstrings provide documentation instead
- Inline docstrings for classes and key functions
## Function Design
- Range: 5-50 lines typical
- Small utility functions: 5-10 lines (`get_price()`, `_is_quiet_hours()`)
- Medium helpers: 20-30 lines (`wait_for_first_price()`, `calc_rsi()`)
- Larger orchestrators: 50-100+ lines (`monitor_trailing()`, `run()` main loops)
- Positional parameters first, then defaults: `def log_trade(coin: str, market: str, ..., max_price: float = 0.0)`
- Optional parameters given defaults: `coin: str = None`, `params: dict = None`
- Keyword-only via `**kwargs` for flexible updates: `update_pump_path(pump_id: int, **kwargs)`
- Limit to 5-7 parameters per function (longer signatures indicate complexity)
- Explicit return types always specified
- Multiple returns allowed: `dict | list` for `get_balance()`
- None used for operations without return: `def log_trade(...) -> None`
- Dictionary returns for structured data: `{"rsi": None, "bb_pct": None, ...}`
- Sentinel returns for errors: `return 0.0` (price), `return False` (boolean operations)
## Module Design
- `bithumb/client.py`: Single class `BithumbClient` with public methods (no private class)
- `bithumb/db.py`: Module-level functions (no classes) for database operations
- `bithumb/indicators.py`: Pure functions for calculations, `snapshot()` as public entry point
- `bithumb/notify.py`: Module-level functions with single entry point `send()` and specialized `notify_*()` helpers
- `bithumb/__init__.py`: Empty (minimal, allows `from bithumb import notify`)
- `strategy/__init__.py`: Empty (minimal)
- No re-exports; direct imports preferred: `from bithumb.client import BithumbClient`
- Core client logic: `client.py` (API communication, authentication)
- Data persistence: `db.py` (SQLite schema, queries)
- Analysis functions: `indicators.py` (technical indicators)
- Side effects: `notify.py` (external notifications)
- Business logic: `scripts/` (trading bots, schedulers, monitors)
## Special Patterns
- YAML config file: `config.yaml` (.gitignored)
- Lazy-loaded global config: `_cfg` in `notify.py`
- PID lockfile for single-instance check: `data/alt_monitor.pid`
- Process termination via `psutil`: `p.terminate()`, `p.wait()`, `p.kill()`
- Exit handling: `atexit.register(lambda: lockfile.unlink(missing_ok=True))`
- F-strings dominant: `f"{coin}/KRW"`, `f"[{coin}] 첫 체결가..."`
- Financial formatting: `:,.0f` for KRW amounts, `:+.2f%` for percentages, `:.6f` for crypto volumes
- Isoformat for timestamps: `entered_at.isoformat()`, `date.today().isoformat()`
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- Non-blocking single-loop main event processor (avoids blocking on long operations)
- WebSocket-based real-time price streaming (Bithumb API 2.0)
- Multi-threaded background trackers for outcome analysis and pullback detection
- State machine phases for position lifecycle management (entry → trailing → exit)
- Learning system based on accumulated losses and pattern detection
## Layers
- Purpose: Abstracts Bithumb REST and WebSocket APIs
- Location: `bithumb/client.py`
- Contains: Authentication (JWT HS256), market data (tickers, candles, orderbook), trading operations (buy/sell/cancel), account balance queries
- Depends on: `requests`, `jwt`, `yaml` (config)
- Used by: Main monitor loop, all trading scripts
- Purpose: Persistent trade history, signal logs, pump detection tracking, daily parameter audits
- Location: `bithumb/db.py`
- Contains: SQLite tables (trades, signal_log, pump_log, daily_params), read/write operations with transaction safety
- Depends on: `sqlite3`
- Used by: Main monitor, outcome tracking, analysis scripts
- Purpose: Real-time price momentum detection and technical indicator calculation
- Location: `bithumb/indicators.py` (RSI, Bollinger Bands, MACD), main loop signal logic
- Contains: RSI (14-period), BB%B (20-period), MACD (12/26/9), volume multiplier checks, BTC correlation filtering
- Depends on: Candle data from API layer
- Used by: Entry decision logic in main monitor
- Purpose: Telegram alerts (entry, exit, errors, state changes)
- Location: `bithumb/notify.py`
- Contains: Message formatting, quiet hours enforcement, retry logic
- Depends on: `requests`, `config.yaml`
- Used by: Main monitor on trade events
- Purpose: Event loop for position monitoring, signal detection, entry/exit execution
- Location: `scripts/alt_monitor.py`
- Contains: PriceTracker (WebSocket subscription + history buffer), position state machine, signal filtering pipeline, loss learning, pullback queue, outcome tracking queue
- Depends on: All layers above
- Used by: Watchdog process keeps it alive
- Purpose: Daemon supervisor keeping alt_monitor.py and tg_bot.py alive
- Location: `scripts/watchdog.py`
- Contains: PID-based liveness checks, automatic restart with Telegram alerts, session log writing, AI analysis triggering
- Depends on: `psutil`, `subprocess`, Telegram config
- Used by: Manual startup or init system
- Purpose: Historical performance analysis, signal effectiveness, pump pattern analysis
- Location: `scripts/signal_stats.py`, `scripts/ai_analyze.py`, `scripts/session_writer.py`
- Contains: Win/loss rate calculation, outcome distribution, pump path statistics, session summaries
- Depends on: Database queries
- Used by: Manual review, watchdog triggers
## Data Flow
- **Active Position:** `data/active_pos.json` — current coin, volume, entry price, phase, highest price, trailing stop level
- **Loss Coins:** `data/loss_coins.json` — cooldown registry with expiration times
- **Bot Lock:** `data/bot.lock` — single-instance enforcement via PID file
## Key Abstractions
- Purpose: Real-time price buffering and signal extraction from WebSocket stream
- Examples: `scripts/alt_monitor.py` lines 429-549
- Pattern: Deque-based circular buffer (MAXLEN=60), thread-safe locking, lookback window sliding to detect momentum
- Purpose: Represents active trade state (coin, volume, entry price, timestamps)
- Pattern: Dict with keys: coin, market, volume, entry_price, cost, entered_at, highest, phase, sold_vol, recv_krw, trail
- Persisted to JSON for restart recovery
- Purpose: Encapsulates detection result (price change %, volume multiplier, indicator snapshots)
- Pattern: Dict with keys: price_chg, vol_mult, rsi, bb_pct, macd_bull, entry_type ("신호감지" or "눌림목")
- Purpose: Deferred tracking of entry signal effectiveness post-execution
- Pattern: Background thread drains `_outcome_queue` items, sleeps 10s, updates signal_log with price deltas at T+5m and T+30m
## Entry Points
- Location: `scripts/alt_monitor.py`
- Triggers: Manual `python scripts/alt_monitor.py` or from `scripts/watchdog.py`
- Responsibilities:
- Location: `scripts/watchdog.py`
- Triggers: Manual startup (e.g., cron, system init) or operator command
- Responsibilities:
- Location: `scripts/tg_bot.py` (not fully shown, but integrated)
- Triggers: Started by watchdog
- Responsibilities: Telegram command listener (manual entry/exit, parameter tweaks, balance query)
## Error Handling
## Cross-Cutting Concerns
- Framework: Python `logging` module
- Format: `"%(asctime)s [ALT][%(levelname)s] %(message)s"` (ISO timestamp, tag, level)
- Sinks: stdout + `logs/alt_monitor.log` (rotating not configured, manual cleanup expected)
- Levels: DEBUG (signal details), INFO (trade events, entry/exit), WARNING (failures, anomalies), ERROR (critical operations)
- **Entry Filters (order matters):**
- **Exit Conditions:**
- Method: JWT HS256 (API 2.0 private endpoints)
- Implementation: `BithumbClient._make_token()` encodes nonce + timestamp + optional query_hash
- Config: API key/secret read from `config.yaml` (external, not committed)
- WebSocket updates via background thread write to `PriceTracker._hist` (protected by `_lock`)
- Main loop reads lock-protected snapshots for signal detection
- Outcome/pump/pullback trackers are separate daemon threads, non-blocking
- No explicit thread coordination beyond queue.Queue() for inter-thread communication
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
