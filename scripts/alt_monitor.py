"""
Altcoin Momentum Monitor  (Strategy D - Aggressive Real-time)

Non-blocking single-loop architecture:
  - 포지션 모니터링 + 신호 탐색을 같은 루프에서 처리
  - monitor_trailing() 블로킹 제거 → 봇이 항상 살아있는 로그 출력

Run: python scripts/alt_monitor.py
"""
import sys
import os
import atexit
import time
import json
import logging
import threading
import yaml
from collections import deque
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient
from bithumb.db import init_db, log_trade, log_signal, DB_PATH
from bithumb.indicators import snapshot as indicator_snapshot
from bithumb import notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ALT][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/alt_monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 전략 파라미터 ──────────────────────────────────────────────────────────────
WS_URL               = "wss://pubwss.bithumb.com/pub/ws"
WS_MIN_INTERVAL      = 1.0
SCAN_SEC             = 1
WINDOW_SEC           = 60
PRICE_THRESH         = 0.03
VOLUME_MULT          = 5.0   # 7→5, RSI/MACD 복합 조건으로 보완
ALT_ENTRY_RATIO      = 0.15
TP_HALF              = 0.02   # 빠른 익절 +2%
TRAIL_PCT            = 0.02   # 트레일 2%
TIGHT_TRAIL          = 0.015  # 2차 트레일 1.5%
HOLD_MIN_SEC         = 600    # 진입 후 최소 10분 보유
INITIAL_STOP_PCT     = -0.03  # 초기 보유 중 급락 손절 -3% (펌프덤프 대응)
DAILY_LIMIT_PCT      = -0.05
MIN_KRW              = 5001
COOLDOWN_SEC         = 120
MIN_COIN_PRICE       = 10
MAX_DAILY_TRADES     = 30
MIN_TRADE_KRW_PER_MIN = 1_000_000
BTC_DROP_LIMIT       = -0.015
OB_BID_RATIO         = 1.5
TICK_BUY_RATIO       = 0.60
VOLUME_POWER_MIN     = 100.0

# 선진입(거래량 선행) 파라미터
PRE_ENABLED          = False  # 데이터 기반 비활성화 (14% 승률, 손실 지속)
PRE_VOL_MULT         = 12.0  # 최근 10초 거래량이 이전 대비 12배 (8→12, 가짜신호 감소)
PRE_PRICE_MAX        = 0.01  # 가격 변화 아직 +1% 미만 (안 오른 상태)
PRE_TIMEOUT_SEC      = 600   # 10분 안에 목표 미달 시 청산 (5→10, 데이터 기반 조정)
PRE_MIN_MOVE         = 0.01  # 타임아웃 판단 기준 최소 상승 +1%
PRE_HARD_STOP        = -0.03 # 선진입 급락 손절 -3%

# 신규 상장 파라미터
NEW_LIST_TP_PCT   = 0.20   # 익절 +20%
NEW_LIST_TRAIL    = 0.07   # 트레일 7%
NEW_LIST_STOP     = -0.05  # 손절 -5%
NEW_LIST_HOLD_MIN = 120    # 최소 보유 2분
NEW_LIST_SCAN_SEC = 10     # 신규 코인 체크 주기 (초)
NEW_LIST_ENTRY_KRW = 300_000  # 신규 상장 전용 진입금액 (일반 15만원과 별도)

# 코인별 누적 손실 학습 (횟수별 차단 시간)
LOSS_CD = {1: 4*3600, 2: 24*3600, 3: 72*3600}  # 1회=4h, 2회=24h, 3회+=72h
LOSS_CD_BIG    = 48 * 3600  # -5% 이상 큰 손실
LOSS_CD_PUMP   = 72 * 3600  # 펌프덤프 패턴
STRICT_WINDOW        = 5
STRICT_LOSS_CNT      = 3
STRICT_MULT          = 1.3

SKIP_COINS = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}

STATE_FILE      = Path("data/active_pos.json")
LOCK_FILE       = Path("data/bot.lock")
LOSS_COINS_FILE = Path("data/loss_coins.json")


# ── 프로세스 중복 방지 ─────────────────────────────────────────────────────────

def acquire_lock() -> None:
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            try:
                os.kill(pid, 0)
                log.error(f"[LOCK] 봇이 이미 실행 중입니다 (PID {pid}). 종료합니다.")
                sys.exit(1)
            except OSError:
                pass  # 해당 PID 없음 → 오래된 락 파일
        except ValueError:
            pass
    LOCK_FILE.parent.mkdir(exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: LOCK_FILE.unlink(missing_ok=True))
    log.info(f"[LOCK] 획득 완료 (PID {os.getpid()})")


# ── 포지션 영속성 ──────────────────────────────────────────────────────────────

def save_active(pos: dict, highest: float, phase: int,
                sold_vol: float, recv_krw: float, trail: float) -> None:
    data = {**pos, "highest": highest, "phase": phase,
            "sold_vol": sold_vol, "recv_krw": recv_krw, "trail": trail}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, default=str), encoding="utf-8")


def load_active() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_active() -> None:
    STATE_FILE.unlink(missing_ok=True)


def save_loss_coins(loss_coins: dict) -> None:
    LOSS_COINS_FILE.parent.mkdir(exist_ok=True)
    LOSS_COINS_FILE.write_text(json.dumps(loss_coins), encoding="utf-8")


def load_loss_coins() -> dict:
    if not LOSS_COINS_FILE.exists():
        return {}
    try:
        data = json.loads(LOSS_COINS_FILE.read_text(encoding="utf-8"))
        # 구 포맷 {coin: float} → 신 포맷 {coin: {count, until}} 마이그레이션
        migrated = {}
        for coin, val in data.items():
            if isinstance(val, (int, float)):
                migrated[coin] = {"count": 1, "until": val + LOSS_CD[1]}
            else:
                migrated[coin] = val
        return migrated
    except Exception:
        return {}


def record_loss_coin(loss_coins: dict, coin: str,
                     pnl_pct: float, exit_reason: str) -> dict:
    """누적 손실 횟수에 따라 차단 기간을 점진적으로 늘린다."""
    prev = loss_coins.get(coin, {})
    count = prev.get("count", 0) + 1

    # 패턴별 최소 차단 시간 결정
    is_pump = ("초기손절" in exit_reason and pnl_pct < -0.03)
    is_big  = (pnl_pct <= -0.05)

    base_cd = LOSS_CD.get(count, LOSS_CD[3])
    if is_pump:
        cd = max(base_cd, LOSS_CD_PUMP)
        label = "펌프덤프"
    elif is_big:
        cd = max(base_cd, LOSS_CD_BIG)
        label = "대형손실"
    else:
        cd = base_cd
        label = f"{count}회손실"

    loss_coins[coin] = {"count": count, "until": time.time() + cd}
    log.info(f"[학습] {coin} {label} → {cd//3600}시간 재진입 차단 (총 {count}회)")
    return loss_coins


