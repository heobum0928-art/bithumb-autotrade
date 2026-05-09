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
import logging
import yaml
from collections import deque
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient
from bithumb.db import init_db, log_trade
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
SCAN_SEC        = 10     # 폴링 주기 (초)
WINDOW_SEC      = 60     # 가격 변화 측정 윈도우 (초)
PRICE_THRESH    = 0.02   # +2% 이상
VOLUME_MULT     = 3.0    # 최근 거래속도 3배 이상
ALT_ENTRY_RATIO = 0.30   # 가용 KRW의 30%
TP_HALF         = 0.04   # 1차 익절 +4%
TRAIL_PCT       = 0.025  # 트레일링 폭 2.5%
TIGHT_TRAIL     = 0.015  # 분할 후 조인 1.5%
DAILY_LIMIT_PCT = -0.05
MIN_KRW         = 5001
COOLDOWN_SEC    = 120    # 청산 후 재진입 대기 (초)
MIN_COIN_PRICE  = 10     # 최소 코인 단가 (원) - 저가 코인 노이즈 제거
BTC_DROP_LIMIT  = -0.015 # BTC 1시간 낙폭이 이 이상이면 진입 중단 (-1.5%)

SKIP_COINS = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}


# ── 실시간 가격 추적기 ─────────────────────────────────────────────────────────

class PriceTracker:
    """
    ticker/ALL 폴링 결과를 메모리에 누적.
    각 코인별로 (timestamp, price, acc_vol_24H) 스냅샷 deque 유지.
    """
    MAXLEN = 30  # 최대 300초치 (10초 * 30)

    def __init__(self):
        self._hist: dict[str, deque] = {}

    def update(self, ticker_all: dict) -> None:
        now = time.time()
        for coin, data in ticker_all.items():
            if coin == "date":
                continue
            try:
                price   = float(data["closing_price"])
                acc_vol = float(data["acc_trade_value_24H"])
                if price <= 0:
                    continue
                if coin not in self._hist:
                    self._hist[coin] = deque(maxlen=self.MAXLEN)
                self._hist[coin].append((now, price, acc_vol))
            except Exception:
                continue

    def get_signal(self, coin: str) -> dict | None:
        hist = self._hist.get(coin)
        if not hist or len(hist) < 4:
            return None

        snaps     = list(hist)
        now_ts, now_price, now_vol = snaps[-1]

        # ── 가격 변화: WINDOW_SEC 전 스냅샷과 비교 ──────────────────────────
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
            return None  # 저가 코인 제외 (노이즈 심함)

        price_chg = (now_price - old_price) / old_price
        if price_chg < PRICE_THRESH:
            return None

        # ── 거래 속도: 후반부 vs 전반부 비교 ────────────────────────────────
        mid    = len(snaps) // 2
        recent = snaps[mid:]
        older  = snaps[:mid]

        if len(recent) < 2 or len(older) < 2:
            return None

        r_vol  = recent[-1][2] - recent[0][2]
        r_time = max(recent[-1][0] - recent[0][0], 1)
        o_vol  = older[-1][2]  - older[0][2]
        o_time = max(older[-1][0]  - older[0][0],  1)

        if o_vol <= 0:
            return None

        r_rate   = r_vol / r_time
        o_rate   = o_vol / o_time
        vol_mult = r_rate / o_rate if o_rate > 0 else 0

        if vol_mult < VOLUME_MULT:
            return None

        return {
            "coin":      coin,
            "price_chg": price_chg,
            "vol_mult":  vol_mult,
            "price":     now_price,
            "elapsed":   elapsed,
        }

    def coins(self) -> list[str]:
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
    # 실제 잔고 확인 후 min(요청량, 실제잔고) 사용 - 소수점 오차 방지
    actual_bal = get_coin_balance(client, coin)
    if actual_bal < volume * 0.995:
        volume = actual_bal
    if volume <= 0:
        log.error(f"[{coin}] 매도 수량 0 - 스킵")
        return 0.0
    log.info(f"[{coin}] {reason} - 매도 {volume:.8f} (잔고={actual_bal:.8f})")
    for attempt in range(3):  # 최대 3회 재시도
        try:
            r     = client.market_sell(pos["market"], volume)
            uuid  = r.get("uuid")
            order = wait_for_order(client, uuid)
            if order.get("state") != "done":
                log.warning(f"[{coin}] 매도 미체결 재시도 {attempt+1}/3 UUID={uuid}")
                time.sleep(2)
                continue
            received = float(order.get("executed_funds", 0)) - float(order.get("paid_fee", 0))
            log.info(f"[{coin}] 매도 체결 | 수령={received:,.0f}원")
            return received
        except Exception as e:
            log.warning(f"[{coin}] 매도 재시도 {attempt+1}/3: {e}")
            time.sleep(3)
    # 3회 모두 실패
    notify.notify_error(f"{coin} 매도 실패! 수동 확인 필요 (사유: {reason})")
    return -1.0  # -1 = 매도 실패 sentinel (0과 구분)


