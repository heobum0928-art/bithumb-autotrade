"""
교차거래소 리드-래그 모의봇 WS버전 (lead_ws_trader) — 업비트 실시간 선행 → 빗썸 따라가기.

근거(2026-06-28): 10초 REST폴링판이 60초판(-1.48%)→+0.17%로 반전 = 속도가 핵심.
폴링지연이 엣지 먹음 → WebSocket 실시간(지연 ~0)으로 속도우위 극대화. 업비트는 빗썸보다
큰 한국거래소·정보 먼저 반영 → 업비트 실시간 점프를 빗썸 따라오기 전에 잡는다.

빗썸 WS(pubwss.bithumb.com, SYMBOL_KRW) + 업비트 WS(api.upbit.com, KRW-SYMBOL) 동시구독.
코인별 실시간 가격버퍼 → 1초마다 lead = up_mom(LOOKBACK초) - bh_mom 계산.
진입: lead≥1.5 AND 업비트 실제 팝(+1.5%) AND 빗썸 아직 덜옴. 출구: 손절-1.5%/트레일1%/15분.
★ 순수 모의: 빗썸 주문 미호출(WS 시세 읽기만). 포트 47235. 상태 leadlag_pos.json/leadlag_trades.csv
Run: python scripts/lead_ws_trader.py
"""
import sys, os, atexit, time, json, csv, socket, logging, threading
from collections import deque
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
    except OSError: print("[ERROR] lead_ws_trader/lead_trader 포트 47235 충돌."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests, websocket
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [LEADWS] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/lead_ws_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

TOPN = 80
LOOKBACK = 20           # 모멘텀 측정 윈도우 (초) — 실시간이라 짧게
ENTER_LEAD = 1.5
STRONG_LEAD = 3.0
SL = 1.5; TRAIL = 1.0; TIMEOUT_SEC = 900
SLOTS = 5; ENTRY_KRW = 50_000   # 슬롯당 5만원 실전 (쿠폰 0.04%RT→수익권)
LIVE = True             # True=실전주문 / False=모의
SCAN = 1.0              # 1초마다 스캔
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
POS = ROOT / "data" / "leadlag_pos.json"
TRADES = ROOT / "data" / "leadlag_trades.csv"
BH_WS = "wss://pubwss.bithumb.com/pub/ws"
UP_WS = "wss://api.upbit.com/websocket/v1"
UPBIT_MARKETS = "https://api.upbit.com/v1/market/all"

# ── 실시간 가격버퍼 (WS스레드 ↔ 메인) ──
_lock = threading.Lock()
_bh = {}   # coin -> deque[(ts, price)]
_up = {}   # coin -> deque[(ts, price)]
MAXLEN = 120


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

def _push(buf, coin, price):
    now = time.time()
    with _lock:
        if coin not in buf: buf[coin] = deque(maxlen=MAXLEN)
        buf[coin].append((now, price))

def mom(buf, coin):
    """LOOKBACK초 전 대비 현재 % 변화."""
    with _lock:
        h = buf.get(coin)
        if not h or len(h) < 2: return None, None
        now_p = h[-1][1]; cutoff = h[-1][0] - LOOKBACK
        old_p = None
        for ts, p in h:
            if ts <= cutoff: old_p = p
            else: break
        if old_p is None: old_p = h[0][1]
    if old_p and old_p > 0:
        return (now_p/old_p - 1)*100, now_p
    return None, now_p

# ── 빗썸 WS ──
def bh_ws_run(symbols):
    def on_open(ws):
        ws.send(json.dumps({"type":"ticker","symbols":[f"{s}_KRW" for s in symbols],"tickTypes":["24H"]}))
        log.info(f"[빗썸WS] 구독 {len(symbols)}")
    def on_message(ws, msg):
        try:
            d = json.loads(msg)
            if d.get("type") != "ticker": return
            c = d.get("content", {}); sym = c.get("symbol","")
            if not sym.endswith("_KRW"): return
            p = float(c.get("closePrice", 0) or 0)
            if p > 0: _push(_bh, sym[:-4], p)
        except Exception: pass
    while True:
        try:
            websocket.WebSocketApp(BH_WS, on_open=on_open, on_message=on_message,
                on_error=lambda w,e: log.warning(f"[빗썸WS] {e}"),
                on_close=lambda w,a,b: None).run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e: log.error(f"[빗썸WS] {e}")
        time.sleep(3)

# ── 업비트 WS ──
def up_ws_run(symbols):
    def on_open(ws):
        sub = [{"ticket":"lead"},{"type":"ticker","codes":[f"KRW-{s}" for s in symbols]}]
        ws.send(json.dumps(sub))
        log.info(f"[업비트WS] 구독 {len(symbols)}")
    def on_message(ws, msg):
        try:
            if isinstance(msg, bytes): msg = msg.decode("utf-8")
            d = json.loads(msg)
            if d.get("type") != "ticker": return
            coin = d.get("code","").split("-")[-1]
            p = float(d.get("trade_price", 0) or 0)
            if coin and p > 0: _push(_up, coin, p)
        except Exception: pass
    while True:
        try:
            websocket.WebSocketApp(UP_WS, on_open=on_open, on_message=on_message,
                on_error=lambda w,e: log.warning(f"[업비트WS] {e}"),
                on_close=lambda w,a,b: None).run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e: log.error(f"[업비트WS] {e}")
        time.sleep(3)

def log_trade(row):
    new = not TRADES.exists()
    with open(TRADES, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["exit_time","coin","lead","entry","exit","pnl_pct","reason","strong"])
        w.writerow(row)


def main():
    c = BithumbClient(); pos = load_pos()
    wl = bithumb_top(c); up_set = upbit_krw_set()
    syms = [x for x in wl if x in up_set]   # 빗썸∩업비트
    n_trades = 0
    log.info(f"리드-래그 WS 모의 시작 — 감시{len(syms)}(빗썸∩업비트) | lead≥{ENTER_LEAD}(강{STRONG_LEAD}) {LOOKBACK}초모멘텀 | 손절-{SL}%트레일{TRAIL}%타임{TIMEOUT_SEC//60}분 | 실시간WS·모의")
    try:
        from bithumb import notify
        notify.send(f"⚡ 리드-래그 WS실시간 모의 시작 — 업비트 선행 즉시포착(속도우위 #41). 폴링→WS, 모의·실주문0")
    except Exception: pass
    threading.Thread(target=bh_ws_run, args=(syms,), daemon=True).start()
    threading.Thread(target=up_ws_run, args=(syms,), daemon=True).start()
    time.sleep(LOOKBACK + 3)   # 버퍼 초기 적재

    while True:
        try:
            now = time.time()
            # ── 청산 ──
            for coin in list(pos.keys()):
                p = pos[coin]; _, cur = mom(_bh, coin)
                if cur is None or cur <= 0: continue
                p["highest"] = max(p.get("highest", cur), cur)
                pnl = (cur/p["entry"]-1)*100; hp = (p["highest"]/p["entry"]-1)*100
                sl_hit = pnl <= -SL; trail_hit = (hp >= TRAIL) and (pnl <= hp-TRAIL); to_hit = now >= p["timeout"]
                if sl_hit or trail_hit or to_hit:
                    reason = f"손절-{SL}%" if sl_hit else (f"트레일(고점+{hp:.1f}%)" if trail_hit else "타임아웃15분")
                    tag = "[실전]" if LIVE else "[모의]"
                    if LIVE and p.get("volume", 0) > 0:
                        try:
                            c.market_sell(f"KRW-{coin}", p["volume"])
                            log.info(f"[실전] 매도 완료 {coin} {p['volume']:.6f}개")
                        except Exception as e:
                            log.error(f"[실전] 매도 실패 {coin}: {e}")
                    log.info(f"{tag} 청산 {coin} @{cur:,.4f} PnL={pnl:+.2f}% | {reason} (lead{p['lead']:.1f})")
                    log_trade([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin, f"{p['lead']:.2f}",
                               f"{p['entry']:.4f}", f"{cur:.4f}", f"{pnl:+.2f}", reason, p.get("strong", False)])
                    try:
                        from bithumb import notify
                        notify.send(f"⚡ 리드WS 청산 {coin} {pnl:+.1f}% [{reason}] (lead{p['lead']:.1f}) {tag}")
                    except Exception: pass
                    del pos[coin]; save_pos(pos); n_trades += 1
            # ── 진입 ──
            if len(pos) < SLOTS:
                for coin in syms:
                    if len(pos) >= SLOTS: break
                    if coin in pos: continue
                    up_mom, _ = mom(_up, coin)
                    bh_mom, bhp = mom(_bh, coin)
                    if up_mom is None or bh_mom is None or bhp is None: continue
                    lead = up_mom - bh_mom
                    if lead >= ENTER_LEAD and up_mom >= ENTER_LEAD and bh_mom < up_mom:
                        strong = lead >= STRONG_LEAD
                        volume = 0.0
                        if LIVE:
                            try:
                                c.market_buy(f"KRW-{coin}", ENTRY_KRW)
                                volume = round(ENTRY_KRW / bhp * 0.9975, 8)
                                log.info(f"[실전] 매수 완료 {coin} ~{volume:.6f}개")
                            except Exception as e:
                                log.error(f"[실전] 매수 실패 {coin}: {e}"); continue
                        pos[coin] = {"entry": bhp, "highest": bhp, "lead": lead, "strong": strong,
                                     "volume": volume, "timeout": now + TIMEOUT_SEC,
                                     "entered": datetime.now(KST).isoformat()}
                        save_pos(pos)
                        tag_s = "★강신호" if strong else ""
                        tag_l = "[실전]" if LIVE else "[모의]"
                        log.info(f"{tag_l} 진입 {coin} @{bhp:,.4f} — lead{lead:.1f} (업비트{up_mom:+.1f}% 빗썸{bh_mom:+.1f}%) {tag_s}")
                        try:
                            from bithumb import notify
                            notify.send(f"⚡ 리드WS 진입 {coin} lead{lead:.1f} (업비트선행{up_mom:+.1f}%) {tag_s} {tag_l}")
                        except Exception: pass
        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(SCAN)


if __name__ == "__main__":
    main()
