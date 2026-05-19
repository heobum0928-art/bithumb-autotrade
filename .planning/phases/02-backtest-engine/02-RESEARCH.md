# Phase 2: Backtest Engine - Research

**Researched:** 2026-05-19
**Domain:** 틱 데이터 재생 백테스트 엔진 (이벤트 기반, 단일 포지션, 오프라인 Python 스크립트)
**Confidence:** HIGH

## Summary

Phase 2는 `pump_ticks` 테이블의 틱 데이터를 시간순으로 재생해 눌림목 진입 전략을 시뮬레이션하는 독립 스크립트 `scripts/backtest.py`를 만든다. 16개 구현 결정(D-01~D-16)이 이미 CONTEXT.md에 잠겨 있으므로, 본 리서치는 **전략 재설계가 아닌 "어떻게 잘 구현하는가"**에 집중한다 — DataSlice lookahead 강제 패턴, 틱 재생 루프 구조, 정규근사 신뢰구간, MDD 계산, CSV/stdout 리포트 형식.

핵심 발견: 이 백테스트는 학술적으로 **event-driven backtester**의 단순화 버전이다. 시장 데이터가 이미 이벤트(펌핑 1건 = pump_log 1행) 단위로 분절돼 있고, 이벤트당 진입 1회 + 단일 포지션이므로 벡터화(pandas/numpy 일괄 연산)가 아닌 **명시적 틱 루프(for-loop)**가 올바른 구조다. 벡터화는 lookahead를 숨기기 쉬워 D-14(물리적 차단)와 충돌한다. numpy/pandas/scipy가 환경에 모두 설치돼 있으나, **표준 라이브러리 + numpy만으로 충분**하다 — backtrader/vectorbt 같은 프레임워크는 OHLCV/멀티심볼 가정이 강해 이 틱 단위·이벤트 단위 모델에 부적합하고 REQUIREMENTS Out of Scope("거래소 OHLCV 백테스트 제외")와도 어긋난다.

**Primary recommendation:** 프레임워크 없이 순수 Python으로 구현한다. 구조 = `iter_events()` (pump_log 읽기 전용 조회) → 이벤트별 `DataSlice` 래퍼로 틱 노출 → `simulate_event()`가 진입/청산 판정 → `Trade` dict 리스트 수집 → `metrics()`가 승률·EV·MDD·CI 산출 → stdout 4행 슬리피지 테이블 + CSV 상세. 통계는 `statistics`(stdlib) 또는 numpy로 평균/표준오차, z=1.96 상수로 정규근사 CI. scipy는 불필요(추가 의존성 회피).

## User Constraints (from CONTEXT.md)

### Locked Decisions

**진입 전략 정의:**
- **D-01:** 백테스트 대상 전략은 **눌림목 진입** 하나. 펌핑 추격·즉시진입·선진입은 EV 음수로 판정돼 제외.
- **D-02:** 진입 트리거 = **주행 고점(running peak) 대비 -N% 하락**. 틱 재생 중 갱신되는 이벤트 내 최고가 기준. base_price 고정 기준 아님.
- **D-03:** 진입 대기 구간 상한 없음 — 이벤트 전구간(~60틱) 어디서든 -N% 눌림 충족 시 진입.
- **D-04:** 진입 체결가 = **진입 조건이 충족된 틱의 다음 틱 price**. 조건 판정과 체결 분리 → lookahead 방지 + 주문 지연 현실성.
- **D-05:** 한 펌핑 이벤트당 진입 **1회**. 처음 충족 시점에 1회 진입, 청산 후 이벤트 종료.

**청산 규칙 모델:**
- **D-06:** 청산 = **익절(TP) + 손절(SL) + 시간초과** 3종. TP(+X%)·SL(-Y%) 먼저 도달하는 것으로 청산. 둘 다 미도달 시 마지막 틱 price로 강제 청산.
- **D-07:** 청산 체결가 = **임계 돌파를 감지한 틱의 다음 틱 price**. D-04와 동일한 "다음 틱" 규칙.
- **D-08:** TP/SL/시간초과 = Phase 2에서 **고정 상수**(파일 상단 기본값 1조합). Phase 3가 파라미터화.
- **D-09:** `gap_before=1` 틱 구간에서는 TP/SL 돌파 판정 **안 함** — 갭 틱은 가격 갱신에만 쓰고 다음 정상 틱에서 판정 재개.

**리포트 출력 형식:**
- **D-10:** 출력 = **stdout 요약 테이블 + CSV 상세 파일**.
- **D-11:** 슬리피지 시나리오 = **0% / 0.5% / 1% / 2% 4행 비교 테이블 항상 출력**.
- **D-12:** EV 95% 신뢰구간 = **정규근사**(거래별 손익 평균 ± z·표준오차). 부트스트랩/Monte Carlo 제외.
- **D-13:** 출력 지표 = 승률·EV·MDD·거래수·95% CI. 수수료(왕복 0.5%)·슬리피지 적용값 명시.

**Lookahead 강제 + 데이터 품질:**
- **D-14:** Lookahead 방지 = **DataSlice 래퍼 객체**. 현재 커서까지의 틱만 노출, 커서 이후 접근 시 `IndexError` raise.
- **D-15:** 갭 비율이 임계(상수) 초과 펌핑 이벤트는 **통째로 제외**, 리포트에 제외 건수 표기.
- **D-16:** 재생 시간축 = **exchange_ts**. `ts_estimated=1` 틱도 그대로 쓰되 이벤트당 추정 틱 비율을 리포트 경고로 표기.

