"""
레버리지 코어 — BTC 200일선 타이밍 + 2배 레버리지 (바이낸스 무기한선물, 모의 추적).

배경(2026-07-09): 유일하게 검증된 엣지(BTC/ETH 200일선, +34.8% vs HODL -1.2%)에
레버리지를 얹어 배수를 키우자는 결정. 빗썸은 레버리지 미지원 → 바이낸스 BTCUSDT
무기한선물 시세로 모의 추적. 신호는 core_trader.py와 100% 동일(SMA50/200, 1%밴드).

★ 안전 레버리지 산출 근거 (800일 데이터, 보유구간 6개 중 최악):
  2024-11-17~2025-03-09(113일) 보유 중 -24.1% 낙폭이 관측 최악치.
  표본 6개뿐이라(과거 미관측 -50%급 폭락 가능성 배제 못 함) 레버리지 2배로 제한
  — 2배면 과거 최악치(-24%)의 2배(-48%)까지 버팀, 그 이상(-50%대)에서는 청산 위험.
  3배 이상은 과거 최악치만으로도 청산에 근접해 채택 안 함.

포지션 로직: FULL(200일선 위)=자본 100% 증거금×2배 레버리지(명목노출 200%),
SCOUT(50일선 위)=자본 30%×2배(명목노출 60%), 둘 다 아래=CASH.
청산위험 경보: 미실현손실이 증거금의 70% 도달 시 CRITICAL 알림(청산선 근접).
펀딩비: 바이낸스 실시간 펀딩레이트를 8시간마다 정산 반영.

⚠️ 현재 100% 모의(노셔널). 바이낸스 API 키 연동 전까지 실주문 불가능 — 계좌·키 준비되면
별도로 live_guard 스타일 실행가드 추가 예정. 절대 이 상태로 실거래 안 나감.
상태: data/core_leveraged_state.json | 로그: logs/core_leveraged.log
Run: python scripts/core_leveraged.py
"""
import sys, os, atexit, time, json, socket, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
KST = timezone(timedelta(hours=9))

_sock = None
def _single():
    global _sock
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM); _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try: _sock.bind(("127.0.0.1", 47249))
    except OSError: print("[ERROR] core_leveraged 이미 실행 중 (포트 47249)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb import notify
from bithumb.binance_guard import BinanceGuard, live_status as bn_live_status, get_futures_usdt, get_position

ENGINE = "core_lev"

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [CORE-LEV] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/core_leveraged.log", encoding="utf-8")])
log = logging.getLogger(__name__)

LEVERAGE = 2.0
NOTIONAL_KRW = 1_000_000     # 모의 노셔널 자본 (core_trader.py와 동일선상 비교용)
SCOUT_FRAC, FULL_FRAC = 0.30, 1.0
SMA_FAST, SMA_SLOW = 50, 200
BAND = 0.01
COST = 0.0004                # 바이낸스 선물 테이커 0.04%/사이드 근사
LIQ_WARN_FRAC = 0.70         # 증거금의 70% 손실 시 경보
CHECK_SEC = 1800             # 30분 (일봉 신호라 충분, funding은 별도 8h 정산)
FUNDING_INTERVAL_SEC = 8 * 3600

BTC_FILE = ROOT / "data" / "candles_daily" / "BTC_1d.json"
STATE = ROOT / "data" / "core_leveraged_state.json"
FAPI = "https://fapi.binance.com"


def sma_signal():
    """일봉 BTC(빗썸 KRW 기준)로 SMA50/200 신호 계산 — core_trader.py와 동일 신호."""
    try:
        cl = [float(x["trade_price"]) for x in json.loads(BTC_FILE.read_text(encoding="utf-8"))]
        if len(cl) < SMA_SLOW: return None
        cur = cl[-1]; s50 = sum(cl[-SMA_FAST:]) / SMA_FAST; s200 = sum(cl[-SMA_SLOW:]) / SMA_SLOW
        return cur > s50 * (1 + BAND), cur > s200 * (1 + BAND)
    except Exception as e:
        log.warning(f"신호 조회 실패: {e}"); return None


def binance_price() -> float:
    r = requests.get(f"{FAPI}/fapi/v1/ticker/price", params={"symbol": "BTCUSDT"}, timeout=8)
    r.raise_for_status()
    return float(r.json()["price"])


def binance_funding_rate() -> float:
    """가장 최근 실현 펀딩레이트(비율, 8시간당)."""
    r = requests.get(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": "BTCUSDT"}, timeout=8)
    r.raise_for_status()
    return float(r.json().get("lastFundingRate", 0) or 0)


def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"state": "CASH", "equity": float(NOTIONAL_KRW), "margin_frac": 0.0,
            "entry_price": 0.0, "last_price": 0.0, "last_funding_ts": 0.0}


def save_state(s):
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=2), encoding="utf-8"); os.replace(tmp, STATE)


