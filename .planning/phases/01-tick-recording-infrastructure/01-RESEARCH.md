# Phase 1: Tick Recording Infrastructure - Research

**Researched:** 2026-05-19
**Domain:** Python SQLite 시계열 데이터 수집, WebSocket 갭 탐지, 펌핑 틱 기록
**Confidence:** HIGH

## Summary

이 phase는 신규 라이브러리 도입이 거의 없는 **순수 통합 작업**이다. 봇이 이미 가진 인프라(SQLite `bithumb/db.py`, WebSocket `PriceTracker`, 10초 루프 `start_pump_tracker`)를 재사용해 (1) 실거래 차단 게이트, (2) `pump_ticks` 신규 테이블, (3) 거래소/수신 시각 분리 기록, (4) WS 갭 플래그를 추가하면 된다. CONTEXT.md의 12개 결정(D-01~D-12)이 구현 경로를 거의 완전히 확정했으므로, 리서치의 핵심 가치는 **단 하나의 미해결 질문(D-11) 해소**와 **SQLite 시계열 스키마/마이그레이션 모범 사례 검증**이다.

**핵심 발견 (D-11 해소):** 봇이 사용하는 WebSocket은 빗썸 **레거시 공개 WS** `wss://pubwss.bithumb.com/pub/ws`이다 (코드 L58 `WS_URL`). 이 WS의 `ticker` 메시지 `content` 페이로드는 **`date`(YYYYMMDD)와 `time`(HHMMSS) 거래소 발생 시각 필드를 포함**한다 (빗썸 공식 API 문서 확인). 따라서 `exchange_ts`는 **실제 거래소 시각으로 채울 수 있으며**, D-11의 폴백(`recv_ts` 복사 + `ts_estimated=1`)은 메시지에 해당 필드가 누락/파싱 실패한 경우의 **예외 경로**로만 동작한다. `exchange_ts`/`recv_ts` 두 컬럼 스키마는 항상 유지된다.

**Primary recommendation:** 신규 의존성 0개. `bithumb/db.py`에 `pump_ticks` 테이블 + `log_tick()`/`get_ticks()` 함수를 기존 `log_pump`/`update_pump_path` 스타일로 추가하고, `PriceTracker.on_message`가 `date`/`time`을 파싱해 deque 튜플에 5번째 원소로 적재하도록 확장한 뒤, `start_pump_tracker`의 10초 루프에서 `log_tick()`을 호출한다. RECORD_ONLY 게이트는 `do_buy`/`do_buy_limit`/`do_sell` 첫 줄에 1개 상수 + 3개 가드 클로즈로 삽입한다.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**실거래 차단 (RECORD_ONLY)**
- **D-01:** 실거래 차단 게이트는 `scripts/alt_monitor.py`의 `do_buy` / `do_buy_limit` / `do_sell` 함수 첫 줄에 둔다. RECORD_ONLY가 켜져 있으면 즉시 `return None` 하고 "RECORD_ONLY 차단됨" 로그를 남긴다. 진입 결정 로직(신호 감지·필터)은 그대로 동작하며 주문 실행만 막는다.
- **D-02:** RECORD_ONLY 스위치는 `config.yaml`의 `trading.record_only` 플래그로 관리한다. 기본값 `true`(안전). 실거래 재개는 별도 마일스톤이므로 이번 마일스톤 동안 `true` 고정.
- **D-03:** 차단 상태에서도 신호 감지는 평소처럼 `signal_log` / `pump_log`에 계속 기록한다. 데이터 수집이 이번 phase의 목적이므로 신호·펌핑 이벤트는 모두 남긴다.

**틱 기록 범위·간격**
- **D-04:** 틱 기록 간격은 10초 고정. 기존 `start_pump_tracker`의 `sleep(10)` 루프를 재사용한다 — 추가 API 호출 없음, 구현 리스크 최소.
- **D-05:** 펌핑 이벤트 1건당 틱 추적 지속시간은 10분(현재 5분에서 연장). 10초 간격 × 10분 = 약 60틱.
- **D-06:** 틱 기록 대상은 기존 펌핑 감지 로직이 `pump_log`에 등록하는 이벤트만(`queue_pump` 시점에 틱 추적도 함께 시작). 감지 기준은 변경하지 않는다.