### Claude's Discretion

- 진입 -N%, TP, SL, 시간초과, 갭 임계값(D-09/D-15), 갭 이벤트 제외 임계값의 **구체 상수값** — 플랜 단계 또는 파일 상단 상수.
- `DataSlice` 클래스의 정확한 인터페이스(인덱싱 방식, 노출 메서드).
- CSV 파일 경로·컬럼 구성, stdout 테이블 포맷.
- MDD 계산 방식(거래 시퀀스 누적손익 기반 최대낙폭).
- `get_ticks` 외에 백테스트가 읽을 DB 쿼리(pump_log 목록 조회 등) 구현 — 단, **읽기 전용**.
- 틱 4개 미만 등 데이터 부족 이벤트의 스킵 처리.

### Deferred Ideas (OUT OF SCOPE)

- 트레일링 스톱 청산 — Phase 2는 고정 TP/SL만.
- TP/SL/진입 파라미터 그리드 서치 — Phase 3(VAL-02).
- 펌핑 추격 전략 백테스트 — EV 음수 확정.
- 거래량 기반 전략(acc_value·volume_power) — Phase 3 이후.
- 부트스트랩/Monte Carlo 신뢰구간 — REQUIREMENTS Out of Scope.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BT-01 | 백테스트 엔진이 봇 코드와 완전 분리된 오프라인 스크립트로 동작 (DB 읽기 전용) | `scripts/backtest.py`는 `bithumb.db`의 `get_ticks`만 import. 추가 pump_log 조회는 같은 모듈에 읽기전용 함수로 신설 또는 backtest.py 내부 `_conn` 패턴 직접 사용. `alt_monitor.py`·`bithumb/client.py` import 금지 — 아래 §Architecture Patterns "독립 스크립트 구조" 참조 |
| BT-02 | 틱 데이터 시간순 재생하며 진입 조건 평가·가상 포지션 청산 시뮬레이션 | 명시적 틱 for-loop. `get_ticks`가 이미 seq 순 정렬 보장. exchange_ts 시간축(D-16). 아래 §"틱 재생 루프 구조" |
| BT-03 | 진입 판정에 미래 정보(lookahead) 사용 안 함 | `DataSlice` 래퍼 — 커서 이후 인덱스 접근 시 `IndexError`. 아래 §"DataSlice lookahead 강제 패턴" |
| BT-04 | 수수료(왕복 0.5%)·슬리피지(기본 1%)를 상수 파라미터로 손익 계산에 반영 | 파일 상단 상수 `ROUND_TRIP_FEE = 0.005`, `SLIPPAGE_SCENARIOS = (0.0, 0.005, 0.01, 0.02)`. 아래 §"수수료·슬리피지 손익 모델" |
| BT-05 | 백테스트 결과로 승률·EV·MDD·거래수 출력 | 거래별 pnl_pct 리스트 → 집계. 정규근사 CI(D-12). 아래 §"지표 계산" |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `sqlite3` | 3.13 내장 | `data/trades.db` 읽기 전용 조회 | 기존 `bithumb/db.py` 전체가 이 패턴. `_conn()` + `sqlite3.Row` factory 재사용 |
| Python stdlib `csv` | 3.13 내장 | 거래 상세 CSV 출력 (D-10) | 추가 의존성 0, `csv.DictWriter`로 dict 리스트 직접 기록 |
| Python stdlib `statistics` | 3.13 내장 | 평균(`mean`)·표준편차(`stdev`) → 표준오차 → 정규근사 CI | 표본 수십~수백 건 규모엔 stdlib로 충분. scipy 불필요 |
| Python stdlib `argparse` | 3.13 내장 | CLI 인자 (DB 경로, CSV 출력 경로, 상수 오버라이드) | 기존 분석 스크립트는 인자 없으나 backtest.py는 재실행 편의상 권장 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| numpy | 2.2.6 (설치됨) | 누적합(`cumsum`)·최댓값 누적(`maximum.accumulate`)으로 MDD 계산 간결화 | MDD를 벡터로 계산하면 코드가 짧아짐. 단 stdlib for-loop으로도 동일 — **선택**, 필수 아님 |
| Python stdlib `dataclasses` | 3.13 내장 | `Trade` 결과 레코드를 dataclass로 (dict 대신) | 타입 명확성. 단 기존 코드는 dict 위주(`get_ticks`도 list[dict]) — dict 유지가 일관성 측면 유리 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| 순수 Python 틱 루프 | backtrader / vectorbt / zipline | 프레임워크는 OHLCV 캔들·멀티심볼·브로커 추상화 가정이 강함. 초단위 틱·이벤트 단위·단일 포지션 모델엔 과한 추상화. lookahead를 프레임워크 내부에 위임 → D-14의 "물리적 차단" 검증 불가. REQUIREMENTS가 "거래소 OHLCV 백테스트" 명시 제외 → 프레임워크 데이터 모델과 충돌. **사용 금지** |
| `statistics` (stdlib) | `scipy.stats` | scipy는 설치돼 있으나 정규근사 CI엔 z=1.96 상수 하나면 충분. 추가 import는 의존성·학습 부담만 증가. 사용자가 Python 학습 중 — 단순할수록 좋음 |
| numpy MDD | 순수 Python for-loop MDD | numpy가 한 줄로 짧지만, for-loop이 사용자(C++ 출신)에게 로직이 더 투명. **플랜에서 선택** |
| dataclass `Trade` | dict `Trade` | dataclass가 타입 안전하나 `get_ticks`가 list[dict] 반환 → dict 일관성. **dict 권장** |

