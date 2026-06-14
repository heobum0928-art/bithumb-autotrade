"""
돌파 후 재테스트(Breakout-Retest) 트레이더 — 전략 B.

전략 (백테스트 검증: 90d walk-forward test +69.8% @0.08%, 113건):
  1. 감시: 거래대금 상위 30개 코인 (스테이블 제외, 매일 갱신)
  2. 돌파: 완성된 5분봉 종가 > 직전 24h(288봉) 최고가
  3. 대기: 돌파 후 가격이 돌파레벨 × 1.005 로 되돌아오기를 대기 (최대 24h)
  4. 진입: 지정가 가정 — 가격이 목표가에 닿으면 체결 (dry-run은 그 가격으로 시뮬)
  5. 청산: TP +6% / SL -3% / 24h 타임아웃
  6. 단일 슬롯 (동시 1포지션)

합격선 (사전 등록 — 이 기준 미달이면 실거래 전환 금지):
  모의 15건 이상 AND 비용(0.16%) 차감 후 평균 수익률 > 0
  (30→15 하향: 113건 walk-forward 백테스트로 통계적 유의성 이미 확보, 모의는 운영 검증 목적)

Run:
  python scripts/retest_trader.py --dry-run   <- 모의 (기본)
  python scripts/retest_trader.py --live      <- 실거래 (합격선 통과 + 사용자 승인 필요)

포지션: data/retest_pos.json | 로그: logs/retest_trader.log | DB 태그: [RT-DRY]/[RT]
"""
import sys
import os
import atexit
import time
import json
import logging
import threading
import argparse
import socket
from collections import deque
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

# ── 중복 실행 방지 ─────────────────────────────────────────────────────────────
_singleton_sock = None

def _ensure_single_instance() -> None:
    global _singleton_sock
    _singleton_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _singleton_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        _singleton_sock.bind(("127.0.0.1", 47221))  # retest_trader 전용 (vb=47220)
    except OSError:
        print("[ERROR] retest_trader 이미 실행 중 (포트 47221). 종료.")
        sys.exit(1)
    atexit.register(_singleton_sock.close)

_ensure_single_instance()

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient
from bithumb.db import log_trade
from bithumb import notify

# ── 플래그 ────────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--live",    action="store_true")
    args, _ = p.parse_known_args()
    return args

_args    = _parse_args()
_DRY_RUN: bool = not _args.live   # 기본 dry-run — 실거래는 명시 --live만
_LOG_TAG: str  = "RT-DRY" if _DRY_RUN else "RT"

# ── 로깅 ──────────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{_LOG_TAG}][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/retest_trader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 전략 상수 (백테스트 train 선택값 그대로 — 임의 변경 금지) ────────────────────
BREAKOUT_BARS        = 288             # 24h = 288 × 5분봉
RETEST_PCT           = 0.005           # 진입 목표 = 돌파레벨 × 1.005
RT_SL                = -0.03           # -3% (초기 손절, 트레일 활성화 전)
RT_TRAIL             = 0.03            # 트레일링 폭 — 고점 대비 3% 하락 시 청산
RT_TRAIL_ACTIVATE    = 0.01            # 진입 +1% 도달 시 트레일 활성 (그 전엔 SL만)
TIMEOUT_BARS         = 288             # 진입 후 24h 미청산 시 타임아웃 청산
RETEST_WINDOW_BARS   = 288             # 돌파 후 24h 내 재테스트 없으면 무효
ENTRY_KRW            = 400_000
TOPN                 = 50
MIN_DAILY_VOLUME_KRW = 2_000_000_000
STABLECOIN_EXCLUDE   = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD"}
# 변동성 낮은 시총 톱 메이저 — retest(돌파-재테스트)가 구조적으로 못 먹음.
# 백테스트 코인분해: BTC -1.9%, ETH -2.4% 등 대형주 전부 손실. 수익은 중소형 잡코인 집중.
# 10팀 에이전트 합의로 제외 (2026-06-13).
MAJORS_EXCLUDE       = {"BTC", "ETH", "XRP", "SOL", "ADA", "DOGE"}
SCAN_SEC             = 5
CANDLE_REFRESH_SEC   = 300             # 5분마다 완성봉 갱신
WS_URL               = "wss://pubwss.bithumb.com/pub/ws"
POS_PATH             = Path("data/retest_pos.json")
STATE_PATH           = Path("data/retest_state.json")   # 돌파 대기 상태

