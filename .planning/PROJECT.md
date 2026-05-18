# 빗썸 펌핑 단타봇 — 검증 체계 전환

## What This Is

빗썸 거래소에서 펌핑하는 신규/알트코인을 단타하는 자동매매 봇 프로젝트다. 지금까지는 전략을 검증 없이 즉흥적으로 운영해 손실(-65,000원)이 누적됐다. 이번 마일스톤은 봇을 **"검증 우선" 체계**로 전환하는 것 — 실거래 없이 데이터를 수집하고, 백테스트로 전략을 검증한 뒤에만 실거래를 허용한다. 머신비전 엔지니어가 Python·퀀트를 학습하며 장기적으로 발전시키는 프로젝트다.

## Core Value

**검증되지 않은 전략에는 실제 돈을 넣지 않는다.** 데이터 → 백테스트 → 검증을 통과한 것만 실거래로 간다.

## Requirements

### Validated

<!-- 기존 코드에서 확인된 동작 (brownfield) -->

- ✓ 빗썸 REST API 클라이언트 (인증, 잔고, 주문) — existing
- ✓ WebSocket 실시간 시세 수신 (458코인) — existing
- ✓ 펌핑 감지 + pump_log 가격경로 기록 (1/2/3/5분 스냅샷) — existing
- ✓ SQLite 거래/신호/펌핑 로그 DB — existing
- ✓ watchdog 프로세스 감시 + 자동 재시작 — existing
- ✓ 텔레그램 알림 + 일일 AI 분석 자동화 — existing
- ✓ 눌림목 진입 전략 (지정가 매수, -7% 타겟) — existing

### Active

<!-- 이번 마일스톤 목표 — 검증 전엔 가설 -->

- [ ] 봇을 기록 전용 모드로 전환 (실거래 OFF, 데이터 수집만)
- [ ] pump_log를 초 단위 틱 경로 저장으로 업그레이드 (4개 스냅샷 → ~120개 틱)
- [ ] 충분한 틱 데이터셋 축적 (2~3주, 실거래 위험 0)
- [ ] 백테스트 엔진 제작 (틱 데이터로 전략을 실거래 없이 시뮬레이션)
- [ ] 전략 검증 사이클 확립 (가설 → 백테스트 → out-of-sample → 페이퍼)
- [ ] 데이터 기반 전략 결론 도출 (EV 양수/음수 명확히 판정)

### Out of Scope

<!-- 명시적 경계 — 재추가 방지 -->

- 검증 안 된 전략의 실거래 — 손실 누적의 근본 원인. 백테스트 통과 전엔 절대 금지
- 즉시진입·신규상장·선진입 전략 — 데이터로 EV 음수 확인되어 폐기
- 실거래 재개 — 검증 결론 도출 후 별도 마일스톤에서 판단
- 파라미터 즉흥 변경 — "바꾸고 실거래로 보고 또 바꾸기" 사이클 자체가 폐기 대상

## Context

- **개발자**: 머신비전 엔지니어 (C#/C++ 주력, Python 학습 중). 장기 학습 프로젝트로 진행.
- **누적 성과**: 약 57건 거래, 승률 ~25%, -65,000원. 초기 4건 행운거래(+57K)가 음의 EV를 가렸음.
- **17개 에이전트 진단 결론**: 펌핑 추격 전략은 엣지 없음. 2초 폴링 속도 열위 + 펌핑 후 평균 -1.2~2.5% 하락 + 왕복비용 2.5~3%. 현 구조로는 어떤 파라미터도 EV 음수.
- **데이터 한계**: 현재 pump_log는 2일치(5/16~5/18), 36코인, 1/2/3/5분 스냅샷뿐. 진입 후 가격 경로가 없어 정밀 백테스트 불가.
- **봇 현황**: 2026-05-18 정지. Windows 시작프로그램 자동시작 비활성화(`.disabled`). 미청산 포지션 없음.
- **자금**: 빗썸에 KRW 122만원 + XRP/SOL(봇 무관 본인 투자분).

## Constraints

- **Tech stack**: Python 3.13, 빗썸 API 2.0 (JWT HS256), SQLite, websocket-client, 단일 봇 프로세스 + watchdog
- **Timeline**: 틱 데이터 축적에 실세계 2~3주 소요 — 백테스트 엔진 단계는 그 기간 동안 병행 가능
- **Budget**: 데이터 수집·백테스트 단계 실거래 손실 0원 (실거래 OFF)
- **Performance**: 틱 기록은 기존 WS 시세 재사용 — API 추가 호출 없음
- **Security**: config.yaml(API 키) git 커밋 금지. 매매 코드 변경은 사용자 검토 후

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 실거래 중단 후 검증 체계로 전환 | 17개 에이전트가 현 전략 엣지 없음 진단, 즉흥 운영이 손실 근본원인 | — Pending |
| 마일스톤 범위 = 검증 체계까지 | 실거래 재개는 검증 결과 본 뒤 판단해야 함 | — Pending |
| 성공 기준 = 검증 결론 도출 | EV 양수든 음수든 데이터로 명확히 알면 성공. 더 잃기 전 아는 것이 가치 | — Pending |
| pump_log 틱 경로 업그레이드 | 추적기가 이미 10초마다 가격을 읽지만 4개만 저장·26개 폐기 중 | — Pending |
| GSD 방식 채택 | 즉흥 변경 사이클을 끊고 조사→계획→검증 규율 강제 | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-19 after initialization*
