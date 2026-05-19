# Phase 3: Strategy Validation - Context

**Gathered:** 2026-05-19
**Status:** Ready for planning

<domain>
## Phase Boundary

축적된 `pump_ticks` 틱 데이터를 시간순으로 train/test 두 구간으로 분할하고, train 셋에만 파라미터 그리드 서치를 돌려 최적 조합을 뽑은 뒤, test 셋으로 단 1회 out-of-sample 검증을 수행한다. 결과를 코인별·진입 시간대별로 분해하고, 표본 부족·과적합 위험을 코드로 경고하며, 최종적으로 눌림목 전략의 EV가 양수인지 음수인지 GO/NO-GO 판정을 명시한다.

요구사항: VAL-01, VAL-02, VAL-03, VAL-04, VAL-05.

백테스트 엔진(`scripts/backtest.py` — Phase 2)은 그대로 재사용한다. Phase 3은 그 위에 검증 사이클(분할·서치·판정)을 씌우는 것이지 엔진을 다시 만드는 게 아니다. 실거래 재개·페이퍼 트레이딩·walk-forward·Monte Carlo는 범위 밖.

</domain>

<decisions>
## Implementation Decisions

### GO/NO-GO 판정 기준
- **D-01:** GO 판정의 핵심 조건 = **test 셋 EV의 95% 신뢰구간 하한 > 0**. 평균 EV가 양수여도 CI 하한이 0을 걸치면 NO-GO. "검증되지 않은 전략에는 실제 돈을 넣지 않는다"는 핵심 가치를 따른 보수적 기준.
- **D-02:** GO/NO-GO는 **슬리피지 1% 시나리오의 test 셋 EV**로 판정한다. 슬리피지 0/0.5/1/2% 4행 비교 테이블은 참고용으로 함께 출력하되, 최종 판정의 기준선은 1%(왕복비용 포함 ~2.5% 현실적 기대치).
- **D-03:** 표본이 MIN_SAMPLE 미달이거나 신뢰구간이 너무 넓을 때는 **무조건 NO-GO** 선언. INCONCLUSIVE 같은 별도 3상태를 두지 않는다 — "모르면 돈 안 넣는다"와 일치하는 2값 판정(GO / NO-GO).
- **D-04:** CI 폭 자체의 별도 임계 경고는 두지 않는다 — D-01의 "CI 하한 > 0" 기준이 넓은 CI를 이미 흡수한다(CI가 넓으면 하한이 0 아래로 내려가 자동 NO-GO). 표본 게이트(D-11)와 CI 하한 기준(D-01) 두 가지로 충분.
- **D-05:** 최종 리포트에 "GO: test 셋 CI 하한 양수, 전략 유효" 또는 "NO-GO: test 셋 CI 하한 음수/표본 불충분, 전략 폐기" 판정을 명시하며, NO-GO일 때는 사유(CI 하한 음수 / 표본 미달)를 함께 표기한다.

### train/test 분할 정책
- **D-06:** 분할 비율 = **train 70% / test 30%**. 표본이 적은 프로젝트에서 train 그리드 서치 안정성과 test 검증 가능성의 균형.
- **D-07:** 분할 단위 = **펌핑 이벤트 수 기준**. 펌핑 이벤트(`pump_log` 1행)를 `detected_at` 시간순으로 정렬한 뒤 앞 70%를 train, 뒤 30%를 test로 자른다. 날짜별 이벤트 편중과 무관하게 train/test 표본 수가 예측 가능.
- **D-08:** 시간순 분할 — train은 항상 test보다 시간적으로 앞선다. 파라미터 서치 중 test 구간 이벤트를 단 한 번도 쿼리하지 않는다(VAL-01, 로드맵 성공기준 1).

### 그리드 서치 범위
- **D-09:** 탐색 파라미터 = **ENTRY_DROP_PCT(진입%) / TP_PCT(익절) / SL_PCT(손절) 3개**. TIMEOUT_SEC은 600초 고정 — 이벤트 길이(~10분)와 같아 사실상 전구간 보유라 탐색 의미가 없다.
- **D-10:** 그리드 조밀도 = **거친 그리드, 파라미터당 3~4단계**. 총 조합 수십 개 수준 — 2~3주 표본 대비 과적합 위험이 낮고 결과 해석이 쉽다. 세밀 그리드(수백 조합)는 우연한 고EV 조합을 집을 위험이 커 배제.
- **D-11:** 그리드 서치는 train 셋에만 실행하고, 조합별 EV가 정렬된 테이블로 출력된다(VAL-02). train 최고 EV 1조합만 test로 넘긴다 — 상위 N개를 test에 돌리면 multiple-testing 선택 편향이 생기므로 단일 조합만 OOS 검증(D-02 연계).

