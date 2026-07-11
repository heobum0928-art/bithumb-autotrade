"""
마진 숏 실전 트레이더 (margin_short_trader) — 거래량 폭발 급등주 숏.

검증(2026-07-11): 빗썸∩바이낸스마진숏 314코인 백테스트에서 모든 관문 통과 —
거래량 8~10배 폭발 + 1~2h +15~20% 급등 → 8h 무손절 숏.
2배 레버리지 정직계산(청산 3% 반영) 후 TE +11%/건(t2.9), 승률 73%, 상위3코인 제거·train/test 생존.
이 프로젝트 3개월 만의 첫 전관문 통과 엣지.

동작:
  ① 바이낸스 전 선물+마진 심볼 5분봉 폴링 → 거래량 VOL_MULT배 & 2h +PUMP% 감지
  ② margin_guard로 크로스마진 숏 진입(증거금 상한 내, 4중 관문)
  ③ HOLD_H 후 자동 청산(되사서 상환). 무손절(백테스트가 무손절 기준).
  ④ 손익 기록.

★ margin_guard OFF면 자동 dry. 실전은 data/margin_live_config.json arm 필요(현재 arm됨).
포지션 data/margin_short_pos.json | 기록 data/margin_short_trades.csv | 로그 logs/margin_short_trader.log
포트 47251. Run: python scripts/margin_short_trader.py
"""
import sys, os, atexit, time, json, csv, socket, logging, statistics
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
    try: _sock.bind(("127.0.0.1", 47251))
    except OSError: print("[ERROR] margin_short_trader 이미 실행 중 (포트 47251)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb import notify
from bithumb.margin_guard import MarginGuard, live_status, get_margin_usdt, load_config, get_borrowed

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [MSHORT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/margin_short_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"; BASE = "https://api.binance.com"
ENGINE = "mshort"
POLL_SEC = 300
PUMP_PCT = 20.0            # 2h(24봉) 상승률
LOOKBACK = 24
VOL_MULT = 10.0           # 거래량 폭발 배수 (백테스트 안전셀)
# 2026-07-11 업그레이드: 오토리서치 검증 — 8h무손절(2xEV+7.9%,청산3%) → 48h+40%스탑(2xEV+19.4%,청산0%).
# 되돌림이 며칠에 걸쳐 오므로 홀딩 연장이 최대 레버. 40%스탑은 2배 청산선(+50%) 안쪽이라 청산위험을
# 공짜로 0으로 제거(꼬리 삭제). train/test 부호일관·상위3제거·최고주제거 생존, 랜덤숏 벤치 -0.8% 대비 알파.
HOLD_H = 48
STOP_PCT = 40.0           # 진입가 대비 +40% 상승 시 손절(2배 청산선 +50% 안쪽)
COOLDOWN_H = 6
MARGIN_PER_TRADE = 100.0  # 증거금(상한과 동일). 실제 사용은 min(잔고,상한)

BUF_PATH = ROOT / "data" / "margin_short_buf.json"
POS_PATH = ROOT / "data" / "margin_short_pos.json"
TRADES_PATH = ROOT / "data" / "margin_short_trades.csv"

# 유니버스: 바이낸스 마진 대출가능 코인 전체 (2026-07-11 확장 — 빗썸 교집합 제한 제거)
# ★ 발견1: 백테스트 314개 중 실제 빌릴 수 있는 건 절반뿐(급등 소형알트는 대출재고 없어 -3045 거부).
#   재검증: 엣지는 오히려 대출가능 쪽이 큼 → 대출가능만 감시.
# ★ 발견2: 빗썸 교집합으로 좁힐 이유 없음(거래는 바이낸스에서만 함). 제한 풀면 177→210개,
#   신호 1.8→3.2건/주로 78%↑, 승률76%·청산2%(오히려 개선). 단 신규분 TEST 수익은 약해
#   전체 TE +31%→+17%로 희석 — 기대치는 낮추되 표본이 2배라 실전검증이 빨라지는 게 더 중요.
BORROWABLE_PATH = ROOT / "data" / "_borrowable_all.txt"
BORROWABLE_REFRESH_H = 6

def refresh_borrowable():
    """바이낸스 마진 숏가능 + 실제 대출재고 있는 코인 전체 (klines 보유 여부 무관)."""
    from bithumb.margin_guard import _signed
    try:
        pairs = _signed("GET", "/sapi/v1/margin/allPairs").json()
        cands = sorted(set(p["symbol"].replace("USDT", "") for p in pairs
                           if p.get("quote") == "USDT" and p.get("isSellAllowed") and p.get("isMarginTrade")))
    except Exception as e:
        log.warning(f"마진쌍 조회 실패: {e}"); return []
    ok = []
    for coin in cands:
        try:
            r = _signed("GET", "/sapi/v1/margin/maxBorrowable", {"asset": coin})
            if r.status_code == 200 and float(r.json().get("amount", 0)) > 0:
                ok.append(coin)
        except Exception:
            pass
        time.sleep(0.1)
    if ok:
        BORROWABLE_PATH.write_text("\n".join(ok), encoding="utf-8")
        log.info(f"대출가능 유니버스 갱신: {len(ok)}/{len(cands)}개")
    return ok

def load_borrowable():
    try:
        return [c for c in BORROWABLE_PATH.read_text(encoding="utf-8").split() if c]
    except Exception:
        return []

UNIVERSE = load_borrowable()   # 비면 main()의 첫 refresh가 채움


def _load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception: return d
def _save(p, o):
    tmp = Path(p).with_suffix(".tmp"); tmp.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8"); tmp.replace(p)


def all_prices():
    """현물+선물 통합 현재가 (마진은 현물가 기준)."""
    r = requests.get(f"{BASE}/api/v3/ticker/price", timeout=10)
    r.raise_for_status()
    return {x["symbol"]: float(x["price"]) for x in r.json()}


def volume_ok(sym):
    """급등 후보의 거래량 폭발 확인 (5m klines 1회)."""
    try:
        r = requests.get(f"{BASE}/api/v3/klines", params={"symbol": sym, "interval": "5m", "limit": 25}, timeout=8)
        if r.status_code != 200: return False, 0.0
        vl = [float(x[7]) for x in r.json()]
        if len(vl) < 21: return False, 0.0
        avg = sum(vl[-21:-1]) / 20
        if avg <= 0: return False, 0.0
        return vl[-1] / avg >= VOL_MULT, vl[-1] / avg
    except Exception:
        return False, 0.0


def log_trade(row):
    new = not TRADES_PATH.exists()
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry_time","exit_time","symbol","pump_2h","vol_mult",
                                           "entry_price","exit_price","margin_usdt","pnl_pct","pnl_usdt","live","reason"])
        if new: w.writeheader()
        w.writerow(row)


