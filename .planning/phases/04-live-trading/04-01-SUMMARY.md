---
phase: "04-live-trading"
plan: "01"
subsystem: "vb_trader"
tags: ["volatility-breakout", "bithumb-api", "skeleton", "single-instance"]
dependency_graph:
  requires: []
  provides: ["BithumbClient.get_daily_candles", "scripts/vb_trader.py skeleton"]
  affects: ["scripts/vb_trader.py (04-02 extends run())", "scripts/watchdog.py (04-03 adds vb_trader entry)"]
tech_stack:
  added: []
  patterns: ["TCP port single-instance lock (47220)", "argparse --dry-run flag", "volume whitelist from get_ticker(ALL)"]
key_files:
  created:
    - scripts/vb_trader.py
  modified:
    - bithumb/client.py
key_decisions:
  - "get_daily_candles uses /v1/candles/days (not unit=1440 — unsupported by Bithumb API)"
  - "Port 47220 for vb_trader single-instance (alt_monitor uses 47219)"
  - "run() is a stub in this plan — full VB loop implemented in 04-02"
  - "_LOG_TAG = 'VB-DRY' when --dry-run, 'VB' when --live"
metrics:
  duration: "1m 39s"
  completed: "2026-06-06"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 2
---

# Phase 04 Plan 01: VB Trader Skeleton Summary

**One-liner:** `get_daily_candles()` via `/v1/candles/days` added to BithumbClient; `vb_trader.py` skeleton with port-47220 single-instance lock, VB constants, volume whitelist, and pos I/O created.

---

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | BithumbClient.get_daily_candles() | c80f145 | bithumb/client.py |
| 2 | vb_trader.py skeleton | 18a5524 | scripts/vb_trader.py (created) |

---

## What Was Built

### Task 1 — `BithumbClient.get_daily_candles()`

Added to `bithumb/client.py` after `get_candles()`. Uses the `/v1/candles/days` public endpoint (no JWT auth). Returns newest-first list where:
- `idx[0]` = today's candle (`opening_price` = 당일 시가)
- `idx[1]` = yesterday's candle (`high_price`, `low_price` = 전일 고저폭 계산)

Docstring explicitly marks `get_candles(unit=1440)` as anti-pattern (빗썸 API 미지원).

### Task 2 — `scripts/vb_trader.py` Skeleton

Full skeleton created with all components needed by 04-02:

- **Single-instance guard:** TCP bind on `127.0.0.1:47220` — no conflict with alt_monitor (47219)
- **Strategy constants:** `K=0.5`, `VB_TP=0.03`, `VB_SL=-0.02`, `VB_ENTRY_KRW=100_000`, `MIN_DAILY_VOLUME_KRW=20_000_000_000`
- **Logging:** `%(asctime)s [VB][%(levelname)s] %(message)s` to stdout + `logs/vb_trader.log`
- **--dry-run / --live flags:** `_DRY_RUN: bool`, `_LOG_TAG: str` set to `"VB-DRY"` or `"VB"` accordingly
- **`_build_volume_whitelist(client)`:** Calls `get_ticker("ALL")`, filters `acc_trade_value_24H >= 20_000_000_000`, logs `[볼륨필터] N개 코인 (20억+)`
- **`load_pos()` / `save_pos()`:** JSON I/O to `data/vb_pos.json`
- **`run(client)`:** Stub — logs `[VB] run() stub — 04-02에서 구현` then `while True: sleep(60)`
- **`main()`:** Logs start, instantiates `BithumbClient`, calls whitelist builder, then `run()`
- **`__main__` block:** `init_db()` then `main()`

---

## Deviations from Plan

None — plan executed exactly as written.

---

## Known Stubs

| File | Location | Description | Resolved in |
|------|----------|-------------|-------------|
| scripts/vb_trader.py | `run()` function | Stub body — no VB entry/exit logic | 04-02-PLAN.md |

The stub is intentional per plan spec: `run()` in this plan is a stub only — full logic in 04-02.

---

## Self-Check: PASSED