**틱 스키마 · pump_log 연계**
- **D-07:** 새 `pump_ticks` 테이블을 만들고 `pump_id` 컬럼으로 `pump_log.id`를 참조한다(FK 관계). 펌핑 1건 = `pump_log` 1행 + `pump_ticks` N행.
- **D-08:** 틱 행 컬럼: `pump_id`, `seq`(이벤트 내 순번), `exchange_ts`, `recv_ts`, `price`, `acc_value`(누적 거래대금), `volume_power`(체결강도), 갭/추정 플래그.
- **D-09:** `pump_log`의 기존 집계 컬럼과 `update_pump_path` 로직은 그대로 유지. `pump_ticks`는 순수 추가. 기존 분석 스크립트(`show_pnl.py` 등)의 동작 보장.

**WS 갭 · 시각 기록**
- **D-10:** WebSocket 단절 갭은 각 틱 행의 `gap_before` 플래그로 기록한다(별도 테이블·센티넬 행 없음). 직전 틱과의 수신 간격이 임계치를 넘으면 그 틱의 `gap_before=1`.
- **D-11:** 거래소 발생 시각(`exchange_ts`)이 빗썸 WS 메시지에 없으면 `recv_ts` 값을 복사하고 `ts_estimated=1` 플래그로 표시한다. `exchange_ts` / `recv_ts` 두 컬럼 스키마는 항상 유지.
- **D-12:** 갭 판정 기준은 틱 간격 임계치. 같은 이벤트 내 직전 틱과의 `recv_ts` 간격이 예상 10초의 N배(예: 30초) 이상이면 갭으로 판정. 임계치는 상수 파라미터.

### Claude's Discretion
- `pump_ticks` 컬럼의 정확한 SQL 타입, 인덱스(`pump_id` 인덱스 권장) 설계
- 갭 판정 임계치(N배)의 구체적 상수값
- `seq` 컬럼을 절대 순번으로 할지 elapsed 초로 할지
- 틱 기록 함수(`log_tick` / `get_ticks`)의 정확한 시그니처 — 단, Phase 2가 의존하므로 명확해야 함
- `init_db()` 마이그레이션 패턴(기존 `ALTER TABLE` try/except 방식 따름)

### Deferred Ideas (OUT OF SCOPE)
- 펌핑 감지 기준 완화로 더 많은 샘플 확보 — 감지 로직 변경은 범위 밖
- 초/1~2초 단위 고해상도 틱 기록 — 10초 간격으로 시작
- 거래량 기반 전략 백테스트 — `acc_value`·`volume_power`를 저장만 해두고 전략화는 Phase 3 이후
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REC-01 | 봇이 RECORD_ONLY 모드로 동작 — 실거래 주문 차단, 데이터 수집만 | D-01/D-02 게이트 위치 확정(`do_buy` L802, `do_buy_limit` L835, `do_sell` L894). `config.yaml` 지연 로딩 패턴(`_get_cfg`) 재사용. 게이트는 함수 첫 줄 가드 클로즈로 구현 — "Architecture Patterns: RECORD_ONLY Gate" 참조 |
| REC-02 | pump_tracker가 펌핑 코인의 초 단위 가격 틱을 DB에 저장 | `start_pump_tracker` 10초 루프(L174~254)에 `log_tick()` 호출 추가. `pump_ticks` 테이블 스키마 + 마이그레이션 패턴 — "Standard Stack", "Code Examples" 참조 |
| REC-03 | 틱에 거래소 시각/수신 시각 분리 기록 (lookahead 방지) | **D-11 해소: 레거시 WS `content`에 `date`+`time` 필드 존재 → `exchange_ts` 실제값 채움 가능.** `on_message` 파싱 확장 필요 — "Open Questions" Q1, "Code Examples" 참조 |
| REC-04 | WS 단절 시 틱 갭 감지·기록 → 백테스트가 오염 구간 식별 | D-10/D-12 `gap_before` 플래그. `recv_ts` 간격 임계치 판정. `on_close`는 로깅만 하므로(L477) 갭 탐지는 틱 INSERT 시점의 시각 비교로 구현 — "Pitfalls" P1, "Code Examples" 참조 |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **합의 우선 / 한 번에 한 단계만:** 코드 수정 전 의견 제시 → 동의 후 진행. 플랜은 작은 태스크 단위로 분할할 것.
- **매매 코드 변경은 사용자 검토 후 커밋:** `do_buy`/`do_sell` 게이트 삽입은 매매 코드 변경에 해당 — 검토 게이트 필수.
- **config.yaml git 커밋 절대 금지:** `trading.record_only` 키 추가 시 config.yaml 자체는 커밋하지 않음. `.gitignore` 확인. config 예시는 별도 문서/주석으로 안내.
- **자동 git 백업:** 작업 마무리 시 add/commit/push (커밋 메시지는 사용자 검토). 커밋 메시지 영어 권장.
- **추측 금지:** 봇 상태/수치는 파일 확인 후 답변.
- **GSD 워크플로우:** Edit/Write 전 GSD 커맨드를 통해 작업 시작.
- **STATE.md 제약:** pump_log 기존 스키마(1m/2m/3m/5m 컬럼) 유지 필수 — `pump_ticks`는 순수 추가, `pump_log`/`update_pump_path` 불변.

