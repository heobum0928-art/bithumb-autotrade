"""
Altcoin Momentum Monitor  (Strategy D - Aggressive Real-time)

Signal:
  - 60초 전 대비 현재가 +2% 이상
  - 최근 거래 속도가 직전 대비 3배 이상
  (캔들 없이 ticker/ALL 10초마다 폴링 -> 메모리 가격 이력으로 계산)

Entry: 가용 KRW 30%, TP +4%, 트레일 2.5%

Run: python scripts/alt_monitor.py
"""
import sys
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
from bithumb.db import init_db, log_trade, DB_PATH
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
WS_URL          = "wss://pubwss.bithumb.com/pub/ws"
WS_MIN_INTERVAL = 2.0    # WebSocket 스냅샷 최소 저장 간격 (초)
SCAN_SEC        = 2      # 신호 체크 주기 (초) - WebSocket 실시간 수신으로 단축
WINDOW_SEC      = 60     # 가격 변화 측정 윈도우 (초)
PRICE_THRESH    = 0.02   # +2% 이상
VOLUME_MULT     = 3.0    # 최근 거래속도 3배 이상
ALT_ENTRY_RATIO = 0.30   # 가용 KRW의 30%
TP_HALF         = 0.04   # 1차 익절 +4%
TRAIL_PCT       = 0.025  # 트레일링 폭 2.5%
TIGHT_TRAIL     = 0.015  # 분할 후 조인 1.5%
DAILY_LIMIT_PCT = -0.05
MIN_KRW         = 5001
COOLDOWN_SEC      = 120    # 청산 후 재진입 대기 (초)
MIN_COIN_PRICE    = 10     # 최소 코인 단가 (원) - 저가 코인 노이즈 제거
MAX_DAILY_TRADES  = 30     # 일일 최대 거래 횟수
MIN_TRADE_KRW_PER_MIN = 1_000_000  # 분당 거래대금 최소 100만원
BTC_DROP_LIMIT  = -0.015 # BTC 1시간 낙폭이 이 이상이면 진입 중단 (-1.5%)
OB_BID_RATIO    = 1.5    # 호가 매수잔량 / 매도잔량 최소 비율
TICK_BUY_RATIO  = 0.60   # 최근 체결 중 매수 비율 최소치 (REST 폴백용)
VOLUME_POWER_MIN = 100.0  # WebSocket volumePower 최소치 (100 = 매수=매도)

LOSS_COIN_COOLDOWN_SEC = 4 * 3600  # 손실 코인 재진입 차단 시간 (4시간)
STRICT_WINDOW   = 5     # 최근 N거래 기준으로 엄격 모드 판단
STRICT_LOSS_CNT = 3     # N거래 중 손실이 이 이상이면 엄격 모드
STRICT_MULT     = 1.3   # 엄격 모드 임계값 배수 (가격·거래량 기준 30% 강화)

SKIP_COINS = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}

STATE_FILE = Path("data/active_pos.json")


def save_active(pos: dict, highest: float, phase: int, sold_vol: float, recv_krw: float, trail: float) -> None:
    data = {**pos, "highest": highest, "phase": phase, "sold_vol": sold_vol, "recv_krw": recv_krw, "trail": trail}
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


# ── 실시간 가격 추적기 ─────────────────────────────────────────────────────────

