# Phase 04: VB Trader Dry Run — Research

**Researched:** 2026-06-06
**Domain:** 변동성 돌파 전략, 독립 봇 프로세스, 모의투자(dry-run) 패턴
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**전략 파라미터 (고정)**
| 파라미터 | 결정값 |
|---------|--------|
| 목표가 공식 | 당일 시가 + 전일 고저폭 × K |
| K값 | 0.5 |
| TP | +3% |
| SL | -2% |
| 트레일링 | 없음 (TP/SL 고정 청산) |
| 자정 강제 청산 | 00:00 KST 미청산 포지션 시장가 청산 |
| 1회 진입금액 | 10만원 (alt_monitor와 별도 자본) |

**대상 코인**
- 일 거래량 20억 KRW 이상 코인만 (alt_monitor 볼륨 화이트리스트와 동일 기준)
- 코인 목록은 매일 자정 갱신

**봇 구조**
- 완전 분리된 독립 프로세스 — `scripts/vb_trader.py`
- 포지션 파일: `data/vb_pos.json`
- 로그 파일: `logs/vb_trader.log`
- watchdog `BOTS` dict에 `"vb_trader"` 항목 추가
- 동일 코인 동시 진입 가능 (alt_monitor와 자본 독립)

**데이터 소스**
- 당일 시가: `BithumbClient.get_candles(market, unit=1440, count=2)` 또는 일봉 — 없으면 KST 00:00 이후 첫 WebSocket 체결가
- 전일 고저폭: API 일봉 캔들 `high - low` (전일 기준)
- 실시간 가격: WebSocket (alt_monitor PriceTracker 패턴 재활용)

**실거래 여부**
- 모의투자 먼저 — `--dry-run` 플래그로 시작, 실제 주문 없이 신호/수익률 확인
- 충분한 검증 후 `--live` 플래그로 실전 전환 (사용자 명시 확인 필요)
- DB에는 `[VB-DRY]` 태그로 기록 (기존 CS-DRY 패턴과 동일)

**DB 기록**
- 기존 `trades` 테이블에 기록 (`exit_reason`에 `[VB]` 태그)
- `log_trade()` 함수 재활용

### Claude's Discretion
- 내부 아키텍처 세부 사항 (단일 루프 vs 스레드 분리, 상태 저장 주기 등)

### Deferred Ideas (OUT OF SCOPE)
- 백테스트 검증 없이 실전 투입 (사용자 결정에 따라 별도 확인 후)
- 그리드 매매, BB스퀴즈 등 다른 전략 (별도 phase)
- 멀티 포지션 (1코인 1포지션)
</user_constraints>

---

## Summary

Phase 04는 변동성 돌파(VB, Volatility Breakout) 전략을 `scripts/vb_trader.py`로 구현하는 작업이다. 래리 윌리엄스 K=0.5 공식을 사용해 "당일 시가 + 전일 고저폭 × 0.5"를 돌파 목표가로 계산하고, 현재가가 목표가를 상향 돌파하면 진입한다. 모의투자(--dry-run) 모드로만 시작해 실제 주문 없이 신호와 수익률을 DB에 기록한다.

코드베이스에 완전히 재활용 가능한 패턴이 이미 갖춰져 있다: `PriceTracker`(WebSocket 실시간 가격), `scan_oversold_candidates()`의 볼륨 화이트리스트 로직, `claude_screener.py`의 `--dry-run`/`[CS-DRY]` 태깅 패턴, `log_trade()` DB 기록 함수, `watchdog.py`의 `BOTS + EXTRA_ARGS` 구조가 그것이다.

핵심 신규 구현 항목은 두 가지다: (1) `BithumbClient`에 일봉 캔들 엔드포인트(`/v1/candles/days`) 래핑 메서드 추가, (2) 코인별로 "VB 목표가 계산 → 돌파 감지 → 모의 진입 → TP/SL/자정 청산" 상태 머신.

