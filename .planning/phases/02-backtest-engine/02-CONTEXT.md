# Phase 2: Backtest Engine - Context

**Gathered:** 2026-05-19
**Status:** Ready for planning

<domain>
## Phase Boundary

수집된 `pump_ticks` 틱 데이터를 시간순으로 재생해, 눌림목 진입 전략을 실거래 없이 시뮬레이션하고 EV·승률·MDD·거래수·신뢰구간을 산출하는 독립 오프라인 스크립트(`scripts/backtest.py`)를 만든다. 봇 프로세스 없이 단독 실행되며 DB는 읽기 전용으로만 접근한다.

요구사항: BT-01, BT-02, BT-03, BT-04, BT-05. 전략 검증 사이클(train/test 분할, 그리드 서치, GO/NO-GO 판정)은 Phase 3 범위 밖.

</domain>

<decisions>
## Implementation Decisions

### 진입 전략 정의
- **D-01:** 백테스트 대상 전략은 **눌림목 진입** 하나다. PROJECT.md의 현재 유효 전략(지정가 매수, 펌핑 후 하락 진입). 펌핑 추격·즉시진입·선진입은 이미 EV 음수로 판정돼 백테스트 대상에서 제외.
- **D-02:** 진입 트리거 = **주행 고점(running peak) 대비 -N% 하락**. 틱 재생 중 계속 갱신되는 이벤트 내 최고가를 기준으로, 가격이 -N% 떨어지면 진입 조건 충족. base_price 고정 기준이 아니라 동적 고점 기준.
- **D-03:** 진입 대기 구간에 상한 없음 — 이벤트 전구간(10초 간격 ~10분, 약 60틱) 어디서든 -N% 눌림이 충족되면 진입.
- **D-04:** 진입 체결가 = **진입 조건이 충족된 틱의 다음 틱 price**. 조건 판정과 주문 체결을 분리해 현실성(주문 지연) 확보 + lookahead 방지.
- **D-05:** 한 펌핑 이벤트(`pump_log` 1행)당 진입 **1회**. 진입 조건이 처음 충족된 시점에 1회 진입, 청산 후 해당 이벤트 종료. 현 봇의 단일 포지션 설계와 일치.

### 청산 규칙 모델
- **D-06:** 청산 조건 = **익절(TP) + 손절(SL) + 시간초과** 3종. TP(+X%)·SL(-Y%) 중 먼저 도달하는 것으로 청산. 둘 다 안 닿고 이벤트 틱이 소진되면 마지막 틱 price로 강제 청산(시간초과 청산).
- **D-07:** 청산 체결가 = **임계 돌파를 감지한 틱의 다음 틱 price**. 진입 체결(D-04)과 동일한 "다음 틱" 규칙으로 일관성 유지 + lookahead 방지.
- **D-08:** TP / SL / 시간초과 값은 Phase 2에서 **고정 상수**(파일 상단에 기본값 1조합). Phase 3 그리드 서치가 이 상수들을 파라미터화할 대상이다. Phase 2는 1조합만 돌린다.
- **D-09:** `gap_before=1` 틱 구간에서는 TP/SL 돌파 판정을 하지 않는다 — 갭 직전/직후 사이 가격을 모르므로 낙관적 청산을 막기 위해 갭 틱은 가격 갱신에만 쓰고 다음 정상 틱에서 판정 재개.

### 리포트 출력 형식
- **D-10:** 출력 = **stdout 요약 테이블 + CSV 상세 파일**. 터미널에 요약 지표를 즉시 보여주고, 거래별 상세(진입/청산 시각·가격·손익)는 CSV로 내보낸다. REQUIREMENTS의 "CLI + CSV로 충분"과 일치.
- **D-11:** 슬리피지 시나리오는 **0% / 0.5% / 1% / 2% 4행 비교 테이블을 항상 출력**한다. 한 번 실행에 4개 시나리오 EV를 나란히 — 슬리피지 민감도를 한눈에. 로드맵 성공기준 4와 일치.
- **D-12:** EV 95% 신뢰구간은 **정규근사**(거래별 손익의 평균 ± z·표준오차)로 산출. 부트스트랩/Monte Carlo는 REQUIREMENTS Out of Scope. 표본이 적을 때 CI가 넓게 나와 Phase 3의 VAL-04 경고와 자연 연결.
- **D-13:** 출력 지표 = 승률·EV·MDD·거래수·95% CI(BT-05). 수수료(왕복 0.5%)·슬리피지 적용값이 리포트에 명시(BT-04).

