---
phase: 02-backtest-engine
verified: 2026-05-19T05:00:00Z
status: passed
score: 5/5 must-haves verified
---

# Phase 2: Backtest Engine Verification Report

**Phase Goal:** 수집된 틱 데이터를 재생해 전략을 실거래 없이 시뮬레이션하고 EV·승률·MDD를 산출하는 독립 스크립트가 존재한다 (An independent script exists that replays collected tick data to simulate the strategy without real trading and computes EV, win rate, and MDD).
**Verified:** 2026-05-19T05:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth | Status | Evidence |
| --- | ----- | ------ | -------- |
| 1   | scripts/backtest.py가 봇 프로세스 없이 단독 실행된다 | ✓ VERIFIED | `python scripts/backtest.py --db data/trades.db` → exit 0, no exceptions. `python scripts/backtest.py --self-test` → "self-test PASS" exit 0. No `alt_monitor`/`bithumb.client` imports (grep 0 matches) |
| 2   | DataSlice가 lookahead를 코드로 차단한다 (미래 인덱스 접근 시 IndexError) | ✓ VERIFIED | `_self_test()` PASS — verifies `[6]`/`[10]` (cursor 5 exceeded) raise IndexError, `[-1]` returns current tick. `raise IndexError` present in `__getitem__` |
| 3   | simulate_event()가 틱을 시간순 재생해 진입/TP/SL/시간초과 청산을 시뮬레이션한다 | ✓ VERIFIED | Behavioral spot-checks: TP case → reason "익절" pnl +0.065; SL case → reason "손절" pnl -0.055; MIN_TICKS skip → None; gap tick (gap_before=1) TP correctly skipped, exit deferred to next normal tick |
| 4   | 수수료(왕복 0.5%)·슬리피지가 손익 계산에 반영된다 | ✓ VERIFIED | `_apply_slip(1000,0.01,'buy')==1010`, `_apply_slip(1000,0.01,'sell')==990`, `_net_pnl_pct(100,100)==-0.005` (round-trip fee). Slippage test: entry 100→101 (buy +1%). 4 slippage scenarios (0/0.5/1/2%) wired into run_backtest |
| 5   | 리포트에 승률·EV·MDD·거래수·95% CI가 출력되고 CSV가 생성된다 | ✓ VERIFIED | Synthetic-data render shows 4-row slippage table with 거래수/승률/EV/95% CI/MDD columns + "수수료(왕복): 0.50%" header + ⚠표본부족 warning. `data/backtest_trades.csv` created with correct utf-8-sig header. 6/6 metrics regression tests pass |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `scripts/backtest.py` | 골격 + DataSlice + load_events + simulate_event + 지표 + 리포트 + main (408 lines) | ✓ VERIFIED | All functions present: load_events, DataSlice, _apply_slip, _net_pnl_pct, _close, simulate_event, ev_ci, max_drawdown, compute_metrics, _gap_ratio, _est_ratio, run_backtest, print_report, write_csv, _self_test, main. Single self-contained script |
| `scripts/test_backtest_metrics.py` | 지표 함수 회귀 테스트 | ✓ VERIFIED | 6/6 tests pass (ev_ci, max_drawdown, compute_metrics boundary cases) |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| backtest.py load_events | bithumb.db (sqlite3 SELECT) | 읽기 전용 쿼리 | ✓ WIRED | `load_events` runs SELECT-only query joining pump_log + pump_ticks. No INSERT/UPDATE/DELETE/CREATE (grep 0 — "DROP" false-positive matched `ENTRY_DROP_PCT`) |
| backtest.py DataSlice.__getitem__ | IndexError | 커서 초과 인덱스 접근 시 raise | ✓ WIRED | `raise IndexError` confirmed, exercised by _self_test |
| simulate_event | DataSlice | 재생 루프가 advance로 커서 전진 | ✓ WIRED | `DataSlice(ticks)` instantiated, `sl.advance()` in replay loop |
| simulate_event | 체결가 (cursor+1) | 다음 틱 price로 진입/청산 체결 | ✓ WIRED | `ticks[sl.cursor + 1]["price"]` used for both entry fill and exit fill (D-04/D-07) |
| run_backtest | simulate_event | 전 이벤트 x 4 슬리피지 시나리오 순회 | ✓ WIRED | Nested loop over `events` x `SLIPPAGE_SCENARIOS` calling simulate_event |
| main | data/backtest_trades.csv | write_csv로 거래 상세 출력 | ✓ WIRED | `csv.DictWriter` with utf-8-sig encoding; file created at runtime, verified header |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| run_backtest / print_report | events / ticks | `load_events(db)` → `get_ticks(id)` (SQLite) | DB has 350 pump_log rows but 0 pump_ticks rows | ⚠️ STATIC (expected) — tick accumulation period not elapsed; engine verified against synthetic ticks instead |