**Primary recommendation:** `claude_screener.py`의 dry-run 구조를 템플릿으로 삼아 `vb_trader.py`를 작성한다. 단, 멀티 포지션 대신 1코인 1포지션으로 단순화하고, 진입 판단을 Claude 호출 없이 순수 가격 비교(목표가 돌파)로 교체한다.

---

## Standard Stack

### Core
| Library/Module | Version/Source | Purpose | Why Standard |
|---------------|----------------|---------|--------------|
| `bithumb.client.BithumbClient` | 프로젝트 내부 | REST API, WebSocket 구독 | 이미 검증된 인증/요청 래퍼 |
| `bithumb.db.log_trade` | 프로젝트 내부 | trades 테이블 기록 | 기존 스키마와 호환, 통일된 분석 |
| `bithumb.notify.send` | 프로젝트 내부 | Telegram 알림 | 진입/청산 알림 |
| `websocket-client==1.7.0` | requirements.txt | 실시간 가격 스트리밍 | 이미 사용 중, PriceTracker 재활용 |
| `pyyaml==6.0.1` | requirements.txt | config.yaml 로드 | 표준 설정 방식 |
| `argparse` | stdlib | --dry-run 플래그 파싱 | claude_screener.py와 동일 패턴 |

### 신규 추가 필요
| 항목 | 위치 | 내용 |
|------|------|------|
| `get_daily_candles(market, count)` | `bithumb/client.py` | `/v1/candles/days` 엔드포인트 래핑 (현재 미존재) |

**Installation:** 신규 패키지 없음 — 기존 requirements.txt 그대로 사용.

---

## Architecture Patterns

### Recommended Project Structure
```
scripts/
└── vb_trader.py          # VB 전략 독립 봇 (신규)

data/
└── vb_pos.json           # VB 포지션 상태 파일 (신규, auto-created)

logs/
└── vb_trader.log         # 전용 로그 (신규, auto-created)

bithumb/
└── client.py             # get_daily_candles() 메서드 추가 (기존 파일 수정)

scripts/
└── watchdog.py           # BOTS dict에 "vb_trader" 항목 추가 (기존 파일 수정)
```

### Pattern 1: argparse로 --dry-run 플래그 결정 (claude_screener.py 패턴)

`claude_screener.py:63-78`의 패턴을 그대로 복사한다.

```python
# Source: scripts/claude_screener.py:63-78
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--live",    action="store_true")
    args, _ = p.parse_known_args()
    return args

_args = _parse_args()
_DRY_RUN = _args.dry_run
_LOG_TAG  = "VB-DRY" if _DRY_RUN else "VB"
_LOG_FILE = "logs/vb_trader.log"
_POS_PATH = Path("data/vb_pos.json")
```

### Pattern 2: [VB-DRY] 태깅과 log_trade() 호출 (claude_screener.py 패턴)

`claude_screener.py:518-536`의 `_record()` 함수 구조를 그대로 복사한다. `exit_reason`에 `[{_LOG_TAG}]`를 prefix로 붙이면 DB 쿼리 시 VB 트레이드만 필터링 가능.

```python
# Source: scripts/claude_screener.py:518-536
def _record(pos: dict, exit_price: float, recv_krw: float, reason: str) -> None:
    try:
        log_trade(
            coin=pos["coin"], market=pos["market"],
            entry_price=pos["entry_price"], exit_price=exit_price,
            volume=pos["volume"], cost_krw=pos["cost_krw"],
            received_krw=recv_krw,
            exit_reason=f"[{_LOG_TAG}] {reason}",   # "[VB-DRY] TP+3%"
            entered_at=datetime.fromisoformat(pos["entered_at"]).replace(tzinfo=None),
            exited_at=datetime.now(),
            max_price=pos.get("highest", exit_price),
        )
    except Exception as e:
        log.error(f"[DB] 기록 실패: {e}")
```

### Pattern 3: 볼륨 화이트리스트 갱신 (alt_monitor.py 패턴)