### 결과 분해 + 과적합 경고
- **D-12:** 진입 시간대 분해 = **KST 6시간 블록 4구간**(새벽 0–6 / 오전 6–12 / 오후 12–18 / 저녁 18–24). 구간당 표본이 적어도 패턴이 보이는 해석 친화적 granularity (VAL-03).
- **D-13:** 코인별 분해는 지표 테이블 + **단일 코인 지배율 경고**. 거래 또는 이익의 대부분이 한 코인에 몰리면(예: 1개 코인이 거래수 50% 초과) 경고를 출력해 "우연한 1코인 행운"이 EV를 가리는 상황을 탐지(VAL-03, VAL-04).
- **D-14:** MIN_SAMPLE = **30건 유지**(Phase 2 `backtest.py` 기존 상수). test 셋 거래수가 30 미만이면 표본 부족으로 NO-GO(D-03). 2~3주로 test 30건을 못 채우면 솔직히 NO-GO가 맞다는 보수적 입장.

### Claude's Discretion
- 그리드 각 파라미터의 구체 후보값(예: 진입 -5/-7/-10%, TP +3/+5/+7/+10%, SL -2/-3/-5%) — 플랜 단계에서 backtest.py 현 상수(진입 -7%, TP +5%, SL -3%)를 중심으로 3~4단계 결정.
- 단일 코인 지배율 경고의 정확한 임계값(거래수 비중 % 기준).
- 신규 검증 스크립트 파일명·구조, backtest.py 함수 재사용 방식(import vs 리팩터), 파라미터를 상수에서 인자로 끌어올리는 방법.
- train/test 분할 함수의 인터페이스, 그리드 서치 EV 테이블·시간대/코인 분해 테이블의 stdout 포맷과 CSV 컬럼.
- 그리드 서치 결과 정렬 기준 보조 지표(승률·MDD 동점 처리 등).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

이 프로젝트에는 별도 외부 spec/ADR 문서가 없다. 요구사항·제약은 아래 계획 문서와 코드에 담겨 있다.

### 프로젝트 요구사항·범위
- `.planning/PROJECT.md` — 핵심 가치(검증 전 실거래 금지), 진단 결론(펌핑 추격 엣지 없음), Out of Scope, 누적 성과(-65,000원)
- `.planning/REQUIREMENTS.md` — VAL-01~05 상세 정의, Out of Scope(walk-forward·Monte Carlo·멀티심볼·ML·웹UI·OHLCV 백테스트 제외)
- `.planning/ROADMAP.md` §"Phase 3: Strategy Validation" — 목표, 5개 성공 기준
- `.planning/STATE.md` §"Pitfalls to Watch" — 그리드 서치 과적합(train 전용 서치 + test 최종 1회)

### Phase 2 산출물 (Phase 3가 재사용·확장하는 백테스트 엔진)
- `scripts/backtest.py` — 재사용 핵심 자산. `simulate_event`, `compute_metrics`, `ev_ci`, `max_drawdown`, `DataSlice`, `load_events`, `run_backtest`. 전략 상수(ENTRY_DROP_PCT/TP_PCT/SL_PCT/TIMEOUT_SEC)가 모듈 레벨 — Phase 3 그리드 서치는 이를 파라미터로 끌어올려야 함.
- `.planning/phases/02-backtest-engine/02-CONTEXT.md` — Phase 2 결정 D-01~D-16(눌림목 전략 정의, 다음 틱 체결, 갭 처리, 슬리피지 4시나리오, 정규근사 CI)
- `.planning/phases/01-tick-recording-infrastructure/01-CONTEXT.md` — pump_ticks 스키마, gap_before/ts_estimated 의미

### 데이터 계약
- `bithumb/db.py` §`get_ticks(pump_id)` — 이벤트 단위 틱 경로 조회. backtest.py가 import하는 동결 계약.
- `data/trades.db` — `pump_ticks` + `pump_log` 테이블 (읽기 전용 접근)