**Installation:** 추가 설치 불필요. numpy/pandas/scipy 모두 이미 설치돼 있으나, **stdlib + (선택)numpy만 사용**.

**Version verification:** 2026-05-19 확인 — Python 3.13.9, numpy 2.2.6, pandas 2.3.3, scipy 1.16.3 (이 환경에 설치 확인됨). `requirements.txt`에 백테스트용 신규 의존성 추가 불필요.

## Project Constraints (from CLAUDE.md)

CLAUDE.md에서 추출한 강제 준수 사항 — 플래너는 이를 잠긴 결정과 동일 권한으로 취급:

- **합의 우선 / 한 번에 한 단계:** 코드 수정 전 항상 의견 제시 → 동의 후 진행. 여러 단계 묶음 금지. 플랜의 각 task는 독립 단계로 분리.
- **자동 git 백업:** 작업 마무리 시 add/commit/push 자동 수행, 커밋 메시지는 사용자 검토.
- **config.yaml/.env git 커밋 절대 금지** — 백테스트는 config.yaml을 읽지 않으므로(봇 분리) 해당 없음. CSV 출력 경로가 `.gitignore`에 잡히는지 확인 권장.
- **매매 관련 코드 변경은 사용자 검토 후** — backtest.py는 매매 코드가 아니므로 완화 적용 가능하나, 봇 코드(`alt_monitor.py` 등)는 일절 건드리지 않음(BT-01).
- **추측 금지:** 봇 상태·수치 언급 시 파일 확인. 백테스트 결과 해석 시 데이터 근거 제시.
- **GSD 워크플로우 강제:** Edit/Write는 GSD 명령(`/gsd:execute-phase`)을 통해서만.
- **네이밍 규약:** snake_case 함수, UPPERCASE 상수, 타입힌트 필수, 4-space 들여쓰기, ~100자 줄. 스크립트는 `sys.path.insert(0, ...)` 패턴으로 부모 import.

## Architecture Patterns

### Recommended Project Structure
```
scripts/
└── backtest.py        # 신규 — 단일 파일 백테스트 스크립트
                        #   상단: 상수 (전략 파라미터, 수수료, 슬리피지 시나리오)
                        #   DataSlice 클래스 (lookahead 강제)
                        #   load_events()      — pump_log 읽기전용 조회
                        #   simulate_event()   — 이벤트 1건 진입/청산 시뮬
                        #   run_backtest()     — 전 이벤트 순회 + 슬리피지 4시나리오
                        #   compute_metrics()  — 승률·EV·MDD·CI
                        #   print_report()     — stdout 4행 테이블
                        #   write_csv()        — 거래 상세
                        #   main()             — argparse + 오케스트레이션
bithumb/
└── db.py              # 기존 — get_ticks() import. pump_log 조회 함수
                        #   신설 시 여기에 (get_pump_events 등, 읽기전용)
data/
└── trades.db          # 읽기 전용 — pump_ticks + pump_log
└── backtest_trades.csv # 신규 출력 (덮어쓰기, .gitignore 확인 권장)
```

**단일 파일 권장 이유:** 사용자가 Python 학습 중 + GSD "한 번에 한 단계" 규율. 분석 스크립트(`show_pnl.py`, `signal_stats.py`)도 모두 단일 파일. Phase 3가 전략 로직을 재사용할 수 있으나(CONTEXT "필수는 아님"), Phase 2는 단일 파일로 시작하고 함수 경계를 깔끔히 두면 Phase 3에서 추출 가능.

### Pattern 1: 독립 스크립트 구조 (BT-01)
**What:** backtest.py는 봇 런타임과 완전 분리. `bithumb.db`의 데이터 접근 함수만 import.
**When to use:** 항상 — 성공기준 1.
**Example:**
```python
# Source: 기존 scripts/show_pnl.py, scripts/show_stats.py 패턴
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # 프로젝트 루트

from bithumb.db import get_ticks, DB_PATH   # 데이터 접근만
# 금지: from scripts.alt_monitor import ...
# 금지: from bithumb.client import BithumbClient
```
**검증 방법:** 플랜에 "backtest.py 상단 import 블록에 `alt_monitor` / `client` 문자열이 없다" 체크를 둔다. `grep -E "(alt_monitor|bithumb.client)" scripts/backtest.py` 가 비어야 함.

### Pattern 2: DataSlice lookahead 강제 패턴 (BT-03, D-14)
**What:** 틱 리스트를 감싸 "현재 커서까지만" 노출하는 래퍼. 미래 인덱스 접근 시 `IndexError`.
**When to use:** 진입·청산 판정 함수가 받는 유일한 데이터 인터페이스.
**핵심 설계 원칙:**
1. 진입/청산 판정 로직은 **원본 `list[dict]`을 절대 받지 않는다** — 오직 `DataSlice`만 받는다. 그래야 미래 접근이 물리적으로 불가능.
2. 커서는 재생 루프가 매 틱 `advance()`로 1씩 증가시킨다.
3. 음수 인덱스(`slice[-1]` = 현재 틱)는 허용하되, `cursor`보다 큰 인덱스·미래 슬라이싱은 차단.
4. "다음 틱 체결"(D-04/D-07)은 DataSlice 밖에서 — 판정이 끝나 진입이 결정된 **후** 재생 루프가 `cursor+1` 틱을 체결가로 쓴다. 판정 함수는 미래를 못 보고, 루프만 다음 틱을 안다.