`alt_monitor.py:257-284`의 `scan_oversold_candidates()` 내부 볼륨 화이트리스트 갱신 로직만 추출해 재사용한다. `get_ticker("ALL")`을 호출해 `acc_trade_value_24H >= 20_000_000_000` 코인 집합을 반환한다.

```python
# Source: scripts/alt_monitor.py:257-284 (볼륨 필터 부분만 추출)
def _build_volume_whitelist(client: BithumbClient) -> set[str]:
    """24h 거래대금 20억+ 코인 심볼 반환."""
    try:
        tickers = client.get_ticker("ALL")
        wl: set[str] = set()
        for coin, data in tickers.items():
            if coin == "date":
                continue
            vol = float(data.get("acc_trade_value_24H", 0))
            if vol >= MIN_DAILY_VOLUME_KRW:
                wl.add(coin)
        log.info(f"[볼륨필터] {len(wl)}개 코인 (20억+)")
        return wl
    except Exception as e:
        log.warning(f"[볼륨필터] 갱신 실패: {e}")
        return set()
```

### Pattern 4: WebSocket PriceTracker (alt_monitor.py 패턴)

`alt_monitor.py:713-936`의 `PriceTracker` 클래스를 `vb_trader.py`에 인라인으로 복사하거나, 필요한 메서드만 추출한다. 핵심 메서드: `start_ws(symbols)`, `get_latest_price(coin)`, `stop_ws()`.

VB 전략은 볼륨 신호 대신 순수 가격 비교(목표가 돌파)만 필요하므로, `get_signal()`/`get_preemptive_signal()` 등 복잡한 메서드는 불필요하다. `get_latest_price()` + `start_ws()` + `stop_ws()`만 사용한다.

```python
# Source: scripts/alt_monitor.py:713-936 (핵심 메서드만)
class PriceTracker:
    MAXLEN = 60
    def __init__(self): ...
    def start_ws(self, symbols: list[str]) -> None: ...
    def get_latest_price(self, coin: str) -> float: ...
    def stop_ws(self) -> None: ...
```

### Pattern 5: watchdog BOTS dict + EXTRA_ARGS 추가

`watchdog.py:36-64`의 `BOTS`와 `EXTRA_ARGS` 패턴.

```python
# Source: scripts/watchdog.py:36-64
BOTS = {
    "alt_monitor":         ROOT / "scripts" / "alt_monitor.py",
    "tg_bot":              ROOT / "scripts" / "tg_bot.py",
    "claude_intelligence": ROOT / "scripts" / "claude_intelligence.py",
    "swing_monitor":       ROOT / "scripts" / "swing_monitor.py",
    "vb_trader":           ROOT / "scripts" / "vb_trader.py",   # 추가
}

EXTRA_ARGS: dict[str, list[str]] = {
    "claude_screener_dry":   ["--dry-run"],
    "claude_screener_watch": ["--watch-mode"],
    "swing_monitor":         ["--loop"],
    "vb_trader":             ["--dry-run"],   # 추가 — 모의투자 모드로 시작
}
```

### Pattern 6: 모의매도 (dry-run sell)

```python
# Source: scripts/claude_screener.py:470-484
def _do_sell_dry(pos: dict, current_price: float, reason: str) -> None:
    """모의투자 청산: 실제 주문 없이 현재가로 PnL 계산 후 DB 기록."""
    vol     = pos["volume"]          # 모의 수량 = 진입금액 / 진입가
    recv    = current_price * vol    # 슬리피지 없음 (페이퍼)
    pnl_krw = recv - pos["cost_krw"]
    pnl_pct = pnl_krw / pos["cost_krw"] * 100
    log.info(f"[{pos['coin']}] [DRY] 청산 @{current_price:,.1f}원 PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}")
    _record(pos, current_price, recv, reason)
```

