# Phase 2: Backtest Engine - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-19
**Phase:** 2-backtest-engine
**Areas discussed:** 진입 전략 정의, 청산 규칙 모델, 리포트 출력 형식, Lookahead 강제 + 데이터 품질

---

## 진입 전략 정의

### Q: 백테스트가 시뮬레이션할 진입 전략은 무엇인가요?

| Option | Description | Selected |
|--------|-------------|----------|
| 눌림목 진입 (현 전략) | 펌핑 감지 후 고점 대비 -N% 눌림에 진입. PROJECT.md의 현재 유효 전략 | ✓ |
| 프러그형 전략 인터페이스 | 전략을 should_enter(slice)->bool 함수로 추상화 | |
| 눌림목 + 펌핑추격 둘 다 | 두 전략 동시 백테스트해 EV 비교 | |

**User's choice:** 눌림목 진입 (현 전략)

### Q: 진입 체결 가격은 어느 틱의 값을 쓸까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 다음 틱의 price | 조건 충족 틱 다음 틱 가격으로 체결 (주문 지연 반영 + lookahead 안전) | ✓ |
| 해당 틱의 price 그대로 | 조건 충족 그 틱 가격으로 즉시 체결 | |

**User's choice:** 다음 틱의 price

### Q: 한 펌핑 이벤트(pump_log 1행)당 진입 횟수는?

| Option | Description | Selected |
|--------|-------------|----------|
| 이벤트당 1회 | 첫 충족 시 1회 진입, 청산 후 이벤트 종료 | ✓ |
| 이벤트당 다중 진입 | 청산 후 재충족 시 재진입 허용 | |

**User's choice:** 이벤트당 1회

### Q: 눌림목 진입 트리거 — 이벤트 내 진입 조건 정의?

| Option | Description | Selected |
|--------|-------------|----------|
| 주행 고점 대비 -N% | 동적 running peak 대비 -N% 하락 시 진입 | ✓ |
| 감지가(base_price) 대비 -N% | pump_log base_price 고정 기준 -N% | |

**User's choice:** 주행 고점 대비 -N%

### Q: 고점 갱신 후 진입 대기 구간에 상한을 둘까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 이벤트 전구간 허용 | 10분(~60틱) 전체에서 언제든 -N% 충족 시 진입 | ✓ |
| 감지 후 N분 이내만 | 펌핑 감지 후 일정 시간 안의 눌림만 진입 | |

**User's choice:** 이벤트 전구간 허용

---

## 청산 규칙 모델

### Q: 가상 포지션 청산 조건을 어떻게 구성할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 익절 + 손절 + 시간초과 | TP·SL 미도달 시 마지막 틱 강제 청산 | ✓ |
| 익절 + 손절만 | TP/SL 중 먼저 닿는 것 | |
| 익절+손절+트레일링스톱 | 고정 TP/SL + 고점 추종 스톱 | |

**User's choice:** 익절 + 손절 + 시간초과

### Q: TP/SL 도달 판정 시 청산 체결가는?

| Option | Description | Selected |
|--------|-------------|----------|
| 다음 틱의 price | 임계 돌파 감지 틱의 다음 틱 가격 (진입과 일관) | ✓ |
| 임계값 그대로 | 정확히 +X%/-Y% 가격으로 체결 가정 | |

**User's choice:** 다음 틱의 price

### Q: Phase 2에서 TP/SL/시간초과 값은 어떻게 다룰까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 고정 상수 (기본값 하나) | 파일 상단 상수, 1조합만. Phase 3가 파라미터화 | ✓ |
| CLI 인자로 주입 | --tp --sl 플래그로 다양한 값 테스트 | |

**User's choice:** 고정 상수 (기본값 하나)

### Q: gap_before 틱 구간에서 TP/SL 판정은?

| Option | Description | Selected |
|--------|-------------|----------|
| 갭 구간 건너뛰고 다음 틱 판정 | gap_before=1 틱은 가격 갱신만, 판정 제외 (낙관적 청산 방지) | ✓ |
| 갭 틱도 일반 틱처럼 처리 | 모든 틱 동등 판정 | |

**User's choice:** 갭 구간 건너뛰고 다음 틱에서 판정

---

## 리포트 출력 형식

### Q: 백테스트 결과 출력 채널은?

| Option | Description | Selected |
|--------|-------------|----------|
| stdout 요약 + CSV 상세 | 터미널 요약 테이블 + 거래별 CSV 내보내기 | ✓ |
| stdout 테이블만 | 파일 없음 | |
| CSV만 | 즉시 결과 확인 불편 | |

**User's choice:** stdout 요약 + CSV 상세

### Q: 슬리피지 시나리오(0/0.5/1/2%) EV는 어떻게 보여줄까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 4행 비교 테이블 항상 출력 | 한 실행에 4개 시나리오 EV 나란히 | ✓ |
| 기본 1개 + 플래그로 선택 | --slippage 플래그로 값 교체 | |

**User's choice:** 4행 비교 테이블 항상 출력

### Q: EV 95% 신뢰구간 산출 방식은?

| Option | Description | Selected |
|--------|-------------|----------|
| 정규근사 (표준오차 기반) | 평균±z·SE. 단순, VAL-04 경고와 연결 | ✓ |
| 부트스트랩 재샘플링 | REQUIREMENTS Out of Scope, 표본 적어 과신뢰 위험 | |

**User's choice:** 정규근사 (표준오차 기반)

---

## Lookahead 강제 + 데이터 품질

### Q: Lookahead 방지를 어떤 추상화로 강제할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| DataSlice 래퍼 객체 | 커서까지만 노출, 이후 접근 시 IndexError raise | ✓ |
| assert 체크만 | 함수 내부 assert로 검증, 강제력 약함 | |

**User's choice:** DataSlice 래퍼 객체

### Q: 갭(gap_before)이 많은 펌핑 이벤트 자체는 어떻게 처리할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 갭 비율 임계 초과 시 이벤트 제외 | 임계 넘으면 이벤트 통째 제외, 리포트에 제외 건수 표기 | ✓ |
| 모든 이벤트 포함 | 갭 틱만 판정 제외, 이벤트는 유지 | |

**User's choice:** 갭 비율 임계 초과 시 이벤트 제외

### Q: 재생 시간축과 ts_estimated 틱 처리는?

| Option | Description | Selected |
|--------|-------------|----------|
| exchange_ts 시간축 + 추정비율 경고 | exchange_ts 기준 재생, 이벤트당 추정틱 비율을 리포트 경고로 | ✓ |
| recv_ts 시간축 고정 | 수신 시각 기준, 수집 지연 오차 섞임 | |

**User's choice:** exchange_ts 시간축 + 추정비율 경고

## Claude's Discretion

- 진입 -N%, TP, SL, 시간초과, 갭 임계값, 갭 이벤트 제외 임계값의 구체 상수값
- DataSlice 클래스 인터페이스 세부
- CSV 경로·컬럼, stdout 테이블 포맷
- MDD 계산 방식, 데이터 부족 이벤트 스킵 처리

## Deferred Ideas

- 트레일링 스톱 청산 (Phase 3 별도 판단)
- TP/SL/진입 파라미터 그리드 서치 (Phase 3, VAL-02)
- 펌핑 추격 전략 백테스트 (EV 음수 확정, 제외)
- 거래량 기반 전략 (Phase 3 이후)
- 부트스트랩/Monte Carlo CI (Out of Scope)
