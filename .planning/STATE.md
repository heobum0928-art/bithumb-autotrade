---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: unknown
last_updated: "2026-05-19T04:10:15.385Z"
progress:
  total_phases: 3
  completed_phases: 2
  total_plans: 6
  completed_plans: 6
  percent: 100
---

# Project State: 빗썸 펌핑 단타봇 — 검증 체계 전환

## Project Reference

**Core Value:** 검증되지 않은 전략에는 실제 돈을 넣지 않는다 — 데이터 → 백테스트 → 검증 통과한 것만 실거래로 간다

**Current Focus:** Phase 02 — backtest-engine

---

## Current Position

Phase: 02 (backtest-engine) — READY FOR VERIFICATION
Plan: 3 of 3
| Field | Value |
|-------|-------|
| Milestone | 검증 체계 전환 |
| Current Phase | 2 — Backtest Engine |
| Current Plan | 3 of 3 (all plans complete) |
| Phase Status | Ready for verification |
| Overall Progress | 1/3 phases complete (6/6 plans) |

```
Progress: [██████████] 100%
Phase 1: [✓] Phase 2: [~] Phase 3: [ ]
```

---

## Phase Summary

| Phase | Goal | Status |
|-------|------|--------|
| 1 — Tick Recording Infrastructure | 봇 실거래 차단 + 초 단위 틱 DB 축적 시작 | Not started |
| 2 — Backtest Engine | 틱 재생 기반 독립 시뮬레이션 엔진 | Not started |
| 3 — Strategy Validation | Train/test 분할 OOS 검증 + GO/NO-GO 판정 | Not started |

---

## Accumulated Context

### Key Decisions

| Decision | Rationale | Date |
|----------|-----------|------|
| 3-phase coarse structure | 요구사항이 수집→엔진→검증 세 단계로 자연 분리됨. 세분화 불필요 | 2026-05-19 |
| Phase 1+2 병행 개발 허용 | 틱 DB 스키마가 확정되면 엔진 개발은 데이터 없이도 가능. 2~3주 대기 기간을 활용 | 2026-05-19 |
| log_tick/get_ticks 시그니처 동결 | Phase 2 백테스트 엔진이 직접 import — 변경 시 계약 파기 | 2026-05-19 |
| RECORD_ONLY 기본값 True | config.yaml 미설정/읽기실패 시에도 실거래 차단 — 안전 우선 (01-02) | 2026-05-19 |
| 거래소 시각 별도 _ex_ts dict 보관 | deque 튜플 3원소 불변 유지 — get_signal/get_preemptive_signal 무중단 (01-02) | 2026-05-19 |
| 펌핑 추적 5분→10분 연장 (D-05) | pump_log 5분 집계는 유지, 루프 종료만 elapsed 기반으로 분리 — 약 60틱/이벤트 축적 (01-03) | 2026-05-19 |
| 라이브 타임존 검증 통과 | 빗썸 WS exchange_ts vs recv_ts 델타 0.65~0.72초 — KST(UTC+9) 가정 정확, ±9h 오류 없음 (01-03) | 2026-05-19 |
| DataSlice로 lookahead 코드 차단 | 판정 함수에 원본 list 대신 커서 래퍼 전달, 미래 인덱스 접근 시 IndexError — bias를 런타임에 물리 차단 (02-01) | 2026-05-19 |
| 백테스트 DB 읽기 전용 | load_events는 SELECT만 — INSERT/UPDATE/DELETE/CREATE 미사용, 봇 데이터 무오염 보장 (02-01) | 2026-05-19 |
| _close() 헬퍼로 Trade dict 생성 분리 | TP/SL/시간초과 3개 청산 경로가 동일 형식·동일 비용 계산을 거치도록 강제 (02-02) | 2026-05-19 |
| 다음 틱(cursor+1) 체결 강제 | 진입/청산 모두 다음 틱 price 사용 — lookahead bias 차단, 갭 틱 TP/SL 판정 제외 (02-02) | 2026-05-19 |
| MDD 누적손익 단순합 모델 채택 | 복리 아님 — Python 학습 중 사용자에게 투명한 for-loop 유지 (02-03) | 2026-05-19 |
| 지표는 stdlib statistics만 사용 | 정규근사 95% CI(Z_95=1.96), pstdev 금지(표본표준편차), scipy 의존 없음 (02-03) | 2026-05-19 |

### Known Constraints

- 틱 데이터 축적에 실세계 2~3주 소요 — Phase 3 착수 불가까지의 최소 대기
- pump_log 기존 스키마(1m/2m/3m/5m 컬럼) 유지 필수 — 기존 분석 스크립트 의존
- config.yaml(API 키) git 커밋 절대 금지
- 매매 코드 변경 시 사용자 검토 후 진행

### Pitfalls to Watch

- WS 무음 단절 → 틱 갭 미탐지 (Phase 1에서 반드시 해결)
- exchange_ts / recv_ts 분리 누락 → 스키마 재설계 불가 (Phase 1 시작 전 확정)
- Lookahead bias — TimeBarrier/DataSlice 추상화 필수 (Phase 2)
- 그리드 서치 과적합 — train 전용 파라미터 서치 + test는 최종 1회 (Phase 3)

### Blockers

(없음)

### Todos

- [ ] Phase 1 검증 (`/gsd:verify-phase 1`) — 3개 플랜 모두 완료, 검증 대기
- [ ] Phase 2 검증 (`/gsd:verify-phase 2`) — 3개 플랜 모두 완료, 검증 대기
- [ ] 틱 데이터 축적 (2~3주) — pump_ticks 비어 있어 실 백테스트는 "이벤트 없음", 엔진 자체는 합성 데이터 검증 완료

---

## Session Continuity

**Last session:** 2026-05-19T04:10:15.381Z
**Next action:** `/gsd:verify-phase 2` — Backtest Engine 검증 (3/3 플랜 완료)

---
*State initialized: 2026-05-19*
*Last updated: 2026-05-19 after roadmap creation*
