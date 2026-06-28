"""
교차거래소 리드-래그 모의봇 (lead_trader) — Upbit/Binance 선행 → 빗썸 따라가기 (실주문0).

근거(2026-06-27 대장 #41 LEAD): crossex 6일 백테 = 40개 중 첫 "정보우위+양수".
lead=max(up_chg,bn_chg)-bh_chg 가 클수록 빗썸 forward 단조증가. 비용0.16%서
lead1.5~2.0 T+5분 +0.41%/t1.7, lead3.0+ 승75%/+2.26%(표본12뿐). → forward 모의로
표본 12→30 늘려 'lead3.0+ 승75%가 진짜냐' 판정. 정보우위 = 빗썸차트엔 없는 타거래소 선행.

진입: lead >= ENTER AND 타거래소(up/bn) 실제 +ENTER% 팝 AND 빗썸 아직 덜옴(bh_chg<타거래소)
청산(오늘 AGLD교훈=출구중요): 손절-1.5% / 고점+1%후 트레일1% / 15분 타임아웃 (T+5분 베스트라 짧게)
★ 순수 모의: 빗썸 주문 미호출(get_ticker 읽기만). 가상자본 슬롯당 20만×5. 포트 47235.
상태 data/leadlag_pos.json | 거래기록 data/leadlag_trades.csv | 로그 logs/lead_trader.log
Run: python scripts/lead_trader.py
"""
import sys, os, atexit, time, json, csv, socket, logging
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
    try: _sock.bind(("127.0.0.1", 47235))
    except OSError: print("[ERROR] lead_trader 이미 실행 중 (포트 47235)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [LEAD] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/lead_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

TOPN = 80
ENTER_LEAD = 1.5        # lead 이 이상 + 타거래소 팝이면 진입 (백테 양수 시작점)
STRONG_LEAD = 3.0       # 강한 신호(승75% 검증 대상) 별도 표시
SL = 1.5                # 손절 -1.5%
TRAIL = 1.0             # 고점 +1% 넘으면 고점-1% 트레일
TIMEOUT_SEC = 900       # 15분 타임아웃 (T+5분 베스트라 짧게)
SLOTS = 5
ENTRY_KRW = 200_000
CYCLE = 10              # 2026-06-28: 60→10초. 60초판 실측 -1.48%/t-2.66(백테+와 반전) = 폴링지연으로 빗썸 이미 따라온 고점진입. 속도가 생명 → 10초로 지연단축 재시도
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
POS = ROOT / "data" / "leadlag_pos.json"
TRADES = ROOT / "data" / "leadlag_trades.csv"
UPBIT_MARKETS = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER = "https://api.upbit.com/v1/ticker"
BINANCE_PRICE = "https://api.binance.com/api/v3/ticker/price"


def load_pos():
    if POS.exists():
        try: return json.loads(POS.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def save_pos(p):
    tmp = POS.with_suffix(".tmp"); tmp.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, POS)

def bithumb_top(c):
    try:
        t = c.get_ticker("ALL"); rows = []
        for coin, d in t.items():
            if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
            try: v = float(d.get("acc_trade_value_24H", 0))
            except Exception: continue
            rows.append((coin, v))
        rows.sort(key=lambda x: -x[1]); return [x[0] for x in rows[:TOPN]]
    except Exception as e:
        log.warning(f"빗썸 watchlist 실패: {e}"); return []

def upbit_krw_set():
    try:
        r = requests.get(UPBIT_MARKETS, timeout=5).json()
        return {m["market"].split("-")[1] for m in r if m["market"].startswith("KRW-")}
    except Exception: return set()

def bithumb_prices(c):
    try:
        t = c.get_ticker("ALL")
        return {k: float(v["closing_price"]) for k, v in t.items() if k != "date" and isinstance(v, dict) and v.get("closing_price")}
    except Exception: return {}

def upbit_prices(coins):
    out = {}
    if not coins: return out
    try:
        r = requests.get(UPBIT_TICKER, params={"markets": ",".join(f"KRW-{x}" for x in coins)}, timeout=5).json()
        for d in r: out[d["market"].split("-")[1]] = float(d["trade_price"])
    except Exception: pass
    return out

def binance_prices():
    out = {}
    try:
        r = requests.get(BINANCE_PRICE, timeout=6).json()
        for d in r:
            s = d.get("symbol", "")
            if s.endswith("USDT"): out[s[:-4]] = float(d["price"])
    except Exception: pass
    return out

def log_trade(row):
    new = not TRADES.exists()
    with open(TRADES, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["exit_time","coin","lead","entry","exit","pnl_pct","reason","strong"])
        w.writerow(row)


def main():
    c = BithumbClient(); pos = load_pos()
    wl = bithumb_top(c); up_set = upbit_krw_set(); wl_day = datetime.now(KST).date()
    prev = {}; n_trades = 0
    log.info(f"리드-래그 모의 시작 — 감시{len(wl)}(빗썸∩업비트{len(up_set)}) | 진입 lead≥{ENTER_LEAD}(강{STRONG_LEAD}) | 손절-{SL}% 트레일{TRAIL}% 타임{TIMEOUT_SEC//60}분 | 모의(실주문0)")
    try:
        from bithumb import notify
        notify.send(f"📡 리드-래그 모의 시작 — 업비트/바이낸스 선행→빗썸 따라가기(정보우위 #41 검증). lead≥{ENTER_LEAD}, 모의·실주문0")
    except Exception: pass

    while True:
        try:
            if datetime.now(KST).date() != wl_day:
                wl_day = datetime.now(KST).date(); wl = bithumb_top(c); up_set = upbit_krw_set()
            bh = bithumb_prices(c)
            up = upbit_prices([x for x in wl if x in up_set])
            bn = binance_prices()
            now = time.time()
            # ── 청산 ──
            for coin in list(pos.keys()):
                p = pos[coin]; cur = bh.get(coin)
                if cur is None or cur <= 0: continue
                p["highest"] = max(p.get("highest", cur), cur)
                pnl = (cur / p["entry"] - 1) * 100
                hp = (p["highest"] / p["entry"] - 1) * 100
                sl_hit = pnl <= -SL
                trail_hit = (hp >= TRAIL) and (pnl <= hp - TRAIL)
                to_hit = now >= p["timeout"]
                if sl_hit or trail_hit or to_hit:
                    reason = f"손절-{SL}%" if sl_hit else (f"트레일(고점+{hp:.1f}%)" if trail_hit else "타임아웃15분")
                    log.info(f"[모의] 청산 {coin} @{cur:,.4f} PnL={pnl:+.2f}% | {reason} (lead{p['lead']:.1f})")
                    log_trade([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin, f"{p['lead']:.2f}",
                               f"{p['entry']:.4f}", f"{cur:.4f}", f"{pnl:+.2f}", reason, p.get("strong", False)])
                    try:
                        from bithumb import notify
                        notify.send(f"📡 리드 청산 {coin} {pnl:+.1f}% [{reason}] (lead{p['lead']:.1f})")
                    except Exception: pass
                    del pos[coin]; save_pos(pos); n_trades += 1
            # ── 진입 ──
            if len(pos) < SLOTS:
                for coin in wl:
                    if len(pos) >= SLOTS: break
                    if coin in pos: continue
                    bhp = bh.get(coin); upp = up.get(coin); bnp = bn.get(coin)
                    if bhp is None: continue
                    pv = prev.get(coin, {})
                    def chg(cur, key):
                        pp = pv.get(key)
                        return (cur / pp - 1) * 100 if (cur and pp and pp > 0) else None
                    bh_chg = chg(bhp, "bh"); up_chg = chg(upp, "up"); bn_chg = chg(bnp, "bn")
                    others = [x for x in (up_chg, bn_chg) if x is not None]
                    if not others or bh_chg is None: continue
                    lead = max(others) - bh_chg
                    other_pop = max(others)
                    # 진입: lead 충분 + 타거래소 실제 팝 + 빗썸 아직 덜옴
                    if lead >= ENTER_LEAD and other_pop >= ENTER_LEAD and bh_chg < other_pop:
                        strong = lead >= STRONG_LEAD
                        pos[coin] = {"entry": bhp, "highest": bhp, "lead": lead, "strong": strong,
                                     "timeout": now + TIMEOUT_SEC, "entered": datetime.now(KST).isoformat()}
                        save_pos(pos)
                        tag = "★강신호" if strong else ""
                        log.info(f"[모의] 진입 {coin} @{bhp:,.4f} — lead{lead:.1f} (타거래소+{other_pop:.1f}% 빗썸+{bh_chg:.1f}%) {tag}")
                        try:
                            from bithumb import notify
                            notify.send(f"📡 리드 진입 {coin} lead{lead:.1f} (타거래소 선행+{other_pop:.1f}%) {tag} [모의]")
                        except Exception: pass
            # prev 갱신
            for coin in wl:
                prev[coin] = {"bh": bh.get(coin), "up": up.get(coin), "bn": bn.get(coin)}
        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
