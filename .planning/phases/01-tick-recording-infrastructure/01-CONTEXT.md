# Phase 1: Tick Recording Infrastructure - Context

**Gathered:** 2026-05-19
**Status:** Ready for planning

<domain>
## Phase Boundary

봇이 실거래를 완전히 차단한 채(RECORD_ONLY), 펌핑 감지 이벤트의 십초 단위 가격 경로를 새 DB 테이블(`pump_ticks`)에 축적한다. 거래소 시각/수신 시각을 분리 기록하고, WebSocket 단절 갭을 명시적으로 표시해 백테스트가 오염 구간을 식별할 수 있게 한다.

요구사항: REC-01, REC-02, REC-03, REC-04. 백테스트 엔진(Phase 2)과 전략 검증(Phase 3)은 범위 밖.

</domain>

<decisions>
## Implementation Decisions

### 실거래 차단 (RECORD_ONLY)
- **D-01:** 실거래 차단 게이트는 `scripts/alt_monitor.py`의 `do_buy` / `do_buy_limit` / `do_sell` 함수 첫 줄에 둔다. RECORD_ONLY가 켜져 있으면 즉시 `return None` 하고 "RECORD_ONLY 차단됨" 로그를 남긴다. 진입 결정 로직(신호 감지·필터)은 그대로 동작하며 주문 실행만 막는다.
- **D-02:** RECORD_ONLY 스위치는 `config.yaml`의 `trading.record_only` 플래그로 관리한다. 기본값 `true`(안전). 실거래 재개는 별도 마일스톤이므로 이번 마일스톤 동안 `true` 고정.
- **D-03:** 차단 상태에서도 신호 감지는 평소처럼 `signal_log` / `pump_log`에 계속 기록한다. 데이터 수집이 이번 phase의 목적이므로 신호·펌핑 이벤트는 모두 남긴다.

### 틱 기록 범위·간격
- **D-04:** 틱 기록 간격은 10초 고정. 기존 `start_pump_tracker`의 `sleep(10)` 루프를 재사용한다 — 추가 API 호출 없음, 구현 리스크 최소. 로드맵 성공기준·STATE.md와 일치.
- **D-05:** 펌핑 이벤트 1건당 틱 추적 지속시간은 10분(현재 5분에서 연장). 10초 간격 × 10분 = 약 60틱. 진입 후 익절·손절 경로와 펌핑 후 하락구간까지 포착.
- **D-06:** 틱 기록 대상은 기존 펌핑 감지 로직이 `pump_log`에 등록하는 이벤트만(`queue_pump` 시점에 틱 추적도 함께 시작). 감지 기준은 변경하지 않는다.

### 틱 스키마 · pump_log 연계
- **D-07:** 새 `pump_ticks` 테이블을 만들고 `pump_id` 컬럼으로 `pump_log.id`를 참조한다(FK 관계). 펌핑 1건 = `pump_log` 1행 + `pump_ticks` N행. 백테스트는 JOIN으로 이벤트 단위 경로를 복원한다.
- **D-08:** 틱 행 컬럼: `pump_id`, `seq`(이벤트 내 순번), `exchange_ts`, `recv_ts`, `price`, `acc_value`(누적 거래대금), `volume_power`(체결강도), 갭/추정 플래그. WebSocket이 이미 제공하는 세 값(closePrice·value·volumePower)을 모두 저장해 향후 거래량 기반 전략 백테스트 여지를 남긴다.
- **D-09:** `pump_log`의 기존 집계 컬럼(`price_1m`~`price_5m`, `peak_price`, `max_drop_pct`, `pullback_2pct`, `bounce_after` 등)과 `update_pump_path` 로직은 그대로 유지한다. `pump_ticks`는 순수 추가. 기존 분석 스크립트(`show_pnl.py` 등)의 동작을 보장 — STATE.md 제약("pump_log 기존 스키마 유지 필수")과 일치.

### WS 갭 · 시각 기록
- **D-10:** WebSocket 단절 갭은 각 틱 행의 `gap_before` 플래그로 기록한다(별도 테이블·센티넬 행 없음). 직전 틱과의 수신 간격이 임계치를 넘으면 그 틱의 `gap_before=1`. 백테스트가 틱 재생 중 갭 행을 바로 식별, JOIN 불필요.
- **D-11:** 거래소 발생 시각(`exchange_ts`)이 빗썸 WS 메시지에 없으면 `recv_ts` 값을 복사하고 `ts_estimated=1` 플래그로 표시한다. `exchange_ts` / `recv_ts` 두 컬럼 스키마는 항상 유지 — 백테스트가 추정값 여부를 구분할 수 있다.
- **D-12:** 갭 판정 기준은 틱 간격 임계치. 같은 이벤트 내 직전 틱과의 `recv_ts` 간격이 예상 10초의 N배(예: 30초) 이상이면 갭으로 판정. 임계치는 상수 파라미터로 관리.