### Lookahead 강제 + 데이터 품질
- **D-14:** Lookahead 방지는 **DataSlice 래퍼 객체**로 강제한다. 현재 커서까지의 틱만 노출하고, 커서 이후 인덱스 접근 시 `IndexError`를 raise. 진입·청산 판정 로직은 이 객체만 받아 물리적으로 미래 데이터 접근이 불가능. 로드맵 성공기준 3(미래 참조 시 런타임 에러)을 코드로 강제.
- **D-15:** 갭 비율이 임계(상수)를 초과하는 펌핑 이벤트는 **통째로 백테스트에서 제외**하고, 리포트에 제외 이벤트 건수를 표기한다. 오염이 심한 이벤트가 결과를 왜곡하지 않도록.
- **D-16:** 재생 시간축은 **exchange_ts**(거래소 발생 시각)를 사용한다. `ts_estimated=1` 틱(exchange_ts가 recv_ts 복사값)도 그대로 쓰되, 이벤트당 추정 틱 비율을 리포트에 경고로 표기한다.

### Claude's Discretion
- 진입 -N%, TP, SL, 시간초과, 갭 임계값(D-09/D-15), 갭 이벤트 제외 임계값의 **구체 상수값** — 플랜 단계에서 정하거나 파일 상단 상수로 둠.
- `DataSlice` 클래스의 정확한 인터페이스(인덱싱 방식, 노출 메서드).
- CSV 파일 경로·컬럼 구성, stdout 테이블 포맷.
- MDD 계산 방식(거래 시퀀스 누적손익 기반 최대낙폭).
- `get_ticks` 외에 백테스트가 읽을 DB 쿼리(pump_log 목록 조회 등)의 구현 — 단, **읽기 전용**이어야 함.
- 틱이 4개 미만 등 데이터가 부족한 이벤트의 스킵 처리.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

이 프로젝트에는 별도 외부 spec/ADR 문서가 없다. 요구사항·제약은 아래 계획 문서와 코드에 담겨 있다.

### 프로젝트 요구사항·범위
- `.planning/PROJECT.md` — 마일스톤 핵심 가치(검증 전 실거래 금지), 제약, Out of Scope, 진단 결론(펌핑 추격 엣지 없음)
- `.planning/REQUIREMENTS.md` — BT-01~05 상세 정의, Out of Scope(walk-forward·Monte Carlo·멀티심볼·ML·웹UI·OHLCV 백테스트 모두 제외)
- `.planning/ROADMAP.md` §"Phase 2: Backtest Engine" — 목표, 5개 성공 기준
- `.planning/STATE.md` §"Pitfalls to Watch" — Lookahead bias, TimeBarrier/DataSlice 추상화 필수

### Phase 1 산출물 (백테스트가 의존하는 데이터 계약)
- `.planning/phases/01-tick-recording-infrastructure/01-CONTEXT.md` — pump_ticks 스키마 설계 결정(D-07~D-12), gap_before/ts_estimated 의미
- `bithumb/db.py` §`pump_ticks` 테이블 `CREATE_SQL` (L78~90) — 컬럼 정의
- `bithumb/db.py` §`get_ticks(pump_id) -> list[dict]` (L230~240) — **Phase 2가 import하는 동결 계약**. seq 순 정렬 반환.
- `bithumb/db.py` §`log_tick` docstring (L206~227) — exchange_ts/ts_estimated/gap_before 동작 명세