# ── 실시간 가격 추적기 ─────────────────────────────────────────────────────────

class PriceTracker:
    MAXLEN = 60

    def __init__(self):
        self._hist: dict[str, deque] = {}
        self._vol_power: dict[str, float] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._ws_running = False

    def start_ws(self, symbols: list[str]) -> None:
        import websocket as _wslib
        self._ws_running = True

        def on_open(ws):
            sub = {"type": "ticker", "symbols": symbols, "tickTypes": ["24H"]}
            ws.send(json.dumps(sub))
            log.info(f"[WS] 구독 완료: {len(symbols)}개 코인")

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("type") != "ticker":
                    return
                c       = data.get("content", {})
                symbol  = c.get("symbol", "")
                if not symbol.endswith("_KRW"):
                    return
                coin    = symbol[:-4]
                price   = float(c.get("closePrice", 0) or 0)
                acc_val = float(c.get("value",      0) or 0)
                vp      = float(c.get("volumePower", 100) or 100)
                if price <= 0:
                    return
                now = time.time()
                with self._lock:
                    if coin not in self._hist:
                        self._hist[coin] = deque(maxlen=self.MAXLEN)
                    hist = self._hist[coin]
                    if not hist or now - hist[-1][0] >= WS_MIN_INTERVAL:
                        hist.append((now, price, acc_val))
                    self._vol_power[coin] = vp
            except Exception as e:
                log.debug(f"[WS] 파싱 오류: {e}")

        def on_error(ws, error):
            log.warning(f"[WS] 에러: {error}")

        def on_close(ws, code, msg):
            log.warning(f"[WS] 연결 종료: {code}")

        def run():
            while self._ws_running:
                try:
                    ws = _wslib.WebSocketApp(
                        WS_URL,
                        on_open=on_open, on_message=on_message,
                        on_error=on_error, on_close=on_close,
                    )
                    self._ws = ws
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    log.error(f"[WS] 실행 오류: {e}")
                if self._ws_running:
                    log.info("[WS] 5초 후 재연결...")
                    time.sleep(5)

        threading.Thread(target=run, daemon=True).start()

    def stop_ws(self) -> None:
        self._ws_running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def get_vol_power(self, coin: str) -> float:
        with self._lock:
            return self._vol_power.get(coin, 100.0)

    def get_signal(self, coin: str) -> dict | None:
        with self._lock:
            hist = self._hist.get(coin)
            if not hist or len(hist) < 4:
                return None
            snaps = list(hist)
            now_ts, now_price, now_vol = snaps[-1]

            target_ts = now_ts - WINDOW_SEC
            old_snap  = snaps[0]
            for s in snaps:
                if s[0] <= target_ts:
                    old_snap = s

            old_ts, old_price, old_vol = old_snap
            elapsed = now_ts - old_ts
            if elapsed < 20 or old_price <= 0:
                return None
            if now_price < MIN_COIN_PRICE:
                return None

            price_chg = (now_price - old_price) / old_price
            if price_chg < PRICE_THRESH:
                return None

            mid    = len(snaps) // 2
            recent = snaps[mid:]
            older  = snaps[:mid]
            if len(recent) < 2 or len(older) < 2:
                return None

            r_vol  = recent[-1][2] - recent[0][2]
            r_time = max(recent[-1][0] - recent[0][0], 1)
            o_vol  = older[-1][2]  - older[0][2]
            o_time = max(older[-1][0]  - older[0][0], 1)
            if o_vol <= 0:
                return None

            r_rate   = r_vol / r_time
            o_rate   = o_vol / o_time

            # 거래량 지속성: 이전 구간도 최소 활성화 확인 (완전 침묵 후 단발 스파이크 차단)
            if o_rate * 60 < MIN_TRADE_KRW_PER_MIN * 0.3:
                return None

            vol_mult = r_rate / o_rate if o_rate > 0 else 0
            if vol_mult < VOLUME_MULT:
                return None

            if r_rate * 60 < MIN_TRADE_KRW_PER_MIN:
                return None

            recent_prices = [s[1] for s in snaps[-4:]]
            up_count = sum(1 for i in range(1, len(recent_prices))
                           if recent_prices[i] > recent_prices[i - 1])
            if up_count < 2:
                return None

            return {"coin": coin, "price_chg": price_chg,
                    "vol_mult": vol_mult, "price": now_price, "elapsed": elapsed}

    def get_preemptive_signal(self, coin: str) -> dict | None:
        """
        거래량 선행 신호: 가격은 아직 +1% 미만인데 거래량이 8배 이상 폭발.
        가격 급등 30~60초 전 선진입 포착.
        """
        with self._lock:
            hist = self._hist.get(coin)
            if not hist or len(hist) < 10:
                return None
            snaps = list(hist)
            now_ts, now_price, _ = snaps[-1]

            if now_price < MIN_COIN_PRICE:
                return None

            # 최근 5개(약 10초) vs 이전 스냅샷
            recent = snaps[-5:]
            older  = snaps[:-5]
            if len(older) < 5:
                return None

            # 가격 변화 — 아직 안 올랐어야 함
            old_price = older[-1][1]
            if old_price <= 0:
                return None
            price_chg = (now_price - old_price) / old_price
            if abs(price_chg) >= PRE_PRICE_MAX:
                return None  # 이미 움직였거나 하락 중

            # 거래량 비율
            r_vol  = recent[-1][2] - recent[0][2]
            r_time = max(recent[-1][0] - recent[0][0], 1)
            o_vol  = older[-1][2]  - older[0][2]
            o_time = max(older[-1][0]  - older[0][0], 1)
            if o_vol <= 0:
                return None

            r_rate   = r_vol / r_time
            o_rate   = o_vol / o_time
            vol_mult = r_rate / o_rate if o_rate > 0 else 0
            if vol_mult < PRE_VOL_MULT:
                return None

            # 거래대금 절댓값 (선진입은 2배 기준)
            if r_rate * 60 < MIN_TRADE_KRW_PER_MIN * 2:
                return None

            return {"coin": coin, "price_chg": price_chg,
                    "vol_mult": vol_mult, "price": now_price,
                    "type": "preemptive"}

    def get_latest_price(self, coin: str) -> float:
        with self._lock:
            hist = self._hist.get(coin)
            if hist:
                return hist[-1][1]
            return 0.0

    def coins(self) -> list[str]:
        with self._lock:
            return list(self._hist.keys())