## Standard Stack

### Core (모두 기존 사용 중 — 신규 설치 0개)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `sqlite3` | stdlib (Python 3.13) | `pump_ticks` 테이블 영속화 | 이미 `bithumb/db.py`가 전적으로 사용. 시계열 틱 ~60행/이벤트 규모는 SQLite로 충분 |
| `websocket-client` | 1.7.0 | 빗썸 레거시 공개 WS 수신 | 이미 `PriceTracker.start_ws`가 사용 (`WebSocketApp`) |
| `pyyaml` | 6.0.1 | `config.yaml`에서 `record_only` 플래그 로드 | 기존 `_get_cfg()` 지연 로딩 패턴 |
| `logging` | stdlib | "RECORD_ONLY 차단됨" 로그 | 기존 `[ALT]` 프리픽스 로거 |
| `time` / `datetime` | stdlib | `recv_ts`(`time.time()`), `exchange_ts` 파싱 | 기존 패턴 |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `threading` / `queue` | stdlib | `start_pump_tracker` 데몬 스레드, `_pump_queue` | 이미 사용 — 변경 없음 |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| SQLite 단일 테이블 | Parquet/CSV 틱 파일 | 백테스트(Phase 2)가 JOIN으로 `pump_log`↔`pump_ticks` 복원해야 함(D-07). 파일은 JOIN 불가. **SQLite 유지** |
| `ALTER TABLE` try/except 마이그레이션 | Alembic 등 마이그레이션 도구 | 단일 사용자 봇, 신규 테이블 1개 추가에 도구 도입은 과잉. **기존 패턴 유지** (D-Discretion 명시) |
| WS `date`/`time` 파싱 | `recv_ts`만 사용 | REC-03이 거래소 시각 분리를 명시적으로 요구. lookahead 방지 정확도. **파싱 채택** |

**Installation:** 없음. `requirements.txt` 변경 불필요.

**Version verification:** 신규 패키지 없음 — 레지스트리 확인 대상 없음. 기존 `requirements.txt`(websocket-client==1.7.0, pyyaml==6.0.1)는 변경하지 않는다.

## Architecture Patterns

### 변경 대상 파일 (신규 파일 0개)

```
bithumb/db.py            # pump_ticks 추가: CREATE_SQL, init_db 마이그레이션, log_tick(), get_ticks()
scripts/alt_monitor.py   # RECORD_ONLY 게이트, on_message date/time 파싱, start_pump_tracker log_tick 호출
config.yaml              # trading.record_only: true  (git 커밋 금지)
```

### Pattern 1: RECORD_ONLY Gate (Guard Clause)

**What:** 매매 함수 진입 즉시 플래그 확인 후 조기 반환. 신호·필터 로직은 손대지 않는다.
**When to use:** `do_buy`(L802), `do_buy_limit`(L835), `do_sell`(L894) — 정확히 3곳.

```python
# scripts/alt_monitor.py 상단 상수 영역 (WS_URL 부근, L58 근처)
# config.yaml 의 trading.record_only 를 1회 로드. 기본값 True(안전).
RECORD_ONLY = bool(_get_cfg().get("trading", {}).get("record_only", True))

# do_buy / do_buy_limit / do_sell 첫 줄
def do_buy(client: BithumbClient, coin: str, buy_krw: float) -> dict | None:
    if RECORD_ONLY:
        log.warning(f"[{coin}] RECORD_ONLY 차단됨 — 시장가 매수 {buy_krw:,.0f}원 미실행")
        return None
    market = f"KRW-{coin}"
    ...
```

**주의:** `do_sell`은 `float | None`을 반환한다(다른 둘은 `dict | None`). 게이트는 셋 다 `return None`이면 일관 — 호출부가 이미 None 처리. 단, 봇이 RECORD_ONLY로 재시작될 때 `active_pos.json`에 잔여 포지션이 있으면 `do_sell` 차단으로 청산 불가 → "Pitfalls P3" 참조.