### 코드베이스 맵
- `.planning/codebase/CONVENTIONS.md` — 네이밍·스타일(snake_case 함수, UPPERCASE 상수, 타입힌트 필수, 정규근사 통계는 stdlib `statistics`만)
- `.planning/codebase/ARCHITECTURE.md` — 데이터 흐름, 분석 스크립트 패턴

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/backtest.py` `simulate_event(ticks, slippage)`: 한 이벤트의 진입·청산 시뮬레이션. Phase 3 그리드 서치가 파라미터별로 반복 호출 — 단 현재 전략 상수가 모듈 전역이라 파라미터화 필요.
- `scripts/backtest.py` `compute_metrics` / `ev_ci` / `max_drawdown`: 승률·EV·95% CI·MDD 산출. test 셋 판정과 코인/시간대 분해 지표에 그대로 재사용.
- `scripts/backtest.py` `load_events(db_path)`: pump_ticks 있는 이벤트를 `detected_at` 순 반환 — train/test 시간순 분할의 입력으로 직접 사용 가능.
- `scripts/backtest.py` `DataSlice`: lookahead 물리 차단 래퍼. 그리드 서치에서도 그대로 적용돼 검증 신뢰성 유지.
- `bithumb/db.py` `get_ticks(pump_id)`: 이벤트 틱 경로 조회 (읽기 전용).

### Established Patterns
- 독립 오프라인 스크립트: DB를 읽어 stdout 리포트 + CSV 출력 (`backtest.py`, `show_pnl.py`, `signal_stats.py`와 동결).
- 통계: stdlib `statistics`만 — 정규근사 95% CI(Z_95=1.96), scipy 의존 없음.
- DB 읽기 전용: SELECT만, INSERT/UPDATE/DELETE 금지 — 봇 데이터 무오염.
- 금융 포맷: 퍼센트 `:+.2f%`, KRW `:,.0f`. CSV는 `utf-8-sig`(한글 깨짐 방지).
- 모듈 레벨 함수(클래스 최소), UPPERCASE 상수, 타입힌트 필수.

### Integration Points
- Phase 3 검증 스크립트(신규 또는 backtest.py 확장): `bithumb/db.py`의 `get_ticks`만 import. `alt_monitor.py`·`bithumb/client.py` import 금지.
- 데이터 소스: `data/trades.db`의 `pump_ticks` + `pump_log` (읽기 전용).
- backtest.py의 전략 상수(ENTRY_DROP_PCT/TP_PCT/SL_PCT)를 그리드 서치가 인자로 주입할 수 있도록 끌어올리는 작업이 핵심 통합 지점 — D-09 연계.
- CSV 출력: 신규 경로(그리드 서치 결과, test 거래 상세 등). 기존 `data/backtest_trades.csv` 덮어쓰기 주의.

</code_context>

<specifics>
## Specific Ideas

- 검증 규율의 핵심: train으로 파라미터를 정하고 test는 마지막에 단 1회만 본다. test를 여러 번 들여다보거나 상위 N개를 test에 돌리면 그 순간 OOS가 아니게 된다 — D-11이 이를 단일 조합으로 강제.
- 판정은 낙관 편향을 최대한 배제: CI 하한 기준(D-01), 슬리피지 1% 기준선(D-02), 표본 미달 시 NO-GO(D-03) — 모두 "백테스트가 실거래보다 좋게 나오는 함정"을 막는 보수적 선택.
- 이 마일스톤의 성공은 GO든 NO-GO든 데이터로 명확히 아는 것. NO-GO도 실패가 아니라 "더 잃기 전에 안 것"이 가치.
- 사용자는 머신비전 엔지니어(C#/C++ 주력, Python 학습 중). 한 번에 한 단계씩, 코드 변경 전 합의. MDD/CI 등 지표는 투명한 for-loop·stdlib 유지 선호(Phase 2 결정 계승).

</specifics>

<deferred>
## Deferred Ideas

- TIMEOUT_SEC 파라미터 탐색 — 이벤트 길이(~10분)와 같아 현재는 고정. 추적 길이가 늘어나면 재검토 가능.
- 트레일링 스톱 청산 전략 — Phase 2/3은 고정 TP/SL만. 별도 마일스톤에서 판단.
- 페이퍼 트레이딩(실시간 시세 가상 거래) — v2 요구사항(PAPER-01/02), 다음 마일스톤.
- walk-forward / 롤링 윈도우 최적화 — REQUIREMENTS Out of Scope (2~3주 표본 부족).
- Monte Carlo / 부트스트랩 신뢰구간 — Out of Scope, 정규근사로 충분.
- 거래량 기반 신호(acc_value·volume_power) 전략화 — 틱에 저장돼 있으나 본 마일스톤 범위 밖.
- 실거래 재개 — 검증 결론(GO/NO-GO) 도출 후 별도 마일스톤에서 판단.

</deferred>

---

*Phase: 03-strategy-validation*
*Context gathered: 2026-05-19*
