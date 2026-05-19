# Roadmap: 빗썸 펌핑 단타봇 — 검증 체계 전환

**Milestone:** 검증 우선 체계 전환 (실거래 없이 EV 판정)
**Granularity:** Coarse (3 phases)
**Coverage:** 14/14 v1 requirements mapped

---

## Phases

- [ ] **Phase 1: Tick Recording Infrastructure** - 봇을 기록 전용 모드로 전환 + 초 단위 틱 DB 수집 시작 (실거래 차단 확인 후 2~3주 운영)
- [ ] **Phase 2: Backtest Engine** - 틱 데이터를 시간순 재생해 전략을 시뮬레이션하는 독립 오프라인 엔진 제작 (Phase 1 운영 기간 중 병행 개발 가능)
- [ ] **Phase 3: Strategy Validation** - 충분한 틱 데이터로 out-of-sample 검증 사이클을 돌려 EV 양수/음수 GO/NO-GO 결론 도출

---

## Phase Details

### Phase 1: Tick Recording Infrastructure
**Goal**: 봇이 실거래를 완전 차단한 채 펌핑 이벤트의 초 단위 가격 경로를 DB에 축적한다
**Depends on**: Nothing (first phase)
**Requirements**: REC-01, REC-02, REC-03, REC-04
**Success Criteria** (what must be TRUE):
  1. 봇을 실행해도 시장가 매수/매도 API 호출이 단 한 번도 발생하지 않는다 — 로그에 "RECORD_ONLY 차단됨" 메시지가 신호마다 기록된다
  2. 펌핑 감지 후 pump_ticks 테이블에 10초 간격 틱 행이 쌓이며, SELECT COUNT(*) 로 증가를 확인할 수 있다
  3. 각 틱 행에 거래소 발생 시각(exchange_ts)과 수집기 수신 시각(recv_ts)이 분리 저장되어 있다
  4. WebSocket이 단절됐다 복구된 구간에 틱 갭이 DB에 명시적으로 기록되어 백테스트가 오염 구간을 식별할 수 있다
**Plans**: 3 plans
- [x] 01-01-PLAN.md — pump_ticks 테이블 스키마 + log_tick/get_ticks 함수
- [x] 01-02-PLAN.md — RECORD_ONLY 게이트 + WS 거래소 시각 파싱
- [x] 01-03-PLAN.md — start_pump_tracker 틱 INSERT 배선 + 갭 감지 + 10분 추적

### Phase 2: Backtest Engine
**Goal**: 수집된 틱 데이터를 재생해 전략을 실거래 없이 시뮬레이션하고 EV·승률·MDD를 산출하는 독립 스크립트가 존재한다
**Depends on**: Phase 1 (pump_ticks 테이블 스키마 + log_tick/get_ticks 함수)
**Requirements**: BT-01, BT-02, BT-03, BT-04, BT-05
**Success Criteria** (what must be TRUE):
  1. scripts/backtest.py가 봇 프로세스 없이 단독 실행되며, alt_monitor.py나 bithumb/client.py를 import하지 않는다
  2. 틱을 시간순으로 재생하며 매 틱에서 손절/익절 조건을 검사한다 — 진입 후 경로 없이 스냅샷만으로 청산을 결정하지 않는다
  3. 진입 판정 시 커서 이후(미래) 데이터를 참조하면 런타임 에러가 발생한다 (lookahead 방지가 코드로 강제된다)
  4. 백테스트 리포트에 수수료(왕복 0.5%)와 슬리피지(기본 1.0%) 적용값이 명시되고, 슬리피지 0%/0.5%/1%/2% 시나리오별 EV가 함께 출력된다
  5. 백테스트 결과로 승률·EV·MDD·거래수·95% 신뢰구간이 출력된다
**Plans**: 3 plans
- [x] 02-01-PLAN.md — 스크립트 골격 + 전략 상수 + DataSlice lookahead 차단 + load_events
- [ ] 02-02-PLAN.md — 비용 헬퍼 + simulate_event 틱 재생 진입/청산 시뮬레이션
- [ ] 02-03-PLAN.md — 지표 계산(EV·MDD·CI) + 슬리피지 4시나리오 리포트 + CSV 출력

### Phase 3: Strategy Validation
**Goal**: 충분한 표본의 틱 데이터를 train/test로 분할해 out-of-sample 검증을 완료하고, 현재 전략의 EV가 양수인지 음수인지 데이터로 명확히 판정한다
**Depends on**: Phase 1 (2~3주 틱 데이터 축적), Phase 2 (백테스트 엔진)
**Requirements**: VAL-01, VAL-02, VAL-03, VAL-04, VAL-05
**Success Criteria** (what must be TRUE):
  1. 틱 데이터가 시간순으로 train/test 두 구간으로 분할되며, 파라미터 서치 중 test 구간 데이터를 단 한 번도 쿼리하지 않는다
  2. 파라미터 그리드 서치가 train 셋에만 실행되고, 조합별 EV가 정렬된 테이블로 출력된다
  3. 결과 리포트가 코인별, 진입 시간대별로 분해되어 "특정 코인/시간대에만 편향이 있는가"를 확인할 수 있다
  4. 표본 수가 사전 정의한 최소 기준(MIN_SAMPLE)에 미달하거나 신뢰구간이 허용 폭을 초과할 때 코드가 경고를 출력하며 결론 선언을 막는다
  5. 최종 리포트에 "GO: test 셋 EV 양수, 전략 유효" 또는 "NO-GO: test 셋 EV 음수/불충분, 전략 폐기" 판정이 명시된다
**Plans**: TBD

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Tick Recording Infrastructure | 0/3 | Not started | - |
| 2. Backtest Engine | 0/3 | Not started | - |
| 3. Strategy Validation | 0/? | Not started | - |

---

## Sequencing Note

Phase 1과 Phase 2는 **병행 개발** 가능하다.

- Phase 1은 완료 즉시 봇을 기록 전용으로 재시작해 틱 축적을 시작한다.
- Phase 2는 Phase 1 스키마가 확정되면 개발 시작 가능하며, 실세계 2~3주 데이터 축적 기간 동안 병행한다.
- Phase 3은 Phase 1의 충분한 틱(최소 표본 기준 충족)과 Phase 2 엔진이 모두 준비된 후 시작한다.

---
*Roadmap created: 2026-05-19*
*Last updated: 2026-05-19 after Phase 2 planning*
