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
HOLD_H = 8
COOLDOWN_H = 6
MARGIN_PER_TRADE = 100.0  # 증거금(상한과 동일). 실제 사용은 min(잔고,상한)

BUF_PATH = ROOT / "data" / "margin_short_buf.json"
POS_PATH = ROOT / "data" / "margin_short_pos.json"
TRADES_PATH = ROOT / "data" / "margin_short_trades.csv"

# 유니버스: 마진 숏 가능 코인 목록 (수집시 저장해둔 spot+perp klines 기준)
UNIVERSE = sorted(set(
    [os.path.basename(f).replace("USDT_5m.json", "") for f in (ROOT / "data" / "binance_klines").glob("*_5m.json")] +
    [os.path.basename(f).replace("USDT_5m.json", "") for f in (ROOT / "data" / "binance_spot_klines").glob("*_5m.json")]
))


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
    buf = _load(BUF_PATH, {}); positions = _load(POS_PATH, {}); cooldown = {}
    ls = live_status()
    mode = "🔴실전" if (ls["enabled"] and ENGINE in ls["armed"]) else "🔵모의(dry)"
    log.info(f"마진숏 트레이더 시작 [{mode}] — {len(UNIVERSE)}코인, 거래량{VOL_MULT:.0f}배+2h+{PUMP_PCT:.0f}%→{HOLD_H}h숏 "
             f"| 증거금상한 {ls['global_cap_usdt']}USDT {ls['leverage']}배 | 마진잔고 {get_margin_usdt():.1f}")
    try: notify.send(f"📉 마진숏 트레이더 시작 [{mode}] — 거래량폭발 급등주 숏, 증거금상한 {ls['global_cap_usdt']}USDT")
    except Exception: pass

    while True:
        try:
            now = time.time()
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

            # 2) 만기 청산
            for sym in list(positions.keys()):
                pos = positions[sym]
                if now < pos["exit_ts"]:
                    continue
                px = prices.get(sym, pos["entry_price"])
                cres = guard.close_short(pos["coin"])
                pnl_pct = (1 - px/pos["entry_price"])*100
                pnl_usdt = pos["margin"] * load_config().get("leverage",2) * (pnl_pct/100)
                if pos["live"]: guard.record_realized(pnl_usdt)
                log_trade(dict(entry_time=pos["entry_iso"], exit_time=datetime.now(KST).isoformat(), symbol=sym,
                               pump_2h=pos["pump"], vol_mult=pos["vr"], entry_price=pos["entry_price"], exit_price=px,
                               margin_usdt=pos["margin"], pnl_pct=round(pnl_pct,2), pnl_usdt=round(pnl_usdt,2),
                               live=pos["live"], reason=f"{HOLD_H}h만기"))
                log.warning(f"★마진숏 청산 {sym} @{px:g} pnl={pnl_pct:+.2f}%({pnl_usdt:+.2f}USDT) → {cres.get('live') and '실청산' or cres}")
                try: notify.send(f"📈 마진숏 청산 {sym} pnl={pnl_pct:+.1f}% ({pnl_usdt:+.1f}USDT)")
                except Exception: pass
                del positions[sym]

            _save(BUF_PATH, buf); _save(POS_PATH, positions)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
