# Coding Conventions

**Analysis Date:** 2026-05-19

## Naming Patterns

**Files:**
- Lowercase with underscores: `client.py`, `auto_trade.py`, `alt_monitor.py`
- Script files in `/scripts/` directory use descriptive names with verbs: `check_status.py`, `monitor_listing.py`, `session_writer.py`
- Test/check scripts prefixed with underscore: `_api_test.py`, `_status_check.py`, `_pump_check.py`
- Modules in `bithumb/` package grouped by function: `client.py`, `db.py`, `indicators.py`, `notify.py`

**Functions:**
- Lowercase with underscores (snake_case): `get_ticker()`, `wait_for_first_price()`, `log_trade()`
- Getter functions prefixed with `get_`: `get_balance()`, `get_accounts()`, `get_all_coins()`
- Helper functions prefixed with underscore for internal use: `_make_token()`, `_closes()`, `_get_cfg()`, `_is_quiet_hours()`
- Boolean functions often prefixed with `is_`, `has_`, or plain verb: `_is_quiet_hours()`, `calc_macd_bull()` (returns bool)

**Variables:**
- Lowercase with underscores: `coin`, `market`, `entry_price`, `total_pnl`, `win_rate`
- Constants in UPPERCASE: `WS_URL`, `CHECK_INTERVAL`, `WINDOW_SEC`, `MIN_KRW`, `PULLBACK_TARGET_PCT`
- Abbreviations allowed for clarity: `krw`, `pnl`, `sl`, `tp`, `vol`, `sec`, `pct`
- Prefixed with underscore for class-level private: `self._api_key`, `self._api_secret`, `self._session`
- Config and path variables: `cfg`, `log`, `ROOT`, `DB_PATH`, `SESS_DIR`, `LOCK_FILE`

**Types:**
- Type hints used extensively in function signatures (Python 3.9+ style with `|` for unions)
- Union types: `dict | list`, `float | None`, `bool | None`
- Generic types with brackets: `list[dict]`, `list[float]`, `set[str]`
- Argument types: `str`, `int`, `float`, `dict`, `bool`, `datetime`
- Return types always specified: `-> dict`, `-> list[dict]`, `-> float | None`, `-> int`

## Code Style

**Formatting:**
- No explicit formatter configured (no .prettierrc or black config found)
- Consistent indentation: 4 spaces
- Line length: appears to be ~100 characters (based on observed wrapping in files like `client.py`)
- Docstrings used for module and function documentation
- Inline comments sparse but used where strategy logic is complex

**Linting:**
- No .eslintrc or .pylintrc found
- No automated linting tool configured in repo
- Code style appears hand-maintained with consistent patterns

**Docstrings:**
- Module-level docstrings explaining purpose: `"""Telegram notification helper."""`, `"""Trade database — SQLite, one row per completed trade."""`
- Function docstrings with Return descriptions: `"""Return current price info. coin='ALL' returns every listed coin."""`
- Docstring format: single-line or multi-line with clear purpose
- Example from `indicators.py`: `"""Bollinger Band %B: 0=lower band, 1=upper band, >1=above upper."""`

## Import Organization

**Order:**
1. Standard library imports (`sys`, `os`, `time`, `logging`, `datetime`, `pathlib`, `json`, `sqlite3`, `hashlib`, `uuid`)
2. Third-party imports (`requests`, `yaml`, `jwt`, `psutil`, `schedule`, `websocket-client`, `python-telegram-bot`)
3. Local imports from `bithumb` package (`from bithumb.client import`, `from bithumb.db import`)

**Path Aliases:**
- No aliases configured (.gitignore shows no path aliasing setup)
- Relative imports via `sys.path.insert(0, ...)` pattern: `sys.path.insert(0, str(Path(__file__).parent.parent))`
- Used consistently in scripts to allow imports from parent directory: `from bithumb.client import BithumbClient`

**Pattern:**
```python
import sys
import time
import logging
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient
from bithumb.db import init_db, log_trade
from bithumb import notify
```

## Error Handling

**Patterns:**
- API errors: `resp.raise_for_status()` with custom error messages on API response check
  ```python
  resp = self._session.get(f"{BASE_URL}/public/ticker/{coin.upper()}_KRW")
  resp.raise_for_status()
  data = resp.json()
  if data["status"] != "0000":
      raise RuntimeError(f"get_ticker error: {data}")
  ```

- Silent failures in indicator snapshot (never raises):
  ```python
  def snapshot(client, market: str) -> dict:
      """Fetch 35 1-min candles and return indicator dict. Never raises."""
      result = {"rsi": None, "bb_pct": None, "macd_bull": None}
      try:
          candles = client.get_candles(market, unit=1, count=35)
          result["rsi"] = calc_rsi(candles)
      except Exception:
          pass
      return result
  ```

- Notification failures caught but logged: `bithumb/notify.py`
  ```python
  try:
      resp = requests.post(url, json=..., timeout=5)
      resp.raise_for_status()
      return True
  except Exception as e:
      log.warning(f"[Telegram] 전송 실패: {e}")
      return False
  ```

- Database operations with soft error handling in migrations:
  ```python
  try:
      con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
  except Exception:
      pass
  ```

- Graceful fallback for missing data: `get_price()` returns 0.0 on any error
  ```python
  try:
      return float(d["closing_price"])
  except (KeyError, ValueError, TypeError):
      return 0.0
  ```