**Example:**
```python
# Source: STATE.md §Pitfalls "TimeBarrier/DataSlice 추상화 필수" 기반 설계
class DataSlice:
    """현재 커서까지의 틱만 노출. 미래 접근 시 IndexError.

    진입·청산 판정 함수는 이 객체만 받는다 — 미래 데이터 물리적 차단.
    """
    def __init__(self, ticks: list[dict]):
        self._ticks = ticks          # 원본 (판정 로직에 직접 노출 안 함)
        self._cursor = 0             # 현재 재생 위치 (0-based, 포함)

    def advance(self) -> None:
        self._cursor += 1

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def current(self) -> dict:
        return self._ticks[self._cursor]

    def __len__(self) -> int:
        return self._cursor + 1      # 노출된 틱 수 (커서 포함)

    def __getitem__(self, i: int) -> dict:
        # 음수 인덱스: 현재까지 노출분 기준
        idx = self._cursor + 1 + i if i < 0 else i
        if idx > self._cursor or idx < 0:
            raise IndexError(
                f"lookahead 위반: 인덱스 {i} → 절대 {idx}, 커서는 {self._cursor}"
            )
        return self._ticks[idx]

    def visible(self) -> list[dict]:
        return self._ticks[: self._cursor + 1]
```
**테스트로 성공기준 3 증명:** 플랜에 "DataSlice가 미래 인덱스 접근 시 IndexError를 던지는지 확인하는 직접 테스트(또는 backtest.py 내 `assert`/`--self-test` 모드)"를 task로 둔다. 예: 커서 5인 슬라이스에 `slice[10]` → IndexError 발생 확인. 이게 성공기준 3("미래 참조 시 런타임 에러")의 실행 가능한 증거다.

### Pattern 3: 틱 재생 루프 구조 (BT-02)
**What:** 이벤트 1건의 틱을 시간순으로 한 틱씩 진행. 매 틱 진입(미보유 시) 또는 청산(보유 시) 판정.
**상태 머신:** `WAITING_ENTRY` → (진입) → `IN_POSITION` → (청산) → `DONE`. CONTEXT D-05(이벤트당 1회 진입)와 일치.
**핵심 흐름:**
```python
# Source: D-02~D-09 결정 기반 설계 (의사코드)
def simulate_event(ticks: list[dict], slippage: float, params) -> dict | None:
    if len(ticks) < params.MIN_TICKS:        # Claude's Discretion: 4틱 미만 스킵
        return None
    sl = DataSlice(ticks)
    running_peak = ticks[0]["price"]
    state = "WAITING_ENTRY"
    entry = None

    # cursor는 0부터; 마지막 틱에서 다음 틱이 없으면 강제 청산
    while sl.cursor < len(ticks):
        tick = sl.current
        running_peak = max(running_peak, tick["price"])   # D-02 동적 고점

        if state == "WAITING_ENTRY":
            drawdown = (tick["price"] - running_peak) / running_peak
            if drawdown <= -params.ENTRY_DROP_PCT:        # D-02 -N% 눌림
                # D-04: 다음 틱 체결. 다음 틱 없으면 진입 불가 → 이벤트 종료
                if sl.cursor + 1 >= len(ticks):
                    return None
                fill = ticks[sl.cursor + 1]["price"]
                entry = {"price": _apply_slip(fill, slippage, "buy"),
                         "tick_idx": sl.cursor + 1,
                         "ts": ticks[sl.cursor + 1]["exchange_ts"]}
                state = "IN_POSITION"

        elif state == "IN_POSITION":
            # D-09: 갭 틱은 TP/SL 판정 제외 (가격 갱신만)
            if tick["gap_before"] != 1:
                chg = (tick["price"] - entry["price"]) / entry["price"]
                hit = None
                if chg >= params.TP_PCT:   hit = "익절"
                elif chg <= -params.SL_PCT: hit = "손절"
                if hit and sl.cursor + 1 < len(ticks):     # D-07 다음 틱 체결
                    return _close(entry, ticks[sl.cursor+1], slippage, hit, ...)
        sl.advance()

    # D-06 시간초과: 틱 소진 → 마지막 틱 price로 강제 청산
    if state == "IN_POSITION":
        return _close(entry, ticks[-1], slippage, "시간초과", ...)
    return None   # 진입 못 함
```
**주의 — "다음 틱 없음" 경계:** D-04/D-07이 "다음 틱"을 체결가로 쓰므로, 마지막 틱에서 조건이 충족되면 체결할 다음 틱이 없다. 진입이면 이벤트 무진입 처리, 청산이면 D-06 시간초과 청산으로 폴백. 이 경계를 명시적으로 다루지 않으면 `IndexError` 또는 잘못된 체결.

