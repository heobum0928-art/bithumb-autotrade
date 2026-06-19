"""
Early-Momentum 트레이더 — 전략 #24 (재량규칙 규칙화, 약세장 전용, 모의 검증).

전략 (백테스트 #24: 약세장 2,657건 +0.75%/t3.00, 비용0.30% t2.42, 순열검정 p=0.000):
  1. 레짐 게이트: BTC 종가 < 200일 SMA (BEAR)일 때만 진입. BULL이면 전면 정지(#24 BULL t-5.63).
  2. 유니버스: 전체 KRW 상장(잡주 포함, 스테이블만 제외). #24는 잡주가 엣지(고거래량 음수).
  3. 진입(매일 1회, KST 일봉 확정 후): 직전 완성일의
       day_ret = close[어제]/close[그제]-1 ∈ [+3%, +12%]  AND  run5 = close[어제]/close[6일전]-1 ≤ +25%
       = '덜 익은 초기 모멘텀'(과펌핑/늦은추격 배제). 현재가로 진입(익일시가 근사, 룩어헤드 없음).
  4. 청산: 트레일 3%(고점 대비, +1.5% 도달 후 활성) / SL -7% / 타임아웃 15일.
  5. 5슬롯(같은 코인 1슬롯), 슬롯당 20만원.

⚠️ 이 전략은 검증된 게 아니라 LEAD다. red-team이 백테 강건성(단일월·생존편향·trail핏팅)을 깸.
   forward 모의로 '한 달 운인지 반복되는지'를 게이트(사전등록)로 판정하기 위한 dry-run.
   사전등록 게이트: CLEAN n≥50, 비용0.30%후 t≥3.0 & 평균>0, 강건성(최대수익1건·최대코인1종 제외해도>0, ≥3개월 분산).
   통과 → 사용자 승인 → 소액 실거래. 미달 → 폐기(파라미터 재시도 금지).

Run:
  python scripts/em_trader.py --dry-run   <- 모의 (기본/강제)
포지션: data/em_pos.json | 로그: logs/em_trader.log | DB 태그: [EM-DRY]
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
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KST = timezone(timedelta(hours=9))

# ── 중복 실행 방지 (전용 포트 47222 — vb=47220, rt=47221) ──────────────────────
_singleton_sock = None

def _ensure_single_instance() -> None:
    global _singleton_sock
    _singleton_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _singleton_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        _singleton_sock.bind(("127.0.0.1", 47222))
    except OSError:
        print("[ERROR] em_trader 이미 실행 중 (포트 47222). 종료.")
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
_DRY_RUN: bool = not _args.live
_LOG_TAG: str  = "EM-DRY" if _DRY_RUN else "EM"

# ── 로깅 ──────────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{_LOG_TAG}][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/em_trader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 전략 상수 (#24 백테스트 값 — 동결, 임의 변경 금지) ──────────────────────────
DAY_RET_LO     = 0.03      # 당일 상승 하한 (이하면 모멘텀 약함)
DAY_RET_HI     = 0.12      # 당일 상승 상한 (초과면 과펌핑/추격)
RUN5_CAP       = 0.25      # 직전 5일 누적 상한 (초과면 늦은 진입)
EM_TRAIL       = 0.03      # 트레일 3% (백테스트 trail3% t최고. 8%↑면 음수 — 동결)
EM_TRAIL_ACT   = 0.015     # 고점 +1.5% 도달 후 트레일 활성 (그 전 SL만)
EM_SL          = -0.07     # SL -7%
TIMEOUT_DAYS   = 15
EM_SLOTS       = 5
ENTRY_KRW      = 200_000
MIN_VOL_KRW    = 100_000_000   # 1억 (잡주 포함하되 완전 죽은 코인 제외)
UNIVERSE_CAP   = 120           # API 부하 상한
STABLECOIN_EXCLUDE = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD", "PYUSD", "USDS"}
SMA_DAYS       = 200
SCAN_SEC       = 5
WS_URL         = "wss://pubwss.bithumb.com/pub/ws"
POS_PATH       = Path("data/em_pos.json")

# ── 유니버스 ──────────────────────────────────────────────────────────────────
def build_universe(client: BithumbClient) -> list[str]:
    """전체 KRW 상장 중 거래대금 1억+ (잡주 포함, 스테이블 제외). #24는 잡주가 엣지."""
    try:
        tickers = client.get_ticker("ALL")
        rows = []
        for coin, d in tickers.items():
            if coin == "date" or coin in STABLECOIN_EXCLUDE:
                continue
            vol = float(d.get("acc_trade_value_24H", 0))
            if vol >= MIN_VOL_KRW:
                rows.append((coin, vol))
        rows.sort(key=lambda x: -x[1])
        wl = [c for c, _ in rows[:UNIVERSE_CAP]]
        log.info(f"[유니버스] {len(wl)}개 (거래대금 1억+, 잡주 포함, 스테이블 제외)")
        return wl
    except Exception as e:
        log.warning(f"[유니버스] 갱신 실패: {e}")
        return []