### Anti-Patterns to Avoid
- **`unit=1440` 분봉 오해:** `get_candles(unit=1440)`은 존재하지 않는다. 빗썸 분봉 API는 unit 1/3/5/10/15/30/60/240만 지원한다. 일봉은 `/v1/candles/days` 별도 엔드포인트다.
- **오늘 캔들을 전일로 착각:** `/v1/candles/days` 응답의 `idx[0]`은 오늘 진행 중인 캔들(당일 시가 포함), `idx[1]`은 어제 완성된 캔들(전일 고저폭에 사용). `count=2`면 인덱스 0=오늘, 1=어제.
- **240분봉으로 당일 시가 얻기:** 240분봉 경계는 KST 00:00, 04:00, 08:00...으로 당일 시가가 아닐 수 있다. 일봉 캔들 `opening_price`를 사용해야 한다.
- **PriceTracker를 그대로 복사해 포트 충돌:** `alt_monitor.py:43-48`에서 포트 47219로 단일 인스턴스를 강제한다. `vb_trader.py`는 다른 포트(예: 47220)를 사용해야 한다.
- **positions dict 대신 pos 단일 변수:** VB 전략은 1코인 1포지션이다. 멀티 포지션 딕셔너리가 필요 없다.
- **config.yaml commit:** 절대 금지 (CLAUDE.md 보안 원칙).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| API 인증/요청 | 직접 JWT 생성 + requests | `BithumbClient` (bithumb/client.py) | 이미 검증된 HS256 인증, 재사용 |
| 일봉 캔들 파싱 | 직접 `/v1/candles/days` 호출 | `BithumbClient.get_daily_candles()` 추가 메서드 | `get_candles()`와 일관된 인터페이스 유지 |
| Telegram 알림 | 직접 requests.post | `bithumb.notify.send()` | 무음 시간대 처리, 재시도 로직 내장 |
| DB 기록 | 직접 sqlite3 INSERT | `bithumb.db.log_trade()` | 스키마 일관성, pnl_krw/pnl_pct 자동 계산 |
| WebSocket 재연결 | 직접 websocket 루프 | `PriceTracker.start_ws()` 복사 | 재연결, 파싱, lock 처리 이미 구현 |
| 포지션 영속성 | 직접 파일 쓰기 | JSON 저장 패턴 (alt_monitor save_active 참고) | 봇 재시작 시 포지션 복구 |

---

## API Behavior: 일봉 캔들 (VERIFIED)

### `/v1/candles/days` 엔드포인트 (라이브 테스트 확인)

```
GET https://api.bithumb.com/v1/candles/days?market=KRW-BTC&count=2
```

**응답 필드:**
- `opening_price`: 당일(또는 해당일) 시가
- `high_price`: 고가
- `low_price`: 저가
- `trade_price`: 현재 종가 (당일은 실시간 갱신)
- `candle_date_time_kst`: KST 기준 캔들 시작 시각 (00:00:00)
- 인증 없음 (Public)

**인덱스 해석 (count=2):**
- `[0]`: 오늘 진행 중인 캔들 → `opening_price`가 당일 시가
- `[1]`: 어제 완성된 캔들 → `high_price - low_price`가 전일 고저폭

**VB 목표가 계산:**
```python
candles = client.get_daily_candles("KRW-BTC", count=2)
today_open    = candles[0]["opening_price"]   # 당일 시가
yesterday_hi  = candles[1]["high_price"]       # 전일 고가
yesterday_lo  = candles[1]["low_price"]        # 전일 저가
vb_target     = today_open + (yesterday_hi - yesterday_lo) * K  # K=0.5
```

**BithumbClient에 추가할 메서드 (bithumb/client.py):**
```python
def get_daily_candles(self, market: str, count: int = 2) -> list[dict]:
    """Get daily candles. Returns newest-first (idx[0]=today, idx[1]=yesterday)."""
    resp = self._session.get(
        f"{BASE_URL}/v1/candles/days",
        params={"market": market.upper(), "count": count},
    )
    resp.raise_for_status()
    return resp.json()
```