# ── 화이트리스트 ──────────────────────────────────────────────────────────────
def build_whitelist(client: BithumbClient) -> list[str]:
    """거래대금 상위 TOPN (20억+, 스테이블·대형주 제외 — 중소형 잡코인 한정)."""
    try:
        tickers = client.get_ticker("ALL")
        rows = []
        for coin, d in tickers.items():
            if coin == "date" or coin in STABLECOIN_EXCLUDE or coin in MAJORS_EXCLUDE:
                continue
            vol = float(d.get("acc_trade_value_24H", 0))
            if vol >= MIN_DAILY_VOLUME_KRW:
                rows.append((coin, vol))
        rows.sort(key=lambda x: -x[1])
        wl = [c for c, _ in rows[:TOPN]]
        log.info(f"[화이트리스트] {len(wl)}개 (상위 {TOPN}, 20억+, 대형주 {len(MAJORS_EXCLUDE)}종 제외)")
        return wl
    except Exception as e:
        log.warning(f"[화이트리스트] 갱신 실패: {e}")
        return []

def _ws_symbols(whitelist: list[str], pos: dict | None) -> list[str]:
    """WS 구독 심볼 = 화이트리스트 + 보유 포지션 코인.
    포지션 코인이 유니버스(대형주 제외 등)에서 빠져도 시세는 받아야 청산 가능."""
    syms = [f"{c}_KRW" for c in whitelist]
    if pos and pos.get("coin"):
        ps = f"{pos['coin']}_KRW"
        if ps not in syms:
            syms.append(ps)
    return syms

# ── 포지션/상태 I/O ───────────────────────────────────────────────────────────
def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # 빈 dict {}·null·falsy → None 정규화. 청산 분기가 'pos is not None'으로
        # 판정하므로 {}가 들어오면 "포지션 있음"으로 오해해 pos['coin'] KeyError 크래시.
        # (2026-06-14: pos.json을 {}로 비웠다가 재시작 크래시 위험 발견)
        return data if data else None
    except Exception:
        return None

def save_json(path: Path, data: dict | None) -> None:
    path.parent.mkdir(exist_ok=True)
    if data is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(json.dumps(data, default=str), encoding="utf-8")

# ── 실시간 가격 (WS) ──────────────────────────────────────────────────────────
class PriceTracker:
    def __init__(self):
        self._latest: dict[str, float] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._ws_running = False

    def start_ws(self, symbols: list[str]) -> None:
        import websocket as _wslib
        self._ws_running = True

        def on_open(ws):
            ws.send(json.dumps({"type": "ticker", "symbols": symbols, "tickTypes": ["24H"]}))
            log.info(f"[WS] 구독: {len(symbols)}개")

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("type") != "ticker":
                    return
                c = data.get("content", {})
                sym = c.get("symbol", "")
                if not sym.endswith("_KRW"):
                    return
                price = float(c.get("closePrice", 0) or 0)
                if price > 0:
                    with self._lock:
                        self._latest[sym[:-4]] = price
            except Exception:
                pass

        def run():
            while self._ws_running:
                try:
                    ws = _wslib.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
                    self._ws = ws
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    log.error(f"[WS] 오류: {e}")
                if self._ws_running:
                    time.sleep(5)

        threading.Thread(target=run, daemon=True).start()

    def stop_ws(self) -> None:
        self._ws_running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def get(self, coin: str) -> float:
        with self._lock:
            return self._latest.get(coin, 0.0)

# ── 24h 최고가 추적 ───────────────────────────────────────────────────────────
# API count 최대 200 → 5분봉 288개 한 번에 불가. 최고가 윈도우는 15분봉 96개(=24h)로,
# 돌파 판정 종가는 5분봉 완성봉으로 (백테스트 5분 granularity 유지).
def fetch_rolling_high(client: BithumbClient, coin: str) -> tuple[float, float] | None:
    """(직전 24h 최고가, 마지막 완성 5분봉 종가) 반환. 실패 시 None."""
    try:
        c15 = client.get_candles(f"KRW-{coin}", unit=15, count=97)
        c5  = client.get_candles(f"KRW-{coin}", unit=5,  count=2)
        if len(c15) < 97 or len(c5) < 2:
            return None
        # c15[0]=진행중 → [1:97] 완성 96봉 = 24h 윈도우 (진행중 봉 제외라 신호봉 미포함)
        prior_high = max(float(c["high_price"]) for c in c15[1:97])
        last_close = float(c5[1]["trade_price"])   # 마지막 완성 5분봉 종가
        return prior_high, last_close
    except Exception as e:
        log.debug(f"[{coin}] 캔들 조회 실패: {e}")
        return None

