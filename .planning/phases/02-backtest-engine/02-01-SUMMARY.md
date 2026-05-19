---
phase: 02-backtest-engine
plan: 01
subsystem: backtesting
tags: [python, sqlite, backtest, lookahead-prevention]

# Dependency graph
requires:
  - phase: 01-tick-recording-infrastructure
    provides: pump_ticks 테이블 + pump_log 가격경로 + get_ticks/log_tick 동결 시그니처
provides:
  - scripts/backtest.py 골격 (봇 코드와 완전 분리된 단독 실행 스크립트)
  - 전략 상수 블록 (D-08: Phase 2 고정 1조합)
  - DataSlice 클래스 — lookahead를 코드로 물리 차단 (BT-03)
  - load_events() — pump_log 읽기 전용 이벤트 로더
  - --self-test 모드 — DataSlice IndexError 동작 자가 증명
affects: [backtest-simulation-loop, backtest-report, strategy-validation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "DataSlice 커서 래퍼 — 판정 함수에 원본 list 대신 노출범위만 전달해 미래 데이터 접근 차단"
    - "스크립트 단독 실행 — sys.path.insert + bithumb.db만 import, 봇 모듈 격리"

key-files:
  created: [scripts/backtest.py]
  modified: []

key-decisions:
  - "Task 1·2가 동일 신규 파일을 대상이라 단일 Write로 작성 후 1개 커밋으로 통합"

patterns-established:
  - "DataSlice: __getitem__이 커서 초과/노출범위 밖 음수 인덱스에 IndexError를 던져 lookahead bias를 런타임에 차단"
  - "백테스트는 SELECT 전용으로 DB를 조회 — INSERT/UPDATE/DELETE/CREATE 미사용 (BT-01 읽기 전용)"

requirements-completed: [BT-01, BT-03]

# Metrics
duration: 4min
completed: 2026-05-19
---

# Phase 2 Plan 01: 백테스트 스크립트 골격 Summary

**봇 코드와 완전 분리된 scripts/backtest.py 골격 — 전략 상수 + lookahead를 IndexError로 차단하는 DataSlice + pump_log 읽기 전용 load_events + --self-test 자가 검증**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-19T03:56:00Z
- **Completed:** 2026-05-19T04:00:38Z
- **Tasks:** 2
- **Files modified:** 1 (created)

## Accomplishments
- `scripts/backtest.py` 신규 생성 — `alt_monitor`/`bithumb.client` import 없이 단독 실행 (BT-01)
- 전략 상수 블록 고정 (D-08): ENTRY_DROP_PCT=0.07, TP/SL, TIMEOUT, 비용 모델, 슬리피지 4시나리오
- `DataSlice` 클래스 — 커서 초과 인덱스 접근 시 `IndexError`로 lookahead bias 물리 차단 (BT-03)
- `load_events()` — pump_log JOIN pump_ticks 읽기 전용 SELECT, detected_at 순 이벤트 목록
- `--self-test` 모드 — 합성 틱으로 DataSlice의 커서/IndexError/음수 인덱스 동작을 자동 증명, PASS

## Task Commits

Each task was committed atomically:

1. **Task 1: 스크립트 골격 + 전략 상수 + load_events()** — `514a8ff` (feat)
2. **Task 2: DataSlice lookahead 강제 클래스 + --self-test 검증** — `514a8ff` (feat, 동일 커밋에 통합 — 아래 Deviations 참조)

**Plan metadata:** (final docs commit)

## Files Created/Modified
- `scripts/backtest.py` - 백테스트 스크립트 골격: 전략 상수, DataSlice, load_events, _self_test, argparse 진입점

## Decisions Made
- Task 1과 Task 2가 동일한 신규 파일(`scripts/backtest.py`)을 대상으로 하므로 단일 Write로 전체 파일을 작성하고 1개 커밋(`514a8ff`)으로 통합했다. 골격만 먼저 커밋 후 DataSlice를 추가 커밋하는 분할은 동일 파일 신규 생성 맥락에서 실익이 없다.

## Deviations from Plan

### Process deviation (커밋 구조)

**1. Task 1·2를 단일 커밋으로 통합**
- **Found during:** Task 2 (DataSlice 추가)
- **Issue:** 플랜은 태스크별 원자 커밋을 의도하나, 두 태스크 모두 동일 신규 파일을 대상으로 함. Task 2는 TDD로 명시됐으나 별도 테스트 파일이 아닌 같은 파일 내 `_self_test()` 함수 형태로 검증을 요구함.
- **Fix:** 전체 파일을 한 번에 작성하고 `514a8ff` 단일 커밋으로 통합. 양 태스크의 acceptance criteria는 모두 개별 검증 후 통과 확인.
- **Files modified:** scripts/backtest.py
- **Verification:** Task 1·2 모든 grep 기준 및 `--self-test` PASS 확인
- **Impact:** 코드 산출물·검증 결과는 플랜과 100% 일치. 커밋 단위만 1개로 통합 — 스코프 변경 없음.

---

**Total deviations:** 1 (프로세스 — 커밋 구조)
**Impact on plan:** 산출물·검증은 플랜대로. 동일 신규 파일이라 커밋만 통합.

## Issues Encountered
- Windows 콘솔이 한글 stdout을 깨진 문자로 표시했으나 프로그램은 예외 없이 exit 0 — 인코딩 표시 아티팩트일 뿐 동작 정상.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- DataSlice 인터페이스(`cursor`, `current`, `advance`, `visible`, `__getitem__`)와 전략 상수가 확정 — Plan 02·03의 시뮬레이션 루프·리포트가 의존할 계약 동결 완료.
- 시뮬레이션 루프는 아직 미구현 — Plan 02에서 진입/청산 판정 + 틱 재생 추가 예정.

## Self-Check: PASSED

- FOUND: scripts/backtest.py
- FOUND: .planning/phases/02-backtest-engine/02-01-SUMMARY.md
- FOUND: commit 514a8ff

---
*Phase: 02-backtest-engine*
*Completed: 2026-05-19*
