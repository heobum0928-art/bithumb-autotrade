---
phase: 01-tick-recording-infrastructure
verified: 2026-05-19T00:00:00Z
status: passed
score: 4/4 must-haves verified
---

# Phase 1: Tick Recording Infrastructure Verification Report

**Phase Goal:** 봇이 실거래를 완전 차단한 채 펌핑 이벤트의 초 단위 가격 경로를 DB에 축적한다
**Verified:** 2026-05-19
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #   | Truth | Status | Evidence |
| --- | ----- | ------ | -------- |
| 1   | 봇을 실행해도 시장가 매수/매도 API 호출이 단 한 번도 발생하지 않는다 — "RECORD_ONLY 차단됨" 로그 기록 | ✓ VERIFIED | `do_buy`(L869), `do_buy_limit`(L908), `do_sell`(L973) 모두 함수 첫 문장이 `if RECORD_ONLY: ... return None` + "RECORD_ONLY 차단됨" 경고 로그. RECORD_ONLY 기본값 True, config.yaml `trading.record_only: true` 확인됨 |
| 2   | 펌핑 감지 후 pump_ticks 테이블에 10초 간격 틱 행이 쌓이며 SELECT COUNT(*)로 증가 확인 가능 | ✓ VERIFIED | 데이터 흐름 완결: 펌핑 감지 → `log_pump`(L1674) → `queue_pump`(L1676) → `start_pump_tracker` _run 10초 루프(L295 `time.sleep(10)`) → `log_tick`(L282). `_pump_tick_sim.py` Part B가 stub로 1사이클 INSERT 검증 통과 |
| 3   | 각 틱 행에 exchange_ts와 recv_ts가 분리 저장된다 | ✓ VERIFIED | pump_ticks 스키마에 `exchange_ts REAL` / `recv_ts REAL NOT NULL` 분리 컬럼 (db.py L82-83). `on_message`가 `_parse_exchange_ts`로 KST date/time을 epoch 파싱(L510), `start_pump_tracker`가 `get_latest_exchange_ts`로 조회해 `log_tick(exchange_ts=...)` 전달(L278,282). exchange_ts None 시 recv_ts 복사 + ts_estimated=1 폴백(db.py L216-218) |
| 4   | WS 단절 복구 구간에 틱 갭이 DB에 명시 기록되어 백테스트가 오염 구간 식별 가능 | ✓ VERIFIED | pump_ticks 스키마에 `gap_before INTEGER DEFAULT 0` 컬럼. `start_pump_tracker`가 `gap = bool(last_recv) and (now_ts - last_recv) >= GAP_THRESHOLD_SEC`(L277, threshold 30s)로 판정해 `log_tick(gap_before=gap)` 전달. `_pump_tick_sim.py` Part A가 갭 틱 INSERT + assert 검증 통과 |