def mark_to_market(s, price):
    """가격 변동을 레버리지 배율만큼 증거금에 반영. margin_frac=0이면 변화없음(현금)."""
    if s["margin_frac"] <= 0 or s["last_price"] <= 0:
        s["last_price"] = price
        return
    price_ret = price / s["last_price"] - 1
    notional_frac = s["margin_frac"] * LEVERAGE
    pnl = s["equity"] * notional_frac * price_ret
    s["equity"] += pnl
    s["last_price"] = price

    margin_krw = s["equity"] * s["margin_frac"]  # 근사(엄밀히는 진입시점 증거금 기준이나 단순화)
    unrealized_from_entry = (price / s["entry_price"] - 1) if s["entry_price"] > 0 else 0
    loss_frac_of_margin = -unrealized_from_entry * LEVERAGE  # 양수면 손실중
    if loss_frac_of_margin >= LIQ_WARN_FRAC:
        # ★ 모의(paper) 경로 전용 — 실제 포지션이 아니므로 텔레그램 알림은 생략, 로그만 (실전에서만 알림 원칙)
        msg = (f"🚨 청산위험 경보(모의) — 증거금의 {loss_frac_of_margin*100:.0f}% 손실 "
               f"(진입가 {s['entry_price']:,.0f} → 현재 {price:,.0f}, {unrealized_from_entry*100:+.1f}%)")
        log.error(msg)


def apply_funding(s, price):
    now = time.time()
    if s["margin_frac"] <= 0:
        s["last_funding_ts"] = now
        return
    if s["last_funding_ts"] <= 0:
        s["last_funding_ts"] = now
        return
    if now - s["last_funding_ts"] < FUNDING_INTERVAL_SEC:
        return
    try:
        rate = binance_funding_rate()
    except Exception as e:
        log.warning(f"펀딩레이트 조회 실패: {e}"); return
    notional_frac = s["margin_frac"] * LEVERAGE
    cost = s["equity"] * notional_frac * rate  # 롱이 펀딩 지불(+rate) 또는 수취(-rate)
    s["equity"] -= cost
    s["last_funding_ts"] = now
    log.info(f"펀딩 정산 rate={rate*100:+.4f}% notional_frac={notional_frac:.2f} → {-cost:+,.0f}원")


def rebalance(s, target_frac, price, reason):
    old_frac = s["margin_frac"]
    if target_frac == old_frac:
        return False
    cost_krw = s["equity"] * abs(target_frac - old_frac) * LEVERAGE * COST
    s["equity"] -= cost_krw
    s["margin_frac"] = target_frac
    s["entry_price"] = price if target_frac > 0 else 0.0
    s["last_price"] = price
    log.warning(f"리밸런싱(모의) → {reason}: 증거금비중 {target_frac*100:.0f}%(명목노출 {target_frac*LEVERAGE*100:.0f}%) "
                f"@{price:,.2f} 비용{-cost_krw:,.0f}원 | 모의자산 {s['equity']:,.0f}원 "
                f"({(s['equity']/NOTIONAL_KRW-1)*100:+.1f}%)")
    # ★ 모의 리밸런싱은 실거래가 아니므로 텔레그램 알림 생략 (실전에서만 알림 원칙, 로그로만 확인)
    return True