### Pattern 2: SQLite 시계열 자식 테이블 (1:N, FK)

**What:** `pump_log`(부모, 이벤트 1행) ↔ `pump_ticks`(자식, 틱 N행). `pump_id` FK + 인덱스로 백테스트 JOIN 복원.
**When to use:** D-07/D-08 스키마. `CREATE_SQL`에 추가.

```sql
CREATE TABLE IF NOT EXISTS pump_ticks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pump_id       INTEGER NOT NULL,        -- pump_log.id 참조
    seq           INTEGER NOT NULL,        -- 이벤트 내 순번 (0,1,2,...)
    exchange_ts   REAL,                    -- 거래소 발생 시각 (epoch sec)
    recv_ts       REAL    NOT NULL,        -- 수집기 수신 시각 (time.time())
    price         REAL    NOT NULL,        -- closePrice
    acc_value     REAL,                    -- 누적 거래대금 (value)
    volume_power  REAL,                    -- 체결강도 (volumePower)
    gap_before    INTEGER DEFAULT 0,       -- 직전 틱과 갭이면 1 (REC-04)
    ts_estimated  INTEGER DEFAULT 0        -- exchange_ts가 recv_ts 복사값이면 1 (REC-03)
);
CREATE INDEX IF NOT EXISTS idx_pump_ticks_pump_id ON pump_ticks(pump_id);
```

타입 권장: 모든 timestamp는 `REAL`(epoch 초, `time.time()`과 동형 — 비교·산술 용이). `seq`/플래그는 `INTEGER`. 가격/거래대금은 `REAL`. **SQLite는 `FOREIGN KEY` 제약을 기본 미강제**하므로 `pump_id`는 논리적 참조로만 두고 인덱스로 JOIN 성능을 확보한다(별도 `PRAGMA foreign_keys=ON` 불필요 — 기존 코드 패턴과 일치).

### Pattern 3: 거래소 시각 파싱 (date+time → epoch)

**What:** 레거시 WS `content`의 `date`(YYYYMMDD) + `time`(HHMMSS, KST)를 epoch 초로 변환.
**When to use:** `on_message`에서 deque 튜플에 적재할 때.

```python
from datetime import datetime, timezone, timedelta
KST = timezone(timedelta(hours=9))

def _parse_exchange_ts(date_s: str, time_s: str) -> float | None:
    """빗썸 WS content date(YYYYMMDD)+time(HHMMSS, KST) → epoch sec. 실패 시 None."""
    try:
        dt = datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=KST).timestamp()
    except (ValueError, TypeError):
        return None
```

### Anti-Patterns to Avoid

- **별도 갭 테이블 / 센티넬 행:** D-10이 명시적으로 배제. `gap_before` 컬럼 플래그만 사용 — 백테스트가 틱 재생 중 즉시 식별, JOIN 불필요.
- **`pump_log` 스키마/`update_pump_path` 수정:** STATE.md 제약 + D-09 위반. `pump_ticks`는 순수 추가.
- **틱 수집을 위한 신규 API 호출:** D-04 위반. `start_pump_tracker`가 이미 호출하는 `tracker.get_latest_price(coin)` 데이터를 재사용. 추가 호출 0.
- **틱 INSERT 실패가 메인 루프를 멈춤:** 기존 `update_pump_path` 호출이 `try/except: pass`로 감싸진 패턴(L243~246)을 그대로 따른다. 데이터 수집 실패는 봇 운영을 막지 않는다.
- **`config.yaml`을 git에 커밋:** CLAUDE.md 절대 금지. `record_only` 키 추가는 로컬 파일에만.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 거래소 발생 시각 | `time.time()`을 거래소 시각으로 가정 | WS `content.date`+`time` 파싱 | 레거시 WS가 실제 거래소 시각 제공 — recv_ts와 다른 정보. lookahead 방지 정확도 |
| 틱 시계열 저장 | CSV/JSON 파일 직접 관리 | SQLite `pump_ticks` 테이블 | 트랜잭션, JOIN(D-07), 동시 쓰기 안전성. Phase 2 백테스트가 SQL 의존 |
| DB 스키마 마이그레이션 | 수동 DROP/CREATE | `init_db()` `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` try/except | 기존 데이터 보존, 멱등. 기존 봇 패턴 |
| WS 재연결 | 신규 재연결 로직 | 기존 `run()` 루프 5초 재연결(L480~494) | 이미 동작 중 — 갭 탐지는 재연결 이벤트가 아니라 틱 간격으로 판정 |

