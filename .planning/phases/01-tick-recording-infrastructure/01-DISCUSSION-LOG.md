# Phase 1: Tick Recording Infrastructure - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-19
**Phase:** 01-tick-recording-infrastructure
**Areas discussed:** 실거래 차단 방식, 틱 기록 범위·간격, 틱 스키마·pump_log 연계, WS 갭·시각 기록

---

## 실거래 차단 방식 (RECORD_ONLY)

### Q: RECORD_ONLY 모드에서 실거래 주문을 어디서 차단할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| do_buy/do_sell 게이트 | 함수 첫 줄 RECORD_ONLY 체크 → return None + 로그 | ✓ |
| client 레벨 차단 | BithumbClient 주문 메서드에서 예외 발생 | |
| 두 지점 모두 | 게이트 + client 안전망 이중 방어 | |

**User's choice:** do_buy/do_sell 게이트 (추천)

### Q: RECORD_ONLY 스위치는 어떻게 관리할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| config.yaml 플래그 | trading.record_only: true, 기본 true | ✓ |
| 코드 상수 | alt_monitor.py 상단 RECORD_ONLY 상수 | |
| 환경변수 / .env | .env로 외부 제어 | |

**User's choice:** config.yaml 플래그 (추천)

### Q: 차단되더라도 신호 감지 기록(signal_log)은 계속 쌓을까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 계속 기록 | 신호·펌핑은 signal_log/pump_log에 평소대로 저장 | ✓ |
| 기록 안 함 | 틱 데이터만 수집 | |

**User's choice:** 계속 기록 (추천)

---

## 틱 기록 범위·간격

### Q: 틱 기록 간격을 얼마로 할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 10초 고정 | 기존 pump_tracker 루프 재사용 | ✓ |
| WS 수신마다 | 초·1~2초 단위 고해상도 | |
| 5초 간격 | 10초와 초단위의 절충 | |

**User's choice:** 10초 고정 (추천)

### Q: 펌핑 이벤트 1건당 틱을 얼마동안 추적할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 10분 | ~60틱, 진입 후 경로 + 펌핑 후 하락 포착 | ✓ |
| 5분 유지 | 기존 동일, ~30틱 | |
| 20분 | ~120틱, 긴 보유까지 분석 | |

**User's choice:** 10분 (추천)

### Q: 어떤 이벤트의 틱을 기록할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 펌핑 감지분만 | 기존 pump_log 등록 이벤트만 | ✓ |
| 펌핑 기준 완화해 더 넓게 | 감지 기준 완화로 샘플 확보 | |

**User's choice:** 펌핑 감지분만 (추천)

---

## 틱 스키마·pump_log 연계

### Q: 새 pump_ticks 테이블을 pump_log와 어떻게 연결할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| pump_id FK 참조 | pump_ticks.pump_id → pump_log.id | ✓ |
| 독립 테이블 (coin+시각 키) | pump_log와 무관하게 식별 | |

**User's choice:** pump_id FK 참조 (추천)

### Q: 틱 행마다 저장할 컬럼 구성은?

| Option | Description | Selected |
|--------|-------------|----------|
| 가격+거래대금+체결강도 | price, acc_value, volume_power + 시각/gap | ✓ |
| 가격만 최소한 | price + 시각/gap만 | |

**User's choice:** 가격+거래대금+체결강도 (추천)

### Q: pump_log의 기존 집계 컬럼은 어떻게 할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 그대로 유지 | 기존 컬럼·update_pump_path 유지, pump_ticks 순수 추가 | ✓ |
| pump_ticks로 일원화 | 집계 컬럼 제거, 틱에서 파생 | |

**User's choice:** 그대로 유지 (추천)

---

## WS 갭·시각 기록

### Q: WebSocket 단절 갭을 어떻게 DB에 기록할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 틱 행에 gap 플래그 | 각 틱 행 gap_before 컬럼 | ✓ |
| 갭 센티넬 행 삽입 | price=NULL 마커 행 | |
| 별도 ws_gaps 테이블 | 단절 구간 별도 테이블 | |

**User's choice:** 틱 행에 gap 플래그 (추천)

### Q: 빗썸 WS가 exchange_ts를 제공하지 않을 경우 어떻게 할까요?

| Option | Description | Selected |
|--------|-------------|----------|
| 수신시각 복사 + 플래그 | recv_ts 복사 + ts_estimated=1 | ✓ |
| recv_ts만 저장 | exchange_ts 컬럼 NULL | |
| 리서치 후 결정 | WS 메시지 필드 확인 후 결정 | |

**User's choice:** 수신시각 복사 + 플래그 (추천)
**Notes:** WS 메시지의 실제 시각 필드 존재 여부는 별도 리서치 대상으로 CONTEXT.md canonical_refs에 명시.

### Q: 갭으로 판정하는 기준은?

| Option | Description | Selected |
|--------|-------------|----------|
| 틱 간격 임계치 | recv 간격이 예상의 N배 초과 시 갭 | ✓ |
| WS on_close 이벤트 기반 | 단절 이벤트 시각 직접 기록 | |
| 둘 다 사용 | on_close + 간격 임계치 | |

**User's choice:** 틱 간격 임계치 (추천)

---

## Claude's Discretion

- pump_ticks 컬럼 SQL 타입·인덱스 설계
- 갭 판정 임계치(N배) 상수값
- seq 컬럼: 절대 순번 vs elapsed 초
- log_tick/get_ticks 함수 시그니처
- init_db 마이그레이션 패턴(기존 ALTER TABLE try/except)

## Deferred Ideas

- 펌핑 감지 기준 완화로 샘플 확보 — phase 범위 밖
- 초/1~2초 고해상도 틱 — 10초로 시작, 부족 시 재검토
- 거래량 기반 전략 백테스트 — Phase 3 이후
