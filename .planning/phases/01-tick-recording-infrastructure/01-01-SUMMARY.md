---
phase: 01-tick-recording-infrastructure
plan: 01
subsystem: database
tags: [sqlite, schema, tick-data, time-series]

# Dependency graph
requires: []
provides:
  - "pump_ticks SQLite 테이블 — 펌핑 이벤트 초 단위 가격 틱 영속화"
  - "log_tick() 함수 — 틱 1행 INSERT, exchange_ts None 폴백"
  - "get_ticks(pump_id) 함수 — seq 순 정렬 틱 조회 (Phase 2 백테스트 계약)"
affects: [tick-recording, backtest-engine, strategy-validation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "시계열 자식 테이블 — pump_log.id를 논리적 FK로 참조, idx 인덱스로 JOIN 성능 확보"
    - "exchange_ts/recv_ts 분리 + ts_estimated 플래그 — 거래소 시각 추정 여부 추적"

key-files:
  created:
    - scripts/_tick_test.py
  modified:
    - bithumb/db.py

key-decisions:
  - "모든 timestamp는 REAL(epoch 초), seq/플래그는 INTEGER"
  - "seq는 절대 순번(0,1,2,...) — 시간 공백은 recv_ts/exchange_ts와 gap_before로 표현"
  - "SQLite FOREIGN KEY 미강제 — pump_id는 논리적 참조 + idx 인덱스만, PRAGMA 추가 안 함"

patterns-established:
  - "Phase 2 의존 계약: log_tick/get_ticks 시그니처 동결 — 백테스트 엔진이 import"

requirements-completed: [REC-02, REC-03, REC-04]

# Metrics
duration: 1min
completed: 2026-05-18
---

# Phase 1 Plan 1: Tick Recording Schema Summary

**pump_ticks SQLite 테이블과 log_tick/get_ticks 함수로 펌핑 이벤트 초 단위 가격 틱을 영속화 — exchange_ts/recv_ts 분리, gap_before 플래그, Phase 2 백테스트 의존 계약 동결**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-05-18T23:06:50Z
- **Completed:** 2026-05-18T23:07:58Z
- **Tasks:** 2
- **Files modified:** 2 (1 modified, 1 created)

## Accomplishments
- `pump_ticks` 테이블 추가 — 10개 컬럼(id, pump_id, seq, exchange_ts, recv_ts, price, acc_value, volume_power, gap_before, ts_estimated) + `idx_pump_ticks_pump_id` 인덱스
- `log_tick()` — 틱 1행 INSERT, exchange_ts가 None이면 recv_ts 복사 + ts_estimated=1 강제 (REC-03)
- `get_ticks(pump_id)` — seq 오름차순 정렬 조회, 존재하지 않는 pump_id는 빈 리스트 (Phase 2 백테스트 계약)
- `pump_log` 스키마 및 기존 함수 일절 미변경 — 기존 분석 스크립트 무중단

## Task Commits

Each task was committed atomically:

1. **Task 1: pump_ticks 테이블 스키마 추가** - `0248d1a` (feat)
2. **Task 2: log_tick/get_ticks 함수 추가** - `652faf6` (test, RED) → `58e6f7e` (feat, GREEN)

**Plan metadata:** (this commit) (docs: complete plan)

## Files Created/Modified
- `bithumb/db.py` - CREATE_SQL에 pump_ticks 테이블+인덱스 추가, log_tick/get_ticks 모듈 함수 추가
- `scripts/_tick_test.py` - log_tick/get_ticks behavior Test 1~3 검증용 일회성 테스트 스크립트

## Decisions Made
- 모든 timestamp는 REAL(epoch 초), seq/플래그는 INTEGER — 계획서 Claude's Discretion 확정 사항 따름
- seq는 절대 순번 — 시간 공백은 gap_before 플래그로 표현 (RESEARCH Pitfall 5 권장)
- log_tick/get_ticks를 `update_pump_path` 다음 위치(`update_signal_outcome` 앞)에 배치 — 계획서가 지정한 "update_pump_path 바로 아래"와 일치

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None. RED 테스트가 ImportError로 정확히 실패 후, GREEN 구현으로 Test 1~3 전부 통과(exit 0).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- 틱 저장 계층(스키마 + 쓰기/읽기 함수) 완성 — Plan 01-02(WS 수집기 통합)가 log_tick을 호출할 준비 완료
- log_tick/get_ticks 시그니처가 Phase 2 백테스트 의존 계약으로 동결됨
- 블로커 없음

## Self-Check: PASSED

---
*Phase: 01-tick-recording-infrastructure*
*Completed: 2026-05-18*