# ── 유틸리티 ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


def get_available_krw(client: BithumbClient) -> float:
    for a in client.get_accounts():
        if a["currency"] == "KRW":
            return float(a["balance"])
    return 0.0


def get_holdings(client: BithumbClient) -> set[str]:
    held = set()
    try:
        for a in client.get_accounts():
            cur = a["currency"]
            if cur in ("KRW", "P") or cur in SKIP_COINS:
                continue
            bal = float(a.get("balance", 0))
            if bal <= 0:
                continue
            try:
                price = float(client.get_ticker(cur)["closing_price"])
                if bal * price >= 1000:
                    held.add(cur)
            except Exception:
                if bal > 0.0001:
                    held.add(cur)
    except Exception:
        pass
    return held


def is_btc_bullish(client: BithumbClient) -> bool:
    try:
        candles = client.get_candles("KRW-BTC", unit=60, count=2)
        if len(candles) < 2:
            return True
        chg = (candles[0]["trade_price"] - candles[1]["trade_price"]) / candles[1]["trade_price"]
        if chg <= BTC_DROP_LIMIT:
            log.info(f"[BTC 필터] 1시간 낙폭 {chg*100:.1f}% - 진입 차단")
            return False
    except Exception:
        pass
    return True


def get_price(client: BithumbClient, coin: str) -> float:
    try:
        return float(client.get_ticker(coin)["closing_price"])
    except Exception:
        return 0.0


def send_daily_report(target_date: date) -> None:
    import sqlite3
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades WHERE date = ? ORDER BY exited_at",
            (target_date.isoformat(),)
        ).fetchall()
        con.close()

        ds = target_date.strftime("%Y-%m-%d")
        if not rows:
            notify.send(f"<b>[일일 리포트 {ds}]</b>\n거래 없음", force=True)
            return

        total_pnl = sum(r["pnl_krw"] for r in rows)
        wins      = sum(1 for r in rows if r["pnl_krw"] > 0)
        total     = len(rows)
        lines     = [f"<b>[일일 리포트 {ds}]</b>",
                     f"거래: {total}건 | 승률: {wins/total*100:.0f}% ({wins}승 {total-wins}패)"]
        for r in rows:
            sign = "+" if r["pnl_krw"] >= 0 else ""
            lines.append(f"  {r['coin']}: {sign}{r['pnl_krw']:,.0f}원 ({r['pnl_pct']:+.1f}%)")
        lines.append(f"\n<b>총 PnL: {total_pnl:+,.0f}원</b>")
        notify.send("\n".join(lines), force=True)
        log.info(f"[리포트] {ds} 전송 완료 | {total}건 | {total_pnl:+,.0f}원")
    except Exception as e:
        log.error(f"[리포트] 전송 실패: {e}")


def check_orderbook(client: BithumbClient, coin: str) -> bool:
    try:
        ob        = client.get_orderbook(coin)
        bids      = ob.get("bids", [])[:5]
        asks      = ob.get("asks", [])[:5]
        if not bids or not asks:
            return True
        bid_total = sum(float(b["quantity"]) * float(b["price"]) for b in bids)
        ask_total = sum(float(a["quantity"]) * float(a["price"]) for a in asks)
        if ask_total <= 0:
            return True
        ratio = bid_total / ask_total
        log.info(f"[{coin}] 호가불균형 매수/매도={ratio:.2f} (기준 {OB_BID_RATIO})")
        return ratio >= OB_BID_RATIO
    except Exception as e:
        log.debug(f"[{coin}] 호가 조회 실패: {e}")
        return True


def check_tick_ratio(client: BithumbClient, coin: str,
                     tracker: "PriceTracker" = None) -> bool:
    if tracker is not None:
        vp = tracker.get_vol_power(coin)
        log.info(f"[{coin}] 체결강도(WS) volumePower={vp:.0f} (기준 {VOLUME_POWER_MIN:.0f})")
        return vp >= VOLUME_POWER_MIN
    try:
        txs  = client.get_transaction_history(coin, count=20)
        if not txs:
            return True
        buys = sum(1 for t in txs if t.get("type") == "bid")
        ratio = buys / len(txs)
        log.info(f"[{coin}] 체결강도(REST) 매수비율={ratio*100:.0f}%")
        return ratio >= TICK_BUY_RATIO
    except Exception as e:
        log.debug(f"[{coin}] 체결강도 조회 실패: {e}")
        return True


def report_loss(coin: str, pnl_krw: float, pnl_pct: float,
                hold_sec: int, exit_reason: str, entry_type: str) -> None:
    """손실 거래 발생 시 원인 분석 후 텔레그램 보고."""
    hold_min = hold_sec / 60

    if hold_min < 5 and pnl_pct < -0.03:
        pattern = "펌프덤프 의심 — 진입 직후 급락"
    elif hold_min < 10 and "초기손절" in exit_reason:
        pattern = "초기 급락 손절 — 모멘텀 소멸 빠름"
    elif "타임아웃" in exit_reason:
        pattern = "선진입 타임아웃 — 거래량 신호 후 가격 미추종"
    elif "트레일링" in exit_reason and pnl_pct < 0:
        pattern = "트레일링 손절 — 고점 후 되돌림"
    else:
        pattern = "기타 손실"

    msg = (
        f"<b>[손실 분석] {coin}</b>\n"
        f"결과: {pnl_krw:+,.0f}원 ({pnl_pct*100:+.1f}%)\n"
        f"보유: {hold_min:.0f}분 | 진입: {entry_type}\n"
        f"사유: {exit_reason}\n"
        f"패턴: <b>{pattern}</b>"
    )
    try:
        notify.send(msg, force=True)
    except Exception as e:
        log.error(f"[손실보고] 전송 실패: {e}")


# ── 주문 ──────────────────────────────────────────────────────────────────────

def wait_for_order(client: BithumbClient, uuid: str, timeout: int = 20) -> dict:
    for _ in range(timeout):
        time.sleep(1)
        try:
            order = client.get_order(uuid)
            if order.get("state") == "done":
                return order
        except Exception:
            pass
    return {}


