# Testing Patterns

**Analysis Date:** 2026-05-19

## Test Framework

**Runner:**
- No formal test framework configured (no pytest.ini, conftest.py, or tox.ini found)
- No test dependencies in `requirements.txt`
- Tests are manual scripts rather than automated suites

**Assertion Library:**
- None (assertions are implicit in script execution)
- Tests validate behavior through print output and manual inspection

**Run Commands:**
```bash
python scripts/test_order.py      # Manual live trading test
python scripts/_api_test.py       # Manual API connectivity check
python scripts/_status_check.py   # Manual status query
python scripts/_pump_check.py     # Manual pump detection test
```

## Test File Organization

**Location:**
- Test/check scripts co-located with main scripts in `/scripts/` directory
- No separate `tests/` directory
- Naming convention: check scripts use `_` prefix (e.g., `_api_test.py`, `_status_check.py`)

**Naming:**
- Format: `_<system>_check.py` or `<action>_test.py`
- Examples: `_api_test.py`, `_status_check.py`, `_pump_check.py`, `_restart_check.py`, `test_order.py`
- Purpose-driven names: `check_sync.py`, `check_coin.py`, `check_ticker.py`

**Structure:**
```
scripts/
├── test_order.py          # Live trading integration test
├── _api_test.py           # API connectivity verification
├── _status_check.py       # Bot status check
├── _pump_check.py         # Pump detection validation
├── _db_check.py           # Database state check
└── [main scripts...]      # Trading bots and monitors
```

## Test Structure

**Manual Integration Tests:**

`scripts/test_order.py` - Real-money integration test:
```python
def main():
    client = BithumbClient()
    market = "KRW-BTC"
    buy_krw = 5000

    # 잔고 확인
    accounts = client.get_accounts()
    krw = next((float(a["balance"]) for a in accounts if a["currency"] == "KRW"), 0)
    print(f"보유 KRW: {krw:,.0f}원")

    if krw < buy_krw:
        print(f"잔고 부족: {buy_krw:,}원 필요")
        return

    # --- 시장가 매수 ---
    print(f"\n[매수] {market} {buy_krw:,}원 시장가 주문...")
    buy_result = client.market_buy(market, buy_krw)
    print(f"매수 결과: {buy_result}")

    order_uuid = buy_result.get("uuid")
    if not order_uuid:
        print("UUID 없음 — 주문 실패")
        return

    # 체결 대기
    print("체결 대기 중...")
    for _ in range(10):
        time.sleep(1)
        order = client.get_order(order_uuid)
        state = order.get("state")
        print(f"  상태: {state}")
        if state == "done":
            vol = float(order.get("executed_volume", 0))
            print(f"  체결 수량: {vol:.8f} BTC")
            break
    else:
        print("  10초 내 미체결 — 취소 시도")
        client.cancel_order(order_uuid)
        return

    # --- 즉시 시장가 매도 ---
    if vol > 0:
        print(f"\n[매도] {vol:.8f} BTC 시장가 매도...")
        sell_result = client.market_sell(market, vol)
        print(f"매도 결과: {sell_result}")

        sell_uuid = sell_result.get("uuid")
        for _ in range(10):
            time.sleep(1)
            order = client.get_order(sell_uuid)
            if order.get("state") == "done":
                print("매도 체결 완료!")
                break

    print("\n테스트 완료.")
```

**Patterns:**
- No setUp/tearDown methods (manual execution)
- No mocking (real API calls to Bithumb)
- Validation via print output and manual inspection
- Timeout-based waiting: `for _ in range(10): time.sleep(1)`
- Graceful exit on conditions: `if not order_uuid: return`

## What is Tested

**API Layer (`bithumb/client.py`):**
- Public API endpoints: `get_ticker()`, `get_all_coins()`, `get_orderbook()`
- Private API endpoints: `get_accounts()`, `market_buy()`, `market_sell()`, `cancel_order()`
- Authentication: JWT token generation, HMAC-SHA512 signing
- Error handling: `raise_for_status()` and status code validation

**Database Layer (`bithumb/db.py`):**
- Schema initialization: `init_db()` creates tables if missing
- Data insertion: `log_trade()`, `log_signal()`, `log_pump()`
- Data updates: `update_pump_path()`, `update_signal_outcome()`
- Data retrieval: `get_trades()`, `get_stats()`
- Tested via manual inspection of `data/trades.db`

**Indicators (`bithumb/indicators.py`):**
- Calculation functions: `calc_rsi()`, `calc_bb_pct()`, `calc_macd_bull()`
- Data extraction: `_closes()` reverses candles to chronological order
- Safe execution: `snapshot()` returns default dict on any exception
- Tested implicitly in bot logs when indicator values are printed

**Notification System (`bithumb/notify.py`):**
- Telegram message sending: `send()` returns bool success
- Quiet hours enforcement: `_is_quiet_hours()` bypassed with `force=True`
- Config caching: `_get_cfg()` loads once globally
- Specialized messages: `notify_buy()`, `notify_sell()`, `notify_daily()`, `notify_error()`
- Tested via manual Telegram message receipt

