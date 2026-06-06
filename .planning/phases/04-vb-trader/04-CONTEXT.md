# Phase 04: VB Trader — Context

**Phase goal:** 변동성 돌파(Volatility Breakout) 전략을 실전 단타 모듈로 구현한다.
`scripts/vb_trader.py` 신규 스크립트, watchdog 통합, 즉시 실거래.

---

## Decisions

### 전략 파라미터 (고정)

| 파라미터 | 결정값 | 비고 |
|---------|--------|------|
| 목표가 공식 | 당일 시가 + 전일 고저폭 × K | |
| K값 | **0.5** | 래리 윌리엄스 원래 설정 |
| TP | **+3%** | 목표가 돌파 후 +3% |
| SL | **-2%** | 진입가 대비 -2% |
| 트레일링 | 없음 | TP/SL 고정 청산 |
| 자정 강제 청산 | **00:00 KST** 미청산 포지션 시장가 청산 | 당일 전략 기준 초기화 |
| 1회 진입금액 | **10만원** | alt_monitor와 별도 자본 |

### 대상 코인

- 일 거래량 **20억 KRW 이상** 코인만 대상 (alt_monitor의 볼륨필터 화이트리스트와 동일 기준)
- 기존 `alt_monitor.py`의 볼륨 화이트리스트 갱신 로직 재활용 가능
- 코인 목록은 매일 자정 갱신 (당일 거래량 기준)

### 봇 구조

- **완전 분리된 독립 프로세스** — `scripts/vb_trader.py`
- 포지션 파일: `data/vb_pos.json` (alt_monitor의 `active_pos.json`과 별도)
- 로그 파일: `logs/vb_trader.log`
- watchdog `BOTS` dict에 `"vb_trader"` 항목 추가
- 동일 코인 동시 진입 가능 (alt_monitor와 자본 독립)

### 데이터 소스

- **당일 시가**: `BithumbClient.get_candles(market, unit=1440, count=2)` 240분 캔들 or 일봉 — 없으면 KST 00:00 이후 첫 웹소켓 체결가
- **전일 고저폭**: API 일봉 캔들 `high - low` (전일 기준)
- 실시간 가격: 웹소켓 (alt_monitor PriceTracker 패턴 재활용)

### 실거래 여부

- **즉시 실전** — RECORD_ONLY 게이트 **통과** (config.yaml `trading.record_only` 무시, 별도 플래그 `vb_live: true` 사용)
- 단, config.yaml에 `vb_trader.live: true` 명시 필요 — 미설정 시 dry 동작 (안전 기본값)

### DB 기록

- 기존 `trades` 테이블에 기록 (`exit_reason` 에 `[VB]` 태그)
- `log_trade()` 함수 재활용

---

## 재활용 가능 코드 자산

| 자산 | 위치 | 재활용 방식 |
|------|------|------------|
| `BithumbClient` | `bithumb/client.py` | market_buy, market_sell, get_candles |
| `log_trade()` | `bithumb/db.py` | 그대로 호출 |
| `send()` (TG 알림) | `bithumb/notify.py` | 진입/청산 알림 |
| 볼륨 화이트리스트 로직 | `alt_monitor.py` ~L900 | 20억+ 코인 필터 패턴 |
| PriceTracker WebSocket | `alt_monitor.py` | 실시간 가격 구독 구조 참고 |
| `watchdog.py` BOTS dict | `scripts/watchdog.py` L36-42 | `"vb_trader"` 항목 추가 |

---

## 제약

- config.yaml API 키 git 커밋 금지
- 매매 코드 변경 시 사용자 검토 후 커밋
- 기존 OVERSOLD 파라미터 동결(~2026-06-22) 미영향 — 완전 별개 전략
- `claude_screener_dry` watchdog 제거는 이 phase와 함께 처리

---

## 스코프 외 (미포함)

- 백테스트 검증 없이 실전 투입 (사용자 결정)
- 그리드 매매, BB스퀴즈 등 다른 전략 (별도 phase)
- 멀티 포지션 (1코인 1포지션)

---

## canonical_refs

- `scripts/alt_monitor.py` — 봇 구조, WS, 볼륨 필터 패턴
- `bithumb/client.py` — API 메서드 시그니처
- `bithumb/db.py` — log_trade() 시그니처
- `bithumb/notify.py` — TG 알림 패턴
- `scripts/watchdog.py` — BOTS dict, EXTRA_ARGS 패턴
- `config.yaml` (로컬) — trading 섹션에 `vb_trader.live` 추가 필요
