"""
캐스케이드-반등 단타 (cascade_trader) — walk-forward 통과한 첫 전략(2026-06-24).

근거: 90일 5분봉 111종목 백테스트. 대형 투매(드롭)+거래량급증+반등캔들 진입,
'손실은 짧게(-1.5%) 수익은 길게(트레일1.5%, 익절상한 없음)' 출구.
TEST[0.6,1.0) 비용0.30%후 +0.91%/거래 t2.44 (드롭4%+ 거래량3배+). 전략대장 #40.
핵심: 진입신호가 아니라 *출구구조*가 엣지 — 기존 +2%TP/-3%SL은 -0.18%(음수)였음.
단 90일=약세장 단일 장세 + 트레일 슬리피지 위험 → 모의로 실측 먼저.

진입: 5m 최근25분 고점대비 드롭 <= -4%, 진입봉 거래대금 >= 20봉평균×3, 반등캔들(종가>시가)
청산: 진입가 -1.5% 손절 / 고점수익-1.5% 트레일(고점이 +1.5% 넘은 뒤 작동) / 2h 타임아웃
모의 기본(live_guard engine 'cascade' 미arm). 포트 47232.
상태 data/cascade_pos.json | 로그 logs/cascade_trader.log
Run: python scripts/cascade_trader.py
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
    try: _sock.bind(("127.0.0.1", 47232))
    except OSError: print("[ERROR] cascade_trader 이미 실행 중 (포트 47232)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb import notify
from bithumb.live_guard import LiveGuard, live_status, load_config

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [CASC] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/cascade_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

# ── 진입 파라미터 (백테스트 검증값) ──
K = 5                # 낙폭 측정 윈도우 (25분)
DROP = -3.5          # 고점대비 드롭 임계 % (2026-06-27: 슬리피지 흡수 스윗스팟, SL-2% 가정 t2.41)
VOL_MULT = 2.5       # 진입봉 거래대금 / 20봉평균 (2026-06-27: 슬리피지 후 t2.41 유지 최소조건)
# ── 출구 파라미터 (백테스트 검증값 — 엣지의 핵심) ──
SL = 1.5             # 진입가 -1.5% 손절
TRAIL = 1.5          # 고점수익 -1.5%pt 트레일(고점이 +TRAIL% 넘으면 작동)
TIMEOUT_H = 2        # 2시간 타임아웃
SLOTS = 5
TOPN = 150            # 2026-06-27: 60→150, 거래대금3억+ 종목 전체(144개) 커버 — 표본축적 가속(엣지 무관, 유동성하한 유지)
LIQ_FLOOR = 300_000_000
COOLDOWN_MIN = 60
ENTRY_KRW_DRY = 200_000
CYCLE = 60           # 1분마다
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
POS = ROOT / "data" / "cascade_pos.json"


def is_live():
    ls = live_status(); return bool(ls.get("enabled")) and "cascade" in ls.get("armed", [])


def load_pos():
    if POS.exists():
        try: return json.loads(POS.read_text(encoding="utf-8"))
        except Exception: pass
    return {}


def save_pos(p):
    tmp = POS.with_suffix(".tmp"); tmp.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, POS)


def watchlist(c):
    try:
        t = c.get_ticker("ALL"); rows = []
        for coin, d in t.items():
            if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
            try: v = float(d.get("acc_trade_value_24H", 0))
            except Exception: continue
            if v >= LIQ_FLOOR: rows.append((coin, v))
        rows.sort(key=lambda x: -x[1]); return [x[0] for x in rows[:TOPN]]
    except Exception as e:
        log.warning(f"watchlist 실패: {e}"); return []


def candles_5m(c, coin, n=30):
    """(closes, opens, vols) oldest→newest."""
    try:
        k = c.get_candles(f"KRW-{coin}", unit=5, count=n)[::-1]
        return ([x["trade_price"] for x in k],
                [x["opening_price"] for x in k],
                [float(x.get("candle_acc_trade_price", 0)) for x in k])
    except Exception:
        return None, None, None


def price(c, coin):
    try: return float(c.get_ticker(coin)["closing_price"])
    except Exception: return 0.0


def main():
    c = BithumbClient(); pos = load_pos(); cooldown = {}
    EXCLUDE = set(STABLE)
    try:
        for a in c.get_accounts():
            cur = a.get("currency"); bal = float(a.get("balance", 0) or 0)
            if cur and cur != "KRW" and bal > 0:
                try: val = bal * float(c.get_ticker(cur)["closing_price"])
                except Exception: val = 0
                if val >= 10000: EXCLUDE.add(cur)
    except Exception: pass
    mode = "🔴실전" if is_live() else "모의"
    wl = watchlist(c); wl_day = datetime.now(KST).date()
    log.info(f"캐스케이드-반등 시작 [{mode}] — 드롭{DROP}%+거래량{VOL_MULT}배 진입 / 손절-{SL}% 트레일{TRAIL}% | 감시{len(wl)} | 보유제외 {sorted(EXCLUDE-set(STABLE))}")
    try: notify.send(f"🩸 캐스케이드-반등 시작 [{mode}] — 대형투매 줍기, 짧은손절+트레일. (백테 t2.44 — forward 게이트는 별개, 30건 후 판정)")
    except Exception: pass
    while True:
        try:
            if datetime.now(KST).date() != wl_day:
                wl_day = datetime.now(KST).date(); wl = watchlist(c)
            live = is_live()
            cap = load_config().get("engine_caps_krw", {}).get("cascade", 0)
            entry_krw = (cap / SLOTS) if (live and cap) else ENTRY_KRW_DRY
            # ── 청산 점검 ──
            for coin in list(pos.keys()):
                p = pos[coin]; cur = price(c, coin)
                if cur <= 0: continue
                p["highest"] = max(p.get("highest", cur), cur)
                pnl = (cur / p["entry"] - 1) * 100
                hp = (p["highest"] / p["entry"] - 1) * 100      # 고점수익 %
                sl_hit = pnl <= -SL
                trail_hit = (hp >= TRAIL) and (pnl <= hp - TRAIL)
                to_hit = time.time() >= p["timeout"]
                if sl_hit or trail_hit or to_hit:
                    reason = f"손절-{SL}%" if sl_hit else (f"트레일(고점+{hp:.1f}%)" if trail_hit else "타임아웃")
                    if live:
                        sell_vol = p["vol"]
                        try:
                            for a in c.get_balance(coin):
                                if a.get("currency") == coin:
                                    b = float(a.get("balance", 0) or 0)
                                    if b > 0: sell_vol = min(p["vol"], b)
                        except Exception: pass
                        g = LiveGuard("cascade")
                        res = g.execute_sell(c, f"KRW-{coin}", sell_vol, krw_hint=cur*sell_vol)
                        if res.get("error"):
                            log.error(f"[실전] 매도 실패 {coin}: {res.get('error')} — 포지션 유지")
                            try: notify.send(f"🚨 캐스케이드 매도 실패 {coin} [{reason}] {res.get('error')} — 포지션 유지")
                            except Exception: pass
                            continue
                        g.record_realized((cur - p["entry"]) * sell_vol)
                    log.warning(f"[{mode}] 청산 {coin} @{cur:,.4f} PnL={pnl:+.2f}% | {reason}")
                    try: notify.send(f"🩸 캐스케이드 청산 {coin} {pnl:+.1f}% [{reason}] ({mode})")
                    except Exception: pass
                    cooldown[coin] = time.time() + COOLDOWN_MIN*60
                    del pos[coin]; save_pos(pos)
            # ── 진입 스캔 ──
            if len(pos) < SLOTS:
                for coin in wl:
                    if len(pos) >= SLOTS: break
                    if coin in pos or coin in EXCLUDE or cooldown.get(coin, 0) > time.time(): continue
                    cl, op, vl = candles_5m(c, coin)
                    if not cl or len(cl) < 26: continue
                    local_high = max(cl[-(K+1):])
                    drop = (cl[-1] / local_high - 1) * 100
                    if drop > DROP: continue                     # 드롭 부족
                    avgv = sum(vl[-21:-1]) / 20 if len(vl) >= 21 else 0
                    vr = vl[-1] / avgv if avgv > 0 else 0
                    if vr < VOL_MULT: continue                   # 거래량 부족
                    if cl[-1] <= op[-1]: continue                # 반등캔들 아님(종가>시가 필요)
                    cur = cl[-1]
                    if cur <= 0: continue
                    if live:
                        g = LiveGuard("cascade"); res = g.execute_buy(c, f"KRW-{coin}", entry_krw)
                        if res.get("dry"): log.info(f"진입 차단 {coin}: {res.get('reason')}"); continue
                        vol = entry_krw*(1-0.0004)/cur
                    else:
                        vol = entry_krw/cur
                    pos[coin] = {"entry": cur, "vol": vol, "highest": cur, "drop": round(drop,1), "vr": round(vr,1),
                                 "timeout": time.time()+TIMEOUT_H*3600, "entered": datetime.now(KST).isoformat()}
                    save_pos(pos)
                    log.warning(f"[{mode}] 진입 {coin} @{cur:,.4f} {entry_krw:,.0f}원 — 드롭{drop:.1f}% 거래량{vr:.1f}배 반등")
                    try: notify.send(f"🩸 캐스케이드 진입 {coin} (드롭{drop:.1f}%, 거래량{vr:.1f}배 투매반등) [{mode}]")
                    except Exception: pass
            else:
                log.info(f"[{mode}] 슬롯 {len(pos)}/{SLOTS} 보유 {list(pos)}")
        except KeyboardInterrupt: break
        except Exception as e: log.error(f"루프오류: {e}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
