# Stack Research

**Domain:** 암호화폐 스캘핑 봇 — 틱 데이터 수집 + 백테스트 인프라
**Researched:** 2026-05-19
**Confidence:** HIGH (SQLite/pandas/numpy — 공식 문서 직접 검증. 프레임워크 비교 — 훈련 데이터 기반이나 설계 원칙으로 검증 가능)

---

## 핵심 판단 요약

이 마일스톤은 **pandas + numpy 자체 구현**으로 충분하다. backtrader / vectorbt는 쓰지 않는다.

근거: 우리가 백테스트할 전략은 "펌핑 감지 후 N% 눌림에서 진입, M% TP / K% SL" 단일 규칙이다. 이 전략은 포트폴리오 최적화도, 복수 자산 동시 거래도, 복잡한 주문 유형도 없다. 프레임워크가 제공하는 95%의 기능이 낭비다. 직접 구현하면 코드가 100줄 이하로 끝나고, 디버깅이 단순하며, 기존 SQLite 데이터와 직결된다.

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.13 (기존) | 전체 런타임 | 기존 시스템과 동일. 변경 없음 |
| sqlite3 | stdlib | 틱 데이터 저장 + 백테스트 입력 | 추가 의존성 0. 기존 trades.db와 같은 파일에 tick_log 테이블 추가 |
| pandas | 2.2.x | 틱 데이터 로드, 시계열 집계, 백테스트 결과 분석 | DataFrame.rolling(), resample(), vectorized 연산이 이 분석에 정확히 맞음 |
| numpy | 2.0.x | 수익률 계산, 통계 지표, 누적 수익 배열 | pandas 내부적으로 사용. 명시적 np 연산은 배열 레벨 계산에만 필요 |

**Confidence:** HIGH — sqlite3는 stdlib이므로 확실. pandas 2.2, numpy 2.0는 현재 stable 릴리스 (공식 문서 확인).

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| scipy.stats | 1.14.x (scipy 설치 시 포함) | t-test, bootstrap confidence interval, Sharpe ratio z-score | 백테스트 결과의 통계적 유의성 검증 단계에서만 사용. EV 양/음 판정에 p-value 필요할 때 |
| scikit-learn | 1.5.x | TimeSeriesSplit (walk-forward 분할) | 데이터를 in-sample / out-of-sample로 분할할 때. 단 sklearn 전체를 설치하는 대신 TimeSeriesSplit만 참고해 직접 구현 가능 (20줄) |

**Confidence (scipy):** MEDIUM — stdlib 없이 통계 검정이 필요한 시점에 추가. 지금 당장 불필요.

**Confidence (sklearn):** MEDIUM — TimeSeriesSplit 로직 자체는 단순. 직접 구현하면 의존성 0으로 동일 효과.

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| pip | 패키지 설치 | pandas, numpy만 추가. requirements.txt에 고정 버전 명시 |
| sqlite3 CLI | 틱 DB 스키마 확인, 데이터 점검 | Windows에서 `sqlite3 data/trades.db .schema` |

---

## Installation

```bash
# 추가 의존성 (최소)
pip install "pandas>=2.2,<3.0" "numpy>=2.0,<3.0"

# 통계 검증이 필요할 때 (백테스트 결과 분석 단계)
pip install "scipy>=1.14,<2.0"
```

requirements.txt에 추가:
```
pandas>=2.2,<3.0
numpy>=2.0,<3.0
# scipy>=1.14,<2.0  # 통계 검증 단계에서 주석 해제
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| pandas + numpy 자체 구현 | backtrader | 여러 전략을 동시에 A/B 테스트하거나, 복잡한 주문 유형(OCO, stop-limit)을 시뮬레이션해야 할 때. 우리 전략엔 과함 |
| pandas + numpy 자체 구현 | vectorbt | 파라미터 격자 탐색(수백 개 파라미터 조합)이 필요할 때. 현재는 파라미터 1~2개. 나중에 최적화가 주요 목표가 되면 재검토 |
| sqlite3 (기존) | InfluxDB / TimescaleDB | 초당 10만 건 이상 틱을 여러 봇이 동시에 기록할 때. 우리는 WebSocket 1개, 단일 프로세스, 코인당 최대 초당 1~2건 |
| sqlite3 (기존) | Parquet / HDF5 | 백테스트 입력 데이터가 수십 GB를 넘을 때. 2~3주 틱 데이터는 최대 수십 MB 수준 |
| 자체 walk-forward 구현 | sklearn.TimeSeriesSplit | 외부 의존성을 허용할 수 있을 때. 로직이 동일하므로 직접 구현 권장 |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| backtrader | 2019년 이후 실질적 개발 중단. Python 3.11+ 호환 문제 보고. 이벤트 드리븐 OOP 구조가 단순 전략에 불필요한 복잡도 추가 | pandas 자체 구현 |
| vectorbt | numba 의존성 (~300MB 설치), numpy 버전 충돌 위험. 파라미터 최적화 목적 설계 — 지금은 "전략이 동작하는가" 검증만 필요 | pandas 자체 구현 |
| zipline / zipline-reloaded | 미국 주식 시장 전제로 설계. 빗썸 틱 데이터 스키마와 전혀 맞지 않음 | pandas 자체 구현 |
| InfluxDB / TimescaleDB / Redis | 인프라 추가 의존성 발생. 우리 규모(단일 봇, 458코인, 초당 수 건)에서는 SQLite WAL이 충분 | sqlite3 with WAL mode |
| pandas 1.x | 2023년 EOL. Copy-on-Write 미지원. 2.x API와 혼용 시 경고 다수 | pandas 2.2.x |

---

## Stack Patterns — 각 단계별 구체적 선택

**틱 데이터 수집 (Recording Mode):**

```
SQLite tick_log 테이블 (WAL 모드 활성화)
→ executemany() 로 배치 insert (10~30개 틱 묶음)
→ 인덱스: (coin, detected_at)
→ 기존 pump_log.id를 외래 키로 연결
```

**백테스트 엔진:**

```
sqlite3 → pandas DataFrame 로드
→ 코인별 그룹화 (groupby coin)
→ numpy 배열 레벨 시뮬레이션 루프
→ 결과를 DataFrame으로 집계
→ 출력: win_rate, avg_pnl, EV, max_drawdown
```

**통계 검증 (Walk-Forward):**

```
전체 데이터를 시간 순서로 N 분할 (직접 구현, 20줄)
→ 앞 70% in-sample: 파라미터 탐색
→ 뒤 30% out-of-sample: 검증
→ scipy.stats.ttest_1samp 으로 EV > 0 가설 검정
```

---

## SQLite 틱 데이터 스키마 권고

```sql
-- tick_log: 펌핑 이벤트당 초 단위 가격 경로
CREATE TABLE IF NOT EXISTS tick_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pump_id     INTEGER NOT NULL,           -- pump_log.id 외래 키
    coin        TEXT    NOT NULL,
    ts          INTEGER NOT NULL,           -- Unix timestamp (정수, 인덱싱 효율)
    price       REAL    NOT NULL,
    elapsed_sec INTEGER NOT NULL,           -- 펌핑 감지 후 경과 초
    FOREIGN KEY (pump_id) REFERENCES pump_log(id)
);