class PriceTracker:
    """
    WebSocket 실시간 수신으로 각 코인별 (timestamp, price, acc_val) 스냅샷 유지.
    2초 간격 최소 저장, Lock으로 스레드 안전 보장.
    """
    MAXLEN = 60  # 60개 스냅샷 = 120초치 (2초 * 60)

    def __init__(self):
        self._hist: dict[str, deque] = {}
        self._vol_power: dict[str, float] = {}  # 최신 체결강도 (volumePower)
        self._lock = threading.Lock()
        self._ws = None
        self._ws_running = False

    def start_ws(self, symbols: list[str]) -> None:
        """WebSocket 구독 시작 (백그라운드 스레드)."""
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
                c = data.get("content", {})
                symbol = c.get("symbol", "")
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
                        on_open=on_open,
                        on_message=on_message,
                        on_error=on_error,
                        on_close=on_close,
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

            # ── 가격 변화: WINDOW_SEC 전 스냅샷과 비교 ────────────────────────
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

            # ── 거래 속도: 후반부 vs 전반부 비교 ──────────────────────────────
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
            vol_mult = r_rate / o_rate if o_rate > 0 else 0

            if vol_mult < VOLUME_MULT:
                return None

            # 거래대금 절댓값 필터
            if r_rate * 60 < MIN_TRADE_KRW_PER_MIN:
                return None

            # 상승봉 연속성: 최근 4개 중 2번 이상 상승
            recent_prices = [s[1] for s in snaps[-4:]]
            up_count = sum(1 for i in range(1, len(recent_prices))
                           if recent_prices[i] > recent_prices[i - 1])
            if up_count < 2:
                return None

            return {
                "coin":      coin,
                "price_chg": price_chg,
                "vol_mult":  vol_mult,
                "price":     now_price,
                "elapsed":   elapsed,
            }

    def coins(self) -> list[str]:
        with self._lock:
            return list(self._hist.keys())


# ── 설정 / 잔고 ───────────────────────────────────────────────────────────────

def load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


def get_available_krw(client: BithumbClient) -> float:
    for a in client.get_accounts():
        if a["currency"] == "KRW":
            return float(a["balance"])
    return 0.0


def get_holdings(client: BithumbClient) -> set[str]:
    """현재 보유 중인 코인 심볼 셋 반환 (KRW·P 제외, 1원 이상 평가 코인만)."""
    held = set()
    try:
        accounts = client.get_accounts()
        for a in accounts:
            cur = a["currency"]
            if cur in ("KRW", "P"):
                continue
            bal = float(a.get("balance", 0))
            if bal <= 0:
                continue
            try:
                price = float(client.get_ticker(cur)["closing_price"])
                if bal * price >= 1000:  # 1,000원 이상이면 보유 중으로 간주
                    held.add(cur)
            except Exception:
                if bal > 0.0001:
                    held.add(cur)
    except Exception:
        pass
    return held


def is_btc_bullish(client: BithumbClient) -> bool:
    """BTC 1시간 추세 확인. 낙폭이 BTC_DROP_LIMIT 이상이면 False 반환."""
    try:
        candles = client.get_candles("KRW-BTC", unit=60, count=2)
        if len(candles) < 2:
            return True
        cur_price  = candles[0]["trade_price"]
        prev_price = candles[1]["trade_price"]
        chg = (cur_price - prev_price) / prev_price
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
    """target_date 하루치 거래 손익을 텔레그램으로 전송."""
    import sqlite3
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades WHERE date = ? ORDER BY exited_at", (target_date.isoformat(),)
        ).fetchall()
        con.close()

        ds = target_date.strftime("%Y-%m-%d")
        if not rows:
            notify.send(f"<b>[일일 리포트 {ds}]</b>\n거래 없음", force=True)
            return

        total_pnl = sum(r["pnl_krw"] for r in rows)
        wins      = sum(1 for r in rows if r["pnl_krw"] > 0)
        total     = len(rows)
        win_rate  = wins / total * 100

        lines = [f"<b>[일일 리포트 {ds}]</b>",
                 f"거래: {total}건 | 승률: {win_rate:.0f}% ({wins}승 {total-wins}패)"]
        for r in rows:
            sign = "+" if r["pnl_krw"] >= 0 else ""
            lines.append(f"  {r['coin']}: {sign}{r['pnl_krw']:,.0f}원 ({r['pnl_pct']:+.1f}%)")
        lines.append(f"\n<b>총 PnL: {total_pnl:+,.0f}원</b>")
        notify.send("\n".join(lines), force=True)
        log.info(f"[리포트] {ds} 전송 완료 | {total}건 | {total_pnl:+,.0f}원")
    except Exception as e:
        log.error(f"[리포트] 전송 실패: {e}")