### Claude's Discretion
- `pump_ticks` 컬럼의 정확한 SQL 타입, 인덱스(`pump_id` 인덱스 권장) 설계
- 갭 판정 임계치(N배)의 구체적 상수값
- `seq` 컬럼을 절대 순번으로 할지 elapsed 초로 할지
- 틱 기록 함수(`log_tick` / `get_ticks`)의 정확한 시그니처 — 단, Phase 2가 의존하므로 명확해야 함
- `init_db()` 마이그레이션 패턴(기존 `ALTER TABLE` try/except 방식 따름)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

이 프로젝트에는 별도 외부 spec/ADR 문서가 없다. 요구사항과 제약은 아래 계획 문서에 담겨 있다.

### 프로젝트 요구사항·범위
- `.planning/PROJECT.md` — 마일스톤 핵심 가치(검증 전 실거래 금지), 제약, Out of Scope
- `.planning/REQUIREMENTS.md` — REC-01~04 상세 정의
- `.planning/ROADMAP.md` §"Phase 1: Tick Recording Infrastructure" — 목표, 4개 성공 기준
- `.planning/STATE.md` §"Known Constraints" / "Pitfalls to Watch" — pump_log 스키마 유지 필수, WS 무음 단절·exchange_ts 분리 주의

### 코드베이스 맵
- `.planning/codebase/ARCHITECTURE.md` — PriceTracker, pump_tracker, 데이터 흐름
- `.planning/codebase/CONVENTIONS.md` — 네이밍·스타일 규칙

### 리서치 필요 (코드베이스만으로 불충분)
- 빗썸 API 2.0 WebSocket ticker 메시지 스키마 — `content`에 거래소 발생 시각 필드(date/time/timestamp 등)가 있는지 확인 필요. D-11(exchange_ts 부재 시 대응)의 적용 여부를 결정.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/alt_monitor.py` `start_pump_tracker()` (L174~254): 이미 10초마다 `tracker.get_latest_price(coin)`를 읽어 1/2/3/5분 스냅샷만 저장하고 나머지는 폐기. 이 루프에 틱 INSERT를 추가하면 D-04·D-05 구현 — 추가 API 호출 없음.
- `scripts/alt_monitor.py` `queue_pump()` (L323~333): 펌핑 감지 시 추적 큐 등록 지점. 틱 추적 시작 트리거(D-06).
- `bithumb/db.py` `log_pump()` / `update_pump_path()` (L168~190): pump_log 쓰기 패턴. `log_tick()` / `get_ticks()`를 같은 모듈·같은 스타일로 추가.
- `bithumb/db.py` `init_db()` (L88~102): 테이블 생성 + `ALTER TABLE` try/except 마이그레이션 패턴. `pump_ticks`를 `CREATE_SQL`에 추가.
- `do_buy` (L802), `do_buy_limit` (L835), `do_sell` (L894): RECORD_ONLY 게이트 삽입 지점(D-01).

### Established Patterns
- WebSocket `on_message` (L448~472)는 `closePrice`·`value`·`volumePower`만 버퍼링하며 `time.time()`(수신 시각)으로 deque에 적재. 거래소 시각은 현재 미사용 — D-11 리서치 대상.
- WS 재연결: `on_close`/`run()` 루프가 5초 후 자동 재연결(L477~496). on_close 이벤트는 로깅만 함 — 갭 탐지에 활용 가능.
- DB 접근: `_conn()` 컨텍스트 매니저, `sqlite3.Row` factory, 모듈 레벨 함수(클래스 없음).
- config 로딩: `config.yaml` → `_get_cfg()` 지연 로딩 패턴.

### Integration Points
- `pump_ticks` 테이블: `bithumb/db.py` `CREATE_SQL`에 추가, `pump_id`로 `pump_log.id` 참조.
- 틱 쓰기: `start_pump_tracker()` 루프 내 `update_pump_path()` 호출 옆에 `log_tick()` 추가.
- RECORD_ONLY 플래그: `config.yaml` `trading.record_only` → `do_buy`/`do_buy_limit`/`do_sell` 게이트.
- Phase 2 백테스트 엔진은 `pump_ticks` 스키마 + `log_tick`/`get_ticks` 함수에 의존 — 시그니처를 명확히 확정해야 함.

</code_context>

<specifics>
## Specific Ideas

- 사용자는 머신비전 엔지니어(C#/C++ 주력, Python 학습 중). 매매 관련 코드 변경은 사용자 검토 후 진행, 한 번에 한 단계씩.
- 핵심 원칙: "검증 전엔 실제 돈을 넣지 않는다." RECORD_ONLY 기본 true 고정은 타협 불가.
- 기존 분석 스크립트를 깨지 않는 것이 우선 — pump_ticks는 순수 추가, pump_log는 불변.

</specifics>

<deferred>
## Deferred Ideas

- 펌핑 감지 기준 완화로 더 많은 샘플 확보 — 감지 로직 변경은 이번 phase 범위 밖. 백테스트로 진입 기준을 검증한 뒤 별도 판단.
- 초/1~2초 단위 고해상도 틱 기록 — 10초 간격으로 시작. 데이터 부족이 확인되면 향후 재검토.
- 거래량 기반 전략 백테스트 — `acc_value`·`volume_power`를 틱에 저장만 해두고(D-08), 실제 전략화는 Phase 3 이후.

</deferred>

---

*Phase: 01-tick-recording-infrastructure*
*Context gathered: 2026-05-19*