## Logging

**Framework:** `logging` (Python standard library)

**Patterns:**
- Module-level logger: `log = logging.getLogger(__name__)`
- Configured per script with file and stdout handlers:
  ```python
  logging.basicConfig(
      level=logging.INFO,
      format="%(asctime)s [%(levelname)s] %(message)s",
      handlers=[
          logging.StreamHandler(sys.stdout),
          logging.FileHandler("logs/auto_trade.log", encoding="utf-8"),
      ],
  )
  log = logging.getLogger(__name__)
  ```

- Contextual prefixes in logs for readability:
  - `[ALT]` in alt_monitor.py: `"%(asctime)s [ALT][%(levelname)s] %(message)s"`
  - `[WD]` in watchdog.py: `"%(asctime)s [WD][%(levelname)s] %(message)s"`
  - `[TG]` in tg_bot.py: `"%(asctime)s [TG][%(levelname)s] %(message)s"`

- Log levels used:
  - `log.debug()`: Low-level details (`"[Telegram] 무음 시간대 — 전송 생략"`)
  - `log.info()`: Normal flow, trades, signals (`"[DB] 거래 저장: {coin} PnL={pnl_krw:+,.0f}원..."`)
  - `log.warning()`: Recoverable issues (`"[{coin}] 첫 체결가 미확인 — 진입 포기"`)
  - `log.error()`: Process failures (`"[{name}] {error_msg}"`)

- Korean language used consistently in log messages for trading domain
- Format strings with financial metrics: `{price:,.0f}원`, `{pnl_pct:+.2f}%`, `{vol:.6f}`

## Comments

**When to Comment:**
- Strategy section header markers used with dashes: `# ── 로깅 ────────────────`
- API section headers: `# ------ Public API (no auth) — legacy endpoints ------`
- Complex logic explained: HMAC-SHA512 token generation, indicator calculations
- Parameter comments inline for strategy tunables: `VOLUME_MULT = 5.0   # 7→5, RSI/MACD 복합...`
- Trade flow comments showing step-by-step process

**JSDoc/TSDoc:**
- Not used (Python project without TypeScript)
- Function docstrings provide documentation instead
- Inline docstrings for classes and key functions

## Function Design

**Size:**
- Range: 5-50 lines typical
- Small utility functions: 5-10 lines (`get_price()`, `_is_quiet_hours()`)
- Medium helpers: 20-30 lines (`wait_for_first_price()`, `calc_rsi()`)
- Larger orchestrators: 50-100+ lines (`monitor_trailing()`, `run()` main loops)

**Parameters:**
- Positional parameters first, then defaults: `def log_trade(coin: str, market: str, ..., max_price: float = 0.0)`
- Optional parameters given defaults: `coin: str = None`, `params: dict = None`
- Keyword-only via `**kwargs` for flexible updates: `update_pump_path(pump_id: int, **kwargs)`
- Limit to 5-7 parameters per function (longer signatures indicate complexity)

**Return Values:**
- Explicit return types always specified
- Multiple returns allowed: `dict | list` for `get_balance()`
- None used for operations without return: `def log_trade(...) -> None`
- Dictionary returns for structured data: `{"rsi": None, "bb_pct": None, ...}`
- Sentinel returns for errors: `return 0.0` (price), `return False` (boolean operations)

## Module Design

**Exports:**
- `bithumb/client.py`: Single class `BithumbClient` with public methods (no private class)
- `bithumb/db.py`: Module-level functions (no classes) for database operations
- `bithumb/indicators.py`: Pure functions for calculations, `snapshot()` as public entry point
- `bithumb/notify.py`: Module-level functions with single entry point `send()` and specialized `notify_*()` helpers

**Barrel Files:**
- `bithumb/__init__.py`: Empty (minimal, allows `from bithumb import notify`)
- `strategy/__init__.py`: Empty (minimal)
- No re-exports; direct imports preferred: `from bithumb.client import BithumbClient`

**Organization Strategy:**
- Core client logic: `client.py` (API communication, authentication)
- Data persistence: `db.py` (SQLite schema, queries)
- Analysis functions: `indicators.py` (technical indicators)
- Side effects: `notify.py` (external notifications)
- Business logic: `scripts/` (trading bots, schedulers, monitors)

## Special Patterns

**Configuration Loading:**
- YAML config file: `config.yaml` (.gitignored)
- Lazy-loaded global config: `_cfg` in `notify.py`
  ```python
  _cfg = None
  def _get_cfg() -> dict:
      global _cfg
      if _cfg is None:
          _cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
      return _cfg
  ```

**Process Management:**
- PID lockfile for single-instance check: `data/alt_monitor.pid`
- Process termination via `psutil`: `p.terminate()`, `p.wait()`, `p.kill()`
- Exit handling: `atexit.register(lambda: lockfile.unlink(missing_ok=True))`

**String Formatting:**
- F-strings dominant: `f"{coin}/KRW"`, `f"[{coin}] 첫 체결가..."`
- Financial formatting: `:,.0f` for KRW amounts, `:+.2f%` for percentages, `:.6f` for crypto volumes
- Isoformat for timestamps: `entered_at.isoformat()`, `date.today().isoformat()`

---

*Convention analysis: 2026-05-19*
