---
phase: 01-tick-recording-infrastructure
plan: 03
subsystem: trading-bot
tags: [tick-recording, websocket-gap, pump-tracker, wiring]

# Dependency graph
requires:
  - "Plan 01-01 — pump_ticks 스키마 + log_tick/get_ticks (틱 저장 계층)"
  - "Plan 01-02 — get_latest_exchange_ts / get_latest_acc_value getter + RECORD_ONLY 게이트"
provides:
  - "start_pump_tracker 10초 루프 내 log_tick INSERT — 펌핑 1건당 ~60틱 축적 (REC-02)"
  - "WS 갭 판정 — recv_ts 간격 ≥ GAP_THRESHOLD_SEC 시 gap_before=1 (REC-04)"
  - "거래소 시각/수신 시각 분리 + acc_value 매 틱 저장 (REC-03, D-08)"
  - "10분 펌핑 추적 — PUMP_TRACK_SEC 기반 종료 조건"
  - "scripts/_pump_tick_sim.py — 봇 없이 틱 경로 + 루프 wiring 자동 검증 스크립트"
affects: [backtest-engine, strategy-validation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "추가 API 호출 없는 틱 기록 — tracker 버퍼(WS 시세) 재사용, log_tick은 순수 DB INSERT"
    - "item 튜플 인덱스 영속화 — tick_seq(item[13])/last_recv(item[14])를 매 사이클 다시 써서 다음 루프에 전달"
    - "5분 집계와 10분 추적 분리 — update_pump_path 5분 저장 유지, 루프 종료만 elapsed 기반 10분으로 확장"

key-files:
  created:
    - scripts/_pump_tick_sim.py
  modified:
    - scripts/alt_monitor.py

key-decisions:
  - "펌핑 추적 5분→10분 연장 (D-05) — pump_log 5분 집계는 그대로 5분에 저장, 루프 종료 조건만 elapsed < PUMP_TRACK_SEC로 분리"
  - "GAP_THRESHOLD_SEC=30 — 예상 틱 간격 10초의 3배. 첫 틱(last_recv==0)은 bool(last_recv) False라 갭 아님 (D-12)"
  - "acc_value 매 틱 저장 (D-08) — 거래량 전략은 deferred지만 데이터 수집은 명시 요구, 향후 거래량 백테스트 여지 확보"
  - "log_tick INSERT는 try/except로 감싸 추적 루프 중단 방지 — 기존 update_pump_path 패턴 따름"

requirements-completed: [REC-02, REC-03, REC-04]

# Metrics
duration: ~2min (Task 1~3 사전 에이전트 + 본 에이전트 체크포인트 재개)
completed: 2026-05-19
---

# Phase 1 Plan 3: 펌핑 틱 기록 루프 배선 Summary

**start_pump_tracker 10초 루프가 매 사이클 log_tick을 호출해 pump_ticks에 틱을 INSERT하도록 배선 — 거래소/수신 시각 분리, acc_value 저장, WS 갭 판정, 10분 추적이 동작하며 펌핑 1건당 ~60틱이 축적된다. Plan 01의 log_tick과 Plan 02의 getter를 실제 데이터 흐름에 연결하는 wiring 단계.**

## Performance

- **Duration:** ~2 min (Task 1~3 코드는 사전 에이전트 커밋, Task 4는 사용자 검증 체크포인트)
- **Tasks:** 4 (3 auto + 1 checkpoint:human-verify)
- **Files:** 1 created (`scripts/_pump_tick_sim.py`), 1 modified (`scripts/alt_monitor.py`)

## Accomplishments

- **추적 10분 연장 (D-05)** — `PUMP_TRACK_SEC=600` 상수 추가, 종료 조건을 `if not item[12]`(done_5m)에서 `if elapsed < PUMP_TRACK_SEC`로 변경. pump_log 5분 집계(price_5m 등)와 `update_pump_path`는 한 줄도 미변경 — 5분 집계는 5분에 그대로 저장되고 루프만 10분까지 지속.
- **item 튜플 확장** — `queue_pump`가 put하는 13원소 리스트를 15원소로 확장 (인덱스 13=`tick_seq`, 14=`last_recv_ts`). `start_pump_tracker` _run의 언패킹도 15개 변수로 갱신.
- **log_tick 배선 (REC-02)** — `start_pump_tracker` 10초 루프 내 `update_pump_path` 부근에 틱 INSERT 추가. `p = tracker.get_latest_price(coin)`로 이미 얻은 가격 재사용, 추가 API 호출 없음.
- **WS 갭 판정 (REC-04)** — `gap = bool(last_recv) and (now_ts - last_recv) >= GAP_THRESHOLD_SEC`. `GAP_THRESHOLD_SEC=30` 상수 추가. 첫 틱은 갭 아님.
- **시각 분리 + acc_value 저장 (REC-03, D-08)** — `get_latest_exchange_ts`/`get_latest_acc_value`/`get_vol_power` getter로 거래소 시각·누적 거래대금·체결강도 조회 후 `log_tick`에 전달. `exchange_ts` None이면 `log_tick`이 `recv_ts` 복사 + `ts_estimated=1` 폴백.
- **seq 영속화** — `item[13]=tick_seq+1`, `item[14]=now_ts`를 `still.append(item)` 전에 갱신해 다음 사이클에 반영.
- **검증 스크립트** — `scripts/_pump_tick_sim.py` (Part A: log_tick/get_ticks 계약 + 갭 판정 + exchange_ts/acc_value 폴백 / Part B: stub PriceTracker로 start_pump_tracker 루프 1사이클 wiring 검증). 테스트 데이터는 실행 후 정리.

## Task Commits

1. **Task 1: 추적 5분→10분 연장 + item 튜플 틱 상태 필드** — `21df3b3` (feat)
2. **Task 2: 틱 기록 경로 + 루프 wiring 검증 스크립트** — `e1a22cf` (test)
3. **Task 3: 10초 루프 log_tick 호출 + WS 갭 판정 + acc_value 배선** — `33d5aa5` (feat)
4. **Task 4: 라이브 봇 틱 축적 + 타임존 정합성 검증** — 체크포인트(코드 변경 없음), 사용자 "approved"

**Plan metadata:** (this commit) (docs: complete plan)

## Files Created/Modified

- `scripts/alt_monitor.py` — `PUMP_TRACK_SEC`/`GAP_THRESHOLD_SEC` 상수, `log_tick` import, `queue_pump` 15원소 item, `start_pump_tracker` _run 언패킹 + log_tick 배선 + 갭 판정 + elapsed 기반 10분 종료 + item[13]/item[14] 영속화
- `scripts/_pump_tick_sim.py` (신규) — 봇·실제 펌핑 없이 틱 INSERT/읽기/갭/폴백 경로 + 루프 wiring을 검증하는 일회성 스크립트

## Live 타임존 정합성 검증 결과 (Task 4 체크포인트)

사용자가 라이브 빗썸 WS ticker 덤프로 타임존 정합성을 검증함:

- `ticker` WS 메시지는 `_parse_exchange_ts`가 기대하는 그대로 `date`(YYYYMMDD)·`time`(HHMMSS) 필드를 포함.
- 측정된 `exchange_ts` vs `recv_ts` 델타는 BTC/ETH/XRP 전반에서 **0.65~0.72초** — 순수 네트워크 지연. **±9h 타임존 오류 없음.**
- 파싱이 정상 성공하므로 `ts_estimated`는 0으로 기록됨.
- **결론:** `_parse_exchange_ts`의 KST(UTC+9) 가정이 정확함 (RESEARCH Open Question 2 해소). 상수 수정 불필요.

## Decisions Made

- 펌핑 추적 5분→10분 연장 (D-05) — pump_log 5분 집계는 그대로 5분에 저장, 루프 종료 조건만 `elapsed < PUMP_TRACK_SEC`로 분리. `update_pump_path` 불변.
- `GAP_THRESHOLD_SEC=30` — 예상 틱 간격 10초의 3배. 첫 틱(`last_recv==0`)은 `bool(last_recv)` False라 갭 아님.
- acc_value 매 틱 저장 (D-08) — 거래량 기반 *전략*은 deferred지만 거래량 *데이터* 수집은 D-08이 명시 요구. WS 세 값(closePrice·value·volumePower)을 모두 저장해 향후 거래량 백테스트 여지 확보.
- log_tick INSERT를 try/except로 감싸 틱 INSERT 실패가 추적 루프를 멈추지 않게 함 — 기존 `update_pump_path` 패턴.

## Deviations from Plan

**1. [Rule 3 - Blocking] _pump_tick_sim.py cp949 UnicodeEncodeError — 사전 에이전트 수정**
- **Found during:** Task 3 (사전 에이전트 실행 — commit `33d5aa5`에 검증 스크립트 7줄 변경 포함)
- **Issue:** Windows 콘솔 기본 코드페이지(cp949)에서 `_pump_tick_sim.py`가 출력하는 한글/특수문자가 `UnicodeEncodeError`를 일으켜 검증 스크립트가 비정상 종료. CLAUDE.md 환경(Windows 11)에서 PowerShell 콘솔 인코딩 한계.
- **Fix:** `_pump_tick_sim.py`의 stdout 처리/출력 인코딩을 cp949 안전하게 조정 (commit `33d5aa5`에 반영). 재실행 시 Part A·Part B 모두 정상 통과, "OK" 출력.
- **Files modified:** `scripts/_pump_tick_sim.py`
- **Commit:** `33d5aa5`

## Issues Encountered

None remaining. `python scripts/_pump_tick_sim.py` 실행 시 종료 코드 0, "Part A OK" + "Part B OK" + "OK" 출력. 라이브 타임존 검증도 정상(델타 0.65~0.72초).

## Next Phase Readiness

- 틱 기록 인프라(REC-02/03/04) 완성 — 봇 재기동 시 펌핑 감지마다 10초 루프가 `pump_ticks`에 틱을 INSERT, 거래소/수신 시각 분리·acc_value·갭 플래그 저장.
- Phase 2 백테스트 엔진이 소비할 `get_ticks(pump_id)` 계약 + 실제 데이터 적재 경로 모두 준비 완료.
- 봇 재기동 시 RECORD_ONLY 게이트로 실거래 차단 상태에서 2~3주 틱 데이터 축적 시작 가능.
- 블로커 없음.

## Self-Check: PASSED

- FOUND: scripts/_pump_tick_sim.py
- FOUND: scripts/alt_monitor.py (log_tick/GAP_THRESHOLD_SEC/PUMP_TRACK_SEC 배선 확인)
- FOUND: commit 21df3b3, e1a22cf, 33d5aa5
- VERIFY: `python scripts/_pump_tick_sim.py` 종료 코드 0, "OK" 출력

---
*Phase: 01-tick-recording-infrastructure*
*Completed: 2026-05-19*
