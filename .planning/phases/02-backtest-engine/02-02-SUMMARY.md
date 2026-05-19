---
phase: 02-backtest-engine
plan: 02
subsystem: backtesting
tags: [python, backtest, simulation, lookahead-prevention, fees, slippage]

# Dependency graph
requires:
  - phase: 02-backtest-engine
    plan: 01
    provides: scripts/backtest.py 골격 + 전략 상수 + DataSlice 커서 래퍼
provides:
  - _apply_slip / _net_pnl_pct — 슬리피지·수수료 비용 모델 (BT-04)
  - simulate_event() — 한 펌핑 이벤트의 틱 재생 시뮬레이션 (BT-02)
  - _close() — 청산 Trade dict 생성 내부 헬퍼
affects: [backtest-report, strategy-validation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "상태 머신 틱 재생 — WAITING_ENTRY -> IN_POSITION, running_peak 누적으로 동적 고점 추적"
    - "다음 틱 체결 — 진입/청산 모두 cursor+1 price 사용해 lookahead bias 코드 차단"
    - "비용 분리 모델 — 슬리피지는 체결가에만, 수수료는 손익률에만 (이중 계산 금지)"

key-files:
  created: []
  modified: [scripts/backtest.py]

key-decisions:
  - "_close() 내부 헬퍼로 Trade dict 생성을 분리 — TP/SL/시간초과 3경로가 동일 형식 보장"

patterns-established:
  - "simulate_event: running_peak는 재생 중 max()로만 누적 — 이벤트 전체 최고가 사전 계산 안 함 (lookahead 차단)"
  - "다음 틱 없음 경계: 마지막 틱 진입 충족 시 무진입(None), 청산 보유 중이면 시간초과 청산으로 폴백"

requirements-completed: [BT-02, BT-04]

# Metrics
duration: 3min
completed: 2026-05-19
---

# Phase 2 Plan 02: 틱 재생 시뮬레이션 Summary

**scripts/backtest.py에 simulate_event 틱 재생 시뮬레이션 추가 — 눌림목 진입·TP/SL/시간초과 청산을 시간순 재생하며, 진입/청산 모두 다음 틱 체결로 lookahead를 차단하고 수수료·슬리피지를 손익에 반영**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-19T04:02:40Z
- **Completed:** 2026-05-19T04:05:50Z
- **Tasks:** 2
- **Files modified:** 1 (scripts/backtest.py)

## Accomplishments
- `_apply_slip(price, slippage, side)` — 슬리피지를 체결가에 반영 (매수 +, 매도 -). 슬리피지 0이면 변화 없음 (BT-04)
- `_net_pnl_pct(entry, exit_price)` — gross 손익률에서 왕복 수수료(ROUND_TRIP_FEE=0.5%) 차감. 무변동도 -0.5% 손실
- `simulate_event(ticks, slippage)` — 한 이벤트 틱을 시간순 재생하는 상태 머신 (WAITING_ENTRY → IN_POSITION)
  - 진입: 주행 고점 대비 -7% 눌림 충족 시 다음 틱 price로 매수 체결 (D-02/D-04)
  - 청산: TP +5% / SL -3% 돌파 시 다음 틱 체결, 틱 소진 시 마지막 틱으로 시간초과 청산 (D-06/D-07)
  - 갭 틱(gap_before=1): TP/SL 돌파 판정 건너뜀, running_peak만 갱신, 다음 정상 틱에서 재개 (D-09)
  - MIN_TICKS(4) 미만 이벤트 / 무진입 이벤트 / 마지막 틱 진입 충족 시 → None 반환
- `_close()` 내부 헬퍼 — TP/SL/시간초과 3경로가 동일 Trade dict 형식 (entry/exit ts·price, exit_reason, hold_sec, pnl_pct, slippage) 생성

## Task Commits

Each task was committed atomically:

1. **Task 1: 비용 헬퍼 (_apply_slip, _net_pnl_pct)** — `9252fe6` (feat)
2. **Task 2: simulate_event() 틱 재생 + 진입/청산 시뮬레이션** — `83b4703` (feat)

## Files Created/Modified
- `scripts/backtest.py` - `_apply_slip`, `_net_pnl_pct` 비용 헬퍼 + `_close`, `simulate_event` 시뮬레이션 함수 추가 (83라인 추가, 기존 골격·DataSlice 무변경)

## Decisions Made
- `_close()` 내부 헬퍼로 Trade dict 생성을 분리했다 (플랜의 Discretion 허용). TP/SL/시간초과 3개 청산 경로가 같은 형식·같은 비용 계산을 거치도록 강제해 형식 불일치 버그를 차단한다.

## Deviations from Plan

None - 플랜이 명세한 대로 정확히 구현. 의사코드 L206-245 및 비용 모델 L260-265를 그대로 따랐고, `_close()` 헬퍼 분리는 플랜 action 9번이 명시적으로 허용한 Discretion이다.

## Issues Encountered
- 플랜의 inline verify 명령(`python -c "..."`)에 한글 문자열 리터럴(`'익절'`, `'손절'`)이 포함되어 Windows 콘솔의 CP949 인코딩에서 깨진 바이트로 전달돼 AssertionError가 발생했다. 코드 결함이 아닌 콘솔 인코딩 아티팩트 — UTF-8 인코딩 임시 테스트 파일(`python -X utf8`)로 재실행해 MIN_TICKS 스킵·익절·손절·시간초과·무진입·갭 6개 케이스 전부 통과 확인. 임시 파일은 검증 후 삭제. `--self-test` 회귀도 PASS.

## Verification Results
- MIN_TICKS(4) 미만 → None ✓
- TP 케이스 → exit_reason="익절", pnl_pct > 0 ✓
- SL 케이스 → exit_reason="손절", pnl_pct < 0 ✓
- 시간초과 케이스 → exit_reason="시간초과", 마지막 틱 청산 ✓
- 무진입(-7% 눌림 미발생) → None ✓
- 갭 케이스 → gap_before=1 틱의 TP 돌파 무시, 다음 정상 틱에서 청산 ✓
- `python scripts/backtest.py --self-test` → PASS (Plan 01 회귀 없음)
- grep: `def simulate_event`=1, `DataSlice`=5, `gap_before`=2, `cursor + 1`=9, `ROUND_TRIP_FEE`=3, `def _apply_slip`=1, `def _net_pnl_pct`=1

## Next Phase Readiness
- `simulate_event()`가 이벤트 1건 → Trade dict|None 계약을 확정 — Plan 03 리포트가 이벤트별 결과를 집계해 슬리피지 4시나리오 × 지표(EV/CI/MDD)를 출력하는 데 필요한 입력 계약 동결.
- 슬리피지 4시나리오(`SLIPPAGE_SCENARIOS`)는 상수로 존재하나 아직 루프에서 미사용 — Plan 03이 시나리오별 `simulate_event` 호출을 배선할 예정.

## Self-Check: PASSED

- FOUND: scripts/backtest.py
- FOUND: commit 9252fe6
- FOUND: commit 83b4703
- FOUND: .planning/phases/02-backtest-engine/02-02-SUMMARY.md

---
*Phase: 02-backtest-engine*
*Completed: 2026-05-19*