-- 복합 인덱스: 코인별 시간 조회 (백테스트 쿼리 패턴)
CREATE INDEX IF NOT EXISTS idx_tick_coin_ts ON tick_log (coin, ts);

-- 펌핑 단위 조회 (단일 이벤트 전체 경로 로드)
CREATE INDEX IF NOT EXISTS idx_tick_pump_id ON tick_log (pump_id, elapsed_sec);
```

**WAL 모드 활성화 (db.py init_db에 추가):**

```python
def init_db() -> None:
    with _conn() as con:
        # WAL: 실시간 기록 중 백테스트 읽기 동시 가능
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")   # WAL에서 충분
        con.execute("PRAGMA cache_size = -32000")    # 32MB 캐시
        con.executescript(CREATE_SQL)
        ...
```

**배치 Insert 패턴 (10~30개 묶어서 한 번에):**

```python
# alt_monitor.py의 펌핑 추적 루프 내부
tick_buffer = []   # 모듈 레벨 버퍼

def flush_ticks(con, ticks: list) -> None:
    con.executemany(
        "INSERT INTO tick_log (pump_id, coin, ts, price, elapsed_sec) VALUES (?,?,?,?,?)",
        ticks
    )

# 10개 쌓이면 한 번에 commit — 개별 commit의 100배 이상 빠름
if len(tick_buffer) >= 10:
    with _conn() as con:
        flush_ticks(con, tick_buffer)
    tick_buffer.clear()
```

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| pandas 2.2.x | numpy 1.23+ / 2.0.x | 2.0 둘 다 지원. numpy 2.0 권장 (일관된 타입 승급 규칙) |
| numpy 2.0.x | Python 3.9+ | Python 3.13 완전 지원 |
| scipy 1.14.x | numpy 1.23+ / 2.0.x | numpy 2.0과 호환 |
| websocket-client 1.7.0 | 변경 없음 | 기존 유지 |
| PyJWT 2.8.x | 변경 없음 | 기존 유지 |

**주의:** vectorbt는 numba를 사용하며 numpy 2.0과 호환 문제가 있다. 이것도 vectorbt를 피해야 하는 이유 중 하나.

---

## Sources

- SQLite 공식 문서 (sqlite.org/pragma.html) — WAL 모드, PRAGMA 권고 설정 직접 확인. **HIGH confidence**
- SQLite 공식 문서 (sqlite.org/limits.html) — 대량 insert 한계, parameterized insert 권고 확인. **HIGH confidence**
- Python 공식 문서 (docs.python.org/3/library/sqlite3.html) — executemany 동작 및 트랜잭션 처리 확인. **HIGH confidence**
- pandas 2.2 릴리스 노트 (pandas.pydata.org) — rolling(), resample() 기능 현황 확인. **HIGH confidence**
- numpy 2.0 릴리스 노트 (numpy.org) — 타입 승급 변경, Windows int64 기본값 변경 확인. **HIGH confidence**
- scikit-learn 공식 문서 (scikit-learn.org) — TimeSeriesSplit 파라미터, walk-forward 구현 확인. **HIGH confidence**
- backtrader / vectorbt 비교 — 훈련 데이터 기반. 공식 릴리스 이력으로 유지보수 중단 여부 추론. **MEDIUM confidence** (릴리스 날짜 직접 확인 권장)
- 기존 코드 직접 분석 (bithumb/db.py, alt_monitor.py) — 기존 스키마, 추적 루프 구조 확인. **HIGH confidence**

---

*Stack research for: 빗썸 스캘핑 봇 틱 데이터 수집 + 백테스트 인프라*
*Researched: 2026-05-19*