### Pattern 4: 수수료·슬리피지 손익 모델 (BT-04)
**What:** 슬리피지는 체결가를 불리하게 이동(매수 시 비싸게, 매도 시 싸게), 수수료는 손익률에서 차감.
**모델 (명확한 단일 정의 필요):**
- 매수 체결가 = `tick_price × (1 + slippage)`
- 매도 체결가 = `tick_price × (1 - slippage)`
- 총손익률 = `(매도가 - 매수가) / 매수가 - ROUND_TRIP_FEE`  (ROUND_TRIP_FEE = 0.005)
- 수수료를 왕복 0.5% 한 번에 빼는 방식이 단순·명확. (편도 0.25%를 매수·매도 각각 적용해도 결과 거의 동일 — 플랜에서 한 방식으로 고정하고 리포트에 명시.)
```python
ROUND_TRIP_FEE = 0.005
SLIPPAGE_SCENARIOS = (0.0, 0.005, 0.01, 0.02)   # D-11: 항상 4행

def _apply_slip(price: float, slippage: float, side: str) -> float:
    return price * (1 + slippage) if side == "buy" else price * (1 - slippage)

def _net_pnl_pct(entry: float, exit: float) -> float:
    gross = (exit - entry) / entry
    return gross - ROUND_TRIP_FEE
```
**리포트 명시 의무 (D-13/BT-04):** stdout 헤더에 `수수료(왕복): 0.50%` 와 슬리피지 4행을 반드시 출력. 슬리피지는 체결가에 들어가고 수수료는 손익률에서 빠진다 — 이중 계산 금지.

### Pattern 5: 지표 계산 — 정규근사 CI / MDD (BT-05, D-12)
**EV 95% 신뢰구간 (D-12 정규근사):**
```python
# Source: 표준 통계 — 평균의 정규근사 신뢰구간
import statistics
Z_95 = 1.96   # 표준정규 양측 95%

def ev_ci(pnls: list[float]) -> tuple[float, float, float]:
    """거래별 손익률 리스트 → (EV, CI 하한, CI 상한)."""
    n = len(pnls)
    ev = statistics.mean(pnls)
    if n < 2:
        return ev, ev, ev          # 표본 1건이면 CI 정의 불가
    se = statistics.stdev(pnls) / (n ** 0.5)   # 표준오차 = 표본표준편차/√n
    return ev, ev - Z_95 * se, ev + Z_95 * se
```
주의: `stdev`(n-1 표본표준편차) 사용 — `pstdev`(모표준편차) 아님. 표본이 모집단의 일부이므로 표본표준편차가 맞다. n<2면 CI 계산 불가 → EV만 반환하고 리포트에 "표본 부족" 표기(Phase 3 VAL-04와 자연 연결, D-12).

**MDD (Maximum Drawdown) — 거래 시퀀스 누적손익 기반:**
거래를 시간순으로 정렬해 누적손익(equity curve)을 만들고, 각 시점에서 "이전 최고점 대비 현재 낙폭"의 최댓값.
```python
# Source: 표준 MDD 정의 (equity curve 기반)
def max_drawdown(pnls_ordered: list[float]) -> float:
    """거래를 시간순으로 정렬한 손익률 리스트 → MDD (양수, 0~).

    pnls 단위 주의: 손익률(%)을 누적합으로 쌓는 단순 모델 사용 (복리 아님).
    복리(equity *= 1+r)도 가능 — 플랜에서 한 방식 고정 후 리포트에 명시.
    """
    equity = 0.0
    peak = 0.0
    mdd = 0.0
    for r in pnls_ordered:
        equity += r                       # 누적손익 (단순합 모델)
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)     # 고점 대비 낙폭
    return mdd
```
정렬 키: 진입 시각(`exchange_ts`) 또는 청산 시각 — 플랜에서 하나 고정. MDD는 거래 **순서**에 의존하므로 정렬을 빠뜨리면 무의미한 값이 나온다. numpy 사용 시 `np.maximum.accumulate(np.cumsum(pnls)) - np.cumsum(pnls)` 한 줄로 동일하나, for-loop이 사용자에게 더 투명.

**승률·EV·거래수:** `get_stats()`(db.py L276) 패턴 참고 — wins/losses 분리, win_rate = wins/total.

### Anti-Patterns to Avoid
- **벡터화 일괄 연산으로 진입/청산 판정:** pandas로 전 틱에 한 번에 조건 마스크를 씌우면 미래 행을 보기 쉽다(예: `df['future_max']`). D-14의 "물리적 차단"과 정면 충돌. **명시적 틱 루프 + DataSlice만 사용.**
- **같은 틱에 진입+청산 판정:** 조건 충족 틱에서 즉시 체결하면 lookahead. D-04/D-07이 "다음 틱"을 강제하는 이유. 판정과 체결을 분리.
- **갭 틱에서 TP/SL 판정:** D-09 위반 → 낙관적 청산(갭 사이 가격을 모르는데 유리한 청산가 가정). 갭 틱은 `running_peak` 갱신·가격 추적에만 쓰고 돌파 판정은 건너뛴다.
- **DB 쓰기:** backtest.py는 읽기 전용. `INSERT`/`UPDATE`/`CREATE` 절대 금지. `_conn()`을 쓰더라도 `SELECT`만.
- **봇 모듈 import:** `alt_monitor`·`bithumb.client` import 시 성공기준 1 위반. 데이터 접근만 `bithumb.db`.
- **모표준편차(pstdev)로 CI:** 표본 데이터엔 `stdev`(n-1). pstdev는 CI를 과소추정.
- **recv_ts로 시간축 재생:** D-16은 `exchange_ts` 시간축. recv_ts는 수집 지연이 섞여 있어 시뮬 시각이 왜곡. (단 `get_ticks`가 이미 `seq` 정렬을 보장하므로 재생 순서 자체는 seq, 시각 표기·gap 해석은 exchange_ts.)

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| DB 연결·Row 매핑 | 새 sqlite 연결 코드 | `bithumb.db._conn()` 패턴 또는 `get_ticks()` | 이미 `sqlite3.Row` factory + 경로 처리 완비. 일관성 |
| 틱 조회 | 직접 SELECT pump_ticks | `get_ticks(pump_id)` (동결 계약) | Phase 1이 동결한 계약. seq 정렬 보장. 재구현 금지 |
| 평균·표준편차 | 수동 합/제곱합 루프 | `statistics.mean` / `statistics.stdev` | stdlib, 검증됨, 수치 안정성 |
| CSV 쓰기 | 수동 문자열 join + 콤마 | `csv.DictWriter` | 따옴표·이스케이프·개행 자동 처리 |
| 정규분포 z값 | scipy.stats import | 상수 `Z_95 = 1.96` | 95% 양측은 고정 상수. scipy 의존성 불필요 |
| 시각 파싱 | 수동 epoch 변환 | `datetime.fromtimestamp` (exchange_ts가 epoch sec) | exchange_ts는 epoch 초 (db.py 주석 확인) |

