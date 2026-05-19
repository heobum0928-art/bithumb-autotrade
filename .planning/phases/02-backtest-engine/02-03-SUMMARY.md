---
phase: 02-backtest-engine
plan: 03
subsystem: backtesting
tags: [python, backtest, metrics, confidence-interval, slippage, csv-report]

# Dependency graph
requires:
  - phase: 02-backtest-engine
    plan: 02
    provides: simulate_event() — 한 펌핑 이벤트의 틱 재생 시뮬레이션 (Trade dict|None 계약)
provides:
  - ev_ci / max_drawdown / compute_metrics — 승률·EV·정규근사 95% CI·MDD 지표 (BT-05)
  - run_backtest() — 전 이벤트 x 슬리피지 4시나리오 순회 + 갭 오염 이벤트 제외 (D-15)
  - print_report() — 슬리피지 4행 비교 테이블 stdout 출력 (D-10/D-11/D-16)
  - write_csv() — 거래 상세 utf-8-sig CSV 출력 (D-10)
  - main() — argparse 오케스트레이션 (--db / --csv / --self-test)
affects: [strategy-validation, phase-3-oos-validation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "정규근사 신뢰구간 — statistics.stdev(n-1 표본표준편차) / Z_95 상수, scipy 의존 없음"
    - "누적손익 단순합 MDD — entry_ts 정렬 후 고점 대비 최대 낙폭 (복리 아님)"
    - "갭 오염 이벤트 사전 제외 — _gap_ratio > GAP_EXCLUDE_PCT면 시뮬 전 통째 스킵"
    - "단일 CSV 멀티 시나리오 — slippage 컬럼으로 4시나리오 거래를 한 파일에 구분"

key-files:
  created: [scripts/test_backtest_metrics.py]
  modified: [scripts/backtest.py]

key-decisions:
  - "MDD 모델은 누적손익 단순합 채택 — 복리 아님, 사용자에게 투명한 for-loop 유지"
  - "compute_metrics의 sample_warning은 count < 30 기준 — Phase 3 VAL-04 경고와 연결"
  - "data/backtest_trades.csv는 .gitignore 등록 — 실행 시 생성되는 런타임 산출물"

patterns-established:
  - "지표 함수는 stdlib statistics만 사용 — pstdev 금지, n<2 경계는 CI=EV로 안전 폴백"
  - "print_report는 이벤트 0건 시 '틱 데이터 축적 필요' 메시지로 방어적 종료 (RESEARCH Open Q3)"

requirements-completed: [BT-05]

# Metrics
duration: 3min
completed: 2026-05-19
---

# Phase 2 Plan 03: 백테스트 지표·리포트·오케스트레이션 Summary

**scripts/backtest.py를 완성 — 모든 펌핑 이벤트를 슬리피지 4시나리오로 시뮬레이션하고(갭 오염 이벤트 제외), 승률·EV·정규근사 95% CI·MDD를 산출해 stdout 4행 비교 테이블 + utf-8-sig CSV 상세로 출력하며, 데이터가 비어도 예외 없이 종료한다**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-19T04:06:06Z
- **Completed:** 2026-05-19T04:08:54Z
- **Tasks:** 2
- **Files created:** 1 (scripts/test_backtest_metrics.py)
- **Files modified:** 1 (scripts/backtest.py)

## Accomplishments
- `ev_ci(pnls)` — 거래별 손익률 리스트에서 (EV, CI 하한, CI 상한) 산출. 정규근사(z=1.96), `statistics.stdev`(n-1 표본표준편차) 사용. n<2면 CI=EV로 안전 폴백 (D-12)
- `max_drawdown(pnls_ordered)` — 시간순 누적손익(단순합)에서 고점 대비 최대 낙폭(양수) 산출
- `compute_metrics(trades)` — 빈 리스트는 `{count:0}`, 그 외 count·win_count·win_rate·ev·ci_low·ci_high·mdd·sample_warning 집계. MDD는 entry_ts 정렬 후 계산 (순서 의존)
- `run_backtest(db_path)` — `load_events`로 이벤트 조회 → 각 이벤트 `get_ticks` → `_gap_ratio > GAP_EXCLUDE_PCT`면 제외(D-15) → 제외 안 된 이벤트는 슬리피지 4시나리오로 `simulate_event` 호출. Trade dict에 pump_id·coin 주입
- `print_report(result)` — 헤더(대상/제외 건수·추정틱 비율·전략 상수·왕복 수수료 0.50%) + 슬리피지 4행 테이블(거래수·승률·EV·95% CI·MDD). 표본 부족 시 ⚠경고. 이벤트 0건이면 "틱 데이터 축적 필요" 출력
- `write_csv(result, path)` — 4시나리오 거래를 `slippage` 컬럼으로 구분해 한 CSV에 기록. `encoding="utf-8-sig"`로 Excel 한글 깨짐 방지
- `main()` — argparse(`--db`/`--csv`/`--self-test`) 오케스트레이션. self-test면 기존 `_self_test` 실행
- `scripts/test_backtest_metrics.py` — ev_ci/max_drawdown/compute_metrics 회귀 테스트 6건 (TDD RED→GREEN)

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): 지표 함수 실패 테스트** — `282dab2` (test)
2. **Task 1 (GREEN): 지표 계산 구현 (ev_ci/max_drawdown/compute_metrics)** — `8c5c136` (feat)
3. **Task 2: run_backtest + print_report + write_csv + main 오케스트레이션** — `05ba3f3` (feat)
4. **chore: 생성 CSV .gitignore 등록** — `41446c9` (chore)

