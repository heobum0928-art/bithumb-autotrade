# Requirements: 빗썸 펌핑 단타봇 — 검증 체계 전환

**Defined:** 2026-05-19
**Core Value:** 검증되지 않은 전략에는 실제 돈을 넣지 않는다 — 데이터 → 백테스트 → 검증 통과한 것만 실거래로 간다.

## v1 Requirements

검증 체계의 최소 사이클. 각 요구사항은 로드맵 phase에 매핑된다.

### 데이터 수집 (Recording)

- [x] **REC-01**: 봇이 기록 전용 모드(`RECORD_ONLY`)로 동작 — 실거래 주문을 차단하고 데이터 수집만 수행
- [x] **REC-02**: pump_tracker가 펌핑 감지 코인의 초 단위 가격 틱을 DB 테이블에 저장
- [x] **REC-03**: 틱 데이터에 lookahead 방지를 위해 거래소 시각/수신 시각이 구분되어 기록됨
- [x] **REC-04**: WebSocket 단절 시 틱 데이터의 갭이 감지·기록되어 백테스트가 오염 구간을 식별 가능

### 백테스트 엔진 (Backtest)

- [x] **BT-01**: 백테스트 엔진이 봇 코드와 완전 분리된 오프라인 스크립트로 동작 (DB 읽기 전용)
- [x] **BT-02**: 틱 데이터를 시간순 재생하며 진입 조건 평가·가상 포지션 청산을 시뮬레이션
- [x] **BT-03**: 진입 판정에 미래 정보(lookahead)를 사용하지 않음 — 진입 시점에 알 수 있는 데이터만 사용
- [x] **BT-04**: 수수료(왕복 0.5%)와 슬리피지(기본 1%)를 상수 파라미터로 손익 계산에 반영
- [ ] **BT-05**: 백테스트 결과로 승률·EV·MDD·거래수 지표를 출력

### 전략 검증 (Validation)

- [ ] **VAL-01**: 틱 데이터를 train/test로 시간순 분할해 out-of-sample 검증 수행
- [ ] **VAL-02**: 파라미터 그리드 서치로 조합별 EV를 산출·정렬 (train 셋에만 적용)
- [ ] **VAL-03**: 백테스트 결과를 코인별·진입 시간대별로 분해해 통계 출력
- [ ] **VAL-04**: 최소 표본 수 미달·과적합 위험 시 결론을 신뢰하지 않도록 경고
- [ ] **VAL-05**: 전략의 EV 양수/음수를 데이터로 명확히 판정하는 GO/NO-GO 결론 도출

## v2 Requirements

다음 마일스톤(실거래 재개 전 검문소)으로 연기.

### 페이퍼 트레이딩

- **PAPER-01**: 검증 통과 전략을 실시간 시세로 가상 거래 시뮬레이션
- **PAPER-02**: 페이퍼 결과에 백테스트와 동일한 수수료·슬리피지 모델 적용

## Out of Scope

명시적 제외. 스코프 크리프 방지.

| Feature | Reason |
|---------|--------|
| 검증 안 된 전략의 실거래 | 손실 누적의 근본 원인. 백테스트 통과 전 절대 금지 |
| 실거래 재개 | 검증 결론 도출 후 별도 마일스톤에서 판단 |
| Walk-forward / 롤링 윈도우 최적화 | 2~3주 데이터로는 윈도우당 표본 부족, 통계 의미 없음. train/test 2분할로 충분 |
| Monte Carlo 시뮬레이션 | 수십~수백 건 표본에서 재샘플링은 신뢰구간이 너무 넓어 잘못된 확신만 줌 |
| 멀티 심볼 동시 포지션 백테스트 | 현재 봇이 단일 포지션 설계. 범위 밖 |
| 머신러닝 신호 모델 | 데이터가 ML 학습 최소량에 한참 못 미침. 오버피팅 확정 |
| 웹 대시보드 / 실시간 UI | 이 마일스톤의 가치는 UI가 아니라 EV 판정. CLI + CSV로 충분 |
| 거래소 OHLCV로 과거 백테스트 | 캔들 단위라 초단위 진입·슬리피지 재현 불가, 낙관적 오류 유발. 직접 수집 틱만 사용 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| REC-01 | Phase 1 | Complete |
| REC-02 | Phase 1 | Complete |
| REC-03 | Phase 1 | Complete |
| REC-04 | Phase 1 | Complete |
| BT-01 | Phase 2 | Complete |
| BT-02 | Phase 2 | Complete |
| BT-03 | Phase 2 | Complete |
| BT-04 | Phase 2 | Complete |
| BT-05 | Phase 2 | Pending |
| VAL-01 | Phase 3 | Pending |
| VAL-02 | Phase 3 | Pending |
| VAL-03 | Phase 3 | Pending |
| VAL-04 | Phase 3 | Pending |
| VAL-05 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 14 total
- Mapped to phases: 14 (Phase 1: 4, Phase 2: 5, Phase 3: 5)
- Unmapped: 0

---
*Requirements defined: 2026-05-19*
*Last updated: 2026-05-19 after roadmap creation (traceability filled)*