### 코드베이스 맵
- `.planning/codebase/ARCHITECTURE.md` — PriceTracker, pump_tracker, 데이터 흐름
- `.planning/codebase/CONVENTIONS.md` — 네이밍·스타일 규칙(snake_case, 타입힌트, 모듈 레벨 함수)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bithumb/db.py` `get_ticks(pump_id)` (L230~240): 이벤트 단위 틱 경로 조회. 백테스트의 1차 데이터 소스 — import해서 그대로 사용.
- `bithumb/db.py` `_conn()` / `sqlite3.Row` 패턴 (L94~98): pump_log 목록 조회 등 추가 읽기 쿼리를 같은 스타일로 작성. 단 쓰기 금지(읽기 전용).
- `bithumb/db.py` `get_stats()` (L276~298): 승률·평균손익 집계 패턴. 백테스트 지표 산출의 참고 모델.
- `pump_log` 테이블 (`base_price`, `pump_pct`, `detected_at`, `coin`): 이벤트 메타데이터. 백테스트가 이벤트 목록을 순회할 때 JOIN/조회.

### Established Patterns
- DB 접근: `_conn()` 컨텍스트 매니저, `sqlite3.Row` factory, 모듈 레벨 함수(클래스 없음).
- 네이밍: snake_case 함수, UPPERCASE 상수(`WINDOW_SEC`, `MIN_KRW` 류), 타입힌트 필수.
- 금융 포맷: KRW `:,.0f`, 퍼센트 `:+.2f%`.
- 기존 분석 스크립트(`scripts/show_pnl.py`, `signal_stats.py`)는 DB를 읽어 stdout 리포트를 출력 — backtest.py도 같은 결의 독립 스크립트.

### Integration Points
- `scripts/backtest.py` (신규): `bithumb/db.py`의 `get_ticks`만 import. `alt_monitor.py`·`bithumb/client.py`는 **import 금지**(성공기준 1).
- 데이터 소스: `data/trades.db`의 `pump_ticks` + `pump_log` 테이블 (읽기 전용).
- CSV 출력: 신규 파일 경로(예: `data/` 또는 인자 지정). 기존 파일 덮어쓰기 주의.
- Phase 3가 backtest.py의 함수/전략 인터페이스를 재사용할 수 있으므로, 진입·청산 로직을 호출 가능한 함수로 분리하면 유리(필수는 아님).

</code_context>

<specifics>
## Specific Ideas

- 핵심 원칙: "검증 전엔 실제 돈을 넣지 않는다." 백테스트는 봇과 완전 분리된 읽기 전용 오프라인 스크립트여야 한다 — 실거래 경로와 절대 얽히지 않음.
- 낙관적 오류(optimistic bias) 회피가 최우선: 진입·청산 모두 "다음 틱" 체결, 갭 구간 판정 제외, 슬리피지 4시나리오, lookahead 물리적 차단 — 모두 백테스트 결과가 실거래보다 좋게 나오는 함정을 막기 위한 결정.
- 사용자는 머신비전 엔지니어(C#/C++ 주력, Python 학습 중). 한 번에 한 단계씩, 코드 변경 전 합의.

</specifics>

<deferred>
## Deferred Ideas

- 트레일링 스톱 청산 — Phase 2는 고정 TP/SL만. 트레일링 추가는 Phase 3에서 별도 판단 가능.
- TP/SL/진입 파라미터 그리드 서치 — Phase 3 범위(VAL-02). Phase 2는 고정 상수 1조합.
- 펌핑 추격 전략 백테스트 — 진단상 EV 음수 확정. 눌림목만 검증.
- 거래량 기반 전략(acc_value·volume_power 활용) — 틱에 저장은 돼 있으나 전략화는 Phase 3 이후.
- 부트스트랩/Monte Carlo 신뢰구간 — REQUIREMENTS Out of Scope. 정규근사로 충분.

</deferred>

---

*Phase: 02-backtest-engine*
*Context gathered: 2026-05-19*