def do_buy(client: BithumbClient, coin: str, buy_krw: float) -> dict | None:
    market = f"KRW-{coin}"
    log.info(f"[{coin}] 시장가 매수 {buy_krw:,.0f}원")
    try:
        r     = client.market_buy(market, buy_krw)
        uuid  = r.get("uuid")
        if not uuid:
            log.error(f"[{coin}] UUID 없음")
            return None
        order = wait_for_order(client, uuid)
        if order.get("state") != "done":
            log.warning(f"[{coin}] 매수 미체결 - 취소")
            try:
                client.cancel_order(uuid)
            except Exception:
                pass
            return None
        vol   = float(order.get("executed_volume", 0))
        funds = float(order.get("executed_funds",  0))
        fee   = float(order.get("paid_fee",        0))
        if vol <= 0:
            return None
        entry = funds / vol
        log.info(f"[{coin}] 매수 체결 | 수량={vol:.8f} 단가={entry:,.3f}원")
        notify.notify_buy(coin, entry, vol, funds + fee)
        return {"coin": coin, "market": market, "volume": vol,
                "entry_price": entry, "cost": funds + fee,
                "entered_at": datetime.now()}
    except Exception as e:
        log.error(f"[{coin}] 매수 실패: {e}")
        return None


def get_coin_balance(client: BithumbClient, coin: str) -> float:
    try:
        for a in client.get_accounts():
            if a["currency"] == coin.upper():
                return float(a["balance"])
    except Exception:
        pass
    return 0.0


