---
phase: 01-tick-recording-infrastructure
plan: 02
subsystem: trading-bot
tags: [record-only, websocket, exchange-timestamp, safety-gate]

# Dependency graph
requires:
  - "Plan 01-01 — pump_ticks 스키마 + log_tick/get_ticks (틱 저장 계층)"
provides:
  - "RECORD_ONLY 게이트 — do_buy/do_buy_limit/do_sell 실거래 차단 (REC-01)"
  - "PriceTracker._parse_exchange_ts — 빗썸 WS date/time → epoch sec 변환 (REC-03 입력단)"
  - "PriceTracker.get_latest_exchange_ts(coin) — 코인별 거래소 발생 시각 조회"
  - "PriceTracker.get_latest_acc_value(coin) — 코인별 누적 거래대금 조회 (D-08)"
affects: [tick-recording, backtest-engine]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "안전 우선 게이트 — RECORD_ONLY 기본값 True, config.yaml 미설정/읽기실패 시에도 차단"
    - "거래소 시각 별도 dict 보관 — deque 튜플 3원소 불변 유지, get_signal 무중단"

key-files:
  created: []
  modified:
    - scripts/alt_monitor.py

key-decisions:
  - "RECORD_ONLY 기본값 True — config.yaml 미설정/읽기실패 시에도 실거래 차단 (안전 우선)"
  - "거래소 시각은 별도 self._ex_ts dict에 보관 — deque 튜플 4원소 확장 시 get_signal/get_preemptive_signal 언패킹 파손 회피"
  - "acc_value는 deque 튜플 인덱스 2에 이미 존재 — 별도 dict 없이 hist[-1][2] 조회 getter만 추가"

requirements-completed: [REC-01, REC-03]

# Metrics
duration: ~1min
completed: 2026-05-19
---

# Phase 1 Plan 2: RECORD_ONLY 게이트 + 거래소 시각 파싱 Summary

**봇을 기록 전용 모드로 전환하는 RECORD_ONLY 게이트를 매매 함수 3곳에 삽입하고, WebSocket on_message가 빗썸 거래소 발생 시각(date+time, KST)을 epoch로 파싱·보관하도록 확장 — 검증 전 실거래 손실 0원을 보장하는 핵심 안전장치**

## Performance

- **Duration:** ~1 min (Task 1~2 실행), Task 3 사용자 검토 체크포인트
- **Tasks:** 3 (2 auto + 1 checkpoint:human-verify)
- **Files modified:** 1 (scripts/alt_monitor.py)

## Accomplishments

- `_parse_exchange_ts(date_s, time_s)` 헬퍼 — 빗썸 WS content의 `date`(YYYYMMDD) + `time`(HHMMSS, KST)을 epoch sec로 변환, 실패 시 None
- `PriceTracker._ex_ts` dict 추가 — 코인별 최근 거래소 발생 시각 보관
- `on_message` 확장 — 수신 시 거래소 시각을 파싱해 `_ex_ts`에 저장 (deque 튜플은 3원소 `(now, price, acc_val)` 그대로 유지)
- `get_latest_exchange_ts(coin)` — 코인별 거래소 발생 시각 조회 getter (REC-03 입력단, Plan 03 소비)
- `get_latest_acc_value(coin)` — 코인별 누적 거래대금 조회 getter, deque 튜플 인덱스 2 재사용 (D-08, Plan 03 소비)
- `RECORD_ONLY` 상수 + `_load_record_only()` — config.yaml `trading.record_only` 1회 로드, 기본값 True
- `do_buy` / `do_buy_limit` / `do_sell` 함수 첫 줄에 `if RECORD_ONLY:` 게이트 — 주문 API 미호출, None 반환, "RECORD_ONLY 차단됨" 경고 로그
- 신호 감지·필터·`signal_log`/`pump_log` 기록 로직 일절 미변경 — 차단 중에도 신호 기록 계속 (D-03)
- `get_signal`/`get_preemptive_signal`/`get_latest_price` 무중단 — deque 튜플 구조 불변

## Task Commits

