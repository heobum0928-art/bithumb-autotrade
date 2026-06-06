---
phase: "04-live-trading"
plan: "02"
subsystem: "vb_trader"
tags: [volatility-breakout, websocket, dry-run, state-machine]
dependency_graph:
  requires: ["04-01"]
  provides: ["vb_trader.run()", "PriceTracker", "calc_vb_target", "_do_sell_dry"]
  affects: ["scripts/vb_trader.py"]
tech_stack:
  added: []
  patterns: ["PriceTracker WS auto-reconnect", "entered_coins dedup set", "midnight_cleared flag"]
key_files:
  created: []
  modified:
    - scripts/vb_trader.py
decisions:
  - "PriceTracker simplified from alt_monitor.py — only start_ws/stop_ws/get_latest_price retained"
  - "_parse_exchange_ts inlined (not imported from alt_monitor) to keep vb_trader self-contained"
  - "run() takes no args — creates BithumbClient internally (plan spec)"
  - "_do_sell_live() is a stub that logs error — live trading out of scope for this plan"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-06"
  tasks_completed: 2
  files_modified: 1
---

# Phase 04 Plan 02: VB Trader Main Loop Summary

**One-liner:** Full VB state machine implemented — PriceTracker WebSocket + VB target calc + dry-run entry/exit loop with midnight forced liquidation and per-day dedup.

## What Was Built

`scripts/vb_trader.py` now contains a complete, runnable volatility breakout bot loop (dry-run mode):

| Component | Location | Purpose |
|-----------|----------|---------|
| `PriceTracker` | line 137 | WebSocket real-time price subscriber with auto-reconnect |
| `_parse_exchange_ts()` | line 113 | Bithumb WS timestamp parser (inlined from alt_monitor.py) |
| `calc_vb_target()` | line 220 | Returns `(vb_target, today_open)` using `today_open + prev_range * K` |
| `_record()` | line 243 | Writes trade to DB with `[VB-DRY]` exit_reason tag |
| `_do_sell_dry()` | line 259 | Computes PnL, logs, calls `_record()`, sends Telegram alert |
| `_do_sell_live()` | line 277 | Stub — logs error, unimplemented |
| `run()` | line 281 | Full state machine main loop |
| `main()` | line 430 | Startup banner + calls `run()` |

### run() Loop Behaviour

1. **Init:** Build volume whitelist → calc VB targets for each coin → start WebSocket
2. **Date rollover:** Detect `date.today() != today` → clear `entered_coins`, re-fetch whitelist + VB targets, re-subscribe WebSocket
3. **Midnight forced liquidation:** `hour==0 and minute==0 and not midnight_cleared` → liquidate any open position → set `midnight_cleared=True`
4. **No position:** Scan `vb_targets`, skip `entered_coins`, check `current >= target` → mock entry, `entered_coins.add(coin)`, `break` (1 coin 1 position)
5. **Has position:** Get latest price → update `highest` → check `pnl_pct >= VB_TP (+3%)` or `pnl_pct <= VB_SL (-2%)` → `_do_sell_dry()` → `save_pos(None)`

## Commits

| Hash | Message |
|------|---------|
| 5ff0c1e | feat(04-02): add VB trader main loop (PriceTracker + entry/exit) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing functionality] `_parse_exchange_ts` inlined**
- **Found during:** Task 1
- **Issue:** `PriceTracker.start_ws.on_message` in alt_monitor.py calls `_parse_exchange_ts()` but this is a module-level function in alt_monitor, not importable. VB's simplified PriceTracker doesn't need the exchange timestamp tracking at all — it only needs `get_latest_price()`.
- **Fix:** Inlined `_parse_exchange_ts()` in vb_trader.py for completeness, but removed the `_ex_ts` dict and related tracking from `PriceTracker` since VB strategy only needs the price. `on_message` simplified accordingly.
- **Files modified:** scripts/vb_trader.py

**2. [Rule 1 - Plan spec] `run()` signature changed from `run(client)` to `run()`**
- **Found during:** Task 2
- **Issue:** The 04-01 stub had `run(client: BithumbClient)` but the plan's PLAN.md action code specifies `run()` with no args (creates its own client internally), matching the RESEARCH.md loop structure.
- **Fix:** Replaced stub with `run()` (no args). `main()` updated accordingly — no longer passes `client`.
- **Files modified:** scripts/vb_trader.py

## Known Stubs

| Stub | File | Line | Reason |
|------|------|------|--------|
| `_do_sell_live()` | scripts/vb_trader.py | 277 | Live trading out of scope for 04-02. Logs error if called. Resolved in future plan (04-03 or later). |

## Self-Check

- [x] `scripts/vb_trader.py` exists and has syntax OK
- [x] Commit `5ff0c1e` exists in git log
- [x] All Task 1 acceptance criteria pass (PriceTracker, calc_vb_target, _record, _do_sell_dry present)
- [x] All Task 2 acceptance criteria pass (run, entered_coins x4, midnight_cleared x5, save_pos(None) x3, break after entry)
- [ ] Task 3 (checkpoint:human-verify) — awaiting user code review

## Self-Check: PASSED
