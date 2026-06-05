---
plan: 03-02
phase: 03-strategy-validation
status: completed
completed_at: "2026-05-19"
---

# Plan 03-02 Summary: validate.py 골격 + EventSplit + grid_search

## What was done
- `scripts/validate.py` 신규 생성
- `filter_clean_events(events, db_path)`: 분할 전 갭 오염 이벤트 일괄 제외
- `EventSplit` 클래스: detected_at 시간순 70/30 분할, test 셋 물리 봉인 (unlock_test() 전 접근 시 RuntimeError)
- 그리드 상수 정의: ENTRY_GRID(3) × TP_GRID(4) × SL_GRID(3) = 36조합, GRID_SLIPPAGE=0.01
- `grid_search(train_events, db_path, slippage)`: train 전용 36조합 EV↓/승률↓/MDD↑ 정렬
- `_self_test()`: 합성 데이터로 EventSplit 봉인·grid_search 36행·EV정렬 자가검증
- `main()`: --self-test 플래그 지원, run_validation은 Plan 03-03에서 구현

## Verification passed
- `python scripts/validate.py --self-test` → "self-test PASS", exit 0
- 모든 acceptance criteria 통과

## Key design points
- `alt_monitor.py` / `bithumb/client.py` import 없음 — 오프라인 독립
- test 셋 봉인: `_test_unlocked = False` 초기, `unlock_test()` 호출 전 `.test` 접근 시 RuntimeError
- grid_search docstring에 D-11 계약 명시 (train만 전달)