## Files Created/Modified
- `scripts/backtest.py` — 지표 함수 3개 + 오케스트레이션 함수 4개 + main 추가. import에 csv/statistics, `from bithumb.db import get_ticks` 추가. 상수 블록에 Z_95/MIN_SAMPLE 추가
- `scripts/test_backtest_metrics.py` (신규) — 지표 함수 회귀 테스트
- `.gitignore` — `data/backtest_trades.csv` 패턴 추가

## Decisions Made
- **MDD 모델 = 누적손익 단순합**: 02-RESEARCH.md L294가 단순합/복리 둘 다 허용했으나, Python 학습 중인 사용자에게 투명한 for-loop 단순합 모델을 채택하고 docstring에 명시. Phase 3에서 필요 시 복리로 교체 가능
- **sample_warning 기준 = count < 30**: 플랜 action이 "count < 2 또는 count < 30"을 명시 — MIN_SAMPLE=30 상수로 분리. 2~3주치 표본은 수십 건뿐이므로 거의 항상 경고가 켜지는 것이 정상이며 Phase 3 VAL-04와 연결
- **생성 CSV는 .gitignore**: `data/backtest_trades.csv`는 실행 시마다 덮어쓰이는 런타임 산출물 — `data/*.db`와 같은 정책으로 git 제외 (Rule 2: 생성 파일 추적 방지)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical] 생성 CSV의 .gitignore 누락**
- **Found during:** Task 2 (실행 후 git status 확인)
- **Issue:** `run_backtest` 실행이 `data/backtest_trades.csv`를 생성하는데 `.gitignore`에 패턴이 없어 런타임 산출물이 커밋될 위험
- **Fix:** `.gitignore`에 `data/backtest_trades.csv` 추가 (기존 `data/*.db` 정책과 일관)
- **Files modified:** .gitignore
- **Commit:** 41446c9

그 외 플랜이 명세한 대로 구현. ev_ci/max_drawdown 의사코드(L271-308)·stdout 테이블 포맷(L396-413)·CSV 패턴(L420-429)을 그대로 따랐다.

## Issues Encountered
- Windows 콘솔 CP949 인코딩 때문에 한글 출력이 깨질 수 있어 모든 검증을 `python -X utf8`로 실행 (02-02와 동일 아티팩트, 코드 결함 아님). 실 데이터(`data/trades.db`)는 pump_log 350건이지만 pump_ticks 0건 — 틱 데이터 축적 미완료(PROJECT.md "2~3주 소요"). `print_report`의 "이벤트 없음" 방어 경로가 실제로 동작함을 확인했고, 합성 거래로 4행 테이블 렌더링도 별도 검증

## Verification Results
- `python scripts/backtest.py --db data/trades.db` → exit 0, "백테스트 대상 이벤트 없음 — 틱 데이터 축적 필요" 출력 (예외 없음)
- `python scripts/backtest.py --self-test` → self-test PASS (Plan 01/02 lookahead 회귀 없음)
- `scripts/test_backtest_metrics.py` → 6/6 passed (ev_ci·max_drawdown·compute_metrics 경계 케이스)
- 합성 거래 렌더 테스트: 슬리피지 0.0/0.5/1.0/2.0% 4행 + `수수료(왕복): 0.50%` 헤더 + ⚠표본부족 경고 출력 확인. CSV 12행 + utf-8-sig BOM 확인
- grep: `def ev_ci`=1, `def max_drawdown`=1, `def compute_metrics`=1, `statistics.stdev`=1, `pstdev`=0, `Z_95 = 1.96`=1
- grep: `def run_backtest`=1, `def print_report`=1, `def write_csv`=1, `def main`=1, `get_ticks`=2, `utf-8-sig`=2, `GAP_EXCLUDE_PCT`=3
- grep: `(INSERT|UPDATE|DELETE|CREATE TABLE)`=0 — DB 읽기 전용 (BT-01)

## Next Phase Readiness
- Phase 2 (백테스트 엔진) 3개 플랜 완료 — DataSlice lookahead 차단(01) + simulate_event 틱 재생(02) + 지표·리포트(03)가 모두 배선됨. `python scripts/backtest.py` 한 줄로 슬리피지 4시나리오 백테스트 실행 가능
- **틱 데이터 의존성**: pump_ticks 테이블이 비어 있어 현재 실 백테스트는 "이벤트 없음" — Phase 1의 틱 기록 루프가 2~3주 축적해야 의미 있는 EV/CI 산출 가능. 엔진 자체는 합성 데이터로 정상 동작 검증 완료
- Phase 3 (전략 검증)은 `run_backtest` 결과를 train/test 분할해 OOS 검증하며, `sample_warning`이 VAL-04 경고와 연결됨

## Self-Check: PASSED

- FOUND: scripts/backtest.py
- FOUND: scripts/test_backtest_metrics.py
- FOUND: commit 282dab2
- FOUND: commit 8c5c136
- FOUND: commit 05ba3f3
- FOUND: commit 41446c9

---
*Phase: 02-backtest-engine*
*Completed: 2026-05-19*