**신뢰도:** HIGH — 라이브 API 테스트로 직접 확인 (2026-06-06)

---

## VB 목표가 계산 로직

```python
# 당일 시가 획득 우선순위:
# 1순위: /v1/candles/days idx[0].opening_price
# 2순위: /public/ticker/{coin}_KRW 의 opening_price 필드
#        (ticker data에도 opening_price 있음 — alt_monitor get_ticker 반환값에 포함)
# 3순위: 자정 이후 WebSocket에서 첫 체결가 수신 시 그 가격을 시가로 사용

# 전일 고저폭:
# /v1/candles/days idx[1].high_price - idx[1].low_price

# 라이브 테스트 결과 (2026-06-06, BTC):
# today_open = 92,252,000원
# yesterday range = 96,014,000 - 91,513,000 = 4,501,000원
# VB target (K=0.5) = 92,252,000 + 2,250,500 = 94,502,500원
```

**ticker의 `opening_price`도 사용 가능:** `client.get_ticker(coin)`이 반환하는 dict에 `opening_price` 필드가 있음 (`alt_monitor.py` 실 사용 확인). 일봉 캔들 API 호출 실패 시 fallback으로 사용할 수 있다.

---

## 포지션 상태 머신

VB 전략의 포지션은 단순 2상태다 (`alt_monitor`의 복잡한 phase 시스템 불필요):

```
[FREE]
  ↓ 현재가 >= vb_target (목표가 상향 돌파)
[HOLDING]
  ↓ 현재가 >= entry * (1 + TP=0.03)   → "TP+3%" 청산
  ↓ 현재가 <= entry * (1 - SL=0.02)   → "SL-2%" 청산
  ↓ 자정(00:00 KST) 도달              → "자정강제청산" 청산
  ↓ (→ FREE)
```

**포지션 파일 구조 (`data/vb_pos.json`):**
```json
{
  "coin": "BTC",
  "market": "KRW-BTC",
  "entry_price": 94502500.0,
  "volume": 0.001057,
  "cost_krw": 100000,
  "highest": 95000000.0,
  "entered_at": "2026-06-06T10:30:00",
  "vb_target": 94502500.0,
  "today_open": 92252000.0,
  "mock": true
}
```

---

## Common Pitfalls

### Pitfall 1: 목표가 돌파 감지 — 단순 >= 체크만으로는 한 번만 감지
**What goes wrong:** 현재가가 vb_target 위에 계속 있으면 매 루프에서 진입 조건이 참이 되어 중복 진입 시도.
**Why it happens:** 상태 없이 매 루프에서 `current >= vb_target` 비교만 하기 때문.
**How to avoid:** 포지션 있을 때 진입 로직 스킵 (`if pos is not None: continue`). 1코인 1포지션이므로 코인당 한 번만 진입.
**Warning signs:** 로그에 같은 코인으로 연속 진입 로그.

### Pitfall 2: 자정 강제 청산 타이밍
**What goes wrong:** 00:00 KST를 `datetime.now().hour == 0`으로 체크하면 첫 1분 동안 반복 청산 시도.
**Why it happens:** 루프 반복 + 시간 조건 중복.
**How to avoid:** 청산 후 `pos = None`으로 바로 상태 초기화. 또는 `midnight_done: bool` 플래그로 당일 자정 청산 1회만 실행.
**Warning signs:** "자정강제청산" 로그가 같은 코인에 여러 번 출력됨.

### Pitfall 3: 볼륨 화이트리스트 당일 갱신 타이밍
**What goes wrong:** 자정 직후 whitelist를 바로 갱신하면 당일 첫 1분봉 거래대금만으로 판단해 잘못된 필터링.
**Why it happens:** `acc_trade_value_24H`는 24h 롤링 값이므로 자정에도 전날 데이터 기준으로 갱신됨. 실제로는 문제없음.
**How to avoid:** 화이트리스트는 자정 직후 1회 갱신하면 된다. `acc_trade_value_24H`는 24h 롤링이라 자정 직후에도 어제 데이터를 반영한다.