**Key insight:** 이 백테스트는 통계·DB·CSV 모두 stdlib로 끝난다. 새로 만들 것은 **DataSlice 클래스**와 **틱 재생 루프** 둘뿐 — 나머지는 stdlib와 기존 db.py 함수의 조합. 사용자가 Python 학습 중이므로 외부 라이브러리·프레임워크를 줄이는 것이 학습·유지보수 양면에서 유리.

## Common Pitfalls

### Pitfall 1: Lookahead bias (낙관적 오류의 핵심)
**What goes wrong:** 진입/청산 판정이 미래 틱을 참조해 백테스트 EV가 실거래보다 좋게 나온다. 검증 체계 전체를 무의미하게 만든다.
**Why it happens:** 틱 리스트 전체를 판정 함수에 넘기면 `ticks[i+5]` 같은 접근이 가능. 또는 "이벤트 최고가"를 이벤트 전체에서 미리 계산해 진입 기준에 쓰는 실수.
**How to avoid:** D-14 DataSlice — 판정 함수는 DataSlice만 받는다. `running_peak`는 "지금까지 본 틱"의 최고가로 재생 중 누적 갱신(미래 최고가 아님). 체결은 "다음 틱"으로 분리.
**Warning signs:** 백테스트 승률이 비현실적으로 높음. 진입 직후 즉시 익절 비율이 과도. DataSlice 없이 인덱스 산술이 코드에 보임.

### Pitfall 2: "다음 틱 없음" 경계 미처리
**What goes wrong:** 마지막 틱에서 진입/청산 조건이 충족되면 D-04/D-07의 "다음 틱"이 없어 `IndexError` 또는 잘못된 체결.
**Why it happens:** D-04/D-07을 단순히 `ticks[cursor+1]`로 구현하고 경계 검사 누락.
**How to avoid:** 진입이면 무진입 처리, 청산이면 D-06 시간초과 청산으로 폴백. 위 Pattern 3 의사코드의 `if sl.cursor + 1 >= len(ticks)` 가드.
**Warning signs:** 특정 이벤트에서 IndexError. 이벤트의 마지막 틱에서 진입이 잡힘.

### Pitfall 3: 갭 틱 처리 누락 → 낙관적 청산
**What goes wrong:** `gap_before=1` 틱(WS 단절 구간)에서 TP/SL 판정을 하면, 갭 사이 실제 가격 경로를 모르는데 유리한 청산가를 가정하게 된다.
**Why it happens:** D-09를 잊고 모든 틱에서 일률적으로 돌파 판정.
**How to avoid:** D-09 — `tick["gap_before"] == 1`이면 TP/SL 돌파 판정 skip, 가격·peak 갱신만. 다음 정상 틱에서 판정 재개.
**Warning signs:** 갭 직후 틱에서 청산이 몰림. 갭 많은 이벤트의 승률이 비정상적으로 높음.

### Pitfall 4: 갭 오염 이벤트가 결과 왜곡
**What goes wrong:** WS가 오래 끊긴 이벤트는 틱 경로가 듬성듬성해 진입·청산 시뮬이 부정확. 이런 이벤트가 표본에 섞이면 EV·MDD가 오염.
**Why it happens:** D-15(갭 비율 임계 초과 이벤트 통째 제외)를 구현 안 함.
**How to avoid:** D-15 — 이벤트별 `sum(gap_before) / len(ticks)`가 임계(상수) 초과면 이벤트 전체 스킵. 제외 건수를 리포트에 표기. 임계값은 Claude's Discretion(플랜에서 결정, 예: 30%).
**Warning signs:** 일부 이벤트의 틱 수가 60이어야 하는데 10~20.

### Pitfall 5: 표본 부족인데 결론 단정
**What goes wrong:** 2~3주치 데이터가 펌핑 이벤트 수십 건뿐이라 CI가 매우 넓다. 좁은 점추정 EV만 보고 전략을 판단하면 잘못된 확신.
**Why it happens:** EV만 출력하고 CI를 같이 안 보거나, n<2 등 경계에서 CI 계산이 깨짐.
**How to avoid:** D-12/D-13 — EV는 항상 95% CI와 함께 출력. n<2면 "표본 부족" 명시. 이게 Phase 3 VAL-04 경고와 자연 연결(D-12 명시).
**Warning signs:** CI 폭이 EV 절댓값보다 큼 → 부호조차 불확실. 거래수 < 30.