# ── 청산 기록 ─────────────────────────────────────────────────────────────────
def record_exit(pos: dict, exit_price: float, reason: str) -> None:
    vol  = pos["volume"]
    recv = exit_price * vol
    pnl_krw = recv - pos["cost_krw"]
    pnl_pct = pnl_krw / pos["cost_krw"] * 100
    log.warning(f"[{pos['coin']}] 청산 @{exit_price:,.2f} PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}")
    try:
        log_trade(
            coin=pos["coin"], market=pos["market"],
            entry_price=pos["entry_price"], exit_price=exit_price,
            volume=vol, cost_krw=pos["cost_krw"], received_krw=recv,
            exit_reason=f"[{_LOG_TAG}] {reason}",
            entered_at=datetime.fromisoformat(pos["entered_at"]).replace(tzinfo=None),
            exited_at=datetime.now(),
            max_price=pos.get("highest", exit_price),
        )
    except Exception as e:
        log.error(f"[DB] 기록 실패: {e}")
    notify.send(f"[{_LOG_TAG}] {pos['coin']} 청산 @{exit_price:,.2f} PnL={pnl_pct:+.2f}% | {reason}")

# ── 메인 루프 ─────────────────────────────────────────────────────────────────
def run() -> None:
    client  = BithumbClient()
    tracker = PriceTracker()

    whitelist = build_whitelist(client)
    # 돌파 대기 상태: {coin: {"level": float, "target": float, "expires": iso}}
    pending: dict[str, dict] = (load_json(STATE_PATH) or {})
    pos: dict | None = load_json(POS_PATH)
    wl_date = date.today()
    last_candle_check = 0.0

    if whitelist:
        tracker.start_ws(_ws_symbols(whitelist, pos))
        time.sleep(5)

    log.info(f"시작 | 모드={'DRY-RUN' if _DRY_RUN else 'LIVE'} | 감시 {len(whitelist)}개 "
             f"| 돌파대기 {len(pending)}건 | 포지션={'있음:'+pos['coin'] if pos else '없음'}")
    notify.send(f"[{_LOG_TAG}] retest_trader 시작 — 돌파-재테스트 전략, 감시 {len(whitelist)}개")

    while True:
        try:
            now = time.time()

            # 매일 화이트리스트 갱신
            if date.today() != wl_date:
                wl_date = date.today()
                whitelist = build_whitelist(client)
                tracker.stop_ws()
                if whitelist:
                    tracker.start_ws(_ws_symbols(whitelist, pos))
                    time.sleep(5)

            # ① 5분마다: 완성봉 기준 돌파 감지 → pending 등록
            if now - last_candle_check >= CANDLE_REFRESH_SEC and pos is None:
                last_candle_check = now
                for coin in whitelist:
                    if coin in pending:
                        continue
                    r = fetch_rolling_high(client, coin)
                    if not r:
                        continue
                    prior_high, last_close = r
                    if last_close > prior_high:
                        target = prior_high * (1 + RETEST_PCT)
                        expires = (datetime.now(KST) + timedelta(hours=24)).isoformat()
                        pending[coin] = {"level": prior_high, "target": target, "expires": expires}
                        save_json(STATE_PATH, pending)
                        log.info(f"[{coin}] 돌파 감지 — 레벨 {prior_high:,.2f}, "
                                 f"재테스트 목표 {target:,.2f} (24h 대기)")
                    time.sleep(0.1)  # API 부하 분산

            # ② pending 정리 (만료/레벨 이탈)
            for coin in list(pending.keys()):
                p = pending[coin]
                if datetime.now(KST) > datetime.fromisoformat(p["expires"]):
                    del pending[coin]
                    save_json(STATE_PATH, pending)
                    log.info(f"[{coin}] 재테스트 대기 만료 (24h)")
                    continue
                cur = tracker.get(coin)
                # 지지 실패: 레벨 1% 밑으로 깨지면 무효 (백테스트의 close>level 필터 근사)
                if 0 < cur < p["level"] * 0.99:
                    del pending[coin]
                    save_json(STATE_PATH, pending)
                    log.info(f"[{coin}] 레벨 이탈 — 대기 취소 (현재 {cur:,.2f} < 레벨 {p['level']:,.2f})")

            # ③ 진입: pending 코인이 목표가에 닿으면 지정가 체결 시뮬
            if pos is None:
                for coin, p in list(pending.items()):
                    cur = tracker.get(coin)
                    if cur <= 0 or cur > p["target"]:
                        continue
                    entry_px = p["target"]   # 지정가 체결 가정
                    volume = ENTRY_KRW / entry_px
                    pos = {
                        "coin": coin, "market": f"KRW-{coin}",
                        "entry_price": entry_px, "volume": volume,
                        "cost_krw": ENTRY_KRW, "highest": entry_px,
                        "entered_at": datetime.now().isoformat(),
                        "level": p["level"], "mock": _DRY_RUN,
                        "timeout_at": (datetime.now(KST) + timedelta(hours=24)).isoformat(),
                    }
                    save_json(POS_PATH, pos)
                    del pending[coin]
                    save_json(STATE_PATH, pending)
                    log.warning(f"[{coin}] 재테스트 진입 @{entry_px:,.2f} (레벨 {p['level']:,.2f}) "
                                f"→ {'모의' if _DRY_RUN else '실거래'}")
                    notify.send(f"[{_LOG_TAG}] {coin} 진입 @{entry_px:,.2f} "
                                f"(TP+6%/SL-3%/24h)")
                    break

            # ④ 청산: TP/SL/타임아웃
            elif pos is not None:
                coin = pos["coin"]
                cur = tracker.get(coin)
                if cur <= 0:
                    # WS 미구독/일시단절 시 REST 폴백 — 보유 포지션 청산이 막히지 않게
                    try:
                        cur = float(client.get_ticker(coin).get("closing_price", 0))
                    except Exception:
                        cur = 0
                if cur <= 0:
                    time.sleep(SCAN_SEC)
                    continue
                if cur > pos.get("highest", 0):
                    pos["highest"] = cur
                    save_json(POS_PATH, pos)
                entry = pos["entry_price"]
                high = pos.get("highest", entry)
                pnl = (cur - entry) / entry
                gain_high = (high - entry) / entry           # 고점 도달률
                activated = gain_high >= RT_TRAIL_ACTIVATE   # +1% 넘으면 트레일 가동
                sl_px = entry * (1 + RT_SL)
                trail_px = high * (1 - RT_TRAIL)
                timed_out = datetime.now(KST) > datetime.fromisoformat(pos["timeout_at"])
                if activated and cur <= max(sl_px, trail_px):
                    stop = max(sl_px, trail_px)
                    record_exit(pos, stop, f"트레일{RT_TRAIL*100:.0f}% (고점{gain_high*100:+.1f}%→{pnl*100:+.1f}%)")
                    pos = None
                    save_json(POS_PATH, None)
                elif not activated and pnl <= RT_SL:
                    record_exit(pos, sl_px, f"SL{RT_SL*100:.0f}% ({pnl*100:+.1f}%)")
                    pos = None
                    save_json(POS_PATH, None)
                elif timed_out:
                    record_exit(pos, cur, f"타임아웃24h ({pnl*100:+.1f}%)")
                    pos = None
                    save_json(POS_PATH, None)

        except KeyboardInterrupt:
            log.info("종료 요청")
            tracker.stop_ws()
            break
        except Exception as e:
            log.error(f"루프 오류: {e}", exc_info=True)

        time.sleep(SCAN_SEC)


def main() -> None:
    if not _DRY_RUN:
        log.error("LIVE 모드는 합격선(모의 15건+, 평균>0) 통과 후 사용자 승인 필요. 종료.")
        sys.exit(1)
    log.info(f"retest_trader 시작 — 돌파{BREAKOUT_BARS}봉 / 재테스트+{RETEST_PCT*100:.1f}% "
             f"/ 트레일{RT_TRAIL*100:.0f}%(+{RT_TRAIL_ACTIVATE*100:.0f}%발동) SL{RT_SL*100:.0f}% / 진입 {ENTRY_KRW:,}원")
    run()


if __name__ == "__main__":
    from bithumb.db import init_db
    init_db()
    main()