### Pitfall 4: get_candles()와 get_daily_candles() 혼동
**What goes wrong:** `get_candles(market, unit=1440, count=2)` 호출 → 빗썸 API가 unit=1440 지원 안 해 HTTP 400 오류.
**Why it happens:** CONTEXT.md에 "unit=1440" 가능성으로 언급된 것을 그대로 사용.
**How to avoid:** 일봉 캔들은 반드시 `/v1/candles/days` 전용 엔드포인트를 사용. `BithumbClient.get_daily_candles()` 메서드를 신규 추가해야 한다.
**Verified:** 라이브 테스트에서 unit=240 (최대) 외 더 큰 unit은 미지원.

### Pitfall 5: 포트 충돌 — alt_monitor 단일 인스턴스 포트와 충돌
**What goes wrong:** alt_monitor가 포트 47219를 점유. vb_trader도 동일 포트 바인딩 시도 → 즉시 종료.
**Why it happens:** alt_monitor:43-48에서 TCP 소켓으로 단일 인스턴스를 강제.
**How to avoid:** vb_trader.py는 다른 포트 사용 (예: 47220).

### Pitfall 6: 모의투자 volume 계산
**What goes wrong:** 진입금액(10만원)을 volume으로 저장하면 log_trade에서 pnl 계산 오류.
**Why it happens:** 실거래에서 volume은 코인 수량이고, 모의투자에서 volume을 금액으로 저장하면 recv_krw 계산 시 가격 × volume이 이상해짐.
**How to avoid:** `volume = cost_krw / entry_price` (코인 수량으로 저장). 청산 시 `recv = exit_price * volume`.

---

## Code Examples

### 일봉 캔들로 VB 목표가 계산
```python
# Verified live: 2026-06-06 with KRW-BTC
def calc_vb_target(client: BithumbClient, coin: str, k: float = 0.5) -> tuple[float, float] | None:
    """(vb_target, today_open) 반환. 실패 시 None."""
    try:
        candles = client.get_daily_candles(f"KRW-{coin}", count=2)
        if len(candles) < 2:
            return None
        today_open   = float(candles[0]["opening_price"])
        prev_high    = float(candles[1]["high_price"])
        prev_low     = float(candles[1]["low_price"])
        prev_range   = prev_high - prev_low
        if prev_range <= 0 or today_open <= 0:
            return None
        vb_target = today_open + prev_range * k
        return vb_target, today_open
    except Exception as e:
        log.warning(f"[{coin}] VB 목표가 계산 실패: {e}")
        return None
```