def _ws_symbols(universe: list[str], positions: list[dict]) -> list[str]:
    syms = [f"{c}_KRW" for c in universe]
    for pos in (positions or []):
        if pos and pos.get("coin"):
            ps = f"{pos['coin']}_KRW"
            if ps not in syms:
                syms.append(ps)
    return syms

# ── 레짐 (BTC 200일선) ────────────────────────────────────────────────────────
BTC_DAILY_FILE = Path("data/candles_daily/BTC_1d.json")

def is_bear(client: BithumbClient) -> bool | None:
    """BTC 종가 < 200일 SMA 면 BEAR(True). 실패 시 None(=진입 보류).
    유지 중인 일봉 파일 우선(API count 200 상한 회피), 없으면 API 폴백(count=200)."""
    try:
        if BTC_DAILY_FILE.exists():
            d = json.loads(BTC_DAILY_FILE.read_text(encoding="utf-8"))  # oldest-first
            closes = [float(x["trade_price"]) for x in d]
            if len(closes) >= SMA_DAYS + 1:
                cur = closes[-1]
                sma = sum(closes[-SMA_DAYS:]) / SMA_DAYS
                return cur < sma
        d = client.get_daily_candles("KRW-BTC", count=SMA_DAYS)  # newest-first, 200 상한
        if len(d) < SMA_DAYS:
            return None
        closes = [float(x["trade_price"]) for x in d]
        return closes[1] < sum(closes[1:SMA_DAYS]) / (SMA_DAYS - 1)
    except Exception as e:
        log.warning(f"[레짐] BTC 조회 실패: {e}")
        return None

# ── 진입 신호 (직전 완성일 기준) ──────────────────────────────────────────────
def entry_signal(client: BithumbClient, coin: str) -> bool:
    """어제(완성일) day_ret∈[3%,12%] AND run5≤25% 이면 True."""
    try:
        d = client.get_daily_candles(f"KRW-{coin}", count=8)  # newest-first
        if len(d) < 7:
            return False
        c = [float(x["trade_price"]) for x in d]  # [0]=오늘(진행중), [1]=어제 완성
        if c[2] <= 0 or c[6] <= 0:
            return False
        day_ret = c[1] / c[2] - 1
        run5 = c[1] / c[6] - 1
        return DAY_RET_LO <= day_ret <= DAY_RET_HI and run5 <= RUN5_CAP
    except Exception:
        return False

# ── 포지션 I/O (retest 패턴 재사용) ───────────────────────────────────────────
def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data else None
    except Exception:
        return None

def load_positions() -> list[dict]:
    data = load_json(POS_PATH)
    if not data:
        return []
    items = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = []
    for p in items:
        if not p or not p.get("coin"):
            continue
        p.setdefault("highest", p.get("entry_price"))
        if "timeout_at" not in p:
            try:
                base = datetime.fromisoformat(p["entered_at"])
                if base.tzinfo is None:
                    base = base.replace(tzinfo=KST)
            except Exception:
                base = datetime.now(KST)
            p["timeout_at"] = (base + timedelta(days=TIMEOUT_DAYS)).isoformat()
        out.append(p)
    return out