**Key insight:** 이 phase의 모든 "어려운 부분"(WS 수신, 10초 루프, DB 트랜잭션, 재연결)은 봇에 이미 구현돼 있다. 신규 코드는 **데이터를 버리지 않고 저장하는 글루(glue)**일 뿐이다. 새 추상화를 만들지 말고 기존 함수에 INSERT 한 줄을 끼워 넣는 방향이 정답이다.

## Common Pitfalls

### Pitfall 1: WS 무음 단절 — 갭이 탐지되지 않음 (REC-04 핵심)

**What goes wrong:** WebSocket이 끊겨도 `on_close`는 로그만 남기고(L477~478), 펌핑 추적 큐의 `start_pump_tracker` 루프는 계속 돈다. `tracker.get_latest_price(coin)`는 deque의 **마지막(오래된) 값**을 그대로 반환하므로, 단절 구간에도 "정상 틱"처럼 행이 INSERT되어 백테스트가 stale 가격을 진짜로 오인한다.
**Why it happens:** deque는 새 데이터가 안 와도 비워지지 않는다. `on_close`/재연결은 비동기이고 추적 루프와 분리돼 있다.
**How to avoid:** 갭 판정을 **틱 간격으로** 한다(D-12). `start_pump_tracker`가 각 이벤트의 직전 INSERT `recv_ts`를 보관하고, 새 틱 INSERT 시 `now - last_recv_ts >= GAP_THRESHOLD_SEC`이면 `gap_before=1`. 또는 deque 마지막 튜플의 수신 시각(`hist[-1][0]`)이 `now`보다 `GAP_THRESHOLD_SEC` 이상 오래됐으면 그 틱을 stale로 간주해 `gap_before=1` 마킹. 임계치 권장: `GAP_THRESHOLD_SEC = 30`(예상 10초의 3배 — D-12 예시와 일치). 상수로 분리.
**Warning signs:** `pump_ticks`에서 동일 `price`가 여러 연속 행에 반복 + `recv_ts` 간격은 정상 10초 → deque가 stale 값 재공급 중. `gap_before` 로직이 이를 잡아야 한다.

### Pitfall 2: exchange_ts / recv_ts 분리 누락 → 스키마 재설계 불가

**What goes wrong:** 둘 중 하나만 저장하면 Phase 2/3에서 lookahead 검증이 불가능해지고, 이미 수집한 2~3주 데이터를 버려야 한다.
**Why it happens:** "어차피 같은 값"이라는 착각. 실제로 거래소 발생 시각과 수신 시각은 네트워크 지연·버퍼링·단절로 갈린다.
**How to avoid:** D-11대로 **두 컬럼을 항상 유지**. `exchange_ts`를 채울 수 없을 때만 `recv_ts` 복사 + `ts_estimated=1`. 정상 경로에서는 `_parse_exchange_ts` 결과를 그대로 저장. 스키마는 첫 INSERT 전에 확정 — STATE.md "Phase 1 시작 전 확정" 제약.

### Pitfall 3: RECORD_ONLY 재시작 시 잔여 포지션 청산 불가

**What goes wrong:** RECORD_ONLY를 켠 채 봇을 재시작했는데 `data/active_pos.json`에 이전 실거래 포지션이 남아 있으면, 손절/익절 시 `do_sell`이 게이트에 막혀 청산되지 않는다. 봇이 "팔았다고 착각"하거나 무한 재시도할 수 있다.
**Why it happens:** RECORD_ONLY는 신규 진입만 막으면 된다는 가정. `do_sell`까지 막으면 기존 포지션이 갇힌다.
**How to avoid:** 플래너가 명시적으로 결정해야 함 — D-01은 `do_sell`도 게이트 대상으로 못박았다. 안전한 절차: **(a) RECORD_ONLY 켜기 전에 잔여 포지션을 수동 청산하고 `active_pos.json`을 비운다**, 또는 (b) 봇 시작 시 `active_pos.json`이 있으면 경고 로그 + 텔레그램 알림으로 운영자에게 알린다. 코드 변경보다 **운영 절차(체크리스트)**로 푸는 것이 D-01을 위반하지 않으면서 안전하다. 플랜에 "RECORD_ONLY 전환 전 포지션 비우기" 태스크를 넣을 것.

### Pitfall 4: 펌핑 추적 5분→10분 연장(D-05)의 부작용

