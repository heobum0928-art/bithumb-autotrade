# Architecture Research

**Domain:** 암호화폐 스캘핑 봇 — 틱 데이터 수집 + 백테스트 인프라 통합
**Researched:** 2026-05-19
**Confidence:** HIGH (기존 코드베이스 직접 분석 기반)

---

## Standard Architecture

### System Overview — 현재 + 이번 마일스톤 추가 컴포넌트

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PROCESS LAYER                               │
│  ┌──────────────────────────────────────────┐  ┌────────────────┐   │
│  │            alt_monitor.py                │  │  watchdog.py   │   │
│  │   (RECORD_ONLY 플래그로 거래 비활성화)       │  │  (unchanged)   │   │
│  │                                          │  └────────────────┘   │
│  │  [Main Loop / 1초마다]                    │                       │
│  │    ↓ (기존 신호감지 파이프라인 유지)          │                       │
│  │    ↓ do_buy() → BLOCKED by flag          │                       │
│  │                                          │                       │
│  │  [Threads - queue.Queue 통신]             │                       │
│  │  ┌──────────────┐ ┌──────────────────┐   │                       │
│  │  │ pump_tracker │ │ pullback_tracker │   │                       │
│  │  │ (기존 5분 스냅 │ │   (기존 유지)     │   │                       │
│  │  │  + NEW 틱 기록│ │                  │   │                       │
│  │  └──────┬───────┘ └──────────────────┘   │                       │
│  │         │ tick_log_queue                  │                       │
│  │  ┌──────▼───────────────────────────┐    │                       │
│  │  │      tick_writer thread (NEW)    │    │                       │
│  │  │   DB write 전담 / 배치 INSERT     │    │                       │
│  │  └──────────────────────────────────┘    │                       │
│  └──────────────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                          DATA LAYER                                  │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  data/trades.db (SQLite)                                    │    │
│  │  기존: trades | signal_log | pump_log | daily_params        │    │
│  │  NEW:  pump_ticks (pump_id FK, elapsed_sec, price)          │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      OFFLINE TOOLS (완전 분리)                        │
│  ┌───────────────────────┐   ┌───────────────────────────────────┐  │
│  │  scripts/backtest.py  │   │  scripts/signal_stats.py (기존)   │  │
│  │  (NEW — standalone)   │   │                                   │  │
│  │  DB 읽기 전용          │   │  DB 읽기 전용                     │  │
│  │  봇 import 없음        │   │                                   │  │
│  └───────────────────────┘   └───────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

| Component | Responsibility | Communicates With |
|-----------|----------------|-------------------|
| `alt_monitor.py` 메인루프 | 1초 폴링, 신호 감지, 포지션 모니터링 | pump_tracker(queue), pullback_tracker(queue), tick_writer(queue) |
| `pump_tracker` thread | 펌핑 감지 후 5분 경로 추적 + 매 폴링마다 틱 emit | tick_log_queue, `bithumb/db.py` |
| `tick_writer` thread (NEW) | DB write 전담 — 배치 INSERT into `pump_ticks` | SQLite only |
| `pullback_tracker` thread | 고점 -7% 눌림목 대기, 진입신호 전송 | `_entry_ready` queue → main loop |
| `outcome_tracker` thread | 신호 T+5m/T+30m 가격 기록 | `bithumb/db.py` |
| `bithumb/db.py` | SQLite 읽기/쓰기 단일 접근점 | 모든 thread가 호출 |
| `scripts/backtest.py` (NEW) | 틱 데이터 재생, 전략 시뮬레이션, EV 산출 | DB 읽기 전용, 봇 코드 import 없음 |
| `watchdog.py` | 프로세스 감시, 자동 재시작 | `alt_monitor.py` PID |

---

## Recommended Project Structure (변경분만)

```
coinbase/
├── bithumb/
│   └── db.py              # pump_ticks 테이블 추가, log_tick() / get_ticks() 함수 추가
│
├── scripts/
│   ├── alt_monitor.py     # RECORD_ONLY 플래그 + tick emit 추가
│   │                      # (기존 파일 수정 — do_buy/do_sell 보호만)
│   └── backtest.py        # NEW — 완전 독립 스크립트
│
└── data/
    └── trades.db          # pump_ticks 테이블 추가 (자동 마이그레이션)
```

---

## Architectural Patterns

### Pattern 1: RECORD_ONLY 플래그 — 단일 플래그 방식 (별도 진입점 아님)

**What:** `alt_monitor.py` 상단 상수로 `RECORD_ONLY = True` 추가. `do_buy()` 진입 직전 한 곳에서만 체크.

**When to use:** 신호 감지 파이프라인 전체(RSI, MACD, pump 감지, pullback 추적)는 그대로 실행하고 실거래 주문 API 호출만 막아야 할 때.

**Trade-offs:**
- 장점: 코드 변경 최소 (1~2줄), 신호 로그는 계속 쌓임, 재활성화가 상수 하나만 바꾸면 됨
- 단점: 플래그를 깜빡하고 False로 두면 실거래 재개. 주석·로그로 명확히 표시 필요

**Example:**
```python
# alt_monitor.py 상단 (전략 파라미터 블록 안에)
RECORD_ONLY = True   # 데이터 수집 전용 — True면 실거래 주문 완전 차단

# do_buy() 호출 직전 단 한 곳에서만 체크
def try_entry(client, coin, krw, ...):
    if RECORD_ONLY:
        log.info(f"[RECORD_ONLY] {coin} 진입 신호 감지 — 실거래 차단됨")
        return None
    return do_buy(client, coin, krw)
```

**결정 근거:** 별도 진입점(`record_monitor.py`)은 기존 코드와 이중 유지보수 부담. 플래그 방식이 Bithumb 봇 같은 단일 프로세스 구조에 적합.

---

### Pattern 2: 틱 기록 — pump_tracker 내 emit + 전담 writer thread

**What:** `pump_tracker` thread 안에서 10초마다 폴링하는 기존 루프에 틱 emit을 추가하되, DB write는 `tick_writer`라는 별도 daemon thread가 배치로 처리.

**When to use:** DB write가 pump_tracker의 추적 루프를 느리게 만들지 않아야 할 때. SQLite는 동시 write 경합이 있으므로 write를 한 스레드로 직렬화하는 것이 안전.

**Trade-offs:**
- 장점: pump_tracker의 기존 루프 구조 유지, SQLite write 경합 최소화, 배치 INSERT로 I/O 효율
- 단점: queue가 하나 더 생김 (`tick_log_queue`). 봇 크래시 시 queue에 남은 틱 유실 가능 (허용 가능 — 수집 기간이 2~3주이므로 틱 몇 개 유실은 무관)
- 별도 스레드 분리 여부: 현재 pump_tracker가 `sleep(10)`으로 블록하므로 같은 스레드 안에서 `update_pump_path()`처럼 직접 INSERT해도 괜찮음. 하지만 틱 빈도가 높아지면(1초 간격으로 늘릴 경우) writer thread 분리가 안전.

**현실적 권장:** 초기에는 pump_tracker 내부에서 직접 INSERT (writer thread 추가 없이). 틱 빈도 = 10초 간격 → 분당 6틱 → 일일 ~864틱/코인. 이 정도 부하는 SQLite 직접 INSERT로 충분.

**Example:**
```python
# pump_tracker 루프 안 — 기존 스냅샷 저장 코드 아래에 추가
# 매 폴링(10초)마다 현재가를 틱으로 저장
if p > 0:
    log_tick(pid, int(elapsed), p)   # NEW: bithumb/db.py에 추가할 함수
```

---

### Pattern 3: 백테스트 엔진 — 완전 독립 오프라인 스크립트

**What:** `scripts/backtest.py`는 `bithumb/` 모듈을 import하지 않고, `bithumb/db.py`만 import(DB 읽기용). 봇 실행 없이 단독 실행 가능.

**When to use:** 항상. 백테스트가 봇 코드에 의존하면 봇 리팩터링이 백테스트를 깨뜨림.

**Trade-offs:**
- 장점: 봇과 완전 분리 → 독립적으로 발전 가능. 백테스트 실행 중 봇 코드 변경 무관.
- 단점: 전략 로직(신호 감지 조건)을 백테스트에서 재구현해야 함 (DRY 위반). 하지만 이는 의도적 분리 — 백테스트용 전략 표현은 봇의 런타임 코드와 달라도 됨.

**컴포넌트 경계:**
```
backtest.py
  ↓ import (읽기 전용)
bithumb/db.py → get_ticks(pump_id)  → pump_ticks 테이블
                get_pump_log()      → pump_log 테이블
  ↓ NO import
alt_monitor.py, bithumb/client.py, bithumb/indicators.py
```

---

## Data Flow

### 틱 수집 흐름 (이번 마일스톤)

```
WebSocket (PriceTracker._hist)
    ↓ tracker.get_latest_price(coin)  [기존 pump_tracker 내부]
pump_tracker thread (10초마다)
    ↓ 기존: update_pump_path(pid, price_1m=...) — 4개 스냅샷
    ↓ NEW:  log_tick(pid, elapsed_sec, price)  — 매 폴링마다
bithumb/db.py → INSERT INTO pump_ticks
data/trades.db [pump_ticks 테이블]
```

### 백테스트 흐름 (이번 마일스톤)

```
data/trades.db [pump_ticks + pump_log]
    ↓ get_ticks(pump_id)
backtest.py
    ↓ pump_id별 틱 시계열 재생
    ↓ 전략 로직 시뮬레이션
      (진입 조건, 익절, 손절, 보유시간)
    ↓ 거래별 PnL 계산
stdout / CSV / md 리포트
```

### 기록전용 모드에서 신호 감지 흐름 (변경 최소화)

```
[Main Loop 1초마다]
    ↓ signal detection pipeline (RSI, MACD, pump — 기존 그대로)
    ↓ 펌핑 감지 → log_pump() → pump_tracker queue → 틱 기록 시작
    ↓ 신호 감지 → log_signal() (기존 그대로)
    ↓ try_entry()
        if RECORD_ONLY: log "차단됨" → return None   ← 단 한 줄
        else: do_buy()
```

### DB 스키마 변경

```
pump_log (기존)
    id | detected_at | coin | base_price | pump_pct | price_1m/2m/3m/5m | ...

pump_ticks (NEW)
    id         INTEGER PK AUTOINCREMENT
    pump_id    INTEGER NOT NULL  → FK → pump_log.id
    elapsed_sec INTEGER NOT NULL  (펌핑 감지 후 경과 초)
    price      REAL    NOT NULL
    recorded_at TEXT   (ISO timestamp — 디버깅용)

인덱스: (pump_id, elapsed_sec) — 백테스트 쿼리 최적화
```

---

## Build Order (의존성 기반)

### Step 1: DB 스키마 확장 (bithumb/db.py)

`pump_ticks` 테이블 추가 + `log_tick()` / `get_ticks()` 함수 추가. 다른 모든 단계가 이것에 의존.

의존성: 없음 (독립적)

### Step 2: RECORD_ONLY 플래그 (alt_monitor.py)

`RECORD_ONLY = True` 상수 추가 + `do_buy()` 호출 직전 가드. 봇 재시작 없이 테스트 가능.

의존성: Step 1 완료 후 (DB 준비된 상태에서 봇 재시작)

### Step 3: 틱 emit (alt_monitor.py → pump_tracker 수정)

pump_tracker 루프 안에 `log_tick()` 호출 추가. Step 1의 `log_tick()` 함수 필요.

의존성: Step 1 (log_tick 함수), Step 2 (봇이 RECORD_ONLY 상태)

### Step 4: 데이터 축적 (실세계 2~3주)

봇을 기록 전용 모드로 운영. 코드 변경 없음. 축적 상황은 `SELECT COUNT(*) FROM pump_ticks` 로 확인.

의존성: Step 1~3 완료

### Step 5: 백테스트 엔진 (scripts/backtest.py)

Step 4와 병행 개발 가능. DB에 틱이 조금이라도 쌓이면 테스트 가능.

의존성: Step 1 (get_ticks 함수), 최소 수십 개의 pump_ticks 행

### Step 6: 검증 사이클

백테스트 결과 분석 → 파라미터 조정 → 재백테스트. 코드 변경 없음.

의존성: Step 5 완료

---

## Anti-Patterns

### Anti-Pattern 1: 별도 진입점으로 "기록 전용 봇" 만들기

**What people do:** `record_monitor.py`를 별도 파일로 만들어 `alt_monitor.py`를 복사 + 수정

**Why it's wrong:** 코드 두 벌 유지. `alt_monitor.py`에 기능이 추가될 때마다 `record_monitor.py`도 동기화해야 함. 결국 방치되어 다름.

**Do this instead:** 단일 파일에 `RECORD_ONLY` 플래그. 진입점은 항상 `alt_monitor.py` 하나.

---

### Anti-Pattern 2: pump_tracker 안에서 매 틱마다 SQLite 개별 INSERT

**What people do:** `time.sleep(10)` 루프마다 개별 `con.execute(INSERT)` 호출 + 즉시 `con.commit()`

**Why it's wrong:** 10초 간격이면 사실 문제없지만, 만약 sleep을 1초로 줄일 경우 SQLite write lock 경합이 다른 thread(outcome_tracker, main loop의 log_signal)와 충돌.

**Do this instead:** `log_tick()`은 `with _conn() as con:` 패턴 사용 (bithumb/db.py의 기존 패턴과 동일). 필요 시 배치 INSERT로 전환.

---

### Anti-Pattern 3: 백테스트 엔진이 봇 런타임 코드를 import

**What people do:** `from scripts.alt_monitor import do_buy, PriceTracker` 등을 backtest.py에서 import

**Why it's wrong:** backtest.py가 봇의 WebSocket 초기화, API 클라이언트, 락 파일 로직까지 끌어들임. 봇 없이 backtest 실행 불가.

**Do this instead:** backtest.py는 `bithumb/db.py`만 import. 전략 로직은 backtest.py 안에 독립적으로 구현 (함수 몇 개로 충분).

---

### Anti-Pattern 4: pump_log 기존 스냅샷(1m/2m/3m/5m) 삭제 후 틱으로 교체

**What people do:** 기존 `price_1m`, `price_2m` 컬럼을 지우고 pump_ticks만 남김

**Why it's wrong:** 기존 분석 스크립트(`show_stats.py`, `signal_stats.py`)가 이 컬럼에 의존. 삭제 시 기존 36개 pump_log 행 분석 불가.

**Do this instead:** pump_log 기존 스키마 유지 + pump_ticks 테이블 추가. 두 테이블은 pump_id FK로 연결.

---

## Integration Points

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| main loop → pump_tracker | `_pump_queue.put([...])` | 기존 그대로 |
| pump_tracker → DB (신규 틱) | `log_tick(pid, elapsed, price)` 직접 호출 | 10초 간격이므로 직접 INSERT 허용 |
| pump_tracker → DB (기존 스냅샷) | `update_pump_path(pid, **kwargs)` | 기존 그대로 |
| backtest.py → DB | `get_ticks(pump_id)`, `get_pump_log()` 읽기 전용 | 봇 미실행 상태에서도 동작해야 함 |
| watchdog → alt_monitor | PID 감시 + 재시작 | RECORD_ONLY 플래그는 재시작 후에도 유지 (상수이므로) |

### 외부 경계

| Service | Usage | Notes |
|---------|-------|-------|
| Bithumb WebSocket | PriceTracker 실시간 시세 캐시 | 틱 기록은 이 캐시에서 읽음 — API 추가 호출 없음 |
| Bithumb REST API | RECORD_ONLY 시 호출 안 함 (do_buy 차단) | 잔고 조회(`/info/balance`)는 허용해도 됨 |
| Telegram | 신호 감지 알림은 RECORD_ONLY에서도 가능 | "차단됨" 알림 추가 여부는 선택적 |

---

## Scaling Considerations

이 프로젝트는 단일 프로세스 + 단일 거래소. 스케일 문제는 없음. 단, 틱 데이터 축적에 관한 용량은:

| Scale | Architecture |
|-------|-------------|
| 2~3주 수집 (~100개 펌핑 이벤트, 평균 30틱/이벤트) | ~3,000행. SQLite 직접 쿼리 충분 |
| 6개월 수집 (~1,000개 이벤트) | ~30,000행. 여전히 SQLite로 충분 |
| 인덱스 없을 경우 | 백테스트 시 full scan. `(pump_id, elapsed_sec)` 인덱스로 해결 |

---

## Sources

- 직접 코드 분석: `C:\code\coinbase\scripts\alt_monitor.py` (1200줄, pump_tracker lines 170-254)
- 직접 코드 분석: `C:\code\coinbase\bithumb\db.py` (249줄, pump_log 스키마 lines 60-77)
- 직접 코드 분석: `C:\code\coinbase\.planning\codebase\ARCHITECTURE.md` (기존 아키텍처 문서)
- 직접 코드 분석: `C:\code\coinbase\.planning\codebase\STRUCTURE.md` (디렉토리 구조)
- 직접 코드 분석: `C:\code\coinbase\.planning\PROJECT.md` (마일스톤 요구사항)

---

*Architecture research for: 빗썸 스캘핑 봇 — 틱 데이터 수집 + 백테스트 인프라*
*Researched: 2026-05-19*