def main():
    global UNIVERSE
    buf = _load(BUF_PATH, {}); positions = _load(POS_PATH, {}); cooldown = {}
    last_refresh = 0.0
    ls = live_status()
    mode = "🔴실전" if (ls["enabled"] and ENGINE in ls["armed"]) else "🔵모의(dry)"
    log.info(f"마진숏 트레이더 시작 [{mode}] — 대출가능 {len(UNIVERSE)}코인, 거래량{VOL_MULT:.0f}배+2h+{PUMP_PCT:.0f}%→{HOLD_H}h숏+스탑{STOP_PCT:.0f}% "
             f"| 증거금상한 {ls['global_cap_usdt']}USDT {ls['leverage']}배 | 마진잔고 {get_margin_usdt():.1f}")
    try: notify.send(f"📉 마진숏 트레이더 시작 [{mode}] — 대출가능 {len(UNIVERSE)}코인, 증거금상한 {ls['global_cap_usdt']}USDT")
    except Exception: pass

    while True:
        try:
            now = time.time()
            # 대출가능 유니버스 주기 갱신 (재고가 수시로 바뀜 → 못 빌리는 코인에 주문 던지는 것 방지)
            if now - last_refresh >= BORROWABLE_REFRESH_H * 3600:
                last_refresh = now
                fresh = refresh_borrowable()
                if fresh: UNIVERSE = fresh
            prices = all_prices()
            guard = MarginGuard(ENGINE)

            # 1) 신호 탐지
            for coin in UNIVERSE:
                sym = f"{coin}USDT"
                px = prices.get(sym)
                if not px or px <= 0: continue
                b = buf.setdefault(sym, [])
                b.append([now, px])
                if len(b) > 30: del b[:len(b)-30]
                if sym in positions or cooldown.get(sym, 0) > now or len(b) < LOOKBACK:
                    continue
                past = b[-LOOKBACK][1]
                if past <= 0: continue
                ret2h = (px/past - 1) * 100
                if ret2h < PUMP_PCT:
                    continue
                vok, vr = volume_ok(sym)
                if not vok:
                    cooldown[sym] = now + COOLDOWN_H*3600
                    continue
                # 누적 노출 상한 확인 (48h 홀딩이라 동시다발 진입 가능 → 전체상한 초과 방지)
                open_margin = sum(p["margin"] for p in positions.values())
                gcap = load_config().get("global_cap_usdt", 0)
                if open_margin + MARGIN_PER_TRADE > gcap:
                    log.info(f"진입 보류 {sym}: 누적노출 {open_margin:.0f}+{MARGIN_PER_TRADE:.0f}>전체상한 {gcap} (기존 포지션 청산 대기)")
                    continue
                # 진입
                margin = min(MARGIN_PER_TRADE, get_margin_usdt())
                res = guard.open_short(coin, margin)
                cooldown[sym] = now + COOLDOWN_H*3600
                if res.get("live"):
                    positions[sym] = {"coin": coin, "entry_ts": now, "entry_price": res.get("price", px),
                                      "qty": res["qty"], "margin": margin, "pump": round(ret2h,1), "vr": round(vr,1),
                                      "exit_ts": now + HOLD_H*3600, "entry_iso": datetime.now(KST).isoformat(), "live": True}
                    log.warning(f"★실전 마진숏 진입 {sym} 2h+{ret2h:.0f}% 거래량{vr:.0f}배 증거금{margin:.0f} → {res['qty']}개")
                    try: notify.send(f"📉 마진숏 진입 {sym} 2h+{ret2h:.0f}% 거래량{vr:.0f}배 (증거금{margin:.0f}USDT)")
                    except Exception: pass
                else:
                    log.info(f"진입 dry/실패 {sym}: {res}")

            # 2) 청산: 40% 스탑(가격이 진입가+40% 상승 = 숏 손실) OR 48h 만기
            for sym in list(positions.keys()):
                pos = positions[sym]
                px = prices.get(sym, pos["entry_price"])
                stop_hit = px >= pos["entry_price"] * (1 + STOP_PCT/100)
                if not stop_hit and now < pos["exit_ts"]:
                    continue
                reason = f"스탑+{STOP_PCT:.0f}%" if stop_hit else f"{HOLD_H}h만기"
                cres = guard.close_short(pos["coin"])
                pnl_pct = (1 - px/pos["entry_price"])*100
                pnl_usdt = pos["margin"] * load_config().get("leverage",2) * (pnl_pct/100)
                if pos["live"]: guard.record_realized(pnl_usdt)
                log_trade(dict(entry_time=pos["entry_iso"], exit_time=datetime.now(KST).isoformat(), symbol=sym,
                               pump_2h=pos["pump"], vol_mult=pos["vr"], entry_price=pos["entry_price"], exit_price=px,
                               margin_usdt=pos["margin"], pnl_pct=round(pnl_pct,2), pnl_usdt=round(pnl_usdt,2),
                               live=pos["live"], reason=reason))
                log.warning(f"★마진숏 청산 {sym} @{px:g} {reason} pnl={pnl_pct:+.2f}%({pnl_usdt:+.2f}USDT) → {cres.get('live') and '실청산' or cres}")
                try: notify.send(f"📈 마진숏 청산 {sym} {reason} pnl={pnl_pct:+.1f}% ({pnl_usdt:+.1f}USDT)")
                except Exception: pass
                del positions[sym]

            _save(BUF_PATH, buf); _save(POS_PATH, positions)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