### 메인 루프 구조 (단순화)
```python
def run():
    # 초기화
    client  = BithumbClient()
    tracker = PriceTracker()
    whitelist: set[str] = set()
    vb_targets: dict[str, float] = {}  # coin -> vb_target
    pos: dict | None = load_pos()       # data/vb_pos.json

    # 화이트리스트 + VB 목표가 갱신 (자정 이후 1회)
    whitelist = _build_volume_whitelist(client)
    for coin in whitelist:
        result = calc_vb_target(client, coin)
        if result:
            vb_targets[coin] = result[0]

    # WebSocket 시작
    symbols = [f"{c}_KRW" for c in whitelist]
    tracker.start_ws(symbols)
    time.sleep(5)

    today = date.today()
    midnight_cleared = False

    while True:
        now_kst = datetime.now(KST)

        # 날짜 바뀌면 VB 목표가 재계산 + whitelist 갱신
        if date.today() != today:
            today = date.today()
            midnight_cleared = False
            whitelist = _build_volume_whitelist(client)
            vb_targets.clear()
            for coin in whitelist:
                result = calc_vb_target(client, coin)
                if result:
                    vb_targets[coin] = result[0]

        # 자정 강제 청산 (00:00 ~ 00:01 KST)
        if now_kst.hour == 0 and now_kst.minute == 0 and not midnight_cleared:
            if pos is not None:
                current = tracker.get_latest_price(pos["coin"])
                _do_sell_dry(pos, current, "자정강제청산")
                pos = None
                save_pos(None)
            midnight_cleared = True

        # 포지션 없을 때: 돌파 감지
        if pos is None:
            for coin, target in vb_targets.items():
                current = tracker.get_latest_price(coin)
                if current <= 0 or current < target:
                    continue
                # 목표가 돌파 → 모의 진입
                volume = VB_ENTRY_KRW / current
                pos = {"coin": coin, "market": f"KRW-{coin}",
                       "entry_price": current, "volume": volume,
                       "cost_krw": VB_ENTRY_KRW, "highest": current,
                       "entered_at": datetime.now().isoformat(), "mock": True}
                save_pos(pos)
                log.warning(f"[VB-DRY] {coin} 목표가 돌파! {target:,.0f} <= {current:,.0f}원 → 모의 진입")
                notify.send(f"[VB-DRY] {coin} 모의 진입 @{current:,.0f}원 (목표={target:,.0f}원)")
                break  # 1코인 1포지션

        # 포지션 있을 때: TP/SL 체크
        elif pos is not None:
            coin    = pos["coin"]
            entry   = pos["entry_price"]
            current = tracker.get_latest_price(coin)
            if current <= 0:
                time.sleep(SCAN_SEC)
                continue

            if current > pos["highest"]:
                pos["highest"] = current
                save_pos(pos)

            pnl_pct = (current - entry) / entry

            if pnl_pct >= VB_TP:
                _do_sell_dry(pos, current, f"TP+3% ({pnl_pct*100:+.1f}%)")
                pos = None; save_pos(None)
            elif pnl_pct <= VB_SL:
                _do_sell_dry(pos, current, f"SL-2% ({pnl_pct*100:+.1f}%)")
                pos = None; save_pos(None)

        time.sleep(SCAN_SEC)
```

