"""
매집 단타 (accum_trader) — 약세장 롱온리의 실전 방법: 역추세로 튀는 알트를 '매집 초입'에 잡는다.

논리(2026-06-22): 약세장엔 숏이 답인데 빗썸은 롱온리 → 유일한 길 = 펌핑 알트를 매집 신호일 때 잡기.
타이코(+117%,매수36% 던짐) 쫓지 말고, 호가가 매수우세+체결매수비 높은 '아직 안 끝난' 놈만.
펌핑을 *쫓는* 게(=죽은 30규칙) 아니라, 호가/체결로 '지금 사 모으는 중'을 골라 진입.

진입(전부 충족):
  - 24h 상승 IN [MIN_CHG, MAX_CHG]   (이미 +100%면 늦음 → 상한컷)
  - 24h 거래대금 >= 유동성 플로어
  - 호가 깊이불균형 depth_imb > +0.10  (매수벽 우세)
  - 최근 체결 매수비중 > 0.55          (지금 사는 중)
  - 최근 15분 모멘텀 > 0               (아직 오름, 꺾인 거 제외)
청산: 트레일 TRAIL% / 손절 -SL% / 타임아웃. (많이 먹고 빠지기)

실거래: live_guard로만 제어. 가드 armed("accum")+enabled 아니면 전부 모의(노셔널).
포트 47228. 상태 data/accum_pos.json | 로그 logs/accum_trader.log
Run: python scripts/accum_trader.py
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
    try: _sock.bind(("127.0.0.1", 47228))
    except OSError: print("[ERROR] accum_trader 이미 실행 중 (포트 47228)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb import notify
from bithumb.live_guard import LiveGuard, live_status, load_config

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ACCUM] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/accum_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

# ── 진입 파라미터 ──────────────────────────────
MIN_CHG, MAX_CHG = 5.0, 60.0     # 24h 상승 5~60% (이미 +100%면 늦음 컷)
LIQ_FLOOR = 300_000_000          # 24h 거래대금 3억+
DEPTH_MIN = 0.10                 # 호가 매수우세
BUY_MIN = 0.55                   # 체결 매수비중
MOM15_MIN = 0.0                  # 최근 15분 아직 오름
MAX_EXT = 25.0                   # 최근 15분 +25%↑면 과열(막차) 컷
# ── 청산 파라미터 ──────────────────────────────
TRAIL = 0.03                     # 고점 대비 -3% 트레일
SL = 0.03                        # 진입가 대비 -3% 손절
TIMEOUT_H = 6                    # 6시간 타임아웃
SLOTS = 3
ENTRY_KRW_DRY = 200_000          # 모의 1건 노셔널
COOLDOWN_MIN = 60                # 같은 코인 재진입 쿨다운
CYCLE = 30
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
# ★ 타겟 한정 — 펌프가 *이어지는* 검증 종목만(walk-forward). 함정(COS/STRAX/OSMO류) 원천 차단.
TARGET = {"XLM","H","WLD","DOGE","HBAR","ICP","SAND","ALGO","ENA","SEI","PEPE","SOON","HOME","ETHFI","BTR"}
POS = ROOT / "data" / "accum_pos.json"


def is_live():
    ls = live_status()
    return bool(ls.get("enabled")) and "accum" in ls.get("armed", [])


def load_pos():
    if POS.exists():
        try: return json.loads(POS.read_text(encoding="utf-8"))
        except Exception: pass
    return {}


def save_pos(p):
    tmp = POS.with_suffix(".tmp"); tmp.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, POS)


def micro(c, coin):
    """호가 깊이불균형 + 체결 매수비중. 실패 None."""
    try:
        ob = c.get_orderbook(coin)
        bids = [(float(b["price"]), float(b["quantity"])) for b in ob.get("bids", [])][:15]
        asks = [(float(a["price"]), float(a["quantity"])) for a in ob.get("asks", [])][:15]
        if not bids or not asks: return None
        bk = sum(p*q for p, q in bids); ak = sum(p*q for p, q in asks)
        depth = (bk-ak)/(bk+ak) if (bk+ak) > 0 else 0
        th = c.get_transaction_history(coin, count=50)
        buy = sum(float(x["units_traded"])*float(x["price"]) for x in th if x.get("type") == "bid")
        sell = sum(float(x["units_traded"])*float(x["price"]) for x in th if x.get("type") == "ask")
        br = buy/(buy+sell) if (buy+sell) > 0 else 0
        return depth, br
    except Exception:
        return None


def mom15(c, coin):
    try:
        k = c.get_candles(f"KRW-{coin}", unit=5, count=3)   # newest first, 15분
        return (k[0]["trade_price"]/k[2]["opening_price"]-1)*100
    except Exception:
        return None


def price(c, coin):
    try: return float(c.get_ticker(coin)["closing_price"])
    except Exception: return 0.0


def scan_entries(c, held, cooldown):
    """매집 신호 코인 리스트 (조건 전부 충족)."""
    out = []
    try:
        t = c.get_ticker("ALL")
    except Exception:
        return out
    cands = []
    for coin, d in t.items():
        if coin == "date" or coin in STABLE or coin not in TARGET or not isinstance(d, dict) or coin in held: continue
        if cooldown.get(coin, 0) > time.time(): continue
        try:
            chg = float(d.get("fluctate_rate_24H", 0)); val = float(d.get("acc_trade_value_24H", 0))
        except Exception: continue
        if MIN_CHG <= chg <= MAX_CHG and val >= LIQ_FLOOR:
            cands.append((coin, chg, val))
    cands.sort(key=lambda x: -x[2])
    for coin, chg, val in cands[:20]:
        m = micro(c, coin)
        if not m: continue
        depth, br = m
        if not (depth > DEPTH_MIN and br > BUY_MIN): continue
        mm = mom15(c, coin)
        if mm is None or mm <= MOM15_MIN or mm >= MAX_EXT: continue
        out.append((coin, chg, depth, br, mm))
    return out


def main():
    c = BithumbClient(); pos = load_pos(); cooldown = {}
    # 기존 보유 코인은 진입 제외(봇 매매와 엉키지 않게)
    EXCLUDE = set(STABLE)
    try:
        for a in c.get_accounts():
            cur = a.get("currency"); bal = float(a.get("balance", 0) or 0)
            if cur and cur != "KRW" and bal > 0: EXCLUDE.add(cur)
    except Exception: pass
    mode = "🔴실전" if is_live() else "모의"
    log.info(f"타겟 {len(TARGET)}종 한정 + 호가필터 | 기존보유 제외: {sorted(EXCLUDE - set(STABLE))}")
    log.info(f"매집 단타 시작 [{mode}] — 진입 매수우세+체결매수>{BUY_MIN} 신호 / 손절-{SL*100:.0f}% 트레일{TRAIL*100:.0f}% / 슬롯{SLOTS}")
    try: notify.send(f"🎯 매집 단타 시작 [{mode}] — 펌핑 쫓기 아니라 '지금 사 모으는' 알트만(호가+체결). 손절-3% 트레일3%")
    except Exception: pass
    while True:
        try:
            live = is_live()
            cap = load_config().get("engine_caps_krw", {}).get("accum", 0)
            entry_krw = (cap / SLOTS) if (live and cap) else ENTRY_KRW_DRY
            # ── 보유 청산 점검 ──
            for coin in list(pos.keys()):
                p = pos[coin]; cur = price(c, coin)
                if cur <= 0: continue
                p["highest"] = max(p.get("highest", cur), cur)
                pnl = cur/p["entry"]-1
                trail_hit = cur <= p["highest"]*(1-TRAIL)
                sl_hit = pnl <= -SL
                to_hit = time.time() >= p["timeout"]
                if trail_hit or sl_hit or to_hit:
                    reason = "손절-3%" if sl_hit else ("트레일3%" if trail_hit else "타임아웃")
                    if live:
                        # ★ 실제 보유량으로 매도(추정치 아님 — 돈 지키는 핵심)
                        # 봇이 산 양(p["vol"])만, 실보유 한도 내에서 매도 — 기존 보유 코인 보호
                        sell_vol = p["vol"]
                        try:
                            for a in c.get_balance(coin):
                                if a.get("currency") == coin:
                                    bal = float(a.get("balance", 0) or 0)
                                    if bal > 0: sell_vol = min(p["vol"], bal)
                        except Exception: pass
                        g = LiveGuard("accum"); g.execute_sell(c, f"KRW-{coin}", sell_vol, krw_hint=cur*sell_vol)
                        g.record_realized((cur-p["entry"])*sell_vol)
                    log.warning(f"[{mode}] 청산 {coin} @{cur:,.4f} PnL={pnl*100:+.2f}% | {reason} (고점+{(p['highest']/p['entry']-1)*100:.1f}%)")
                    try: notify.send(f"🎯 매집단타 청산 {coin} {pnl*100:+.1f}% [{reason}] ({mode})")
                    except Exception: pass
                    cooldown[coin] = time.time()+COOLDOWN_MIN*60
                    del pos[coin]; save_pos(pos)
            # ── 신규 진입 ──
            if len(pos) < SLOTS:
                for coin, chg, depth, br, mm in scan_entries(c, set(pos) | EXCLUDE, cooldown):
                    if len(pos) >= SLOTS: break
                    cur = price(c, coin)
                    if cur <= 0: continue
                    if live:
                        g = LiveGuard("accum"); res = g.execute_buy(c, f"KRW-{coin}", entry_krw)
                        if res.get("dry"):
                            log.info(f"진입 차단(가드) {coin}: {res.get('reason')}"); continue
                        vol = entry_krw*(1-0.0004)/cur   # 대략(쿠폰)
                    else:
                        vol = entry_krw/cur
                    pos[coin] = {"entry": cur, "vol": vol, "highest": cur,
                                 "timeout": time.time()+TIMEOUT_H*3600, "entered": datetime.now(KST).isoformat()}
                    save_pos(pos)
                    log.warning(f"[{mode}] 진입 {coin} @{cur:,.4f} {entry_krw:,.0f}원 — 24h+{chg:.0f}% 깊이{depth:+.2f} 매수{br*100:.0f}% 15분{mm:+.1f}%")
                    try: notify.send(f"🎯 매집단타 진입 {coin} +{chg:.0f}% (호가매수우세 깊이{depth:+.2f}/체결매수{br*100:.0f}%) [{mode}]")
                    except Exception: pass
            else:
                log.info(f"[{mode}] 슬롯 만석({len(pos)}/{SLOTS}) 보유 {list(pos)}")
        except KeyboardInterrupt: break
        except Exception as e: log.error(f"루프오류: {e}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
