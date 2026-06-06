---
phase: "04-live-trading"
plan: "03"
subsystem: "watchdog / strategy-docs"
tags: ["vb-trader", "watchdog", "dry-run", "strategy-docs"]
dependency_graph:
  requires: ["04-01", "04-02"]
  provides: ["vb_trader watchdog integration", "VB strategy parameter history"]
  affects: ["scripts/watchdog.py", "docs/STRATEGY.md", "docs/sessions/2026-06-06.md"]
tech_stack:
  added: []
  patterns: ["watchdog BOTS/EXTRA_ARGS/KILL_KEYWORDS pattern", "CLAUDE.md strategy doc rule"]
key_files:
  modified:
    - scripts/watchdog.py
    - docs/STRATEGY.md
    - docs/sessions/2026-06-06.md
decisions:
  - "vb_trader runs with --dry-run via EXTRA_ARGS, KILL_KEYWORDS added for process identification"
  - "STRATEGY.md VB section kept separate from OVERSOLD section — different freeze rules apply"
metrics:
  duration: "~10min"
  completed: "2026-06-06T03:16:12Z"
  tasks_completed: 3
  files_modified: 3
---

# Phase 04 Plan 03: Watchdog Integration + Strategy Docs Summary

vb_trader watchdog 통합 및 STRATEGY.md VB 전략 파라미터 이력 기록 — CLAUDE.md 기록 규칙 완전 준수.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | watchdog.py에 vb_trader 항목 추가 | 39f462c | scripts/watchdog.py |
| 2 | docs/STRATEGY.md 파라미터 변경 이력 기록 | 39f462c | docs/STRATEGY.md |
| 3 | docs/sessions/2026-06-06.md 기록 + git commit | 39f462c | docs/sessions/2026-06-06.md |

## What Was Done

### Task 1: watchdog.py vb_trader 통합

`scripts/watchdog.py`의 세 dict에 각각 vb_trader 항목 추가:

- `BOTS`: `"vb_trader": ROOT / "scripts" / "vb_trader.py"` — watchdog이 프로세스 생존 감시
- `EXTRA_ARGS`: `"vb_trader": ["--dry-run"]` — 시작 시 모의투자 모드 강제
- `KILL_KEYWORDS`: `"vb_trader": "--dry-run"` — 동일 스크립트를 dry/live 인스턴스로 구별

결과: watchdog 시작 시 vb_trader.py가 `--dry-run` 인자로 자동 기동되고, 크래시 후 30초 이내 자동 재시작됨.

### Task 2: STRATEGY.md VB 전략 파라미터 이력

CLAUDE.md 기록 규칙("전략 관련 코드 수정 시 STRATEGY.md 업데이트") 준수.

파일 끝에 `## VB 전략 파라미터 (scripts/vb_trader.py)` 섹션 신규 추가:
- K=0.5, TP=+3%, SL=-2%, 진입금액 100,000원, 볼륨 기준 20억+ KRW
- 자정 강제청산 00:00 KST, 실행모드 --dry-run
- OVERSOLD 파라미터 동결(~2026-06-22)과의 관계 명시 — 완전 별개 전략

### Task 3: Session 파일 기록 및 커밋

`docs/sessions/2026-06-06.md`에 Phase 04 전체 작업 내용 append.
CLAUDE.md 기록 규칙 순서 완전 준수: 코드 수정(04-01, 04-02) → STRATEGY.md 업데이트 → session 기록 → git commit.

## Verification Results

```
grep -c "vb_trader" scripts/watchdog.py  → 3  (BOTS + EXTRA_ARGS + KILL_KEYWORDS)
grep -c "vb_trader" docs/STRATEGY.md    → 12
grep -c "2026-06-06" docs/STRATEGY.md   → 9
syntax check watchdog.py                → OK
git log --oneline -1                    → 39f462c feat(vb-trader): integrate ...
```

## Deviations from Plan

None — plan executed exactly as written.

`bithumb/client.py` and `scripts/vb_trader.py` referenced in the plan's final commit instruction were already committed in 04-01 (c80f145, 18a5524) and 04-02 (5ff0c1e). Only the 04-03 target files were staged and committed.

## Phase 04 Integration Status

All three plans complete:
- 04-01: `BithumbClient.get_daily_candles()` + `vb_trader.py` skeleton
- 04-02: `vb_trader.py` main loop (PriceTracker + entry/exit + midnight close)
- 04-03: watchdog integration + STRATEGY.md documentation

## Known Stubs

None — this plan is documentation/integration only. No new trading logic introduced.

## Self-Check: PASSED

- `scripts/watchdog.py` — exists and modified (git: 39f462c)
- `docs/STRATEGY.md` — exists and modified (git: 39f462c)
- `docs/sessions/2026-06-06.md` — exists and committed (git: 39f462c)
- commit 39f462c — verified present in git log
