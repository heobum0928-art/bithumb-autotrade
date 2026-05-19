# Phase 3: Strategy Validation - Research

**Researched:** 2026-05-19
**Domain:** Out-of-sample 전략 검증 (train/test 분할, 파라미터 그리드 서치, GO/NO-GO 판정) — 순수 Python stdlib, Phase 2 `backtest.py` 재사용
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions (D-01 ~ D-14 — 재논의 금지)

**GO/NO-GO 판정 기준**
- **D-01:** GO 판정의 핵심 조건 = test 셋 EV의 95% 신뢰구간 **하한 > 0**. 평균 EV가 양수여도 CI 하한이 0을 걸치면 NO-GO.
- **D-02:** GO/NO-GO는 **슬리피지 1% 시나리오의 test 셋 EV**로 판정. 슬리피지 0/0.5/1/2% 4행 비교 테이블은 참고용으로 함께 출력.
- **D-03:** 표본이 MIN_SAMPLE 미달이거나 CI가 너무 넓을 때는 **무조건 NO-GO**. INCONCLUSIVE 같은 3상태 없음 — 2값 판정(GO / NO-GO).
- **D-04:** CI 폭 자체의 별도 임계 경고는 두지 않는다 — D-01의 "CI 하한 > 0"이 넓은 CI를 자동 흡수.
- **D-05:** 최종 리포트에 GO 또는 NO-GO 판정을 명시. NO-GO일 때는 사유(CI 하한 음수 / 표본 미달)를 함께 표기.

**train/test 분할 정책**
- **D-06:** 분할 비율 = **train 70% / test 30%**.
- **D-07:** 분할 단위 = **펌핑 이벤트 수 기준**. `pump_log` 행을 `detected_at` 순 정렬 후 앞 70% train, 뒤 30% test.
- **D-08:** 시간순 분할 — train은 항상 test보다 시간적으로 앞선다. 파라미터 서치 중 test 구간 이벤트를 단 한 번도 쿼리하지 않는다.

**그리드 서치 범위**
- **D-09:** 탐색 파라미터 = **ENTRY_DROP_PCT / TP_PCT / SL_PCT 3개**. TIMEOUT_SEC은 600초 고정.
- **D-10:** 그리드 조밀도 = **거친 그리드, 파라미터당 3~4단계**. 총 조합 수십 개.
- **D-11:** 그리드 서치는 train 셋에만 실행, 조합별 EV 정렬 테이블 출력. **train 최고 EV 1조합만** test로 넘긴다(multiple-testing 편향 방지).

**결과 분해 + 과적합 경고**
- **D-12:** 진입 시간대 분해 = **KST 6시간 블록 4구간**(0–6 / 6–12 / 12–18 / 18–24).
- **D-13:** 코인별 분해는 지표 테이블 + **단일 코인 지배율 경고**(예: 1개 코인이 거래수 50% 초과 시 경고).
- **D-14:** MIN_SAMPLE = **30건 유지**(Phase 2 기존 상수). test 셋 거래수 30 미만이면 표본 부족 NO-GO.

### Claude's Discretion (플랜 단계에서 결정 — 본 리서치가 권고안 제시)
- 그리드 각 파라미터 구체 후보값 (현 상수 진입 -7%, TP +5%, SL -3% 중심 3~4단계).
- 단일 코인 지배율 경고의 정확한 임계값(거래수 비중 % 기준).
- 신규 검증 스크립트 파일명·구조, backtest.py 함수 재사용 방식(import vs 리팩터).
- 파라미터를 상수에서 인자로 끌어올리는 방법.
- train/test 분할 함수의 인터페이스, 그리드 EV 테이블·분해 테이블의 stdout 포맷과 CSV 컬럼.
- 그리드 서치 결과 정렬 보조 지표(승률·MDD 동점 처리).

