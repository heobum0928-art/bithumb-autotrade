"""
Altcoin Momentum Monitor  (Strategy D)

Signal conditions (both must be true):
  1. Current 5-min candle price change >= +3%  (price_thresh)
  2. Current 5-min candle volume >= 5x avg of previous 5 candles  (volume_mult)

Flow:
  Every 60s -> get all KRW tickers -> pre-filter active coins
           -> check 5-min candles -> detect signal
           -> market buy -> trailing stop exit

Run: python scripts/alt_monitor.py  (alongside auto_trade.py)
"""
import sys
import time
import logging
import yaml
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
SCAN_INTERVAL   = 60     # 전체 스캔 주기 (초)
PRICE_THRESH    = 0.03   # 5분봉 가격 변화 최소 +3%
VOLUME_MULT     = 5.0    # 현재봉 거래대금 >= 이전 5봉 평균의 5배
CANDLE_COUNT    = 6      # 봉 개수 (1 현재 + 5 이전)
ALT_ENTRY_RATIO = 0.25   # 가용 KRW의 25% 진입
TP_HALF         = 0.05   # 1차 익절 +5%
TRAIL_PCT       = 0.03   # 트레일링 폭 3%
TIGHT_TRAIL     = 0.018  # 분할 후 조인 트레일 1.8%
DAILY_LIMIT_PCT = -0.05  # 일일 손실 한도
MIN_KRW         = 5001   # 최소 주문 금액
PRE_FILTER_24H  = 2.0    # 24H 변동률 사전 필터 (절대값 %)

# 대형 코인 제외 (변동성 낮고 경쟁 봇 많음)
SKIP_COINS = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}


# ── 설정 로드 ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


# ── 잔고 / 가격 ───────────────────────────────────────────────────────────────

def get_available_krw(client: BithumbClient) -> float:
    for a in client.get_accounts():
        if a["currency"] == "KRW":
            return float(a["balance"])
    return 0.0


def get_price(client: BithumbClient, coin: str) -> float:
    try:
        return float(client.get_ticker(coin)["closing_price"])
    except Exception:
        return 0.0


# ── 신호 스캔 ─────────────────────────────────────────────────────────────────

def scan_signals(client: BithumbClient, skip_coin: str = None) -> list[dict]:
    """전체 KRW 마켓 스캔 → 모멘텀 신호 목록 반환 (강한 순 정렬)."""
    signals = []

    try:
        ticker_all = client.get_ticker("ALL")
    except Exception as e:
        log.error(f"전체 시세 조회 실패: {e}")
        return []

    for coin, data in ticker_all.items():
        if coin == "date":
            continue
        if coin in SKIP_COINS:
            continue
        if skip_coin and coin == skip_coin:
            continue

        try:
            rate_24h = abs(float(data.get("fluctate_rate_24H", 0)))
            if rate_24h < PRE_FILTER_24H:
                continue  # 오늘 움직임 없는 코인 제외

            candles = client.get_candles(f"KRW-{coin}", unit=5, count=CANDLE_COUNT)
            if len(candles) < 2:
                continue

            cur  = candles[0]
            prev = candles[1:]

            open_p  = cur["opening_price"]
            trade_p = cur["trade_price"]
            if open_p <= 0:
                continue

            price_chg = (trade_p - open_p) / open_p
            if price_chg < PRICE_THRESH:
                continue

            cur_vol  = cur["candle_acc_trade_price"]
            avg_vol  = sum(c["candle_acc_trade_price"] for c in prev) / len(prev)
            if avg_vol <= 0 or cur_vol < avg_vol * VOLUME_MULT:
                continue

            vol_mult = cur_vol / avg_vol
            signals.append({
                "coin":        coin,
                "market":      f"KRW-{coin}",
                "price_chg":   price_chg,
                "vol_mult":    vol_mult,
                "price":       trade_p,
            })
            log.info(
                f"  [신호] {coin} | 가격변화={price_chg*100:+.1f}% "
                f"| 거래량배수={vol_mult:.1f}x | 현재={trade_p:,.0f}원"
            )
        except Exception:
            continue

    # 거래량 배수 높은 순 정렬
    signals.sort(key=lambda x: x["vol_mult"], reverse=True)
    return signals


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
        r = client.market_buy(market, buy_krw)
        uuid = r.get("uuid")
        if not uuid:
            log.error(f"[{coin}] UUID 없음")
            return None
        order = wait_for_order(client, uuid)
        if order.get("state") != "done":
            log.warning(f"[{coin}] 매수 미체결 - 취소 시도")
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
        entry_price = funds / vol
        log.info(f"[{coin}] 매수 체결 | 수량={vol:.8f} 단가={entry_price:,.0f}원")
        notify.notify_buy(coin, entry_price, vol, funds + fee)
        return {
            "coin":       coin,
            "market":     market,
            "volume":     vol,
            "entry_price": entry_price,
            "cost":       funds + fee,
            "entered_at": datetime.now(),
        }
    except Exception as e:
        log.error(f"[{coin}] 매수 실패: {e}")
        return None


def do_sell(client: BithumbClient, pos: dict, volume: float, reason: str) -> float:
    coin   = pos["coin"]
    market = pos["market"]
    log.info(f"[{coin}] {reason} - 매도 {volume:.8f}")
    try:
        r    = client.market_sell(market, volume)
        uuid = r.get("uuid")
        order = wait_for_order(client, uuid)
        if order.get("state") != "done":
            log.error(f"[{coin}] 매도 미체결! UUID={uuid}")
            return 0.0
        received = float(order.get("executed_funds", 0)) - float(order.get("paid_fee", 0))
        log.info(f"[{coin}] 매도 체결 | 수령={received:,.0f}원 [{reason}]")
        return received
    except Exception as e:
        log.error(f"[{coin}] 매도 실패: {e}")
        return 0.0


