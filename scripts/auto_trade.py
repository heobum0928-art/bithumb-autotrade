"""
Bithumb New Listing Auto-Trader  (Enhanced v2)

Strategy (based on community best practices):
  1. Detect new KRW listing via API 2.0 market poll
  2. Wait 30s cooldown — avoid initial chaos / manipulated first ticks
  3. Confirm minimum volume before entering
  4. Enter with market buy (capital × entry_ratio)
  5. Trailing stop: SL moves up with price, always -SL% from highest seen
  6. Partial TP: sell 50% at +TP_HALF, let rest trail with tighter stop
  7. Daily loss limit: halt when daily PnL ≤ -5%

Run: python scripts/auto_trade.py
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

# ── logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/auto_trade.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── config helpers ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


def get_available_krw(client: BithumbClient) -> float:
    for a in client.get_accounts():
        if a["currency"] == "KRW":
            return float(a["balance"])
    return 0.0


# ── price & volume helpers ─────────────────────────────────────────────────────

def get_ticker_data(client: BithumbClient, coin: str) -> dict:
    try:
        return client.get_ticker(coin)
    except Exception:
        return {}


def get_price(client: BithumbClient, coin: str) -> float:
    d = get_ticker_data(client, coin)
    try:
        return float(d["closing_price"])
    except (KeyError, ValueError, TypeError):
        return 0.0


def get_24h_volume_krw(client: BithumbClient, coin: str) -> float:
    d = get_ticker_data(client, coin)
    try:
        return float(d["acc_trade_value_24H"])
    except (KeyError, ValueError, TypeError):
        return 0.0


def wait_for_first_price(client: BithumbClient, coin: str, timeout: int = 60) -> float:
    """Wait until a valid closing price appears."""
    log.info(f"[{coin}] 첫 체결가 대기 (최대 {timeout}초)...")
    for i in range(timeout):
        p = get_price(client, coin)
        if p > 0:
            log.info(f"[{coin}] 첫 체결가: {p:,.0f}원 ({i+1}초 경과)")
            return p
        time.sleep(1)
    log.warning(f"[{coin}] 첫 체결가 미확인 — 진입 포기")
    return 0.0


def wait_for_min_volume(client: BithumbClient, coin: str,
                        min_vol_krw: float, timeout: int = 120) -> bool:
    """Wait until accumulated volume exceeds min_vol_krw."""
    if min_vol_krw <= 0:
        return True
    log.info(f"[{coin}] 최소 거래량({min_vol_krw/1e6:.1f}M KRW) 대기...")
    for i in range(timeout):
        vol = get_24h_volume_krw(client, coin)
        if vol >= min_vol_krw:
            log.info(f"[{coin}] 거래량 충족: {vol/1e6:.1f}M KRW ({i+1}초)")
            return True
        time.sleep(1)
    log.warning(f"[{coin}] {timeout}초 내 거래량 미달 — 진입 포기")
    return False


# ── order helpers ──────────────────────────────────────────────────────────────

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
            log.warning(f"[{coin}] 매수 미체결 — 취소 시도")
            try:
                client.cancel_order(uuid)
            except Exception:
                pass
            return None
        vol = float(order.get("executed_volume", 0))
        funds = float(order.get("executed_funds", 0))
        fee = float(order.get("paid_fee", 0))
        if vol <= 0:
            return None
        entry_price = funds / vol
        log.info(f"[{coin}] 매수 체결 | 수량={vol:.8f} 단가={entry_price:,.0f}원 수수료={fee:.2f}원")
        notify.notify_buy(coin, entry_price, vol, funds + fee)
        return {
            "coin": coin, "market": market,
            "volume": vol, "entry_price": entry_price,
            "cost": funds + fee,
            "entered_at": datetime.now(),
        }
    except Exception as e:
        log.error(f"[{coin}] 매수 실패: {e}")
        return None


def do_sell(client: BithumbClient, pos: dict, volume: float, reason: str) -> float:
    """Sell `volume` coins. Returns net KRW received."""
    coin = pos["coin"]
    market = pos["market"]
    log.info(f"[{coin}] {reason} — 매도 {volume:.8f}")
    try:
        r = client.market_sell(market, volume)
        uuid = r.get("uuid")
        order = wait_for_order(client, uuid)
        if order.get("state") != "done":
            log.error(f"[{coin}] 매도 미체결! 수동 확인 필요 UUID={uuid}")
            return 0.0
        received = float(order.get("executed_funds", 0)) - float(order.get("paid_fee", 0))
        log.info(f"[{coin}] 매도 체결 | 수령={received:,.0f}원 [{reason}]")
        return received
    except Exception as e:
        log.error(f"[{coin}] 매도 실패: {e}")
        return 0.0


# ── trailing stop monitor ──────────────────────────────────────────────────────

def monitor_trailing(client: BithumbClient, pos: dict,
                     tp_half: float, trailing_pct: float,
                     tight_trailing_pct: float) -> float:
    """
    Trailing stop exit logic.
      - Phase 1: trail at -trailing_pct from highest. Partial sell at +tp_half.
      - Phase 2: after partial sell, trail at -tight_trailing_pct (tighter).
    Returns total PnL (KRW).
    """
    coin = pos["coin"]
    entry = pos["entry_price"]
    total_vol = pos["volume"]
    total_cost = pos["cost"]

    highest = entry
    phase = 1  # 1 = full position, 2 = partial sold
    sold_vol = 0.0
    received_krw = 0.0
    half_vol = round(total_vol * 0.5, 8)

    trail = trailing_pct
    log.info(
        f"[{coin}] 트레일링 모니터 시작 | "
        f"진입가={entry:,.0f}원 1차익절={tp_half*100:.1f}% 트레일={trail*100:.1f}%"
    )

    while True:
        time.sleep(2)
        current = get_price(client, coin)
        if current <= 0:
            continue

        if current > highest:
            highest = current

        trail_stop = highest * (1 - trail)
        pnl_pct = (current - entry) / entry

        remaining_vol = total_vol - sold_vol
        log.info(
            f"[{coin}] 현재={current:,.0f}원 "
            f"PnL={pnl_pct*100:+.2f}% 고점={highest:,.0f}원 "
            f"트레일스탑={trail_stop:,.0f}원 잔량={remaining_vol:.8f}"
        )

        # --- Phase 1: partial TP ---
        if phase == 1 and pnl_pct >= tp_half:
            recv = do_sell(client, pos, half_vol, f"1차익절 {pnl_pct*100:+.1f}%")
            received_krw += recv
            sold_vol += half_vol
            phase = 2
            trail = tight_trailing_pct  # tighten stop after partial exit
            highest = current            # reset high so trailing is tighter
            log.info(f"[{coin}] 2단계 진입 — 트레일 조임: {trail*100:.1f}%")
            continue

        # --- Trailing stop hit ---
        if current <= trail_stop:
            reason = (
                f"트레일링스탑 {pnl_pct*100:+.1f}% "
                f"(고점 {highest:,.0f}원 → -{trail*100:.1f}%)"
            )
            remaining_vol = total_vol - sold_vol
            recv = do_sell(client, pos, remaining_vol, reason)
            received_krw += recv
            break

    pnl = received_krw - total_cost
    pnl_pct_final = pnl / total_cost * 100
    log.info(f"[{coin}] 포지션 종료 | PnL={pnl:+,.0f}원 ({pnl_pct_final:+.2f}%)")
    notify.notify_sell(coin, pnl, pnl_pct_final, reason if 'reason' in dir() else "종료")

    exit_price = get_price(client, coin)
    final_reason = reason if 'reason' in dir() else "unknown"
    try:
        log_trade(
            coin=coin, market=pos["market"],
            entry_price=entry, exit_price=exit_price,
            volume=total_vol, cost_krw=total_cost,
            received_krw=received_krw,
            exit_reason=final_reason,
            entered_at=pos["entered_at"], exited_at=datetime.now(),
        )
    except Exception as e:
        log.error(f"[DB] 저장 실패: {e}")

    return pnl


# ── main loop ──────────────────────────────────────────────────────────────────

def run():
    cfg = load_config()
    t = cfg["trading"]
    m = cfg["monitor"]

    capital       = t["capital_krw"]
    entry_ratio   = t["entry_ratio"]
    tp_half       = t["take_profit_pct"]          # 첫 익절 기준 (e.g. 0.07)
    sl_trail      = abs(t["stop_loss_pct"])        # 트레일 폭 (e.g. 0.03)
    tight_trail   = sl_trail * 0.6                 # 분할 후 조인 트레일 (e.g. 0.018)
    daily_limit   = t["daily_loss_limit_pct"]
    poll_sec      = m["poll_interval_sec"]
    min_vol_krw   = m.get("min_volume_krw", 5_000_000)  # 기본 500만원 거래량 확인
    entry_delay   = m.get("entry_delay_sec", 30)         # 첫 체결 후 쿨다운

    init_db()
    client = BithumbClient()

    log.info("=== 빗썸 신규상장 자동매매 v2 시작 ===")
    log.info(
        f"자본={capital:,}원 | 진입={entry_ratio*100:.0f}% | "
        f"1차익절={tp_half*100:.1f}% | 트레일={sl_trail*100:.1f}% | "
        f"쿨다운={entry_delay}s | 최소거래량={min_vol_krw/1e6:.1f}M"
    )

    def krw_markets() -> set[str]:
        return {m2["market"] for m2 in client.get_markets() if m2["market"].startswith("KRW-")}

    known: set[str] = krw_markets()
    log.info(f"초기 KRW 마켓: {len(known)}개")

    daily_pnl = 0.0
    today = date.today()
    active = False

    while True:
        try:
            if date.today() != today:
                log.info(f"날짜 변경 | 전일 PnL: {daily_pnl:+,.0f}원")
                daily_pnl = 0.0
                today = date.today()
                active = False

            if daily_pnl / capital <= daily_limit:
                log.warning(f"일일 손실 한도 도달 ({daily_pnl:+,.0f}원) — 매매 중단")
                time.sleep(300)
                continue

            if active:
                time.sleep(poll_sec)
                continue

            time.sleep(poll_sec)
            current: set[str] = krw_markets()
            new_markets = current - known

            if new_markets:
                for market in sorted(new_markets):
                    coin = market.split("-")[1]
                    log.warning(f"*** [신규 상장 감지] {market} ***")

                    # 1. 첫 체결가 대기
                    first_price = wait_for_first_price(client, coin, timeout=60)
                    if first_price <= 0:
                        continue
                    notify.notify_detected(coin, first_price)

                    # 2. 쿨다운 (초기 혼란 회피)
                    log.info(f"[{coin}] {entry_delay}초 쿨다운 대기...")
                    time.sleep(entry_delay)

                    # 3. 거래량 확인
                    if not wait_for_min_volume(client, coin, min_vol_krw, timeout=120):
                        continue

                    # 4. 진입 금액 결정
                    avail = get_available_krw(client)
                    buy_krw = min(capital * entry_ratio, avail * 0.99)
                    if buy_krw < 5001:
                        log.warning(f"KRW 잔고 부족: {avail:,.0f}원")
                        continue

                    # 5. 매수
                    active = True
                    pos = do_buy(client, coin, buy_krw)
                    if not pos:
                        active = False
                        continue

                    # 6. 트레일링 모니터 + 분할 익절
                    pnl = monitor_trailing(
                        client, pos,
                        tp_half=tp_half,
                        trailing_pct=sl_trail,
                        tight_trailing_pct=tight_trail,
                    )
                    daily_pnl += pnl
                    active = False
                    log.info(f"오늘 누적 PnL: {daily_pnl:+,.0f}원")

                known = current
            else:
                known = current

        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"루프 오류: {e}")
            time.sleep(poll_sec)


if __name__ == "__main__":
    run()