def save_json(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    if data is None or data == [] or data == {}:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    os.replace(tmp, path)

# ── 실시간 가격 (WS, 트레일 감시용) ───────────────────────────────────────────
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

        def runner():
            while self._ws_running:
                try:
                    ws = _wslib.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
                    self._ws = ws
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    log.error(f"[WS] 오류: {e}")
                if self._ws_running:
                    time.sleep(5)

        threading.Thread(target=runner, daemon=True).start()

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

# ── 청산 기록 ─────────────────────────────────────────────────────────────────
def record_exit(pos: dict, exit_price: float, reason: str) -> None:
    vol = pos["volume"]
    recv = exit_price * vol
    pnl_krw = recv - pos["cost_krw"]
    pnl_pct = pnl_krw / pos["cost_krw"] * 100
    log.warning(f"[{pos['coin']}] 청산 @{exit_price:,.4f} PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}")
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
    notify.send(f"[{_LOG_TAG}] {pos['coin']} 청산 @{exit_price:,.4f} PnL={pnl_pct:+.2f}% | {reason}")

# ── 일일 진입 평가 ────────────────────────────────────────────────────────────
def daily_entry_scan(client, tracker, universe, positions) -> None:
    """하루 1회: BEAR면 유니버스 스캔 → 신호 코인 진입(슬롯 여유·같은코인 1슬롯)."""
    bear = is_bear(client)
    if bear is None:
        log.info("[진입스캔] 레짐 판정 실패 — 보류")
        return
    if not bear:
        log.info("[진입스캔] BULL 레짐 — 진입 전면 정지 (#24는 BEAR 전용)")
        return
    held = {p["coin"] for p in positions}
    entered = 0
    for coin in universe:
        if len(positions) >= EM_SLOTS:
            break
        if coin in held:
            continue
        if not entry_signal(client, coin):
            time.sleep(0.08)
            continue
        cur = tracker.get(coin)
        if cur <= 0:
            try:
                cur = float(client.get_ticker(coin).get("closing_price", 0))
            except Exception:
                cur = 0
        if cur <= 0:
            continue
        volume = ENTRY_KRW / cur
        new_pos = {
            "coin": coin, "market": f"KRW-{coin}",
            "entry_price": cur, "volume": volume,
            "cost_krw": ENTRY_KRW, "highest": cur,
            "entered_at": datetime.now().isoformat(),
            "mock": _DRY_RUN,
            "timeout_at": (datetime.now(KST) + timedelta(days=TIMEOUT_DAYS)).isoformat(),
        }
        positions.append(new_pos)
        held.add(coin)
        save_json(POS_PATH, positions)
        entered += 1
        log.warning(f"[{coin}] 초기모멘텀 진입 @{cur:,.4f} 슬롯 {len(positions)}/{EM_SLOTS} → 모의")
        notify.send(f"[{_LOG_TAG}] {coin} 진입 @{cur:,.4f} (트레일3%/SL-7%/15일) [{len(positions)}/{EM_SLOTS}]")
        time.sleep(0.08)
    log.info(f"[진입스캔] 완료 — 신규 {entered}건, 보유 {len(positions)}/{EM_SLOTS}")

# ── 메인 루프 ─────────────────────────────────────────────────────────────────
def run() -> None:
    client  = BithumbClient()
    tracker = PriceTracker()
    universe = build_universe(client)
    positions = load_positions()
    uni_date = date.today()
    last_eval_date = None

    if universe:
        tracker.start_ws(_ws_symbols(universe, positions))
        time.sleep(5)

    held = ",".join(p["coin"] for p in positions) if positions else "없음"
    log.info(f"시작 | 모드={'DRY-RUN' if _DRY_RUN else 'LIVE'} | 유니버스 {len(universe)} "
             f"| 포지션 {len(positions)}/{EM_SLOTS} ({held})")
    notify.send(f"[{_LOG_TAG}] em_trader 시작 — #24 초기모멘텀(약세장 전용·모의), 유니버스 {len(universe)}")

    while True:
        try:
            # 유니버스 일일 갱신
            if date.today() != uni_date:
                uni_date = date.today()
                universe = build_universe(client)
                tracker.stop_ws()
                if universe:
                    tracker.start_ws(_ws_symbols(universe, positions))
                    time.sleep(5)

            # ① 하루 1회 진입 평가 (날짜 바뀌면 = 일봉 확정 후)
            if last_eval_date != date.today() and universe and len(positions) < EM_SLOTS:
                last_eval_date = date.today()
                daily_entry_scan(client, tracker, universe, positions)

            # ② 청산: 트레일/SL/타임아웃 (실시간 감시)
            for pos in positions[:]:
                coin = pos["coin"]
                cur = tracker.get(coin)
                if cur <= 0:
                    try:
                        cur = float(client.get_ticker(coin).get("closing_price", 0))
                    except Exception:
                        cur = 0
                if cur <= 0:
                    continue
                if cur > pos.get("highest", 0):
                    pos["highest"] = cur
                    save_json(POS_PATH, positions)
                entry = pos["entry_price"]
                high = pos.get("highest", entry)
                pnl = (cur - entry) / entry
                gain_high = (high - entry) / entry
                activated = gain_high >= EM_TRAIL_ACT
                sl_px = entry * (1 + EM_SL)
                trail_px = high * (1 - EM_TRAIL)
                timed_out = datetime.now(KST) > datetime.fromisoformat(pos["timeout_at"])
                if activated and cur <= max(sl_px, trail_px):
                    record_exit(pos, max(sl_px, trail_px), f"트레일3% (고점{gain_high*100:+.1f}%→{pnl*100:+.1f}%)")
                    positions.remove(pos); save_json(POS_PATH, positions)
                elif not activated and pnl <= EM_SL:
                    record_exit(pos, sl_px, f"SL-7% ({pnl*100:+.1f}%)")
                    positions.remove(pos); save_json(POS_PATH, positions)
                elif timed_out:
                    record_exit(pos, cur, f"타임아웃15일 ({pnl*100:+.1f}%)")
                    positions.remove(pos); save_json(POS_PATH, positions)

        except KeyboardInterrupt:
            log.info("종료 요청"); tracker.stop_ws(); break
        except Exception as e:
            log.error(f"루프 오류: {e}", exc_info=True)

        time.sleep(SCAN_SEC)


def main() -> None:
    if not _DRY_RUN:
        log.error("LIVE 모드는 사전등록 게이트(CLEAN n≥50, 비용0.30%후 t≥3.0, 강건성) 통과 + 사용자 승인 필요. 종료.")
        sys.exit(1)
    log.info(f"em_trader 시작 — #24 초기모멘텀 day∈[{DAY_RET_LO*100:.0f},{DAY_RET_HI*100:.0f}]% "
             f"run5≤{RUN5_CAP*100:.0f}% / 트레일3%(+1.5%발동) SL-7% / 15일 / BEAR전용 / 진입 {ENTRY_KRW:,}원")
    run()


if __name__ == "__main__":
    from bithumb.db import init_db
    init_db()
    main()