def do_sell(client: BithumbClient, pos: dict, volume: float,
            reason: str) -> float | None:
    """
    매도 체결 후 수령액(원) 반환.
    잔고가 이미 0이면 None 반환 (수동 매도 등 외부 청산).
    미체결 시 무한 재시도.
    """
    coin = pos["coin"]
    actual_bal = get_coin_balance(client, coin)
    if actual_bal <= 0:
        log.info(f"[{coin}] 잔고 없음 - 외부 청산으로 처리")
        return None

    volume = min(volume, actual_bal)
    log.info(f"[{coin}] {reason} - 매도 {volume:.8f} (잔고={actual_bal:.8f})")

    attempt = 0
    while True:
        attempt += 1
        last_uuid = None
        try:
            r         = client.market_sell(pos["market"], volume)
            last_uuid = r.get("uuid")
            order     = wait_for_order(client, last_uuid)
            if order.get("state") == "done":
                received = (float(order.get("executed_funds", 0))
                            - float(order.get("paid_fee", 0)))
                log.info(f"[{coin}] 매도 체결 | 수령={received:,.0f}원 ({attempt}회차)")
                return received
            log.warning(f"[{coin}] 매도 미체결 {attempt}회차 - 취소 후 재시도")
            try:
                client.cancel_order(last_uuid)
            except Exception:
                pass
        except Exception as e:
            log.warning(f"[{coin}] 매도 오류 {attempt}회차: {e}")

        if attempt % 5 == 0:
            notify.notify_error(f"{coin} 매도 {attempt}회 실패 중, 자동 재시도...")

        bal = get_coin_balance(client, coin)
        if bal <= 0:
            log.info(f"[{coin}] 잔고 0 확인 - 외부 청산으로 처리")
            return None
        volume = min(volume, bal)
        time.sleep(10)


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run():
    acquire_lock()

    cfg         = load_config()
    capital     = cfg["trading"]["capital_krw"]
    daily_limit = cfg["trading"].get("daily_loss_limit_pct", DAILY_LIMIT_PCT)

    init_db()
    client  = BithumbClient()
    tracker = PriceTracker()

    all_coins = client.get_all_coins_v2()
    symbols   = [f"{c}_KRW" for c in all_coins]
    tracker.start_ws(symbols)
    known_coins     = set(all_coins)
    last_newlist_ts = 0.0
    log.info(f"[WS] {len(symbols)}개 코인 실시간 수신 시작 - 초기 데이터 수집 중...")
    time.sleep(5)

    log.info("=== ALT 모멘텀 봇 시작 [논블로킹 단일루프] ===")
    log.info(
        f"스캔={SCAN_SEC}s | 윈도우={WINDOW_SEC}s | "
        f"가격임계={PRICE_THRESH*100:.0f}% | 거래량배수={VOLUME_MULT:.0f}x | "
        f"진입={ALT_ENTRY_RATIO*100:.0f}% | TP={TP_HALF*100:.0f}% | 트레일={TRAIL_PCT*100:.1f}%"
    )

    # 통계
    daily_pnl        = 0.0
    daily_trades     = 0
    today            = date.today()
    cooldown_end     = 0.0
    scan_count       = 0
    last_report_date = None
    loss_coins: dict[str, float] = {}
    recent_pnls      = deque(maxlen=STRICT_WINDOW)
    strict_mode      = False

    # 활성 포지션 상태 (None이면 포지션 없음)
    pos      = None
    highest  = 0.0
    phase    = 1
    sold_vol = 0.0
    recv_krw = 0.0
    trail    = TRAIL_PCT

    # 손실 코인 쿨다운 복구 (재시작해도 유지)
    loss_coins = load_loss_coins()
    log.info(f"[복구] 손실 쿨다운 코인: {list(loss_coins.keys()) or '없음'}")

    # 재시작 시 저장된 포지션 복구
    saved = load_active()
    if saved:
        saved_coin = saved["coin"]
        saved_bal  = get_coin_balance(client, saved_coin)
        saved_remaining = float(saved.get("volume", 0)) - float(saved.get("sold_vol", 0))

        if saved_bal < saved_remaining * 0.01:
            # 잔고 없음 → 재시작 전에 외부청산된 것
            log.warning(f"[복구] {saved_coin} 잔고 없음 - 외부청산으로 처리")
            ext_recv = float(saved.get("recv_krw", 0))
            ext_cost = float(saved.get("cost", 0))
            ext_pnl  = ext_recv - ext_cost
            ext_pct  = ext_pnl / ext_cost * 100 if ext_cost else 0
            notify.notify_sell(saved_coin, ext_pnl, ext_pct, "외부청산 (재시작 시 잔고 없음)")
            try:
                cur_price = get_price(client, saved_coin)
                log_trade(
                    coin=saved_coin, market=saved["market"],
                    entry_price=float(saved["entry_price"]),
                    exit_price=cur_price,
                    volume=float(saved["volume"]),
                    cost_krw=ext_cost, received_krw=ext_recv,
                    exit_reason="외부청산 (재시작 시 잔고 없음)",
                    entered_at=saved["entered_at"], exited_at=datetime.now(),
                )
            except Exception as e:
                log.error(f"[DB] 외부청산 저장 실패: {e}")
            clear_active()
            if ext_pnl < 0:
                loss_coins[saved_coin] = time.time()
                save_loss_coins(loss_coins)
        else:
            log.info(f"[복구] 저장된 포지션: {saved_coin} - 모니터링 재개")
            pos      = {k: saved[k] for k in
                        ["coin", "market", "entry_price", "volume", "cost", "entered_at"]}
            if "entry_type" in saved:
                pos["entry_type"] = saved["entry_type"]
            highest  = float(saved.get("highest",  pos["entry_price"]))
            phase    = int(saved.get("phase",      1))
            sold_vol = float(saved.get("sold_vol", 0.0))
            recv_krw = float(saved.get("recv_krw", 0.0))
            trail    = float(saved.get("trail",    TRAIL_PCT))
            daily_trades += 1
            log.info(
                f"[{pos['coin']}] 복구 완료 | "
                f"진입={float(pos['entry_price']):,.3f}원 고점={highest:,.3f}원 단계={phase}"
            )

    while True:
        try:
            # ── 날짜 리셋 ────────────────────────────────────────────────────
            if date.today() != today:
                log.info(f"날짜 변경 | 전일 PnL: {daily_pnl:+,.0f}원 | 거래:{daily_trades}건")
                daily_pnl    = 0.0
                daily_trades = 0
                today        = date.today()

            # ── 오전 7시 일일 리포트 ─────────────────────────────────────────
            now_dt = datetime.now()
            if now_dt.hour == 7 and last_report_date != today:
                send_daily_report(today - timedelta(days=1))
                last_report_date = today

            # ── 일일 손실 한도 ───────────────────────────────────────────────
            if capital > 0 and daily_pnl / capital <= daily_limit:
                log.warning("[ALT] 일일 손실 한도 - 매매 중단")
                time.sleep(300)
                continue

            # ── 일일 거래 한도 ───────────────────────────────────────────────
            if daily_trades >= MAX_DAILY_TRADES:
                log.warning(f"[ALT] 일일 거래 한도 {MAX_DAILY_TRADES}건 도달")
                notify.send(
                    f"<b>[한도 도달]</b> 오늘 {MAX_DAILY_TRADES}건 거래 완료. 내일까지 매매 중단.",
                    force=True,
                )
                time.sleep(3600)
                continue

            # ── 포지션 보유 중: 트레일링 스탑 체크 ──────────────────────────
            if pos is not None:
                coin       = pos["coin"]
                entry      = float(pos["entry_price"])
                total_vol  = float(pos["volume"])
                total_cost = float(pos["cost"])
                half_vol   = round(total_vol * 0.5, 8)

                # 30초마다 실제 잔고 체크 → 수동 매도 감지
                try:
                    entered_ts_chk = datetime.fromisoformat(str(pos["entered_at"])).timestamp()
                except Exception:
                    entered_ts_chk = time.time()
                chk_elapsed = int(time.time() - entered_ts_chk)
                if chk_elapsed > 0 and chk_elapsed % 30 == 0:
                    actual_bal = get_coin_balance(client, coin)
                    remaining  = float(pos["volume"]) - float(pos.get("sold_vol", 0))
                    if actual_bal < remaining * 0.01:
                        current_p  = tracker.get_latest_price(coin) or get_price(client, coin)
                        ext_pnl    = recv_krw - total_cost
                        ext_pnl_pct = ext_pnl / total_cost * 100 if total_cost else 0
                        reason     = "외부청산 (수동 매도 감지)"
                        log.warning(f"[{coin}] 수동 매도 감지 - 포지션 종료")
                        notify.notify_sell(coin, ext_pnl, ext_pnl_pct, reason)
                        try:
                            log_trade(
                                coin=coin, market=pos["market"],
                                entry_price=float(pos["entry_price"]),
                                exit_price=current_p,
                                volume=float(pos["volume"]),
                                cost_krw=total_cost, received_krw=recv_krw,
                                exit_reason=reason,
                                entered_at=pos["entered_at"], exited_at=datetime.now(),
                            )
                        except Exception as e:
                            log.error(f"[DB] 저장 실패: {e}")
                        clear_active()
                        daily_pnl += ext_pnl
                        recent_pnls.append(ext_pnl)
                        pos = None; highest = 0.0; phase = 1
                        sold_vol = 0.0; recv_krw = 0.0; trail = TRAIL_PCT
                        cooldown_end = time.time() + COOLDOWN_SEC
                        time.sleep(SCAN_SEC)
                        continue

                current = tracker.get_latest_price(coin)
                if current <= 0:
                    current = get_price(client, coin)
                if current > 0:
                    if current > highest:
                        highest = current

                    trail_stop   = highest * (1 - trail)
                    pnl_pct      = (current - entry) / entry
                    try:
                        entered_ts = datetime.fromisoformat(str(pos["entered_at"])).timestamp()
                    except Exception:
                        entered_ts = time.time() - HOLD_MIN_SEC
                    hold_elapsed = time.time() - entered_ts
                    hold_min     = hold_elapsed / 60

                    log.info(
                        f"[{coin}] {current:,.3f}원  PnL={pnl_pct*100:+.2f}%  "
                        f"고점={highest:,.3f}원  스탑={trail_stop:,.3f}원  "
                        f"보유={hold_min:.0f}분  단계={phase}"
                    )
                    save_active(pos, highest, phase, sold_vol, recv_krw, trail)

                    entry_type   = pos.get("entry_type", "regular")
                    hold_min_sec = NEW_LIST_HOLD_MIN if entry_type == "newlisting" else HOLD_MIN_SEC
                    tp_half_pct  = NEW_LIST_TP_PCT   if entry_type == "newlisting" else TP_HALF

                    # ── 초기 보유 구간 ────────────────────────────────────────
                    if hold_elapsed < hold_min_sec:
                        # 선진입 타임아웃: 5분 안에 +1% 미달 시 청산
                        if (entry_type == "preemptive"
                                and hold_elapsed >= PRE_TIMEOUT_SEC
                                and pnl_pct < PRE_MIN_MOVE):
                            reason   = f"선진입 타임아웃 ({hold_min:.0f}분, 목표 미달)"
                            received = do_sell(client, pos, total_vol - sold_vol, reason)
                            if received is not None:
                                recv_krw += received
                            final_pnl     = recv_krw - total_cost
                            final_pnl_pct = final_pnl / total_cost * 100
                            log.info(f"[{coin}] 선진입 타임아웃 | PnL={final_pnl:+,.0f}원")
                            notify.notify_sell(coin, final_pnl, final_pnl_pct, reason)
                            try:
                                log_trade(
                                    coin=coin, market=pos["market"],
                                    entry_price=entry, exit_price=current,
                                    volume=total_vol, cost_krw=total_cost,
                                    received_krw=recv_krw, exit_reason=reason,
                                    entered_at=pos["entered_at"], exited_at=datetime.now(),
                                )
                            except Exception as e:
                                log.error(f"[DB] 저장 실패: {e}")
                            clear_active()
                            daily_pnl += final_pnl
                            recent_pnls.append(final_pnl)
                            if final_pnl < 0:
                                loss_coins = record_loss_coin(
                                    loss_coins, coin, final_pnl_pct, reason)
                                save_loss_coins(loss_coins)
                                report_loss(coin, final_pnl, final_pnl_pct,
                                            int(hold_elapsed), reason, entry_type)
                            pos = None; highest = 0.0; phase = 1
                            sold_vol = 0.0; recv_krw = 0.0; trail = TRAIL_PCT
                            cooldown_end = time.time() + COOLDOWN_SEC
                            time.sleep(SCAN_SEC)
                            continue

                        # 수익 중(+2% 이상)이면 10분 관계없이 트레일 스탑 적용
                        if pnl_pct >= TP_HALF and current <= trail_stop:
                            reason = (f"조기트레일 {pnl_pct*100:+.1f}% "
                                      f"(고점 {highest:,.3f}원 -{trail*100:.1f}%, {hold_min:.0f}분)")
                            received = do_sell(client, pos, total_vol - sold_vol, reason)
                            if received is not None:
                                recv_krw += received
                            final_pnl     = recv_krw - total_cost
                            final_pnl_pct = final_pnl / total_cost * 100
                            log.info(f"[{coin}] 조기트레일 | PnL={final_pnl:+,.0f}원 ({final_pnl_pct:+.2f}%)")
                            notify.notify_sell(coin, final_pnl, final_pnl_pct, reason)
                            try:
                                log_trade(
                                    coin=coin, market=pos["market"],
                                    entry_price=entry, exit_price=current,
                                    volume=total_vol, cost_krw=total_cost,
                                    received_krw=recv_krw, exit_reason=reason,
                                    entered_at=pos["entered_at"], exited_at=datetime.now(),
                                )
                            except Exception as e:
                                log.error(f"[DB] 저장 실패: {e}")
                            clear_active()
                            daily_pnl += final_pnl
                            recent_pnls.append(final_pnl)
                            pos = None; highest = 0.0; phase = 1
                            sold_vol = 0.0; recv_krw = 0.0; trail = TRAIL_PCT
                            cooldown_end = time.time() + COOLDOWN_SEC
                            time.sleep(SCAN_SEC)
                            continue

                        # 급락 손절 (신규상장 -5%, 선진입 -3%, 일반 -5%)
                        stop_pct = (NEW_LIST_STOP    if entry_type == "newlisting"
                                    else PRE_HARD_STOP if entry_type == "preemptive"
                                    else INITIAL_STOP_PCT)
                        if pnl_pct <= stop_pct:
                            tag    = ("[신규상장] " if entry_type == "newlisting"
                                      else "[선진입] " if entry_type == "preemptive"
                                      else "")
                            reason = f"{tag}초기손절 {pnl_pct*100:+.1f}% (진입 {hold_min:.0f}분, 급락)"
                            received = do_sell(client, pos, total_vol - sold_vol, reason)
                            if received is not None:
                                recv_krw += received
                            final_pnl     = recv_krw - total_cost
                            final_pnl_pct = final_pnl / total_cost * 100
                            log.info(f"[{coin}] 초기손절 | PnL={final_pnl:+,.0f}원 ({final_pnl_pct:+.2f}%)")
                            notify.notify_sell(coin, final_pnl, final_pnl_pct, reason)
                            try:
                                log_trade(
                                    coin=coin, market=pos["market"],
                                    entry_price=entry, exit_price=current,
                                    volume=total_vol, cost_krw=total_cost,
                                    received_krw=recv_krw, exit_reason=reason,
                                    entered_at=pos["entered_at"], exited_at=datetime.now(),
                                )
                            except Exception as e:
                                log.error(f"[DB] 저장 실패: {e}")
                            clear_active()
                            daily_pnl += final_pnl
                            recent_pnls.append(final_pnl)
                            if final_pnl < 0:
                                loss_coins = record_loss_coin(
                                    loss_coins, coin, final_pnl_pct, reason)
                                save_loss_coins(loss_coins)
                                report_loss(coin, final_pnl, final_pnl_pct,
                                            int(hold_elapsed), reason, entry_type)
                            pos = None; highest = 0.0; phase = 1
                            sold_vol = 0.0; recv_krw = 0.0; trail = TRAIL_PCT
                            cooldown_end = time.time() + COOLDOWN_SEC
                        time.sleep(SCAN_SEC)
                        continue

                    # ── 10분 이후: 1차 익절 + 트레일링스탑 ─────────────────
                    # 1차 익절
                    if phase == 1 and pnl_pct >= tp_half_pct:
                        received = do_sell(client, pos, half_vol,
                                           f"1차익절 {pnl_pct*100:+.1f}%")
                        if received is not None:
                            recv_krw += received
                        sold_vol += half_vol
                        phase    = 2
                        trail    = TIGHT_TRAIL
                        highest  = current
                        save_active(pos, highest, phase, sold_vol, recv_krw, trail)
                        log.info(f"[{coin}] 2단계 진입 - 트레일 {trail*100:.1f}%로 조임")

                    # 트레일링 스탑
                    elif current <= trail_stop:
                        tag    = ("[신규상장] " if entry_type == "newlisting"
                                  else "[선진입] " if entry_type == "preemptive"
                                  else "")
                        reason = (f"{tag}트레일링스탑 {pnl_pct*100:+.1f}% "
                                    f"(고점 {highest:,.3f}원 -{trail*100:.1f}%)")
                        received = do_sell(client, pos, total_vol - sold_vol, reason)
                        if received is not None:
                            recv_krw += received

                        final_pnl     = recv_krw - total_cost
                        final_pnl_pct = final_pnl / total_cost * 100
                        log.info(f"[{coin}] 청산 | PnL={final_pnl:+,.0f}원 ({final_pnl_pct:+.2f}%)")
                        notify.notify_sell(coin, final_pnl, final_pnl_pct, reason)

                        # DB 기록 먼저, 파일 삭제 나중
                        try:
                            log_trade(
                                coin=coin, market=pos["market"],
                                entry_price=entry, exit_price=current,
                                volume=total_vol, cost_krw=total_cost,
                                received_krw=recv_krw, exit_reason=reason,
                                entered_at=pos["entered_at"], exited_at=datetime.now(),
                            )
                        except Exception as e:
                            log.error(f"[DB] 저장 실패: {e}")
                        clear_active()

                        daily_pnl += final_pnl

                        # 손실 학습
                        recent_pnls.append(final_pnl)
                        if final_pnl < 0:
                            loss_coins = record_loss_coin(
                                loss_coins, coin, final_pnl_pct, reason)
                            save_loss_coins(loss_coins)
                            report_loss(coin, final_pnl, final_pnl_pct,
                                        int(hold_elapsed), reason, entry_type)

                        if len(recent_pnls) == STRICT_WINDOW:
                            losses = sum(1 for p in recent_pnls if p < 0)
                            if not strict_mode and losses >= STRICT_LOSS_CNT:
                                strict_mode = True
                                log.warning(f"[엄격모드 진입] 최근 {STRICT_WINDOW}건 중 {losses}건 손실")
                                notify.send(
                                    f"<b>[엄격모드 진입]</b> 최근 {STRICT_WINDOW}건 중 {losses}건 손실\n"
                                    f"진입 조건 {STRICT_MULT*100-100:.0f}% 강화",
                                    force=True,
                                )
                            elif strict_mode and losses < STRICT_LOSS_CNT:
                                strict_mode = False
                                log.info(f"[엄격모드 해제] 손실 {losses}건으로 감소")
                                notify.send("<b>[엄격모드 해제]</b> 승률 회복 - 조건 정상화", force=True)

                        log.info(f"[ALT] 오늘 누적 PnL: {daily_pnl:+,.0f}원 | 거래:{daily_trades}건")

                        # 포지션 초기화
                        pos      = None
                        highest  = 0.0
                        phase    = 1
                        sold_vol = 0.0
                        recv_krw = 0.0
                        trail    = TRAIL_PCT
                        cooldown_end = time.time() + COOLDOWN_SEC

                time.sleep(SCAN_SEC)
                continue

            # ── 포지션 없음: 신호 탐색 ───────────────────────────────────────
            if time.time() < cooldown_end:
                time.sleep(SCAN_SEC)
                continue

            scan_count += 1
            if scan_count % 6 == 0:
                log.info(f"[스캔] {len(tracker.coins())}개 코인 추적 중...")

            # ── 신규 상장 감지 ────────────────────────────────────────────────
            if time.time() - last_newlist_ts >= NEW_LIST_SCAN_SEC:
                last_newlist_ts = time.time()
                try:
                    cur_coins = set(client.get_all_coins_v2())
                    new_coins = cur_coins - known_coins
                    known_coins = cur_coins
                    if new_coins:
                        nc = list(new_coins)[0]
                        log.warning(f"*** [신규 상장] {new_coins} 감지! ***")
                        notify.send(
                            f"<b>[신규 상장]</b> {', '.join(new_coins)} 감지!\n즉시 매수 진입",
                            force=True,
                        )
                        if (nc not in SKIP_COINS and nc not in loss_coins
                                and daily_trades < MAX_DAILY_TRADES):
                            # 첫 체결가 > 0 확인 (가격 0이면 거래 미개시)
                            first_price = get_price(client, nc)
                            if first_price <= 0:
                                log.info(f"[{nc}] 신규 상장 감지됐지만 가격 0 — 거래 미개시, 대기")
                                continue
                            avail   = get_available_krw(client)
                            buy_krw = min(NEW_LIST_ENTRY_KRW, avail * 0.99)
                            if buy_krw >= MIN_KRW:
                                new_pos = do_buy(client, nc, buy_krw)
                                if new_pos:
                                    new_pos["entry_type"] = "newlisting"
                                    pos      = new_pos
                                    highest  = float(pos["entry_price"])
                                    phase    = 1
                                    sold_vol = 0.0
                                    recv_krw = 0.0
                                    trail    = NEW_LIST_TRAIL
                                    daily_trades += 1
                                    save_active(pos, highest, phase, sold_vol, recv_krw, trail)
                                    log.info(
                                        f"[{nc}] 신규 상장 진입 | "
                                        f"{highest:,.3f}원 TP={NEW_LIST_TP_PCT*100:.0f}% "
                                        f"트레일={trail*100:.0f}%"
                                    )
                                    try:
                                        log_signal(nc, pos["entered_at"], "newlisting",
                                                   None, None, False)
                                    except Exception:
                                        pass
                                    time.sleep(SCAN_SEC)
                                    continue
                except Exception as e:
                    log.debug(f"[신규상장] 체크 오류: {e}")

            if not is_btc_bullish(client):
                time.sleep(SCAN_SEC * 3)
                continue

            holdings = get_holdings(client)
            if holdings:
                log.info(f"[보유중] {', '.join(holdings)} - 해당 코인 신호 무시")

            now_ts     = time.time()
            loss_coins = {c: v for c, v in loss_coins.items()
                          if now_ts < v.get("until", 0)}

            found = []
            for coin in tracker.coins():
                if coin in SKIP_COINS or coin in holdings:
                    continue
                if coin in loss_coins:
                    continue
                sig = tracker.get_signal(coin)
                if sig:
                    found.append(sig)
                    log.info(
                        f"  [신호] {coin} | "
                        f"가격={sig['price_chg']*100:+.1f}% | "
                        f"거래량={sig['vol_mult']:.1f}x | "
                        f"현재={sig['price']:,.3f}원"
                    )

            # ── 선진입 신호 탐색 (PRE_ENABLED=False 로 비활성화 중) ───────────
            pre_found = []
            if PRE_ENABLED:
                for coin in tracker.coins():
                    if coin in SKIP_COINS or coin in holdings or coin in loss_coins:
                        continue
                    pre_sig = tracker.get_preemptive_signal(coin)
                    if pre_sig:
                        pre_found.append(pre_sig)
                        log.info(
                            f"  [선진입] {coin} | "
                            f"거래량={pre_sig['vol_mult']:.0f}x | "
                            f"가격={pre_sig['price_chg']*100:+.1f}%"
                        )

                if pre_found:
                    best_pre = max(pre_found, key=lambda x: x["vol_mult"])
                    coin     = best_pre["coin"]
                    log.warning(
                        f"*** [선진입 신호] {coin} | "
                        f"거래량={best_pre['vol_mult']:.0f}x | "
                        f"가격={best_pre['price_chg']*100:+.1f}% | 즉시 진입 ***"
                    )
                    ok = (check_orderbook(client, coin)
                          and check_tick_ratio(client, coin, tracker))
                    if not ok:
                        log.info(f"[{coin}] 선진입 필터 미달 - 스킵")
                    else:
                        avail   = get_available_krw(client)
                        buy_krw = min(capital * ALT_ENTRY_RATIO, avail * 0.99)
                        if buy_krw >= MIN_KRW:
                            new_pos = do_buy(client, coin, buy_krw)
                            if new_pos:
                                new_pos["entry_type"] = "preemptive"
                                pos      = new_pos
                                highest  = float(pos["entry_price"])
                                phase    = 1
                                sold_vol = 0.0
                                recv_krw = 0.0
                                trail    = TRAIL_PCT
                                daily_trades += 1
                                save_active(pos, highest, phase, sold_vol, recv_krw, trail)
                                log.info(
                                    f"[{coin}] 선진입 시작 | "
                                    f"진입={highest:,.3f}원 | "
                                    f"타임아웃={PRE_TIMEOUT_SEC//60}분"
                                )
                    time.sleep(SCAN_SEC)
                    continue

            if not found:
                time.sleep(SCAN_SEC)
                continue

            if strict_mode:
                found = [s for s in found
                         if s["price_chg"] >= PRICE_THRESH * STRICT_MULT
                         and s["vol_mult"]  >= VOLUME_MULT  * STRICT_MULT]
                if not found:
                    log.info("[엄격모드] 강화 조건 미달 - 스킵")
                    time.sleep(SCAN_SEC)
                    continue

            best     = max(found, key=lambda x: x["vol_mult"])
            coin     = best["coin"]
            mode_tag = " [엄격]" if strict_mode else ""
            log.warning(
                f"*** [진입 신호{mode_tag}] {coin} | "
                f"가격={best['price_chg']*100:+.1f}% | "
                f"거래량={best['vol_mult']:.1f}x | 오늘 {daily_trades+1}건 ***"
            )

            # 신호 시점 기술지표 스냅샷 (1회 fetch, 이후 재사용)
            _indic = indicator_snapshot(client, f"KRW-{coin}")

            # RSI 필터: 과열(>80) 또는 침체(<45) 구간 차단
            _rsi = _indic.get("rsi")
            if _rsi is not None and (_rsi < 45 or _rsi > 80):
                log.info(f"[{coin}] RSI {_rsi:.1f} 범위 외 (45~80) - 진입 취소")
                try:
                    log_signal(coin, datetime.now(), "skipped",
                               best["price_chg"] * 100, best["vol_mult"], strict_mode,
                               skip_reason=f"RSI범위외({_rsi:.0f})", **_indic)  # 45~80
                except Exception:
                    pass
                time.sleep(SCAN_SEC)
                continue

            # MACD 필터: 하락 추세 구간 차단 (None이면 데이터 부족 → 통과)
            _macd = _indic.get("macd_bull")
            if _macd is not None and not _macd:
                log.info(f"[{coin}] MACD 하락 - 진입 취소")
                try:
                    log_signal(coin, datetime.now(), "skipped",
                               best["price_chg"] * 100, best["vol_mult"], strict_mode,
                               skip_reason="MACD하락", **_indic)
                except Exception:
                    pass
                time.sleep(SCAN_SEC)
                continue

            # BB%B 필터: 볼린저밴드 과도 돌파(>1.1) 차단 - 극도 과열, 되돌림 위험
            _bb = _indic.get("bb_pct")
            if _bb is not None and _bb > 1.1:
                log.info(f"[{coin}] BB%B {_bb:.2f} > 1.1 과열 - 진입 취소")
                try:
                    log_signal(coin, datetime.now(), "skipped",
                               best["price_chg"] * 100, best["vol_mult"], strict_mode,
                               skip_reason=f"BB과열({_bb:.2f})", **_indic)
                except Exception:
                    pass
                time.sleep(SCAN_SEC)
                continue

            # 거래량 상한 필터: 15배 초과는 펌프덤프 특징
            if best["vol_mult"] > 15.0:
                log.info(f"[{coin}] 거래량 {best['vol_mult']:.1f}x > 15x 펌프덤프 의심 - 진입 취소")
                try:
                    log_signal(coin, datetime.now(), "skipped",
                               best["price_chg"] * 100, best["vol_mult"], strict_mode,
                               skip_reason=f"거래량과다({best['vol_mult']:.0f}x)", **_indic)
                except Exception:
                    pass
                time.sleep(SCAN_SEC)
                continue

            if not check_orderbook(client, coin):
                log.info(f"[{coin}] 호가 불균형 미달 - 진입 취소")
                try:
                    log_signal(coin, datetime.now(), "skipped",
                               best["price_chg"] * 100, best["vol_mult"], strict_mode,
                               skip_reason="호가불균형", **_indic)
                except Exception:
                    pass
                time.sleep(SCAN_SEC)
                continue
            if not check_tick_ratio(client, coin, tracker):
                log.info(f"[{coin}] 체결강도 미달 - 진입 취소")
                try:
                    log_signal(coin, datetime.now(), "skipped",
                               best["price_chg"] * 100, best["vol_mult"], strict_mode,
                               skip_reason="체결강도미달", **_indic)
                except Exception:
                    pass
                time.sleep(SCAN_SEC)
                continue

            # ── 진입 확인 딜레이: 60초 대기 후 가격 유지 확인 ────────────────
            signal_price = best["price"]
            log.info(f"[{coin}] 60초 확인 대기 중... (신호가={signal_price:,.3f}원)")
            time.sleep(60)
            confirm_price = get_price(client, coin)
            if confirm_price <= 0 or confirm_price < signal_price * 0.99:
                log.info(
                    f"[{coin}] 확인 실패 - 가격 하락 "
                    f"({signal_price:,.3f} → {confirm_price:,.3f}) - 진입 취소"
                )
                try:
                    log_signal(coin, datetime.now(), "skipped",
                               best["price_chg"] * 100, best["vol_mult"], strict_mode,
                               skip_reason="확인딜레이실패", **_indic)
                except Exception:
                    pass
                time.sleep(SCAN_SEC)
                continue
            log.info(f"[{coin}] 확인 완료 - 가격 유지 ({confirm_price:,.3f}원) → 진입")

            avail   = get_available_krw(client)
            buy_krw = min(capital * ALT_ENTRY_RATIO, avail * 0.99)
            if buy_krw < MIN_KRW:
                log.warning(f"KRW 잔고 부족: {avail:,.0f}원")
                time.sleep(SCAN_SEC)
                continue

            new_pos = do_buy(client, coin, buy_krw)
            if not new_pos:
                time.sleep(SCAN_SEC)
                continue

            # 포지션 세팅
            pos      = new_pos
            highest  = float(pos["entry_price"])
            phase    = 1
            sold_vol = 0.0
            recv_krw = 0.0
            trail    = TRAIL_PCT
            daily_trades += 1
            save_active(pos, highest, phase, sold_vol, recv_krw, trail)
            try:
                log_signal(coin, pos["entered_at"], "regular",
                           best["price_chg"] * 100, best["vol_mult"], strict_mode,
                           **_indic)
            except Exception:
                pass
            log.info(
                f"[{coin}] 모니터링 시작 | "
                f"진입={highest:,.3f}원 TP={TP_HALF*100:.0f}% 트레일={trail*100:.1f}%"
            )

        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"루프 오류: {e}")
            time.sleep(SCAN_SEC)


if __name__ == "__main__":
    run()