def check_orderbook(client: BithumbClient, coin: str) -> bool:
    """상위 5호가 매수잔량 KRW >= 매도잔량 KRW * OB_BID_RATIO 이어야 진입 허용."""
    try:
        ob = client.get_orderbook(coin)
        bids = ob.get("bids", [])[:5]
        asks = ob.get("asks", [])[:5]
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
        return True  # 오류 시 통과


def check_tick_ratio(client: BithumbClient, coin: str, tracker: "PriceTracker" = None) -> bool:
    """체결강도 확인. WebSocket volumePower 우선, 없으면 REST 폴백."""
    if tracker is not None:
        vp = tracker.get_vol_power(coin)
        log.info(f"[{coin}] 체결강도(WS) volumePower={vp:.0f} (기준 {VOLUME_POWER_MIN:.0f})")
        return vp >= VOLUME_POWER_MIN
    try:
        txs = client.get_transaction_history(coin, count=20)
        if not txs:
            return True
        buys = sum(1 for t in txs if t.get("type") == "bid")
        ratio = buys / len(txs)
        log.info(f"[{coin}] 체결강도(REST) 매수비율={ratio*100:.0f}%")
        return ratio >= TICK_BUY_RATIO
    except Exception as e:
        log.debug(f"[{coin}] 체결강도 조회 실패: {e}")
        return True


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
        r    = client.market_buy(market, buy_krw)
        uuid = r.get("uuid")
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
        funds = float(order.get("executed_funds", 0))
        fee   = float(order.get("paid_fee", 0))
        if vol <= 0:
            return None
        entry = funds / vol
        log.info(f"[{coin}] 매수 체결 | 수량={vol:.8f} 단가={entry:,.0f}원")
        notify.notify_buy(coin, entry, vol, funds + fee)
        return {
            "coin":        coin,
            "market":      market,
            "volume":      vol,
            "entry_price": entry,
            "cost":        funds + fee,
            "entered_at":  datetime.now(),
        }
    except Exception as e:
        log.error(f"[{coin}] 매수 실패: {e}")
        return None


def get_coin_balance(client: BithumbClient, coin: str) -> float:
    """실제 거래소 잔고 조회 - 소수점 오차 방지."""
    try:
        for a in client.get_accounts():
            if a["currency"] == coin.upper():
                return float(a["balance"])
    except Exception:
        pass
    return 0.0


def do_sell(client: BithumbClient, pos: dict, volume: float, reason: str) -> float:
    coin = pos["coin"]
    actual_bal = get_coin_balance(client, coin)
    if actual_bal <= 0:
        log.info(f"[{coin}] 잔고 없음 - 이미 매도됨")
        return 0.0
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
                received = float(order.get("executed_funds", 0)) - float(order.get("paid_fee", 0))
                log.info(f"[{coin}] 매도 체결 | 수령={received:,.0f}원 ({attempt}회차)")
                return received
            # 미체결 - 기존 주문 취소 후 재시도
            log.warning(f"[{coin}] 매도 미체결 {attempt}회차 - 주문 취소 후 재시도")
            try:
                client.cancel_order(last_uuid)
            except Exception:
                pass
        except Exception as e:
            log.warning(f"[{coin}] 매도 오류 {attempt}회차: {e}")

        if attempt % 5 == 0:
            notify.notify_error(f"{coin} 매도 {attempt}회 실패 중, 자동 재시도...")

        # 잔고 재확인 - 이미 팔렸으면 중단
        bal = get_coin_balance(client, coin)
        if bal <= 0:
            log.info(f"[{coin}] 잔고 0 확인 - 매도 완료로 처리")
            return 0.0
        volume = min(volume, bal)
        time.sleep(10)


# ── 트레일링 스탑 ─────────────────────────────────────────────────────────────