### Deferred Ideas (OUT OF SCOPE — 절대 다루지 말 것)
- TIMEOUT_SEC 파라미터 탐색.
- 트레일링 스톱 청산 전략.
- 페이퍼 트레이딩 (v2 PAPER-01/02).
- walk-forward / 롤링 윈도우 최적화.
- Monte Carlo / 부트스트랩 신뢰구간 (정규근사로 충분).
- 거래량 기반 신호(acc_value·volume_power) 전략화.
- 실거래 재개.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| VAL-01 | 틱 데이터를 train/test로 시간순 분할해 OOS 검증 수행 | 아래 §"train/test 분할 패턴" — `detected_at` 정렬 후 70/30 인덱스 컷, `EventSplit` 물리 배리어로 test 누수 차단 |
| VAL-02 | 파라미터 그리드 서치로 조합별 EV 산출·정렬 (train 전용) | §"파라미터 주입 리팩터" + §"그리드 서치" — `Strategy` 파라미터 객체, `itertools.product` 조합 생성, EV 내림차순 정렬 |
| VAL-03 | 결과를 코인별·진입 시간대별로 분해해 통계 출력 | §"결과 분해" — KST 6시간 블록 버킷팅, 코인별 `compute_metrics` 그룹 집계 |
| VAL-04 | 최소 표본 미달·과적합 위험 시 경고 | §"과적합·표본 경고" — `MIN_SAMPLE` 게이트, 단일 코인 지배율 경고, train↔test EV 갭 경고 |
| VAL-05 | GO/NO-GO 결론 도출 | §"GO/NO-GO 판정 로직" — 슬리피지 1% test CI 하한 > 0 판정 함수 |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **코드 수정/생성 전 합의 우선** — 무엇을 어떻게 할지 먼저 설명하고 동의 구함. 동의 전 파일 수정 금지.
- **한 번에 한 단계만** — 여러 단계를 묶어 진행 금지. 플랜은 작은 단위로 쪼갤 것.
- **추측 금지** — 봇 상태·수치는 파일 확인 후 답변.
- **config.yaml / .env git 커밋 절대 금지** — Phase 3는 config를 건드리지 않으므로 해당 위험 없음.
- **매매 관련 코드 변경 시 사용자 검토 후 커밋** — Phase 3 검증 스크립트는 읽기 전용 오프라인 스크립트라 매매 코드가 아니지만, `backtest.py` 리팩터는 사용자 검토 대상.
- **GSD 워크플로 강제** — Edit/Write는 GSD 커맨드를 통해서만. 직접 편집 금지.
- **stdlib·투명한 for-loop 선호** — 사용자는 머신비전 엔지니어(C#/C++ 주력, Python 학습 중). MDD/CI 등 지표는 라이브러리 대신 명시적 for-loop로(Phase 2 D-12 계승). scipy/pandas/numpy 도입 금지.
- **커밋 메시지 영어 권장, 기능 1개 단위.**

## Summary

Phase 3는 새로운 알고리즘이나 외부 라이브러리가 필요 없다. 핵심은 **Phase 2의 `scripts/backtest.py`를 재사용 가능하게 리팩터**한 뒤, 그 위에 검증 사이클(분할 → 그리드 서치 → 판정)을 얇게 씌우는 것이다. 모든 도구는 이미 Python 3.13 stdlib에 있다: `itertools.product`(그리드 조합), `datetime.fromisoformat`(KST 시간대 파싱), `statistics`(EV/CI — Phase 2가 이미 사용), `csv`(리포트). 외부 의존성 0개.

가장 중요한 구현 결정은 **`backtest.py`의 모듈 레벨 전략 상수(`ENTRY_DROP_PCT`/`TP_PCT`/`SL_PCT`)를 인자로 끌어올리는 방법**이다. 현재 `simulate_event(ticks, slippage)`는 이 상수들을 전역에서 읽는다. Phase 3 그리드 서치는 조합마다 다른 값으로 `simulate_event`를 호출해야 하므로 파라미터화가 불가피하다. 권고: 작은 frozen `Strategy` 데이터클래스(또는 평범한 dict)를 만들어 `simulate_event(ticks, slippage, strategy)`로 인자를 하나 추가하되, 기본값을 현 상수로 둬서 **Phase 2의 호출 시그니처와 동작을 깨지 않는다**(`backtest.py` 단독 실행은 그대로 작동).

두 번째 핵심은 **test 셋 데이터 누수의 물리적 차단**이다. Phase 2가 lookahead를 `DataSlice`로 코드 강제했듯, Phase 3는 train/test 분할을 `EventSplit` 래퍼로 강제한다 — 그리드 서치 코드는 `split.train`만 접근 가능하고 `split.test`는 봉인(grid search 단계에서 호출 시 예외). 이것이 로드맵 성공기준 1("test 구간을 단 한 번도 쿼리하지 않는다")을 코드로 보증한다.

**중대 발견 (BLOCKER 후보):** 현재 `data/trades.db`에 `pump_log` 350행이 있으나 **`pump_ticks` 테이블은 0행**이다. Phase 1의 틱 기록 인프라가 아직 라이브로 돌지 않았거나, 2~3주 축적이 시작되지 않았다. Phase 3 검증 스크립트는 **합성 데이터로 개발·검증**할 수 있으나(Phase 2도 그렇게 함), 실제 GO/NO-GO 판정은 충분한 `pump_ticks` 표본이 쌓이기 전에는 불가능하다. 플래너는 이를 명시적 선행 조건으로 다뤄야 한다 — 아래 §"Environment Availability" 참조.

**Primary recommendation:** `backtest.py`를 import 가능한 라이브러리로 만들고(전략 상수 → `Strategy` 파라미터 객체), 신규 `scripts/validate.py`가 그 함수들을 import해 검증 사이클을 수행한다. 새 알고리즘·라이브러리 없이 stdlib `itertools`·`datetime`·`statistics`·`csv`만 사용. 합성 데이터로 개발하되, 실 판정은 `pump_ticks` 표본 확보 후.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `itertools` | 3.13 (내장) | `product()`로 그리드 파라미터 조합 생성 | 데카르트 곱의 정석. 수십 조합 규모에 외부 라이브러리 불필요 |
| Python stdlib `datetime` | 3.13 (내장) | `fromisoformat()`로 `detected_at` ISO 문자열 파싱 → `.hour`로 KST 시간대 블록 분류 | `detected_at`이 `2026-05-16T16:29:31.454970` 형식(naive ISO, KST). 추가 의존성 없이 파싱 가능 |
| Python stdlib `statistics` | 3.13 (내장) | EV(`mean`)·CI(`stdev`) — Phase 2 `ev_ci`가 이미 사용 | Phase 2 결정 D-12 계승. scipy 금지 |
| Python stdlib `csv` | 3.13 (내장) | 그리드 서치 결과·분해 테이블 CSV 출력 | Phase 2 `write_csv` 패턴 그대로 |
| Python stdlib `argparse` | 3.13 (내장) | CLI 인자(`--db`, `--csv`, `--split-ratio`, `--self-test`) | Phase 2 `backtest.py` `main()` 패턴 계승 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Python stdlib `dataclasses` | 3.13 (내장) | `@dataclass(frozen=True)` `Strategy` 파라미터 객체 | 전략 상수를 묶는 불변 컨테이너. dict보다 타입 안전·오타 방지. **선택사항** — 평범한 dict로도 충분 |
| `scripts/backtest.py` | Phase 2 산출물 | `simulate_event`/`compute_metrics`/`ev_ci`/`max_drawdown`/`DataSlice`/`load_events` 재사용 | 검증 스크립트가 import. 엔진 재구현 금지 |
| `bithumb/db.py` | 기존 | `get_ticks(pump_id)` — 이벤트 틱 경로 조회 (동결 계약) | 틱 데이터 1차 소스. `alt_monitor.py`·`client.py` import 금지 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib `itertools.product` | 수동 3중 for-loop | for-loop가 더 명시적이라 사용자(Python 학습 중)에게 투명하나, 파라미터 수가 3개로 고정이라 `product`도 1줄로 충분히 읽힌다. **둘 다 허용** — 플래너 재량 |
| `@dataclass` `Strategy` | 평범한 `dict` | dict는 키 오타가 런타임에야 드러남. dataclass는 정적 안전. 단 사용자 학습 곡선 고려 시 dict도 무방. **권고: dataclass**(frozen, 3필드 소규모) |
| 신규 `scripts/validate.py` | `backtest.py`에 검증 함수 직접 추가 | 별도 파일이 관심사 분리·`backtest.py` 단독성 유지에 유리. **권고: 신규 파일** (아래 §"Architecture Patterns" 참조) |
| 정규근사 CI | 부트스트랩 | Out of Scope (D, REQUIREMENTS). 정규근사 고정 |

**Installation:** 없음 — 전부 stdlib. `requirements.txt` 변경 불필요.

**Version verification:** 외부 패키지 추가 없음. Python 3.13.9 확인됨(`python --version`). `requirements.txt`에 신규 항목 추가 금지.

## Architecture Patterns

### Recommended Project Structure
```
scripts/
├── backtest.py        # Phase 2 — 리팩터: 전략 상수를 Strategy 파라미터로 끌어올림.
│                      #            simulate_event/compute_metrics/load_events 등은 import 가능한 함수로 유지.
│                      #            단독 실행(python scripts/backtest.py)은 그대로 작동 (기본 Strategy = 현 상수).
└── validate.py        # Phase 3 신규 — backtest.py를 import해 검증 사이클 수행.
                       #            split_events → grid_search(train) → validate(test) → GO/NO-GO
data/
├── backtest_trades.csv      # Phase 2 — 덮어쓰지 말 것
├── validate_grid.csv        # Phase 3 신규 — 그리드 서치 조합별 EV 테이블
└── validate_test_trades.csv # Phase 3 신규 — test 셋 거래 상세 (최종 1조합)
```

### Pattern 1: 전략 상수를 파라미터로 끌어올리기 (가장 중요한 통합 지점, VAL-02)
**What:** `backtest.py`의 `ENTRY_DROP_PCT`/`TP_PCT`/`SL_PCT` 모듈 전역 상수를, 함수 인자로 주입 가능한 `Strategy` 객체로 전환.
**When to use:** 그리드 서치가 조합마다 다른 파라미터로 `simulate_event`를 호출해야 하므로 필수.
**핵심 원칙 — Phase 2 인터페이스 비파괴:** 인자에 기본값을 주어 기존 호출(`simulate_event(ticks, slippage)`)과 `backtest.py` 단독 실행이 깨지지 않게 한다.

```python
# Source: 권고 패턴 (backtest.py 리팩터)
from dataclasses import dataclass

@dataclass(frozen=True)
class Strategy:
    """그리드 서치가 주입하는 전략 파라미터 묶음. TIMEOUT_SEC은 D-09에 따라 고정."""
    entry_drop_pct: float = 0.07   # 현 ENTRY_DROP_PCT — 기본값 = Phase 2 동작
    tp_pct: float = 0.05           # 현 TP_PCT
    sl_pct: float = 0.03           # 현 SL_PCT

# 모듈 상수는 "기본 전략"으로 보존 (backtest.py 단독 실행이 이를 사용)
DEFAULT_STRATEGY = Strategy()

# simulate_event 시그니처에 strategy 인자 추가 (기본값으로 하위호환)
def simulate_event(ticks: list[dict], slippage: float,
                   strategy: Strategy = DEFAULT_STRATEGY) -> dict | None:
    ...
    # 본문에서 전역 ENTRY_DROP_PCT → strategy.entry_drop_pct 로 치환
    if drawdown <= -strategy.entry_drop_pct:
        ...
    hit = "익절" if chg >= strategy.tp_pct else ("손절" if chg <= -strategy.sl_pct else None)
```

**주의:** `_close()`는 파라미터를 안 쓰므로 변경 불필요. `MIN_TICKS`/`GAP_EXCLUDE_PCT`/`TIMEOUT_SEC`/`ROUND_TRIP_FEE`는 탐색 대상이 아니므로 모듈 상수 유지(D-09). `run_backtest`/`print_report`는 기본 전략을 그대로 쓰면 Phase 2 동작 보존.

### Pattern 2: train/test 분할 + 물리 배리어 (VAL-01, 로드맵 성공기준 1)
**What:** `load_events()`가 `detected_at` 순으로 반환하는 이벤트 리스트를 70/30 인덱스 컷으로 자르고, test 셋을 그리드 서치가 만질 수 없도록 봉인하는 래퍼.
**When to use:** 검증 사이클의 첫 단계. `DataSlice`가 lookahead를 코드로 막듯, `EventSplit`은 OOS 누수를 코드로 막는다.

```python
# Source: 권고 패턴 (DataSlice의 D-14 물리 배리어 사상 계승)
class EventSplit:
    """이벤트를 train/test로 시간순 분할. test는 봉인 — 명시적 unlock 전 접근 시 예외.

    그리드 서치 코드는 .train 만 접근. .test 는 최종 검증 1회에만 unlock_test()로 연다.
    Phase 2 DataSlice가 lookahead를 IndexError로 막은 것과 동일한 '물리 차단' 패턴.
    """
    def __init__(self, events: list[dict], train_ratio: float = 0.70):
        # D-08: events는 load_events()가 detected_at 순 정렬해 반환한 것 — 재정렬 불필요하나 방어적으로 확인
        cut = int(len(events) * train_ratio)   # D-06/D-07: 이벤트 수 기준 70%
        self._train = events[:cut]
        self._test = events[cut:]
        self._test_unlocked = False

    @property
    def train(self) -> list[dict]:
        return self._train

    def unlock_test(self) -> list[dict]:
        """최종 OOS 검증 직전 단 1회 호출. 호출 후 test 셋 반환."""
        self._test_unlocked = True
        return self._test

    @property
    def test(self) -> list[dict]:
        if not self._test_unlocked:
            raise RuntimeError(
                "OOS 위반: 그리드 서치 단계에서 test 셋 접근 시도. "
                "unlock_test()는 최종 검증 직전 1회만 호출돼야 한다 (D-08/D-11)."
            )
        return self._test
```

**왜 인덱스 컷이 안전한가:** `load_events()`의 SQL이 이미 `ORDER BY p.detected_at`이므로 리스트 앞쪽이 시간적으로 앞선다. `cut` 이후가 test — train의 모든 이벤트가 test보다 먼저 발생(D-08 보장).

### Pattern 3: 그리드 서치 (train 전용, VAL-02)
**What:** `itertools.product`로 3개 파라미터 조합을 생성, 각 조합으로 train 셋 전체를 시뮬레이션, EV로 정렬.

```python
# Source: 권고 패턴
import itertools

# Discretion 권고 그리드 (현 상수 진입 -7/TP +5/SL -3 중심, 파라미터당 3~4단계, D-10)
ENTRY_GRID = (0.05, 0.07, 0.10)          # 진입 눌림 -5 / -7 / -10%
TP_GRID    = (0.03, 0.05, 0.07, 0.10)    # 익절 +3 / +5 / +7 / +10%
SL_GRID    = (0.02, 0.03, 0.05)          # 손절 -2 / -3 / -5%
# 총 3 × 4 × 3 = 36 조합 — D-10 "수십 개" 범위 적합

def grid_search(train_events: list[dict], slippage: float = 0.01) -> list[dict]:
    """train 셋에 모든 그리드 조합을 돌려 조합별 지표를 EV 내림차순으로 반환 (D-11).

    D-02: 그리드 서치도 슬리피지 1% 기준으로 비교 (최종 판정과 동일 기준선).
    """
    results = []
    for entry, tp, sl in itertools.product(ENTRY_GRID, TP_GRID, SL_GRID):
        strat = Strategy(entry_drop_pct=entry, tp_pct=tp, sl_pct=sl)
        trades = []
        for ev in train_events:
            ticks = get_ticks(ev["id"])
            # 갭 오염 이벤트 제외는 backtest.py run_backtest와 동일 규칙 적용 권장
            t = simulate_event(ticks, slippage, strat)
            if t is not None:
                t["coin"] = ev["coin"]
                trades.append(t)
        m = compute_metrics(trades)
        results.append({"entry": entry, "tp": tp, "sl": sl, **m})
    # D-11: EV 내림차순 정렬. 동점 시 보조키 — Discretion: 승률↓, 그다음 MDD↑(낮을수록 우선)
    results.sort(key=lambda r: (-r.get("ev", -1e9),
                                -r.get("win_rate", 0),
                                r.get("mdd", 1e9)))
    return results
```

**핵심 규율 (D-11):** `grid_search`는 `EventSplit.train`만 받는다. 정렬된 results의 **0번 인덱스(최고 EV) 1조합만** test로 넘어간다. 상위 N개를 test에 돌리면 multiple-testing 선택 편향이 생긴다.

### Pattern 4: KST 6시간 블록 버킷팅 (VAL-03, D-12)
**What:** `detected_at` ISO 문자열을 파싱해 `.hour`를 4구간으로 분류.

```python
# Source: 권고 패턴
from datetime import datetime

TIME_BLOCKS = {0: "새벽 0-6", 1: "오전 6-12", 2: "오후 12-18", 3: "저녁 18-24"}

def time_block(detected_at: str) -> str:
    """detected_at ISO 문자열 -> KST 6시간 블록 라벨 (D-12).

    detected_at은 2026-05-16T16:29:31.454970 형식 (naive ISO, 이미 KST).
    별도 타임존 변환 불필요 — STATE 결정 '라이브 타임존 검증 통과' 참조.
    """
    hour = datetime.fromisoformat(detected_at).hour
    return TIME_BLOCKS[hour // 6]   # 0-5→0, 6-11→1, 12-17→2, 18-23→3
```

**주의:** 거래(Trade dict)를 블록으로 분류하려면 거래가 속한 이벤트의 `detected_at`이 필요하다. `simulate_event`가 반환하는 Trade dict에 `coin`을 붙이듯 `detected_at`(또는 `time_block` 라벨)도 붙여서 분해 단계에서 그룹핑하라.

### Pattern 5: GO/NO-GO 판정 로직 (VAL-05, D-01~D-05)
**What:** test 셋(슬리피지 1%)의 거래로 CI 하한을 산출, 단일 게이트 통과 여부 판정.

```python
# Source: 권고 패턴
def decide_go_nogo(test_trades: list[dict]) -> dict:
    """test 셋 거래 -> GO/NO-GO 판정 dict (D-01/D-02/D-03/D-05).

    test_trades는 슬리피지 1% 시나리오의 거래여야 한다 (D-02).
    """
    m = compute_metrics(test_trades)          # backtest.py 재사용
    if m["count"] < MIN_SAMPLE:               # D-03/D-14: 표본 미달 무조건 NO-GO
        return {"verdict": "NO-GO", "reason": f"표본 부족 (거래수 {m['count']} < {MIN_SAMPLE})"}
    if m["ci_low"] <= 0:                      # D-01: CI 하한이 0 이하면 NO-GO
        return {"verdict": "NO-GO",
                "reason": f"CI 하한 음수/0 ({m['ci_low']*100:+.2f}%) — EV 양수 확신 불가"}
    return {"verdict": "GO",
            "reason": f"test 셋 CI 하한 양수 ({m['ci_low']*100:+.2f}%), 전략 유효"}
```

### Anti-Patterns to Avoid
- **그리드 서치 단계에서 test 셋 조회** — D-08/D-11 위반. `EventSplit`이 코드로 차단하므로 우회하지 말 것.
- **상위 N개 조합을 test에 검증** — multiple-testing 편향. D-11은 단일 조합만 허용.
- **`backtest.py`의 전역 상수를 그리드 서치 중 직접 변형(`globals()` 조작)** — 비결정적·테스트 불가. 반드시 인자 주입(Pattern 1).
- **`data/backtest_trades.csv` 덮어쓰기** — Phase 2 산출물. 신규 CSV는 별도 경로(`validate_*.csv`).
- **`alt_monitor.py`/`bithumb/client.py` import** — 검증 스크립트는 `bithumb/db.py`의 `get_ticks`만 import (성공기준, Phase 2 계승).
- **INCONCLUSIVE 3상태 도입** — D-03은 2값(GO/NO-GO)만. 애매하면 NO-GO.
- **scipy/pandas/numpy 도입** — CLAUDE.md·D-12 위반. stdlib만.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 파라미터 조합 생성 | 중첩 for-loop를 깊게 쌓기 | `itertools.product(ENTRY_GRID, TP_GRID, SL_GRID)` | 데카르트 곱의 표준. 파라미터 추가 시 인자만 늘리면 됨 |
| EV / 신뢰구간 산출 | 새 통계 함수 | `backtest.py`의 `ev_ci` / `compute_metrics` | Phase 2가 정규근사 CI를 이미 구현·검증. 재구현은 불일치 위험 |
| MDD 계산 | 새 낙폭 로직 | `backtest.py`의 `max_drawdown` | Phase 2 D(누적손익 단순합) 모델과 일관성 유지 |
| 이벤트 시뮬레이션 | 진입/청산 재구현 | `backtest.py`의 `simulate_event` (파라미터화 후) | 엔진 재사용이 Phase 3 전제(CONTEXT D). 다음-틱 체결·갭 처리 검증 완료됨 |
| ISO 시각 파싱 | 문자열 슬라이싱(`s[11:13]`) | `datetime.fromisoformat(s).hour` | 슬라이싱은 형식 변동에 취약. stdlib 파서가 견고 |
| 이벤트 목록 로드 | 새 SQL 쿼리 | `backtest.py`의 `load_events` | 이미 `detected_at` 정렬 + 틱 보유 이벤트 필터. 분할 입력으로 직접 사용 |

**Key insight:** Phase 3는 "엔진을 다시 만드는 게 아니다"(CONTEXT 명시). 새로 작성할 코드는 분할·서치 오케스트레이션·판정·리포트 포맷뿐이다. 계산 로직은 전부 `backtest.py`에서 가져온다 — 그래야 Phase 2와 Phase 3의 EV 정의가 한 글자도 어긋나지 않는다.

## Common Pitfalls

### Pitfall 1: 전략 상수 파라미터화가 Phase 2를 깨뜨림
**What goes wrong:** `simulate_event`에 `strategy` 인자를 필수로 추가하면 `run_backtest`의 기존 호출(`simulate_event(ticks, slip)`)이 `TypeError`로 깨지고, `backtest.py` 단독 실행이 실패한다.
**Why it happens:** 시그니처 변경 시 기본값을 안 줌.
**How to avoid:** `strategy: Strategy = DEFAULT_STRATEGY` 기본값 지정(Pattern 1). `DEFAULT_STRATEGY`는 현 상수값. `print_report`가 상수를 직접 참조하는 부분(`backtest.py` L336-338)도 `DEFAULT_STRATEGY` 필드 참조로 바꾸거나 그대로 두되 일관성 유지.
**Warning signs:** `python scripts/backtest.py` 단독 실행 또는 `--self-test`가 리팩터 후 실패하면 즉시 발견. 플랜에 "리팩터 후 Phase 2 동작 회귀 확인" 검증 단계 필수.

### Pitfall 2: test 셋이 그리드 서치 중 새어 들어감
**What goes wrong:** 그리드 서치 함수에 전체 이벤트 리스트를 넘기거나, "best 조합 비교용"으로 test를 미리 한 번 보면 OOS가 아니게 된다.
**Why it happens:** 분할이 단순 리스트 슬라이싱이라 실수로 전체 리스트를 넘기기 쉽다.
**How to avoid:** `EventSplit` 래퍼(Pattern 2). `grid_search`는 `split.train`만 받고, `split.test`는 `unlock_test()` 호출 전 접근 시 `RuntimeError`. 코드 리뷰로 "`unlock_test()`는 정확히 1곳에서만 호출되는가" 확인.
**Warning signs:** `unlock_test()` grep 결과가 2곳 이상이면 의심.

### Pitfall 3: 표본 부족인데 GO가 나옴
**What goes wrong:** 2~3주로 test 셋 거래수가 30 미만인데 우연히 EV·CI 하한이 양수로 나와 GO 판정.
**Why it happens:** MIN_SAMPLE 게이트를 CI 판정보다 나중에 검사하거나 빠뜨림.
**How to avoid:** `decide_go_nogo`에서 `MIN_SAMPLE` 검사를 **CI 검사보다 먼저**(Pattern 5). D-03/D-14: 표본 미달이면 CI 값과 무관하게 NO-GO.
**Warning signs:** test 거래수가 한 자리/십 단위인데 verdict가 GO.

### Pitfall 4: 진입률 때문에 거래수 ≪ 이벤트수
**What goes wrong:** `simulate_event`는 진입 조건(-N% 눌림) 미충족 시 `None`을 반환한다. 이벤트가 30건 있어도 진입 거래는 그보다 훨씬 적을 수 있다 — 특히 진입 임계가 큰(-10%) 조합.
**Why it happens:** "이벤트 수"와 "거래 수"를 혼동.
**How to avoid:** MIN_SAMPLE(30)은 **거래 수** 기준이지 이벤트 수 기준이 아님(D-14, `compute_metrics`의 `count`). 표본 게이트 계산 시 `compute_metrics` 결과의 `count`를 쓸 것. 리포트에 "이벤트 N건 중 진입 M건" 둘 다 표기 권장.
**Warning signs:** 그리드 조합별로 거래수가 들쭉날쭉 — 정상이나, 진입 -10% 조합이 거래 0~5건이면 표본 부족이 빈번.

### Pitfall 5: 코인/시간대 분해 시 표본이 너무 잘게 쪼개짐
**What goes wrong:** test 셋 거래가 30건이어도 4개 시간 블록 × N개 코인으로 나누면 셀당 1~3건. 셀별 EV·CI는 통계적 의미가 없다.
**Why it happens:** 분해의 목적(편향 탐지)과 판정의 목적(GO/NO-GO)을 혼동.
**How to avoid:** 분해 테이블은 **참고용** — 거래수·승률·EV를 보여주되 셀별 CI 하한으로 GO/NO-GO를 내리지 말 것. GO/NO-GO는 전체 test 셋 단일 판정(D-01). 분해는 "한 코인/시간대 쏠림"을 눈으로 확인하는 도구. 셀별 표본 수를 반드시 같이 출력해 독자가 신뢰도를 가늠하게 함.
**Warning signs:** 분해 테이블 한 셀의 EV가 극단적으로 좋은데 거래수가 1~2건.

### Pitfall 6: 단일 코인 지배 — "우연한 1코인 행운"
**What goes wrong:** test 셋 EV가 양수여도 그 이익 대부분이 단 한 코인에서 나왔다면, 전략의 일반적 엣지가 아니라 그 코인의 우연일 수 있다.
**Why it happens:** 신규 상장 펌핑은 코인별 변동성 편차가 크다.
**How to avoid:** D-13 — 코인별 거래수·이익 집계 후 최대 비중 코인을 검사. **권고 임계: 한 코인이 test 거래수의 40%를 초과하면 경고**(40%는 Discretion — 50%는 두 코인만 있어도 안 걸려 느슨, 30%는 코인 3~4종일 때 정상 분포도 걸려 과민. 40%가 균형). 경고는 NO-GO를 강제하지 않으나 리포트에 명시해 사용자가 판단하게 함.
**Warning signs:** 코인별 분해 테이블에서 한 행의 거래수가 전체의 절반 가까움.

### Pitfall 7: 과적합 — train EV는 좋은데 test EV가 무너짐
**What goes wrong:** 그리드 서치가 train에서 EV 최고 조합을 뽑았으나 그 조합은 train 노이즈에 맞춰진 것이고 test에서 EV가 크게 낮거나 음수.
**Why it happens:** 그리드 서치의 본질적 위험. 36개 조합 중 최고를 고르면 train EV는 낙관 편향됨.
**How to avoid:** D-10이 거친 그리드(36조합)로 위험을 이미 낮춤. 추가로 리포트에 **train 최고 조합의 train EV vs test EV를 나란히** 출력하고, 갭이 크면(예: test EV가 train EV의 절반 미만이거나 부호가 바뀜) 과적합 경고 출력(VAL-04). 단 최종 GO/NO-GO는 어디까지나 test CI 하한(D-01)이 결정 — 경고는 해석 보조.
**Warning signs:** train EV +8%, test EV -2% 같은 큰 괴리.

## Code Examples

### 검증 사이클 오케스트레이션 (validate.py의 main 흐름)
```python
# Source: 권고 패턴 — backtest.py 함수 재사용
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.db import DB_PATH, get_ticks
from backtest import (load_events, simulate_event, compute_metrics,
                      Strategy, MIN_SAMPLE)   # backtest.py 리팩터 후 import 가능

def run_validation(db_path) -> dict:
    events = load_events(db_path)              # detected_at 순 (D-08 보장)
    split = EventSplit(events, train_ratio=0.70)   # D-06/D-07

    # 1) 그리드 서치 — train 셋만 (D-11)
    grid = grid_search(split.train, slippage=0.01) # D-02 슬리피지 1%
    best = grid[0]                                  # 최고 EV 1조합만 (D-11)
    best_strat = Strategy(best["entry"], best["tp"], best["sl"])

    # 2) OOS 검증 — test 셋 단 1회 unlock (D-08)
    test_events = split.unlock_test()
    test_trades = []                                # 슬리피지 0/0.5/1/2% 모두 산출 가능 (D-02 참고 테이블)
    for ev in test_events:
        ticks = get_ticks(ev["id"])
        t = simulate_event(ticks, 0.01, best_strat)  # 판정 기준선 = 1%
        if t is not None:
            t["coin"] = ev["coin"]
            t["detected_at"] = ev["detected_at"]     # 시간대 분해용
            test_trades.append(t)

    # 3) GO/NO-GO 판정 (D-01~D-05, VAL-05)
    verdict = decide_go_nogo(test_trades)
    return {"grid": grid, "best": best, "verdict": verdict,
            "test_trades": test_trades}
```

### 코인별 분해 + 단일 코인 지배율 경고 (VAL-03, D-13)
```python
# Source: 권고 패턴
SINGLE_COIN_DOMINANCE = 0.40   # Discretion: 한 코인이 거래수 40% 초과 시 경고

def decompose_by_coin(trades: list[dict]) -> dict:
    """test 거래를 코인별로 그룹핑해 지표 산출 + 지배율 경고."""
    by_coin: dict[str, list[dict]] = {}
    for t in trades:
        by_coin.setdefault(t["coin"], []).append(t)
    total = len(trades)
    rows = {c: compute_metrics(ts) for c, ts in by_coin.items()}
    top_coin = max(by_coin, key=lambda c: len(by_coin[c])) if by_coin else None
    dominance = len(by_coin[top_coin]) / total if total else 0.0
    return {
        "rows": rows,
        "warning": (dominance > SINGLE_COIN_DOMINANCE),
        "top_coin": top_coin,
        "dominance": dominance,
    }
```

### 시간대 분해 (VAL-03, D-12)
```python
# Source: 권고 패턴
def decompose_by_time_block(trades: list[dict]) -> dict:
    """test 거래를 KST 6시간 블록으로 그룹핑. 각 거래에 detected_at 필요."""
    by_block: dict[str, list[dict]] = {}
    for t in trades:
        block = time_block(t["detected_at"])
        by_block.setdefault(block, []).append(t)
    # 모든 블록 출력 (거래 0건 블록도 표에 표시 — 비교 가능성)
    return {b: compute_metrics(by_block.get(b, [])) for b in TIME_BLOCKS.values()}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Phase 2: 전략 상수 모듈 전역, 1조합 고정 | Phase 3: `Strategy` 파라미터 객체 주입, 그리드 탐색 | 본 Phase | `simulate_event` 시그니처에 `strategy` 인자 추가 (기본값으로 하위호환) |
| Phase 2: 전체 이벤트로 백테스트 | Phase 3: train/test 시간순 분할 OOS | 본 Phase | `EventSplit` 래퍼 신규 |
| Phase 2: 슬리피지 4행 리포트로 끝 | Phase 3: 4행 리포트 + GO/NO-GO 명시 판정 | 본 Phase | `decide_go_nogo` 신규 |

**Deprecated/outdated:**
- 펌핑 추격·즉시진입·선진입 전략: 진단상 EV 음수 확정. 그리드 탐색 대상 아님 — 눌림목 전략만.
- `pump_log`의 `price_1m`~`price_5m` 5분 집계 컬럼: Phase 3 검증은 `pump_ticks` 초 단위 틱만 사용. 5분 집계는 사용 안 함(단 기존 분석 스크립트가 의존하므로 컬럼 자체는 유지 — STATE 제약).

## Open Questions

1. **`pump_ticks` 테이블이 비어 있다 (실측 0행, `pump_log`는 350행)**
   - What we know: Phase 1 인프라(`log_tick`/`get_ticks`/`pump_ticks` 스키마)는 완성·커밋됨. 그러나 라이브 봇이 틱을 실제로 INSERT한 적이 없거나, 2~3주 축적이 아직 시작 전.
   - What's unclear: 틱 축적이 언제 시작되는지, Phase 3 착수 시점에 표본(거래 기준 ≥30)이 채워질지.
   - Recommendation: Phase 3 검증 스크립트는 **Phase 2처럼 합성/소량 데이터로 개발·자가검증**(`--self-test`)할 수 있다. 그러나 실제 GO/NO-GO 판정은 `pump_ticks`에 충분한 표본이 쌓인 후에만 유효하다. 플래너는 (a) 스크립트 개발 플랜과 (b) 실 데이터 판정을 분리하거나, "데이터 축적 완료"를 Phase 3 실행의 명시적 선행 조건으로 표기해야 한다. STATE.md Todos도 "틱 데이터 축적(2~3주)"을 미완으로 명시함.

2. **그리드 서치도 갭 오염 이벤트(`GAP_EXCLUDE_PCT` 초과)를 제외해야 하는가**
   - What we know: `backtest.py`의 `run_backtest`는 갭 비율 30% 초과 이벤트를 통째 제외(D-15). `grid_search`는 `run_backtest`를 거치지 않고 `simulate_event`를 직접 호출하는 구조.
   - What's unclear: train/test 분할 후 그리드 서치·OOS 검증에서도 동일한 갭 제외를 적용할지(권장) 아니면 분할 전에 미리 제외할지.
   - Recommendation: **분할 전에** `_gap_ratio` 기준으로 오염 이벤트를 한 번 걸러낸 뒤 깨끗한 이벤트만 `EventSplit`에 넘기는 것이 가장 단순하고 train/test 일관성도 보장. `backtest.py`의 `_gap_ratio`를 재사용. 플래너가 확정.

3. **그리드 서치 동점(EV 동일) 처리 보조 정렬키**
   - What we know: D는 보조 지표(승률·MDD 동점 처리)를 Discretion으로 둠.
   - What's unclear: 정확한 우선순위.
   - Recommendation: 1차 EV↓, 2차 승률↓, 3차 MDD↑(낮을수록 우선). 부동소수 EV가 정확히 같을 일은 드물지만 결정성을 위해 명시. 플래너 재량.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.13 | 검증 스크립트 실행 | ✓ | 3.13.9 | — |
| stdlib (itertools/datetime/statistics/csv/argparse/dataclasses) | 전 기능 | ✓ | 내장 | — |
| `scripts/backtest.py` (Phase 2 엔진) | 함수 재사용 | ✓ | Phase 2 완료 | — |
| `bithumb/db.py` `get_ticks` | 틱 데이터 조회 | ✓ | 동결 계약 | — |
| `data/trades.db` `pump_log` | 이벤트 목록 | ✓ | 350행 존재 | — |
| `data/trades.db` `pump_ticks` (실 틱 데이터) | 실제 GO/NO-GO 판정 | ✗ | **0행** | 합성 데이터로 스크립트 개발·`--self-test`는 가능. 실 판정은 축적 후 |

**Missing dependencies with no fallback:**
- 없음 — 스크립트 자체는 stdlib + Phase 2 산출물만으로 완성 가능.

**Missing dependencies with fallback:**
- **`pump_ticks` 실 틱 데이터 (0행):** 스크립트 개발·자가검증은 합성 데이터로 진행 가능(Phase 2 선례). 실제 검증 사이클·GO/NO-GO 판정은 2~3주 틱 축적 후에만 의미가 있다. 플래너는 이를 Phase 3 실행의 선행 조건으로 명시할 것.

## Sources

### Primary (HIGH confidence)
- `c:\code\coinbase\scripts\backtest.py` — Phase 2 엔진 전문 정독. `simulate_event`/`compute_metrics`/`ev_ci`/`max_drawdown`/`DataSlice`/`load_events`/`run_backtest`의 정확한 시그니처·동작·전략 상수 위치 확인.
- `c:\code\coinbase\bithumb\db.py` — `get_ticks`/`pump_ticks` 스키마/`pump_log` 스키마 동결 계약 확인.
- `c:\code\coinbase\.planning\phases\03-strategy-validation\03-CONTEXT.md` — D-01~D-14 locked decisions, Discretion 항목.
- `c:\code\coinbase\.planning\phases\02-backtest-engine\02-CONTEXT.md` — Phase 2 D-01~D-16(다음-틱 체결, 갭 처리, 정규근사 CI).
- `c:\code\coinbase\.planning\REQUIREMENTS.md` / `ROADMAP.md` / `STATE.md` — VAL-01~05 정의, 성공기준, Pitfalls.
- `c:\code\coinbase\.planning\codebase\CONVENTIONS.md` / `ARCHITECTURE.md` — 네이밍·스타일·모듈 설계.
- DB 실측: `python` 쿼리로 `pump_log` 350행 / `pump_ticks` 0행 / `detected_at` ISO 형식(`2026-05-16T16:29:31.454970`) 확인.
- `python --version` → 3.13.9 확인.

### Secondary (MEDIUM confidence)
- 없음 — 본 Phase는 외부 라이브러리·웹 리서치 불필요(전부 stdlib + 프로젝트 내부 코드).

### Tertiary (LOW confidence)
- 없음.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 전부 Python 3.13 stdlib, 버전 실측 확인. 외부 의존성 0.
- Architecture: HIGH — `backtest.py` 전문 정독 기반. 리팩터 패턴이 Phase 2 인터페이스를 비파괴함을 코드 레벨에서 확인.
- Pitfalls: HIGH — 7개 함정 모두 코드/CONTEXT 결정에 근거. 특히 빈 `pump_ticks`는 실측.

**Research date:** 2026-05-19
**Valid until:** 2026-06-18 (안정 — stdlib·내부 코드 기반, 빠르게 바뀌지 않음). 단 `pump_ticks` 축적 상태는 Phase 3 착수 직전 재확인 필요.