## Mocking

**Framework:** None (no mocking library imported)

**Current Approach:**
- No unit tests with mocks
- Manual/integration tests only with real API calls
- Database tests against real SQLite file

**What to Mock (if adding unit tests):**
- `requests` calls (replace with mock responses)
- Telegram API calls (avoid spamming chat)
- Database file path (use in-memory SQLite or temp files)
- Time-based operations (frozen time for timezone tests)

**What NOT to Mock:**
- Client initialization (simple object creation)
- Indicator calculations (pure functions, no side effects)
- Database schema (easy to recreate fresh)

## Fixtures and Factories

**Test Data:**

No formal fixtures exist. Manual test data:
- Test order: `market = "KRW-BTC", buy_krw = 5000` (small real trades)
- Test data used: current real balances, live market prices
- Database queries use live `data/trades.db`

**Location:**
- No fixtures directory
- Data loaded from `config.yaml` (real credentials, .gitignored)
- Test scripts accept current state (don't setup synthetic state)

## Coverage

**Requirements:** None enforced (no coverage tool configured)

**Current State:**
- Core modules (`client.py`, `db.py`, `indicators.py`, `notify.py`) are used in production
- Coverage is implicit through bot operation
- Scripts thoroughly exercise APIs through real trading

## Test Types

**Unit Tests:**
- Not present. Pure functions (`calc_rsi()`, `calc_bb_pct()`, `_closes()`) are untested in isolation
- Indicator calculations validated only through live bot execution
- Type hints provide some compile-time safety but no runtime assertion

**Integration Tests:**
- `test_order.py`: Full buy-and-sell cycle on live exchange (real money risk)
- `_api_test.py`: Connectivity check with real API
- `_status_check.py`: Queries real balances and market data
- Manual scripts validate complete workflows end-to-end

**E2E Tests:**
- `alt_monitor.py` and `auto_trade.py` are continuous E2E tests (running 24/7)
- Real trading validates complete signal-to-execution flow
- Market data ingestion tested by bot operation
- All components integrated and tested together in production

## Common Patterns

**Async Testing:**
- No async/await in codebase (synchronous I/O only)
- Polling used instead: `for i in range(timeout): ... time.sleep(1)`
- Websocket client (`websocket-client`) used but no async framework

**Timeout Testing:**
```python
# Wait for filled order with timeout
for _ in range(10):
    time.sleep(1)
    order = client.get_order(order_uuid)
    state = order.get("state")
    if state == "done":
        vol = float(order.get("executed_volume", 0))
        break
else:
    print("Timeout — cancel order")
    client.cancel_order(order_uuid)
```

**Error Testing:**
```python
# API error handling
resp.raise_for_status()  # Raises on HTTP error
data = resp.json()
if data["status"] != "0000":
    raise RuntimeError(f"get_ticker error: {data}")
```

**Graceful Fallback:**
```python
# Indicators never raise
def snapshot(client, market: str) -> dict:
    result = {"rsi": None, "bb_pct": None, "macd_bull": None}
    try:
        candles = client.get_candles(market, unit=1, count=35)
        result["rsi"] = calc_rsi(candles)
        # ... more indicators
    except Exception:
        pass  # Return defaults on any error
    return result
```

**Validation Before Action:**
```python
# Check balance before trading
krw = next((float(a["balance"]) for a in accounts if a["currency"] == "KRW"), 0)
if krw < buy_krw:
    print(f"잔고 부족: {buy_krw:,}원 필요")
    return
```

## Testing Gaps

**Untested Areas:**
- Unit tests for indicator calculations (no isolation from candle data)
- Telegram notification delivery (no mock, real API calls only)
- Database schema migrations (manual ALTER TABLE error handling untested)
- Configuration validation (no schema validation for `config.yaml`)
- Edge cases in signal generation (only live trading validates)

**Risk Areas:**
- `bithumb/client.py`: Authentication changes could break silently (no unit tests)
- `bithumb/indicators.py`: Algorithm changes undetected (only production shows impact)
- `bithumb/db.py`: Column additions risky (except clause swallows migration errors)
- Timestamp handling: KST timezone conversions only tested in production

**Priority Improvements:**
1. Unit tests for `indicators.py` (pure functions, high value)
2. Mock-based tests for `client.py` (authentication critical)
3. Snapshot tests for database queries (verify schema assumptions)
4. Configuration validation schema (prevent config errors)

## Manual Testing Approach

Current testing is manual and exploratory:
- Run `scripts/test_order.py` to validate full order cycle
- Run `scripts/_api_test.py` for quick connectivity check
- Run `scripts/check_status.py` to inspect current state
- Review `logs/alt_monitor.log` for execution traces
- Query `data/trades.db` for historical performance

No continuous integration, no automated regression testing.

---

*Testing analysis: 2026-05-19*