1. **Task 1: _parse_exchange_ts 헬퍼 + PriceTracker 거래소시각/거래대금 getter** - `6ac173a` (feat)
2. **Task 2: RECORD_ONLY 상수 + do_buy/do_buy_limit/do_sell 게이트** - `1c8e65d` (feat)
3. **Task 3: 매매 코드 변경 검토 + config.yaml 설정** - 체크포인트(코드 변경 없음), 사용자 "approved"

**Plan metadata:** (this commit) (docs: complete plan)

## Files Created/Modified

- `scripts/alt_monitor.py` - `_parse_exchange_ts` 헬퍼 + `KST` 상수 추가, `PriceTracker.__init__`에 `_ex_ts` dict, `on_message`에 거래소 시각 파싱, `get_latest_exchange_ts`/`get_latest_acc_value` getter, `RECORD_ONLY` 상수 + `_load_record_only()`, `do_buy`/`do_buy_limit`/`do_sell` 게이트

## Plan 03 소비 인터페이스 (시그니처 동결)

```python
PriceTracker.get_latest_exchange_ts(self, coin: str) -> float | None
    # 코인별 최근 거래소 발생 시각(epoch sec). 미수신/파싱실패 시 None.

PriceTracker.get_latest_acc_value(self, coin: str) -> float | None
    # 코인별 최근 누적 거래대금(WS value). 미수신 코인이면 None.
```

## 운영 체크리스트 (RESEARCH Pitfall 3 — 잔여 포지션)

- **RECORD_ONLY 전환 전 `data/active_pos.json`에 잔여 포지션이 있으면 수동 청산하고 파일을 비울 것.** RECORD_ONLY가 `do_sell`도 막으므로 잔여 포지션은 자동 청산되지 않는다.
- PROJECT.md 기준 현재 미청산 포지션 없음 (봇 2026-05-18 정지) — 추가 조치 불필요하나 봇 재기동 전 재확인 권장.

## User Setup Required

- `config.yaml`의 `trading:` 섹션에 `record_only: true` 추가 — **완료**. config.yaml은 git 비추적(.gitignore)이며 절대 커밋하지 않음.
- `_load_record_only()`는 키 미설정/파일 읽기실패 시에도 `True`를 반환 — 안전 기본값.

## Decisions Made

- RECORD_ONLY 기본값 `True` — config.yaml 미설정/읽기실패 시에도 실거래 차단 (안전 우선)
- 거래소 시각을 별도 `self._ex_ts` dict에 보관 — deque 튜플을 4원소로 확장하면 `get_signal`/`get_preemptive_signal`의 3원소 언패킹이 파손되므로 회피
- acc_value는 deque 튜플 인덱스 2에 이미 존재 — 별도 dict 없이 `hist[-1][2]` 조회 getter만 추가

## Deviations from Plan

**1. [Rule 3 - Blocking] config.yaml에 record_only 키 누락 — 실행 중 추가**
- **Found during:** Task 3 (체크포인트 재개 검증)
- **Issue:** 체크포인트 컨텍스트는 사용자가 `trading.record_only: true`를 설정했다고 명시했으나, config.yaml 검증 결과 `trading:` 섹션에 키가 실제로 존재하지 않았다. CLAUDE.md 규칙(추측 금지·파일 확인)에 따라 검증해 발견.
- **Fix:** config.yaml `trading:` 섹션에 `record_only: true`를 추가. 봇 동작은 `_load_record_only()` 기본값 `True`로 이미 안전했으나, 플랜 Task 3 done 기준이 키 존재를 명시 요구하므로 명시적으로 설정.
- **Files modified:** config.yaml (git 비추적 — 커밋 안 함)
- **Commit:** N/A (config.yaml은 .gitignore 대상, 절대 커밋 금지)

## Issues Encountered

None. Task 1~2 코드는 사전 에이전트가 커밋 완료, verify 스크립트(AST 파싱, RECORD_ONLY 카운트) 전부 통과.

## Next Phase Readiness

- 실거래 차단(REC-01) 활성화 — 봇 재기동 시 주문 API 호출 0회, 신호 기록은 계속
- 거래소 시각 파싱 인프라(REC-03 입력단) 완성 — Plan 03(WS→틱 DB 통합)이 `get_latest_exchange_ts`/`get_latest_acc_value`를 소비할 준비 완료
- 블로커 없음

## Self-Check: PASSED

---
*Phase: 01-tick-recording-infrastructure*
*Completed: 2026-05-19*
