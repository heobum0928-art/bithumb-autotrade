---
plan: 03-03
phase: 03-strategy-validation
status: completed
completed_at: "2026-05-19"
---

# Plan 03-03 Summary: OOS 검증 + GO/NO-GO 판정 + 리포트/CSV 완성

## What was done
- `time_block()` + `decompose_by_time_block()`: KST 6시간 블록 4구간 분해
- `decompose_by_coin()`: 코인별 분해 + 단일 코인 지배율 경고 (임계 40%)
- `overfit_warning()`: test EV 부호 반전 또는 train EV 절반 미만 시 경고
- `decide_go_nogo()`: 표본 게이트(<30) 우선 → CI 하한 게이트(≤0) → GO/NO-GO 판정
- `run_validation()`: 분할→그리드 서치→unlock_test() 단 1회→test OOS→판정 사이클
- `print_validation_report()`: 그리드 상위10/슬리피지4행/코인별/시간대별/과적합/GO-NO-GO 7섹션
- `write_grid_csv()` / `write_test_csv()`: validate_grid.csv / validate_test_trades.csv (utf-8-sig)
- `_self_test()` 확장: 8개 assertion (EventSplit봉인/grid_search36행/판정3케이스/시간블록/분해)
- `main()` 완성: --self-test → _self_test(), else run_validation+report+CSV

## Verification passed
- `python scripts/validate.py --self-test` → "self-test PASS", exit 0
- `python scripts/validate.py` → "검증 대상 이벤트 없음 — 틱 데이터 축적 필요 (2~3주)", exit 0, 크래시 없음

## Key design points
- `unlock_test()` 호출은 `run_validation` 내 정확히 1곳 (D-08 OOS 규율)
- backtest_trades.csv 참조 없음 — Phase 2 CSV와 독립 경로 (Pitfall 4)
- 빈 DB에서도 완전 실행 (데이터 없음 메시지 후 정상 종료)
- 실 GO/NO-GO 판정은 test 거래수 ≥30 축적 후에만 유효

## Requirements covered
- VAL-03: 코인별·KST 6시간 시간대별 분해 테이블 출력
- VAL-04: 표본 미달·단일 코인 지배·과적합 경고, 표본 미달 시 NO-GO 강제
- VAL-05: 슬리피지 1% test CI 하한 기준 GO/NO-GO 판정과 사유 명시
