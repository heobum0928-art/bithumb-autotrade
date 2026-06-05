---
plan: 03-01
phase: 03-strategy-validation
status: completed
completed_at: "2026-05-19"
---

# Plan 03-01 Summary: Strategy 데이터클래스 + simulate_event 파라미터화

## What was done
- `scripts/backtest.py`에 `Strategy` frozen 데이터클래스 추가 (entry_drop_pct/tp_pct/sl_pct, 기본값 0.07/0.05/0.03)
- `DEFAULT_STRATEGY = Strategy()` 정의
- 모듈 상수 `ENTRY_DROP_PCT` / `TP_PCT` / `SL_PCT` 삭제
- `simulate_event` 시그니처에 `strategy: Strategy = DEFAULT_STRATEGY` 인자 추가
- simulate_event 본문에서 전역 상수 참조 → `strategy.entry_drop_pct` / `strategy.tp_pct` / `strategy.sl_pct` 치환
- `print_report` 내 전략 상수 참조 → `DEFAULT_STRATEGY.*` 필드 참조로 수정

## Verification passed
- `python scripts/backtest.py --self-test` → "self-test PASS", exit 0
- `python scripts/backtest.py` → 이벤트 없음 메시지, exit 0, NameError/TypeError 없음

## Key changes
- `scripts/backtest.py` L15-21: Strategy dataclass + DEFAULT_STRATEGY
- `scripts/backtest.py` L129: simulate_event 시그니처 확장
- `scripts/backtest.py` L152, L168: 전략 상수 → strategy 필드 참조
- `scripts/backtest.py` L344-346: print_report → DEFAULT_STRATEGY 참조

## Phase 2 compatibility
- run_backtest의 simulate_event 호출(2인자)은 그대로 — DEFAULT_STRATEGY 자동 적용
- --self-test 통과, 단독 실행 정상