**Score:** 4/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `bithumb/db.py` | pump_ticks 스키마 + log_tick/get_ticks | ✓ VERIFIED | CREATE TABLE pump_ticks (10 컬럼, 순서 정확) + idx_pump_ticks_pump_id 인덱스 생성 확인. `log_tick`(L206)/`get_ticks`(L230) 시그니처 동결, exchange_ts None 폴백 동작. pump_log 스키마·기존 함수 불변 |
| `scripts/alt_monitor.py` | RECORD_ONLY 게이트 + exchange_ts 파싱 + start_pump_tracker 틱 배선 | ✓ VERIFIED | `_parse_exchange_ts`, `_load_record_only`, `RECORD_ONLY`, `PUMP_TRACK_SEC=600`, `GAP_THRESHOLD_SEC=30`, getter 2개, 매매함수 3 게이트, 10초 루프 log_tick 배선 모두 존재. deque 튜플 3원소 불변, get_signal 무중단 |
| `scripts/_pump_tick_sim.py` | 틱 경로 + 루프 wiring 검증 스크립트 | ✓ VERIFIED | Part A(log_tick/get_ticks 계약·갭·폴백) + Part B(stub PriceTracker로 start_pump_tracker 루프 1사이클 wiring) 모두 통과, 종료 코드 0, "OK" 출력. 테스트 데이터 DELETE 정리 포함 |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| `log_tick()` | pump_ticks 테이블 | INSERT INTO pump_ticks | ✓ WIRED | db.py L221 INSERT 문 + 9개 컬럼 바인딩 |
| `get_ticks()` | pump_ticks 테이블 | SELECT ORDER BY seq | ✓ WIRED | db.py L237 `SELECT * FROM pump_ticks WHERE pump_id = ? ORDER BY seq` |
| `do_buy/do_buy_limit/do_sell` | RECORD_ONLY 상수 | 함수 첫 줄 가드 | ✓ WIRED | 3개 함수 모두 첫 문장 `if RECORD_ONLY:` |
| `on_message` | `_parse_exchange_ts` | content date/time 파싱 | ✓ WIRED | L510 호출, 결과 `_ex_ts[coin]`에 저장(L519) |
| `start_pump_tracker` | `log_tick` | 10초 루프 INSERT 호출 | ✓ WIRED | L282 호출, try/except 보호 |
| `start_pump_tracker` | `get_latest_exchange_ts` / `get_latest_acc_value` | 거래소시각·거래대금 조회 | ✓ WIRED | L278, L280 호출 후 log_tick에 전달 |
| 펌핑 감지 | `queue_pump` | log_pump 후 큐 등록 | ✓ WIRED | L1676 `queue_pump(_pid, coin, ...)` — 15원소 item put |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| pump_ticks 행 | exchange_ts/recv_ts/acc_value | WS `on_message` (closePrice/value/date/time) → PriceTracker 버퍼 → start_pump_tracker getter | ✓ (라이브 검증) | ✓ FLOWING — 01-03-SUMMARY 라이브 타임존 검증에서 exchange_ts vs recv_ts 델타 0.65~0.72초 측정, ts_estimated=0. WS 메시지가 실제 date/time 필드 포함 확인 |
| pump_ticks 누적 행 | COUNT(*) | start_pump_tracker 10초 루프 log_tick | ⚠️ 라이브 미발생 | 현재 0행 — 봇 재기동/실제 펌핑 발생 전. 경로 자체는 `_pump_tick_sim.py` Part B가 stub 1사이클 INSERT로 검증. 라이브 펌핑 시 축적은 인프라 검증 범위 밖(2~3주 수집 단계) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| 틱 경로 + 루프 wiring 검증 | `python scripts/_pump_tick_sim.py` | "Part A OK" + "Part B OK" + "OK", exit 0 | ✓ PASS |
| pump_ticks 스키마 생성 | `init_db()` 후 PRAGMA table_info | 10개 컬럼 순서 정확 + idx_pump_ticks_pump_id 인덱스 | ✓ PASS |
| config.yaml record_only | yaml.safe_load 후 trading.record_only 조회 | `True` | ✓ PASS |
| exchange_ts 파싱 | `_parse_exchange_ts` 로직 단위 실행 | 유효 KST date/time → epoch, 빈 입력 → None | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| REC-01 | 01-02 | 봇 RECORD_ONLY 모드 — 실거래 차단 | ✓ SATISFIED | 매매함수 3 게이트 + RECORD_ONLY 기본 True + config.yaml 설정 (Truth 1) |
| REC-02 | 01-01, 01-03 | pump_tracker 초 단위 틱 DB 저장 | ✓ SATISFIED | pump_ticks 테이블 + start_pump_tracker 10초 루프 log_tick 배선 (Truth 2) |
| REC-03 | 01-01, 01-02, 01-03 | 거래소 시각/수신 시각 구분 기록 | ✓ SATISFIED | exchange_ts/recv_ts 분리 컬럼 + 파싱 + ts_estimated 폴백 (Truth 3) |
| REC-04 | 01-01, 01-03 | WS 단절 갭 감지·기록 | ✓ SATISFIED | gap_before 컬럼 + GAP_THRESHOLD_SEC 판정 (Truth 4) |

REC-01..REC-04 전부 PLAN frontmatter에 선언되었고 REQUIREMENTS.md Phase 1 매핑(L61-64)과 일치 — 누락/orphan 없음.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| (none) | — | — | — | log_tick/update_pump_path try/except는 추적 루프 보호용 의도된 패턴. RECORD_ONLY `return None`은 게이트 동작이며 stub 아님. 차단 사유 anti-pattern 없음 |

### Human Verification Required

없음. 타임존 정합성(exchange_ts vs recv_ts 델타)은 01-03 Task 4 라이브 체크포인트에서 이미 사용자 검증 완료(델타 0.65~0.72초, ±9h 오류 없음).

### Gaps Summary

갭 없음. Phase 1의 4개 Success Criteria가 모두 코드·스키마·검증 스크립트로 충족됐다. 실거래는 매매함수 3곳 게이트로 차단되고, 펌핑 감지 → log_pump → queue_pump → start_pump_tracker 10초 루프 → log_tick 의 데이터 흐름이 완결됐다. 거래소/수신 시각 분리·갭 플래그·acc_value가 매 틱 저장되며, `_pump_tick_sim.py`가 봇 없이 INSERT/읽기/갭/wiring 경로를 자동 검증한다.

pump_ticks 현재 0행은 갭이 아니다 — 라이브 펌핑 데이터 축적은 Phase 1 인프라 검증 범위가 아니라 2~3주 실세계 수집 단계의 결과물이며, 인프라(스키마+배선+검증)는 완비됐다.

---

_Verified: 2026-05-19_
_Verifier: Claude (gsd-verifier)_