# ── 트레일링 스탑 ─────────────────────────────────────────────────────────────

def monitor_trailing(client: BithumbClient, pos: dict) -> float:
    coin       = pos["coin"]
    entry      = pos["entry_price"]
    total_vol  = pos["volume"]
    total_cost = pos["cost"]
    highest    = entry
    phase      = 1
    sold_vol   = 0.0
    recv_krw   = 0.0
    half_vol   = round(total_vol * 0.5, 8)
    trail      = TRAIL_PCT
    reason     = "unknown"

    log.info(
        f"[{coin}] 모니터링 시작 | "
        f"진입={entry:,.0f}원 TP={TP_HALF*100:.0f}% 트레일={trail*100:.1f}%"
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
            f"[{coin}] {current:,.0f}원  PnL={pnl_pct*100:+.2f}%  "
            f"고점={highest:,.0f}원  스탑={trail_stop:,.0f}원"
        )

        if phase == 1 and pnl_pct >= TP_HALF:
            recv_krw += do_sell(client, pos, half_vol, f"1차익절 {pnl_pct*100:+.1f}%")
            sold_vol += half_vol
            phase    = 2
            trail    = TIGHT_TRAIL
            highest  = current
            log.info(f"[{coin}] 2단계 - 트레일 {trail*100:.1f}%로 조임")
            continue

        if current <= trail_stop:
            reason = f"트레일링스탑 {pnl_pct*100:+.1f}% (고점 {highest:,.0f}원 -{trail*100:.1f}%)"
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

    log.info("=== ALT 모멘텀 봇 시작 [공격 모드] ===")
    log.info(
        f"스캔={SCAN_SEC}s | 윈도우={WINDOW_SEC}s | "
        f"가격임계={PRICE_THRESH*100:.0f}% | 거래량배수={VOLUME_MULT:.0f}x | "
        f"진입={ALT_ENTRY_RATIO*100:.0f}% | TP={TP_HALF*100:.0f}% | 트레일={TRAIL_PCT*100:.1f}%"
    )

    daily_pnl      = 0.0
    daily_trades   = 0   # 통계용 카운터 (제한 없음)
    today          = date.today()
    active_pos     = None
    cooldown_end   = 0.0
    scan_count     = 0

    while True:
        try:
            if date.today() != today:
                log.info(f"날짜 변경 | 전일 ALT PnL: {daily_pnl:+,.0f}원 | 거래:{daily_trades}건")
                daily_pnl    = 0.0
                daily_trades = 0
                today        = date.today()

            if daily_pnl / capital <= daily_limit:
                log.warning("[ALT] 일일 손실 한도 - 매매 중단")
                time.sleep(300)
                continue

            # 가격 스냅샷 갱신 (포지션 있어도 계속 수집)
            try:
                ticker_all = client.get_ticker("ALL")
                tracker.update(ticker_all)
            except Exception as e:
                log.error(f"시세 조회 실패: {e}")
                time.sleep(SCAN_SEC)
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
            found = []
            for coin in tracker.coins():
                if coin in SKIP_COINS or coin in holdings:
                    continue
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

            # 거래량 배수 기준 최강 신호 선택
            best = max(found, key=lambda x: x["vol_mult"])
            coin = best["coin"]
            log.warning(
                f"*** [진입 신호] {coin} | "
                f"가격={best['price_chg']*100:+.1f}% | "
                f"거래량={best['vol_mult']:.1f}x | 오늘 {daily_trades+1}건 ***"
            )

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

        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"루프 오류: {e}")
            time.sleep(SCAN_SEC)


if __name__ == "__main__":
    run()