**Note:** The empty `pump_ticks` table is not a defect. Per PROJECT.md the 2-3 week tick accumulation runs in parallel with this phase. The engine itself flows data correctly — verified by synthetic-tick spot-checks (TP/SL/gap/slippage all produce correct Trade dicts) and the defensive "이벤트 없음" path against the live empty DB. The data path will produce real results once Phase 1 accumulates ticks.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Standalone execution | `python scripts/backtest.py --db data/trades.db` | exit 0, "이벤트 없음" message, no exception | ✓ PASS |
| Lookahead block proof | `python scripts/backtest.py --self-test` | "self-test PASS", exit 0 | ✓ PASS |
| Cost helpers | inline asserts on _apply_slip / _net_pnl_pct | "cost helpers OK" | ✓ PASS |
| TP/SL/gap/slippage simulation | synthetic-tick simulate_event | TP +0.065 / SL -0.055 / gap skip / slip applied | ✓ PASS |
| Metrics regression suite | `python scripts/test_backtest_metrics.py` | 6/6 passed | ✓ PASS |
| Report rendering | synthetic-data print_report | 4-row slippage table + headers + warning | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| BT-01 | 02-01 | 봇 코드와 완전 분리된 오프라인 스크립트 (DB 읽기 전용) | ✓ SATISFIED | No bot imports; SELECT-only DB access; runs standalone |
| BT-02 | 02-02 | 틱 시간순 재생 + 진입 평가 + 가상 청산 시뮬레이션 | ✓ SATISFIED | simulate_event replay loop verified TP/SL/timeout |
| BT-03 | 02-01 | 진입 판정에 lookahead 미사용 | ✓ SATISFIED | DataSlice raises IndexError on future index; cursor+1 next-tick fill |
| BT-04 | 02-02 | 수수료 왕복 0.5% + 슬리피지를 상수로 손익에 반영 | ✓ SATISFIED | ROUND_TRIP_FEE=0.005, SLIPPAGE_SCENARIOS=(0,0.005,0.01,0.02), helpers verified |
| BT-05 | 02-03 | 승률·EV·MDD·거래수 출력 | ✓ SATISFIED | print_report 4-row table + 95% CI; 6/6 metrics tests pass |

All 5 requirement IDs declared across plans and present in REQUIREMENTS.md. No orphaned requirements.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| — | — | None | — | No TODO/FIXME/placeholder/stub patterns. No empty-return stubs. No bot-code imports. No DB write queries. |

### Human Verification Required

None required for engine correctness. One operational note (not a gap):

- **Live backtest with real ticks** — Once Phase 1 accumulates 2-3 weeks of `pump_ticks` data, re-run `python scripts/backtest.py` to confirm the engine produces meaningful EV/CI/MDD on real data. Currently the engine is verified against synthetic ticks, which is the intended Phase 2 strategy.

### Gaps Summary

No gaps. All 5 observable truths verified, both artifacts substantive and wired, all 6 key links connected, all 5 requirement IDs satisfied, no anti-patterns. The independent backtest script exists, runs without the bot, replays ticks with code-enforced lookahead protection, applies fees/slippage, and outputs win rate / EV / MDD / trade count / 95% CI plus a CSV.

The empty `pump_ticks` table produces an "이벤트 없음" report rather than populated metrics — this is expected per PROJECT.md (tick accumulation runs in parallel) and the engine was correctly verified against synthetic data by executors. Not a defect.

One minor observation: the verify command in 02-02-PLAN.md Task 2 used a TP test case (`tp=[100,110,101,116,117]`) that, under correct D-04 next-tick fill semantics, fills entry at 116 and never reaches +5%, yielding a "시간초과" exit instead of "익절". This was a flaw in the plan's test fixture, not the implementation — re-tested with a correct fixture, TP logic produces "익절" with +6.5% pnl as designed.

---

_Verified: 2026-05-19T05:00:00Z_
_Verifier: Claude (gsd-verifier)_