def live_rebalance(guard, target_frac, price):
    """실전 모드 — 목표 명목노출까지 가드 통해 실주문.
    증거금 = min(선물잔고, 엔진상한) × target_frac. 명목 = 증거금 × LEVERAGE.
    ★ 잔고가 상한보다 커도 상한만큼만 사용(소액검증 안전) — 상한이 진입을 막지 않고 규모만 제한."""
    from bithumb.binance_guard import load_config
    usdt = get_futures_usdt()
    if usdt <= 0:
        log.warning("[LIVE] 선물잔고 0 또는 조회실패 — 거래 보류"); return
    cap = load_config().get("engine_caps_usdt", {}).get(ENGINE, 0)
    margin_base = min(usdt, cap)                    # 상한 안으로 제한
    target_notional = margin_base * target_frac * LEVERAGE
    res = guard.rebalance_long(target_notional)
    log.warning(f"[LIVE] 코어 목표명목 {target_notional:.1f} USDT"
                f"(증거금 min(잔고{usdt:.0f},상한{cap})×{target_frac:.0%}×{LEVERAGE:.0f}배) → {res}")
    # ★ 실제 주문이 나간 경우에만 알림(실전에서만 알림 원칙) — 스킵/dry는 로그만
    if res.get("live"):
        try:
            notify.send(f"[CORE-LEV] ★실전 리밸런싱 {target_frac:.0%}×{LEVERAGE:.0f}배 명목{target_notional:.0f}USDT @{price:,.0f}")
        except Exception: pass


def main():
    s = load_state()
    ls = bn_live_status()
    _live = bool(ls.get("enabled")) and ENGINE in ls.get("armed", [])
    mode = f"🔴실전(바이낸스, {LEVERAGE:.0f}배)" if _live else "🔵순수모의(실주문 불가)"
    log.info(f"레버리지 코어 시작 [{mode}] — SMA{SMA_FAST}>SCOUT30%×{LEVERAGE:.0f}배 / "
             f"SMA{SMA_SLOW}>FULL100%×{LEVERAGE:.0f}배 | 상태={s['state']} 자산={s['equity']:,.0f}원 "
             f"| 가드 enabled={ls.get('enabled')} armed={ls.get('armed')}")
    try:
        notify.send(f"[CORE-LEV] 레버리지코어 시작 — {mode}")
    except Exception: pass

    while True:
        try:
            sig = sma_signal()
            if sig is None:
                time.sleep(CHECK_SEC); continue
            above50, above200 = sig
            try:
                price = binance_price()
            except Exception as e:
                log.warning(f"바이낸스 시세 조회 실패: {e}"); time.sleep(CHECK_SEC); continue

            target_frac = FULL_FRAC if above200 else (SCOUT_FRAC if above50 else 0.0)
            new_state = "FULL" if above200 else ("SCOUT" if above50 else "CASH")

            ls = bn_live_status()
            live = bool(ls.get("enabled")) and ENGINE in ls.get("armed", [])

            if live:   # ★ 실전 (가드 armed) — 바이낸스 실주문
                guard = BinanceGuard(ENGINE)
                if new_state != s["state"]:
                    live_rebalance(guard, target_frac, price)
                    s["state"] = new_state; save_state(s)
                else:
                    pos = get_position()
                    log.info(f"[LIVE] 유지 {s['state']} | BTCUSDT {price:,.2f} | 실포지션 {pos['amt']:.4f}BTC "
                             f"(명목 {pos['notional']:.1f}USDT, 미실현 {pos['unrealized']:+.2f})")
            else:      # 모의
                mark_to_market(s, price)
                apply_funding(s, price)
                if new_state != s["state"]:
                    rebalance(s, target_frac, price, f"{s['state']}→{new_state}")
                    s["state"] = new_state
                else:
                    log.info(f"유지 {s['state']} | BTCUSDT {price:,.2f} | 모의자산 {s['equity']:,.0f}원 "
                             f"({(s['equity']/NOTIONAL_KRW-1)*100:+.1f}%)")
                save_state(s)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CHECK_SEC)


if __name__ == "__main__":
    main()