**What goes wrong:** `start_pump_tracker`의 종료 조건은 현재 `item[12]`(`done_5m`)이다(L248). 추적을 10분으로 늘리려면 종료 조건을 바꿔야 하는데, `update_pump_path`의 5분 집계 저장 로직(L235~240)과 얽혀 있다.
**Why it happens:** 펌핑 추적 루프가 "5분"을 두 용도로 쓴다 — pump_log 집계 저장 시점 + 루프 종료 시점.
**How to avoid:** 둘을 분리. `pump_log` 집계(`price_5m` 등)는 **5분에 그대로 저장**(D-09: `update_pump_path` 불변), 단 루프 종료만 10분으로 연장. 즉 `done_5m` 도달 시 `update_pump_path` 호출은 유지하되 `still.append(item)`을 계속하고, 별도 `elapsed >= 600` 조건에서 루프를 끝낸다. `pump_ticks` INSERT는 10분 내내 10초마다 수행. 추적 item 튜플에 종료용 필드를 추가하거나 `elapsed` 기반 종료로 단순화 — D-Discretion(`seq`를 elapsed로 둘지)과 함께 설계.

### Pitfall 5: seq 채번 — 절대 순번 vs elapsed 초

**What goes wrong:** `seq`를 매 INSERT마다 +1 하는 카운터로 두면, 갭(틱 누락) 구간에서 `seq`가 연속이라 백테스트가 시간 공백을 모른다.
**Why it happens:** `seq`의 의미가 모호. D-Discretion이 명시적으로 이 선택을 플래너에 위임.
**How to avoid:** **`seq`는 절대 순번(0,1,2,...)으로 두고, 시간 공백은 `recv_ts`/`exchange_ts`와 `gap_before`로 표현**하는 것을 권장. elapsed 초는 `recv_ts - pump_log.detected_at`으로 언제든 계산 가능하므로 중복 저장 불필요. `seq`는 "이벤트 내 INSERT 순서"라는 단순 의미만 — 갭 식별은 `gap_before`의 책임. (플래너가 최종 결정.)

## Code Examples

### log_tick / get_ticks — bithumb/db.py 추가 (log_pump 스타일 준수)

```python
# bithumb/db.py — 기존 log_pump / update_pump_path 바로 아래에 추가

def log_tick(pump_id: int, seq: int, recv_ts: float, price: float,
             exchange_ts: float | None = None, acc_value: float | None = None,
             volume_power: float | None = None, gap_before: bool = False,
             ts_estimated: bool = False) -> None:
    """펌핑 이벤트 1틱을 pump_ticks 에 기록. pump_id 는 pump_log.id 참조."""
    if exchange_ts is None:
        exchange_ts = recv_ts
        ts_estimated = True
    with _conn() as con:
        con.execute(
            """INSERT INTO pump_ticks
               (pump_id, seq, exchange_ts, recv_ts, price, acc_value,
                volume_power, gap_before, ts_estimated)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (pump_id, seq, exchange_ts, recv_ts, price, acc_value,
             volume_power, int(gap_before), int(ts_estimated)),
        )


def get_ticks(pump_id: int) -> list[dict]:
    """특정 펌핑 이벤트의 모든 틱을 seq 순으로 반환 (Phase 2 백테스트용)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM pump_ticks WHERE pump_id = ? ORDER BY seq", (pump_id,)
        ).fetchall()
    return [dict(r) for r in rows]
```
*Source: 기존 `bithumb/db.py` `log_pump`(L168) / `get_trades`(L217) 패턴 — 모듈 레벨 함수, `_conn()` 컨텍스트 매니저, `sqlite3.Row`.*

**Phase 2 의존 계약 (확정 필요):** `log_tick(pump_id, seq, recv_ts, price, ...)` 위치 인자 4개 + 키워드 인자. `get_ticks(pump_id) -> list[dict]`. Phase 2 백테스트 엔진이 이 시그니처를 import한다 — 변경 시 Phase 2 영향. 플랜에 "시그니처 동결" 명시.

### init_db 마이그레이션 — bithumb/db.py

```python
# CREATE_SQL 문자열 끝에 pump_ticks CREATE TABLE + CREATE INDEX 추가 (위 Pattern 2 SQL)
# init_db() 는 con.executescript(CREATE_SQL) 가 IF NOT EXISTS 라 신규 테이블 자동 생성.
# 기존 ALTER TABLE 루프는 pump_ticks 가 신규 테이블이므로 추가 항목 불필요.
# (향후 pump_ticks 컬럼 추가 시에만 ("pump_ticks", "새컬럼", "타입") 항목 추가.)
```
*Source: 기존 `init_db()` L88~102.*