# ── 트레일링 스탑 모니터 ──────────────────────────────────────────────────────

def monitor_trailing(client: BithumbClient, pos: dict) -> float:
    coin       = pos["coin"]
    entry      = pos["entry_price"]
    total_vol  = pos["volume"]
    total_cost = pos["cost"]

    highest  = entry
    phase    = 1
    sold_vol = 0.0
    recv_krw = 0.0
    half_vol = round(total_vol * 0.5, 8)
    trail    = TRAIL_PCT
    reason   = "unknown"

    log.info(
        f"[{coin}] 트레일링 모니터 시작 | "
        f"진입={entry:,.0f}원 1차익절={TP_HALF*100:.1f}% 트레일={trail*100:.1f}%"
    )

    while True:
        time.sleep(2)
        current = get_price(client, coin)
        if current <= 0:
            continue

        if current > highest:
            highest = current

        trail_stop = highest * (1 - trail)
        pnl_pct    = (current - entry) / entry
        remaining  = total_vol - sold_vol

        log.info(
            f"[{coin}] 현재={current:,.0f}원 PnL={pnl_pct*100:+.2f}% "
            f"고점={highest:,.0f}원 스탑={trail_stop:,.0f}원 잔량={remaining:.8f}"
        )

        # 1차 익절
        if phase == 1 and pnl_pct >= TP_HALF:
            recv_krw += do_sell(client, pos, half_vol, f"1차익절 {pnl_pct*100:+.1f}%")
            sold_vol += half_vol
            phase  = 2
            trail  = TIGHT_TRAIL
            highest = current
            log.info(f"[{coin}] 2단계 - 트레일 조임: {trail*100:.1f}%")
            continue

        # 트레일링 스탑
        if current <= trail_stop:
            reason = (
                f"트레일링스탑 {pnl_pct*100:+.1f}% "
                f"(고점 {highest:,.0f}원 → -{trail*100:.1f}%)"
            )
            recv_krw += do_sell(client, pos, total_vol - sold_vol, reason)
            break

    pnl     = recv_krw - total_cost
    pnl_pct = pnl / total_cost * 100
    log.info(f"[{coin}] 포지션 종료 | PnL={pnl:+,.0f}원 ({pnl_pct:+.2f}%)")
    notify.notify_sell(coin, pnl, pnl_pct, reason)

    exit_price = get_price(client, coin)
    try:
        log_trade(
            coin=coin, market=pos["market"],
            entry_price=entry, exit_price=exit_price,
            volume=total_vol, cost_krw=total_cost,
            received_krw=recv_krw,
            exit_reason=reason,
            entered_at=pos["entered_at"], exited_at=datetime.now(),
        )
    except Exception as e:
        log.error(f"[DB] 저장 실패: {e}")

    return pnl


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run():
    cfg        = load_config()
    capital    = cfg["trading"]["capital_krw"]
    daily_limit = cfg["trading"].get("daily_loss_limit_pct", DAILY_LIMIT_PCT)

    init_db()
    client = BithumbClient()

    log.info("=== 알트코인 모멘텀 모니터 시작 (전략 D) ===")
    log.info(
        f"가격임계={PRICE_THRESH*100:.0f}% | 거래량배수={VOLUME_MULT:.0f}x | "
        f"1차익절={TP_HALF*100:.0f}% | 트레일={TRAIL_PCT*100:.0f}%"
    )

    daily_pnl  = 0.0
    today      = date.today()
    active_pos = None   # 현재 보유 포지션

    while True:
        try:
            # 날짜 리셋
            if date.today() != today:
                log.info(f"날짜 변경 | 전일 ALT PnL: {daily_pnl:+,.0f}원")
                daily_pnl = 0.0
                today = date.today()

            # 일일 손실 한도
            if daily_pnl / capital <= daily_limit:
                log.warning(f"[ALT] 일일 손실 한도 도달 - 매매 중단")
                time.sleep(300)
                continue

            # 포지션 보유 중이면 스캔 안 함 (모니터링은 별도 스레드 없이 순차 처리)
            if active_pos is not None:
                time.sleep(SCAN_INTERVAL)
                continue

            log.info(f"[스캔] 전체 KRW 마켓 모멘텀 신호 탐색...")
            signals = scan_signals(client)

            if not signals:
                log.info("[스캔] 신호 없음")
                time.sleep(SCAN_INTERVAL)
                continue

            # 가장 강한 신호 선택
            best = signals[0]
            coin = best["coin"]
            log.warning(
                f"*** [모멘텀 신호] {coin} | "
                f"가격변화={best['price_chg']*100:+.1f}% | "
                f"거래량배수={best['vol_mult']:.1f}x ***"
            )

            # 진입 금액 결정
            avail   = get_available_krw(client)
            buy_krw = min(capital * ALT_ENTRY_RATIO, avail * 0.99)
            if buy_krw < MIN_KRW:
                log.warning(f"KRW 잔고 부족: {avail:,.0f}원")
                time.sleep(SCAN_INTERVAL)
                continue

            # 매수
            pos = do_buy(client, coin, buy_krw)
            if not pos:
                time.sleep(SCAN_INTERVAL)
                continue

            active_pos = pos

            # 트레일링 모니터 (블로킹 - 포지션 청산까지 루프 대기)
            pnl = monitor_trailing(client, pos)
            daily_pnl += pnl
            active_pos = None
            log.info(f"[ALT] 오늘 누적 PnL: {daily_pnl:+,.0f}원")

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"루프 오류: {e}")
            time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run()