### watchdog.py 수정 (차이점만)
```python
# Source: scripts/watchdog.py:36-64 — 두 줄 추가
BOTS = {
    ...기존...,
    "vb_trader": ROOT / "scripts" / "vb_trader.py",  # 추가
}

EXTRA_ARGS: dict[str, list[str]] = {
    ...기존...,
    "vb_trader": ["--dry-run"],  # 추가 — 모의투자로 시작
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `get_candles(unit=1440)` (존재 안 함) | `GET /v1/candles/days` | N/A (처음부터) | 일봉은 별도 엔드포인트 필요 |
| Claude 호출로 진입 판단 | 순수 가격 비교 (목표가 돌파) | 이번 phase | VB는 수식 기반, Claude 불필요 |

---

## Open Questions

1. **자정 강제 청산 주체**
   - What we know: watchdog이 자정에 `write_session()` 호출함 (watchdog.py:197-201)
   - What's unclear: watchdog이 재시작 이전에 봇이 직접 자정 처리를 해야 하는지, 아니면 watchdog 재시작에 의존할지
   - Recommendation: vb_trader 내부에서 직접 00:00 KST 자정 강제 청산 구현 (watchdog 타이밍에 의존하지 않는 것이 안전)

2. **동일 코인 재진입 (당일 2회 목표가 돌파)**
   - What we know: VB 전략 원래 설계는 당일 1회 진입
   - What's unclear: 청산 후 같은 날 같은 코인이 다시 목표가를 돌파하면 재진입 여부
   - Recommendation: 당일 `entered_coins: set[str]` 집합을 관리해 당일 1코인 1번만 진입. 자정에 초기화.

3. **화이트리스트 갱신 주기**
   - What we know: alt_monitor는 5분마다 갱신 (`OVERSOLD_SCAN_INTERVAL = 300`)
   - What's unclear: VB 전략에서 자정에만 1회 갱신으로 충분한지, 중간에 추가 상장 코인을 잡을지
   - Recommendation: 자정 1회 갱신으로 단순하게. VB 전략 코인 풀은 안정적인 기존 코인이 대상이므로 중간 갱신 불필요.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | 봇 실행 | ✓ | 3.13 | - |
| websocket-client | PriceTracker WebSocket | ✓ | 1.7.0 | - |
| pyyaml | config 로드 | ✓ | 6.0.1 | - |
| requests | HTTP API 호출 | ✓ | 2.31.0 | - |
| Bithumb `/v1/candles/days` | 일봉 캔들 | ✓ | 라이브 확인 | `/public/ticker/{coin}` opening_price |
| config.yaml (로컬) | API 키 | 사용자 환경에 존재 | N/A | 없으면 봇 시작 불가 |

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | 없음 (프로젝트 테스트 프레임워크 미설정) |
| Config file | 없음 |
| Quick run command | 수동 확인 (로그 검사) |
| Full suite command | N/A |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| VB-01 | vb_trader.py --dry-run 실행 시 시작 로그 출력 | smoke | `python scripts/vb_trader.py --dry-run &` 후 로그 확인 | ❌ Wave 0 |
| VB-02 | VB 목표가 계산 정확성 | manual | `calc_vb_target()` 결과를 BTC로 수동 계산과 비교 | ❌ Wave 0 |
| VB-03 | 볼륨 화이트리스트 20억+ 필터 | smoke | 로그에 "볼륨필터 N개 코인 (20억+)" 출력 확인 | ❌ Wave 0 |
| VB-04 | 모의 진입 후 DB에 [VB-DRY] 태그 기록 | smoke | `sqlite3 data/trades.db "SELECT exit_reason FROM trades WHERE exit_reason LIKE '%VB-DRY%'"` | ❌ Wave 0 |
| VB-05 | watchdog가 vb_trader를 --dry-run으로 재시작 | manual | watchdog 실행 후 vb_trader 프로세스 cmdline 확인 | ❌ Wave 0 |

### Wave 0 Gaps
- [ ] `scripts/vb_trader.py` — 핵심 구현 파일 (신규)
- [ ] `bithumb/client.py`에 `get_daily_candles()` 추가
- [ ] `scripts/watchdog.py`에 `"vb_trader"` 항목 추가

*(테스트 프레임워크 없음 — 스모크 테스트는 실행 후 로그/DB 수동 확인)*

---

## Sources

### Primary (HIGH confidence)
- 라이브 API 테스트 (2026-06-06): `GET /v1/candles/days?market=KRW-BTC&count=2` 직접 호출 확인
- `scripts/alt_monitor.py` (로컬 코드베이스) — PriceTracker, 볼륨 화이트리스트, 진입/청산 패턴
- `bithumb/client.py` (로컬 코드베이스) — API 메서드 시그니처
- `bithumb/db.py` (로컬 코드베이스) — log_trade() 시그니처, trades 테이블 스키마
- `scripts/claude_screener.py` (로컬 코드베이스) — dry-run 패턴, [CS-DRY] 태깅, _record() 함수
- `scripts/watchdog.py` (로컬 코드베이스) — BOTS dict, EXTRA_ARGS 구조

### Secondary (MEDIUM confidence)
- `bithumb/notify.py` — Telegram 알림 패턴 (직접 확인)

---

## Metadata

**Confidence breakdown:**
- API behavior (일봉 캔들): HIGH — 라이브 테스트로 직접 확인
- Dry-run 패턴: HIGH — claude_screener.py 코드 직접 확인
- 볼륨 화이트리스트: HIGH — alt_monitor.py 코드 직접 확인
- watchdog 통합: HIGH — watchdog.py 코드 직접 확인
- log_trade() 시그니처: HIGH — db.py 코드 직접 확인

**Research date:** 2026-06-06
**Valid until:** 2026-07-06 (API 엔드포인트 변경 없을 것으로 예상, 코드베이스는 동일)