### on_message 거래소 시각 파싱 — scripts/alt_monitor.py

```python
# on_message 내부, 기존 closePrice/value/volumePower 파싱 옆 (L458 부근)
ex_ts = _parse_exchange_ts(c.get("date", ""), c.get("time", ""))  # None 가능
now = time.time()
with self._lock:
    ...
    if not hist or now - hist[-1][0] >= WS_MIN_INTERVAL:
        # 튜플 확장: (recv_ts, price, acc_val, exchange_ts)
        hist.append((now, price, acc_val, ex_ts))
```
*주의: deque 튜플을 4원소로 확장하면 `get_signal`(L516,524) 등 기존 언패킹 `now_ts, now_price, now_vol = snaps[-1]`이 깨진다. 모든 언패킹 지점을 4원소로 맞추거나, `*_` 로 흡수하거나, deque 대신 별도 매핑에 `exchange_ts`를 보관하는 방안을 플래너가 선택. 가장 안전: `now_ts, now_price, now_vol, *_ = snap` 형태로 기존 언패킹을 관용적으로 수정.*

### start_pump_tracker 틱 INSERT — scripts/alt_monitor.py

```python
# _run() 루프 내, update_pump_path 호출 옆 (L242 부근). 이벤트 item 에 seq/last_recv_ts 필드 추가 필요.
# 의사코드:
#   tick_seq = item[N]            # 이벤트별 틱 순번
#   last_recv = item[M]           # 직전 틱 recv_ts
#   gap = (now - last_recv) >= GAP_THRESHOLD_SEC if last_recv else False
#   ex_ts = tracker.get_latest_exchange_ts(coin)   # PriceTracker 신규 getter
#   try:
#       log_tick(pid, tick_seq, recv_ts=now, price=p,
#                exchange_ts=ex_ts, acc_value=acc, volume_power=vp,
#                gap_before=gap)
#   except Exception:
#       pass
#   item[N] += 1 ; item[M] = now
```
*Source: 기존 `start_pump_tracker` L242~246 try/except 패턴.*

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| 빗썸 레거시 공개 WS `wss://pubwss.bithumb.com/pub/ws` | API 2.0 WS `wss://api.bithumb.com/websocket/v1` (Upbit 호환 스키마) | API 2.0 출시 후 | 봇은 여전히 레거시 WS 사용 중(L58). 레거시는 `content`에 `date`/`time` 제공 — 이번 phase에 필요. **마이그레이션은 범위 밖** (CONTEXT.md는 레거시 가정) |

**Deprecated/outdated 주의:**
- 검색 결과 다수가 **Bithumb Pro / Global** (`global-api.bithumb.pro`)을 가리킨다 — 이는 한국 빗썸과 **다른 거래소·다른 스키마**다. Pro의 ticker `timestamp`(epoch) 필드는 봇과 무관. 봇이 쓰는 것은 한국 빗썸 레거시 공개 WS이며 `date`/`time` 문자열 필드를 쓴다. 플래너·실행자는 Pro 문서를 참조하지 말 것.

## Open Questions

1. **(해소됨) 레거시 WS `ticker` 메시지에 거래소 시각 필드가 있는가?**
   - 결론: **있다.** 빗썸 공식 API 문서(`apidocs.bithumb.com` "빗썸 거래소 정보 수신")가 `content` 페이로드에 `date`(YYYYMMDD)와 `time`(HHMMSS, KST) 필드를 명시한다. 다른 필드: `symbol, tickType, openPrice, closePrice, lowPrice, highPrice, value, volume, sellVolume, buyVolume, prevClosePrice, chgRate, chgAmt, volumePower`.
   - 영향: `exchange_ts`는 `date`+`time` 파싱으로 실제 채울 수 있다. D-11 폴백(`ts_estimated=1`)은 파싱 실패 시 예외 경로.
   - 신뢰도: HIGH (공식 문서). 단 실제 메시지 샘플로 1회 검증 권장 — "검증 방법" 아래 참조.

2. **레거시 WS `date`/`time`의 타임존이 KST인지?**
   - 알려진 것: 빗썸 한국 거래소의 모든 시각 표기는 관례상 KST(UTC+9). 문서 예시 `"20200129"`/`"121844"`도 KST로 해석된다.
   - 불확실: 문서가 타임존을 명시하지 않음.
   - 권장: `_parse_exchange_ts`는 KST 가정으로 구현하되, 첫 운영 시 `pump_ticks`의 `exchange_ts`와 `recv_ts` 차이가 합리적 범위(수백 ms~수초)인지 확인. 차이가 +9h/-9h면 타임존 오류 — 상수 한 줄 수정. 이 검증을 Phase 1 성공 기준 확인 단계에 포함할 것.

