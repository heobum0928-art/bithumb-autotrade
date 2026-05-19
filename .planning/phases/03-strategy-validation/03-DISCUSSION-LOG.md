# Phase 3: Strategy Validation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-19
**Phase:** 03-strategy-validation
**Areas discussed:** GO/NO-GO 판정 기준, train/test 분할 정책, 그리드 서치 범위, 결과 분해 + 과적합 경고

---

## GO/NO-GO 판정 기준

### Q: GO 판정의 핵심 기준 — test 셋에서 어떤 조건이 충족되면 'GO'인가?

| Option | Description | Selected |
|--------|-------------|----------|
| CI 하한 > 0 (보수적) | test EV 95% CI 하한이 0보다 커야 GO | ✓ |
| test EV > 0 단순 | test 평균 손익률이 양수면 GO | |
| EV > 비용 마진 | test EV가 일정 마진(예: +0.5%) 이상이어야 GO | |

**User's choice:** CI 하한 > 0 (보수적)

### Q: train에서 그리드 서치 후 test로 넘길 파라미터 조합은 몇 개인가?

| Option | Description | Selected |
|--------|-------------|----------|
| train 최고 EV 1조합만 | EV 1위 조합 하나만 test에 1회 적용 | ✓ |
| train 상위 3조합 | 상위 3개를 test에 적용해 비교 | |

**User's choice:** train 최고 EV 1조합만

### Q: 표본이 MIN_SAMPLE에 미달하거나 CI가 너무 넓을 때 최종 판정은?

| Option | Description | Selected |
|--------|-------------|----------|
| 무조건 NO-GO | EV 양수여도 표본 부족 시 NO-GO 선언 | ✓ |
| INCONCLUSIVE 별도 판정 | GO/NO-GO 아닌 3상태 '보류' | |

**User's choice:** 무조건 NO-GO

### Q: GO/NO-GO는 어떤 슬리피지 시나리오의 EV로 판정하는가?

| Option | Description | Selected |
|--------|-------------|----------|
| 슬리피지 1% 기준 | 기본 시나리오(왕복비용 포함 ~2.5%) | ✓ |
| 슬리피지 2% 기준 | 가장 보수적 시나리오 | |
| 4시나리오 모두 GO | 0/0.5/1/2% 전부 통과해야 GO | |

**User's choice:** 슬리피지 1% 기준

**Notes:** GO 기준이 "CI 하한 > 0"이므로 넓은 CI는 자동으로 NO-GO가 됨 — 별도 CI 폭 임계값 불필요.

---

## train/test 분할 정책

### Q: 틱 데이터를 train/test로 시간순 분할할 때 비율은?

| Option | Description | Selected |
|--------|-------------|----------|
| 70 / 30 | train 70% 서치, test 30% 검증 | ✓ |
| 80 / 20 | train 안정 ↑, test 20%는 CI 넓을 위험 | |
| 50 / 50 | test 표본 최대, train EV 추정 불안정 | |

**User's choice:** 70 / 30

### Q: 시간순 분할의 경계를 무엇으로 자르는가?

| Option | Description | Selected |
|--------|-------------|----------|
| 이벤트 수 기준 | 펌핑 이벤트를 detected_at 순 정렬 후 개수로 분할 | ✓ |
| 달력 시간 기준 | 전체 기간을 시간축 절반 지점에서 분할 | |

**User's choice:** 이벤트 수 기준

---

## 그리드 서치 범위

### Q: 그리드 서치로 탐색할 파라미터는 어느 것인가?

| Option | Description | Selected |
|--------|-------------|----------|
| 진입% / TP / SL 3개 | TIMEOUT 600초 고정 | ✓ |
| 4개 전부 | 진입%/TP/SL/시간초과 모두 — 과적합 위험 ↑ | |
| TP / SL 2개 | 진입%도 고정, 청산 규칙만 | |

**User's choice:** 진입% / TP / SL 3개

### Q: 그리드 조밀도 — 각 파라미터당 몇 단계를 시험하는가?

| Option | Description | Selected |
|--------|-------------|----------|
| 거친 그리드 (3~4단계) | 총 조합 수십 개 — 과적합 위험 낮음 | ✓ |
| 세밀 그리드 (6~8단계) | 조합 수백 개 — 우연한 고EV 위험 | |

**User's choice:** 거친 그리드 (3~4단계)

---

## 결과 분해 + 과적합 경고

### Q: 진입 시간대별 분해를 어느 granularity로 할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 4구간 (새벽/오전/오후/저녁) | KST 6시간 블록 4구간 | ✓ |
| 시간별 24구간 | 시간단위 — 구간당 표본 1~2건 위험 | |
| 주간 / 야간 2구간 | 가장 거친 분해 | |

**User's choice:** 4구간 (새벽/오전/오후/저녁)

### Q: 코인별 분해에서 결론 왜곡 경고는 어떻게 다루나요?

| Option | Description | Selected |
|--------|-------------|----------|
| 단일 코인 지배율 경고 | 거래/이익이 한 코인에 몰리면 경고 출력 | ✓ |
| 테이블만 출력 | 코인별 지표만, 판단은 사용자에게 | |

**User's choice:** 단일 코인 지배율 경고

### Q: MIN_SAMPLE — test 셋 거래수가 몇 건 미만이면 결론을 막는가?

| Option | Description | Selected |
|--------|-------------|----------|
| 30건 (Phase 2 상수 유지) | backtest.py 기존 MIN_SAMPLE=30 그대로 | ✓ |
| 20건으로 낮춤 | test 30% 작을 것 감안 완화 | |
| 플랜 단계에서 결정 | 실제 축적량 본 뒤 결정 | |

**User's choice:** 30건 (Phase 2 상수 유지)

---

## Claude's Discretion

- 그리드 각 파라미터의 구체 후보값 (backtest.py 현 상수 중심 3~4단계)
- 단일 코인 지배율 경고의 정확한 임계값
- 신규 검증 스크립트 파일명·구조, backtest.py 함수 재사용 방식
- train/test 분할 함수 인터페이스, EV 테이블·분해 테이블 stdout 포맷, CSV 컬럼

## Deferred Ideas

- TIMEOUT_SEC 파라미터 탐색, 트레일링 스톱 청산
- 페이퍼 트레이딩(v2), walk-forward, Monte Carlo (Out of Scope)
- 거래량 기반 신호 전략화, 실거래 재개