### Pitfall 6: ts_estimated 틱의 시각 신뢰
**What goes wrong:** `ts_estimated=1` 틱은 exchange_ts가 recv_ts 복사값이라 정확한 거래소 시각이 아니다. 이런 틱이 많으면 시간축 기반 분석(시간초과 청산 타이밍 등)이 부정확.
**Why it happens:** D-16을 잊고 추정 틱과 실측 틱을 구분 없이 사용.
**How to avoid:** D-16 — 추정 틱도 재생엔 그대로 쓰되, 이벤트당 `sum(ts_estimated)/len(ticks)` 비율을 리포트에 경고로 표기. (Phase 1 STATE 기록상 빗썸 WS는 exchange_ts를 실제 제공 — 라이브 검증 통과, 델타 0.65~0.72초. 따라서 ts_estimated=1은 드물 것으로 예상되나 코드는 방어적으로 비율 집계.)
**Warning signs:** 전체 추정 틱 비율이 높음 → WS 메시지 파싱 문제 의심.

## Code Examples

### pump_log 이벤트 목록 읽기 전용 조회
```python
# Source: bithumb/db.py _conn() 패턴 (L94~98) 기반. 읽기 전용 SELECT만.
def load_events(db_path) -> list[dict]:
    """백테스트 대상 펌핑 이벤트 목록. pump_ticks가 있는 이벤트만."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT p.id, p.coin, p.base_price, p.pump_pct, p.detected_at,
                   COUNT(t.id) AS tick_count
            FROM pump_log p
            JOIN pump_ticks t ON t.pump_id = p.id
            GROUP BY p.id
            HAVING tick_count >= 1
            ORDER BY p.detected_at
        """).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]
```
이 함수를 `bithumb/db.py`에 추가할지(get_pump_events 등) backtest.py 내부에 둘지는 Claude's Discretion. db.py에 두면 다른 분석 스크립트도 재사용 가능하나, backtest.py 자족성(BT-01 "완전 분리") 측면에선 내부에 둬도 무방. **단 어디에 두든 SELECT만.**

### stdout 슬리피지 4행 비교 테이블 (D-10/D-11)
```
# 예시 출력 포맷 — 플랜에서 정확한 컬럼 폭 확정
빗썸 펌핑 눌림목 전략 백테스트
====================================================================
대상 이벤트: 42건  (제외: 5건 — 갭 오염 임계 초과)
추정 틱 경고: 평균 ts_estimated 비율 2.1%
전략 상수: 진입 -7%  TP +5%  SL -3%  시간초과 600초
수수료(왕복): 0.50%

슬리피지   거래수   승률    EV        95% CI            MDD
--------------------------------------------------------------------
0.0%        38     34.2%  +0.41%   [-0.62%, +1.44%]   3.8%
0.5%        38     34.2%  -0.59%   [-1.62%, +0.44%]   5.1%
1.0%        38     34.2%  -1.59%   [-2.62%, -0.56%]   7.4%
2.0%        38     34.2%  -3.59%   [-4.62%, -2.56%]  11.2%
--------------------------------------------------------------------
상세: data/backtest_trades.csv (38행)
```
승률·거래수는 슬리피지와 무관(같은 진입/청산 틱) — 진입/청산 *판정*은 슬리피지 영향 없고, 체결*가*와 EV·MDD만 달라진다. EV·CI·MDD가 슬리피지별로 변한다.

### CSV 상세 출력 (D-10)
```python
# Source: csv.DictWriter stdlib 패턴
import csv
def write_csv(trades: list[dict], path: str) -> None:
    if not trades:
        return
    cols = ["pump_id", "coin", "entry_ts", "entry_price", "exit_ts",
            "exit_price", "exit_reason", "hold_sec", "pnl_pct", "slippage"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in trades:
            w.writerow({k: t.get(k) for k in cols})
```
`encoding="utf-8-sig"` — 한글 코인명·헤더가 Excel에서 깨지지 않게 BOM 추가(사용자 환경 Windows/Excel). CSV는 기본 슬리피지(1%) 시나리오의 거래를 쓸지 4시나리오 전부 쓸지 Claude's Discretion — `slippage` 컬럼을 두면 전부 한 파일에 가능.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pump_log 1/2/3/5분 스냅샷 백테스트 | pump_ticks 10초 틱 경로 재생 | Phase 1 (2026-05) | 진입 후 정밀 경로 확보 — 초단위 TP/SL·슬리피지 재현 가능 |
| 즉흥 파라미터 변경 후 실거래 관찰 | 데이터 → 백테스트 → 검증 사이클 | 이번 마일스톤 | 본 Phase 2가 이 사이클의 "백테스트" 단계 |

**Deprecated/outdated (이 프로젝트에서 의도적으로 제외):**
- 거래소 OHLCV 캔들 백테스트: REQUIREMENTS Out of Scope. 캔들은 초단위 진입·슬리피지 재현 불가.
- 백테스트 프레임워크(backtrader/vectorbt/zipline): OHLCV·멀티심볼 가정. 이 틱·이벤트 모델에 부적합 + lookahead 물리 검증 불가.
- Monte Carlo / 부트스트랩 CI: Out of Scope. 표본 수십 건에 재샘플링은 잘못된 확신만.