3. **`tickTypes`가 `["24H"]`일 때 `date`/`time`이 실시간 갱신되는가?**
   - 봇 구독은 `tickTypes: ["24H"]`(L444). `date`/`time`은 변동 기준시간(tickType)과 무관하게 **해당 체결 발생 시각**으로 채워지는 것이 일반적.
   - 권장: Q1·Q2와 함께 실제 메시지 1건을 로깅해 한 번에 검증.

**공통 검증 방법 (저비용, 플랜 태스크 1개):** `on_message`에 임시 디버그 로그 한 줄(`log.debug(f"[WS] content={c}")`)을 추가하거나 `scripts/_signal_check.py` 같은 일회성 스크립트로 실제 `content` 1건을 덤프 → `date`/`time` 필드 존재·형식·타임존 확정. 코드 확정 전 5분이면 끝난다.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | 전체 | ✓ | 3.13 (CLAUDE.md) | — |
| `sqlite3` | pump_ticks 저장 | ✓ | stdlib | — |
| `websocket-client` | WS 수신 | ✓ | 1.7.0 (requirements.txt) | — |
| `pyyaml` | config 로드 | ✓ | 6.0.1 | — |
| 빗썸 레거시 공개 WS | 틱 수집 | ✓ | `wss://pubwss.bithumb.com/pub/ws` (인증 불필요) | — |
| `data/trades.db` (SQLite 파일) | 테이블 생성 | ✓ | git status에 `trades.db` 존재 | `init_db()` 자동 생성 |

**Missing dependencies:** 없음. 신규 외부 의존성 0개 — 모두 봇이 이미 사용 중.

**주의:** 펌핑 틱 수집은 실제 펌핑 이벤트 발생에 의존한다. 봇 실행 직후엔 `pump_ticks`가 비어 있는 것이 정상 — 성공 기준 2(`SELECT COUNT(*)` 증가)는 펌핑 감지가 1회 이상 일어난 뒤 확인 가능. 검증을 위해 실제 펌핑을 기다리거나, `queue_pump`를 수동 호출하는 일회성 테스트 스크립트로 INSERT 경로를 먼저 검증할 것.

## Sources

### Primary (HIGH confidence)
- `apidocs.bithumb.com` — "빗썸 거래소 정보 수신" (레거시 공개 WebSocket ticker 스펙: `content`에 `date`/`time` 필드 명시)
- `c:\code\coinbase\bithumb\db.py` — 기존 DB 스키마, `log_pump`/`update_pump_path`/`init_db` 패턴
- `c:\code\coinbase\scripts\alt_monitor.py` — `PriceTracker.on_message`(L448~472), `start_pump_tracker`(L174~254), `queue_pump`(L323), `do_buy`(L802)/`do_buy_limit`(L835)/`do_sell`(L894), `WS_URL`(L58)
- `c:\code\coinbase\.planning\phases\01-tick-recording-infrastructure\01-CONTEXT.md` — D-01~D-12
- `c:\code\coinbase\CLAUDE.md` — 프로젝트 제약

### Secondary (MEDIUM confidence)
- 빗썸 WS `date`/`time` 타임존 = KST: 한국 빗썸 관례 기반 추론, 공식 명시 미확인 — Open Question 2에서 운영 시 검증 권장

### Tertiary (LOW confidence)
- Bithumb Pro/Global API 문서(`global-api.bithumb.pro`) — **봇과 무관한 다른 거래소**. 참조하지 말 것으로 명시 (State of the Art 참조)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 신규 의존성 0개, 모두 봇이 이미 사용 중. 코드로 직접 확인.
- Architecture: HIGH — 변경 지점이 코드 라인 단위로 특정됨(CONTEXT.md + 코드 읽기).
- Pitfalls: HIGH — WS 무음 단절(P1), 포지션 갇힘(P3), 5→10분 연장 부작용(P4)은 실제 코드 흐름에서 도출.
- D-11 (exchange_ts): HIGH — 빗썸 공식 문서로 `date`/`time` 필드 존재 확인. 실제 메시지 샘플 1회 검증만 남음(Open Question 1~3).

**Research date:** 2026-05-19
**Valid until:** 2026-06-18 (안정적 도메인 — stdlib + 기존 코드. 단 빗썸이 레거시 WS를 폐기하면 무효 — 운영 중 모니터링)