def monitor_trailing(client: BithumbClient, pos: dict, state: dict = None) -> float:
    coin       = pos["coin"]
    entry      = float(pos["entry_price"])
    total_vol  = float(pos["volume"])
    total_cost = float(pos["cost"])
    half_vol   = round(total_vol * 0.5, 8)
    reason     = "unknown"

    if state:
        highest  = float(state.get("highest", entry))
        phase    = int(state.get("phase", 1))
        sold_vol = float(state.get("sold_vol", 0.0))
        recv_krw = float(state.get("recv_krw", 0.0))
        trail    = float(state.get("trail", TRAIL_PCT))
        log.info(f"[{coin}] 포지션 복구 | 진입={entry:,.2f}원 고점={highest:,.2f}원 단계={phase}")
    else:
        highest  = entry
        phase    = 1
        sold_vol = 0.0
        recv_krw = 0.0
        trail    = TRAIL_PCT

    save_active(pos, highest, phase, sold_vol, recv_krw, trail)
    log.info(
        f"[{coin}] 모니터링 시작 | "
        f"진입={entry:,.2f}원 TP={TP_HALF*100:.0f}% 트레일={trail*100:.1f}%"
    )

    while True:
        time.sleep(3)
        current = get_price(client, coin)
        if current <= 0:
            continue

        if current > highest:
            highest = current

        trail_stop = highest * (1 - trail)
        pnl_pct    = (current - entry) / entry
        remaining  = total_vol - sold_vol

        log.info(
            f"[{coin}] {current:,.2f}원  PnL={pnl_pct*100:+.2f}%  "
            f"고점={highest:,.2f}원  스탑={trail_stop:,.2f}원"
        )
        save_active(pos, highest, phase, sold_vol, recv_krw, trail)

        if phase == 1 and pnl_pct >= TP_HALF:
            recv_krw += do_sell(client, pos, half_vol, f"1차익절 {pnl_pct*100:+.1f}%")
            sold_vol += half_vol
            phase    = 2
            trail    = TIGHT_TRAIL
            highest  = current
            save_active(pos, highest, phase, sold_vol, recv_krw, trail)
            log.info(f"[{coin}] 2단계 - 트레일 {trail*100:.1f}%로 조임")
            continue

        if current <= trail_stop:
            reason = f"트레일링스탑 {pnl_pct*100:+.1f}% (고점 {highest:,.2f}원 -{trail*100:.1f}%)"
            recv_krw += do_sell(client, pos, total_vol - sold_vol, reason)
            break

    # recv_krw == -1.0 이면 매도 실패 (sentinel)
    if recv_krw < 0:
        log.error(f"[{coin}] 매도 최종 실패 - 수동 처리 필요")
        return 0.0  # PnL 집계에 반영 안 함

    pnl     = recv_krw - total_cost
    pnl_pct = pnl / total_cost * 100
    log.info(f"[{coin}] 종료 | PnL={pnl:+,.0f}원 ({pnl_pct:+.2f}%)")
    notify.notify_sell(coin, pnl, pnl_pct, reason)

    clear_active()

    try:
        log_trade(
            coin=coin, market=pos["market"],
            entry_price=entry, exit_price=get_price(client, coin),
            volume=total_vol, cost_krw=total_cost, received_krw=recv_krw,
            exit_reason=reason,
            entered_at=pos["entered_at"], exited_at=datetime.now(),
        )
    except Exception as e:
        log.error(f"[DB] 저장 실패: {e}")

    return pnl


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run():
    cfg         = load_config()
    capital     = cfg["trading"]["capital_krw"]
    daily_limit = cfg["trading"].get("daily_loss_limit_pct", DAILY_LIMIT_PCT)

    init_db()
    client  = BithumbClient()
    tracker = PriceTracker()

    # WebSocket 실시간 수신 시작
    all_coins = client.get_all_coins_v2()
    symbols   = [f"{c}_KRW" for c in all_coins]
    tracker.start_ws(symbols)
    log.info(f"[WS] {len(symbols)}개 코인 실시간 수신 시작 - 초기 데이터 수집 중...")
    time.sleep(5)  # 초기 데이터 수집 대기

    log.info("=== ALT 모멘텀 봇 시작 [WebSocket 실시간] ===")
    log.info(
        f"스캔={SCAN_SEC}s | 윈도우={WINDOW_SEC}s | "
        f"가격임계={PRICE_THRESH*100:.0f}% | 거래량배수={VOLUME_MULT:.0f}x | "
        f"진입={ALT_ENTRY_RATIO*100:.0f}% | TP={TP_HALF*100:.0f}% | 트레일={TRAIL_PCT*100:.1f}%"
    )

    daily_pnl        = 0.0
    daily_trades     = 0   # 통계용 카운터 (제한 없음)
    today            = date.today()
    active_pos       = None
    cooldown_end     = 0.0
    scan_count       = 0
    last_report_date = None  # 일일 리포트 중복 전송 방지
    loss_coins       = {}    # {coin: 손실 발생 timestamp} 코인별 쿨다운
    recent_pnls      = deque(maxlen=STRICT_WINDOW)  # 최근 N거래 손익
    strict_mode      = False  # 엄격 모드 플래그

    # 재시작 시 저장된 포지션 복구
    saved = load_active()
    if saved:
        log.info(f"[복구] 저장된 포지션 발견: {saved['coin']} - 모니터링 재개")
        pos   = {k: saved[k] for k in ["coin", "market", "entry_price", "volume", "cost", "entered_at"]}
        state = {k: saved[k] for k in ["highest", "phase", "sold_vol", "recv_krw", "trail"]}
        active_pos    = pos
        daily_trades += 1
        pnl           = monitor_trailing(client, pos, state)
        daily_pnl    += pnl
        active_pos    = None
        cooldown_end  = time.time() + COOLDOWN_SEC

    while True:
        try:
            if date.today() != today:
                log.info(f"날짜 변경 | 전일 ALT PnL: {daily_pnl:+,.0f}원 | 거래:{daily_trades}건")
                daily_pnl    = 0.0
                daily_trades = 0
                today        = date.today()

            # 매일 오전 7시 전일 손익 리포트
            now = datetime.now()
            if now.hour == 7 and last_report_date != today:
                yesterday = today - timedelta(days=1)
                send_daily_report(yesterday)
                last_report_date = today

            if daily_pnl / capital <= daily_limit:
                log.warning("[ALT] 일일 손실 한도 - 매매 중단")
                time.sleep(300)
                continue

            if daily_trades >= MAX_DAILY_TRADES:
                log.warning(f"[ALT] 일일 거래 한도 {MAX_DAILY_TRADES}건 도달 - 매매 중단")
                notify.send(f"<b>[한도 도달]</b> 오늘 {MAX_DAILY_TRADES}건 거래 완료. 내일까지 매매 중단.", force=True)
                time.sleep(3600)
                continue

            scan_count += 1

            # 포지션 보유 중 or 쿨다운 중이면 신호 탐색 스킵
            if active_pos is not None or time.time() < cooldown_end:
                time.sleep(SCAN_SEC)
                continue

            # 매 6회(60초)마다 스캔 상태 로그
            if scan_count % 6 == 0:
                log.info(f"[스캔] {len(tracker.coins())}개 코인 추적 중...")

            # ── BTC 추세 필터 ────────────────────────────────────────────────────
            if not is_btc_bullish(client):
                time.sleep(SCAN_SEC * 3)
                continue

            # ── 현재 보유 코인 파악 (중복 진입 방지) ─────────────────────────────
            holdings = get_holdings(client)
            if holdings:
                log.info(f"[보유중] {', '.join(holdings)} - 해당 코인 신호 무시")

            # ── 신호 탐색 ─────────────────────────────────────────────────────────
            now_ts = time.time()
            # 쿨다운 만료된 손실 코인 정리
            loss_coins = {c: ts for c, ts in loss_coins.items()
                          if now_ts - ts < LOSS_COIN_COOLDOWN_SEC}

            found = []
            for coin in tracker.coins():
                if coin in SKIP_COINS or coin in holdings:
                    continue
                if coin in loss_coins:
                    continue  # 손실 쿨다운 중인 코인 스킵
                sig = tracker.get_signal(coin)
                if sig:
                    found.append(sig)
                    log.info(
                        f"  [신호] {coin} | "
                        f"가격={sig['price_chg']*100:+.1f}% | "
                        f"거래량={sig['vol_mult']:.1f}x | "
                        f"현재={sig['price']:,.0f}원"
                    )

            if not found:
                time.sleep(SCAN_SEC)
                continue

            # 엄격 모드: 진입 임계값 30% 강화 후 재필터
            if strict_mode:
                found = [s for s in found
                         if s["price_chg"] >= PRICE_THRESH * STRICT_MULT
                         and s["vol_mult"] >= VOLUME_MULT * STRICT_MULT]
                if not found:
                    log.info("[엄격 모드] 강화 조건 미달 - 스킵")
                    time.sleep(SCAN_SEC)
                    continue

            # 거래량 배수 기준 최강 신호 선택
            best = max(found, key=lambda x: x["vol_mult"])
            coin = best["coin"]
            mode_tag = " [엄격]" if strict_mode else ""
            log.warning(
                f"*** [진입 신호{mode_tag}] {coin} | "
                f"가격={best['price_chg']*100:+.1f}% | "
                f"거래량={best['vol_mult']:.1f}x | 오늘 {daily_trades+1}건 ***"
            )

            # ── 호가 불균형 + 체결강도 2차 확인 ─────────────────────────────────
            if not check_orderbook(client, coin):
                log.info(f"[{coin}] 호가 불균형 미달 - 진입 취소")
                time.sleep(SCAN_SEC)
                continue
            if not check_tick_ratio(client, coin, tracker):
                log.info(f"[{coin}] 체결강도 미달 - 진입 취소")
                time.sleep(SCAN_SEC)
                continue

            avail   = get_available_krw(client)
            buy_krw = min(capital * ALT_ENTRY_RATIO, avail * 0.99)
            if buy_krw < MIN_KRW:
                log.warning(f"KRW 잔고 부족: {avail:,.0f}원")
                time.sleep(SCAN_SEC)
                continue

            pos = do_buy(client, coin, buy_krw)
            if not pos:
                time.sleep(SCAN_SEC)
                continue

            active_pos    = pos
            daily_trades += 1
            pnl           = monitor_trailing(client, pos)
            daily_pnl    += pnl
            active_pos    = None
            cooldown_end  = time.time() + COOLDOWN_SEC
            log.info(f"[ALT] 오늘 누적 PnL: {daily_pnl:+,.0f}원 | 거래:{daily_trades}건 | 쿨다운 {COOLDOWN_SEC}s")

            # ── 손실 학습 ────────────────────────────────────────────────────────
            recent_pnls.append(pnl)
            if pnl < 0:
                loss_coins[coin] = time.time()
                log.info(f"[학습] {coin} 손실 -> {LOSS_COIN_COOLDOWN_SEC//3600}시간 재진입 차단")

            if len(recent_pnls) == STRICT_WINDOW:
                losses = sum(1 for p in recent_pnls if p < 0)
                if not strict_mode and losses >= STRICT_LOSS_CNT:
                    strict_mode = True
                    log.warning(f"[엄격 모드 진입] 최근 {STRICT_WINDOW}거래 중 {losses}건 손실")
                    notify.send(
                        f"<b>[엄격 모드 진입]</b> 최근 {STRICT_WINDOW}거래 중 {losses}건 손실\n"
                        f"진입 조건 {STRICT_MULT*100-100:.0f}% 강화 적용",
                        force=True,
                    )
                elif strict_mode and losses < STRICT_LOSS_CNT:
                    strict_mode = False
                    log.info(f"[엄격 모드 해제] 최근 {STRICT_WINDOW}거래 중 손실 {losses}건으로 감소")
                    notify.send(
                        f"<b>[엄격 모드 해제]</b> 승률 회복 - 진입 조건 정상 복원",
                        force=True,
                    )

        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"루프 오류: {e}")
            time.sleep(SCAN_SEC)


if __name__ == "__main__":
    run()