## Open Questions

1. **전략 상수 구체값 (진입 -N%, TP, SL, 시간초과, 갭 임계)**
   - 알고 있는 것: PROJECT.md 기록상 기존 봇은 "눌림목 -7% 타겟". CLAUDE.md 리스크 룰 초안 = 익절 +5~10%, 손절 -3%.
   - 불확실한 것: Phase 2용 정확한 1조합 값. Claude's Discretion으로 명시됨.
   - 권장: 플랜에서 파일 상단 상수로 — 진입 -7%, TP +5%, SL -3%, 시간초과 600초(10분 = 이벤트 길이), 갭 이벤트 제외 임계 30%. Phase 3가 이를 그리드 서치 대상으로 파라미터화하므로 Phase 2는 합리적 기본값 1개면 충분.

2. **pump_log 조회 함수를 db.py에 넣을지 backtest.py에 둘지**
   - 알고 있는 것: BT-01은 "봇 코드와 분리". `bithumb.db`는 데이터 계층이지 봇 런타임이 아님 — import 허용.
   - 불확실한 것: 자족성 vs 재사용성 트레이드오프.
   - 권장: backtest.py 내부 `load_events()`로 두면 backtest.py가 완전 자족 + BT-01 검증이 단순(import 한 줄만 확인). db.py 수정 최소화도 GSD "한 번에 한 단계"와 잘 맞음.

3. **실제 축적된 틱 데이터 존재 여부 / 규모**
   - 알고 있는 것: Phase 1 완료, 봇은 2026-05-18 정지 상태(PROJECT.md). 틱 축적은 봇 재가동 후 2~3주 필요.
   - 불확실한 것: 현재 `pump_ticks`에 행이 몇 개나 있는지. 백테스트 개발 시점에 데이터가 비어 있을 수 있음(로드맵 §Sequencing — Phase 1·2 병행 개발).
   - 권장: backtest.py는 데이터가 적거나 비어도 깨지지 않게(빈 결과 시 "이벤트 없음" 메시지) 방어적으로. 개발 중 검증은 소량 실데이터 또는 합성 틱으로. 본격 결론은 Phase 3에서 충분한 표본 확보 후.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.13 | 전체 스크립트 | ✓ | 3.13.9 | — |
| stdlib sqlite3/csv/statistics/argparse | DB·CSV·통계·CLI | ✓ | 내장 | — |
| numpy | (선택) MDD 벡터 계산 | ✓ | 2.2.6 | 순수 for-loop |
| pandas | 미사용 (벡터화 안티패턴) | ✓ | 2.3.3 | 사용 안 함 |
| scipy | 미사용 (z=1.96 상수로 대체) | ✓ | 1.16.3 | Z_95 상수 |
| data/trades.db (pump_ticks/pump_log) | 데이터 소스 | 조건부 | — | Phase 1 산출, 틱은 봇 운영 후 축적 |

**Missing dependencies with no fallback:** 없음 — 모든 코드 의존성 충족.

**Missing dependencies with fallback:** 실 틱 데이터는 봇 운영 2~3주 후 축적(로드맵 병행 개발 인정). 엔진 코드 자체 개발·검증은 데이터 없이 가능.

## Sources

### Primary (HIGH confidence)
- `bithumb/db.py` (직접 읽음) — `pump_ticks` CREATE_SQL(L78~90), `get_ticks`(L230~240), `log_tick` docstring(L206~227), `_conn`(L94~98), `get_stats`(L276~298), `pump_log`(L60~77)
- `.planning/phases/02-backtest-engine/02-CONTEXT.md` — 16개 잠긴 결정 D-01~D-16
- `.planning/phases/01-tick-recording-infrastructure/01-CONTEXT.md` — pump_ticks 스키마 결정, gap_before/ts_estimated 의미
- `.planning/REQUIREMENTS.md` — BT-01~05, Out of Scope
- `.planning/ROADMAP.md` §Phase 2 — 5개 성공 기준
- `.planning/STATE.md` §Pitfalls — Lookahead bias, DataSlice 추상화 필수
- `scripts/show_pnl.py`, `scripts/show_stats.py` (직접 읽음) — 기존 분석 스크립트 패턴(sys.path.insert, sqlite3.Row, stdout 리포트)
- 환경 직접 검증 — Python 3.13.9 / numpy 2.2.6 / pandas 2.3.3 / scipy 1.16.3 설치 확인

### Secondary (MEDIUM confidence)
- 정규근사 신뢰구간(평균 ± z·SE), MDD(equity curve peak-to-trough) — 표준 통계·퀀트 정의. 보편적으로 합의된 공식이라 외부 출처 없이도 HIGH에 가깝게 신뢰.

### Tertiary (LOW confidence)
- 없음 — 본 리서치는 잠긴 결정 구현 방법에 집중, 미검증 외부 주장 없음.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 환경에서 직접 버전 확인, stdlib 위주라 변동 없음
- Architecture: HIGH — 16개 결정이 잠겨 있고 기존 코드 패턴이 명확
- Pitfalls: HIGH — STATE.md가 lookahead/DataSlice를 명시 경고, 나머지는 결정에서 직접 도출

**Research date:** 2026-05-19
**Valid until:** 2026-06-18 (안정적 — stdlib·잠긴 결정 기반. 단 Phase 1 검증 결과가 pump_ticks 스키마에 영향 주면 재검토)
