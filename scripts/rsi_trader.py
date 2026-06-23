"""
RSI 과매도 반등 단타 (rsi_trader) — 검증된 첫 후보(walk-forward 통과).

근거(2026-06-23 백테스트): 5분봉 RSI<20 진입 → RSI 50 회복 매도.
TEST 비용0.30%후 +0.32%/거래 t2.38(랜덤보다 +2%/거래), 엣지가 *약세장*에 집중(t3.24).
39개 시도 중 처음으로 walk-forward 유의(t>2). 단 엣지 얇아(~0.3%) 슬리피지 위험 → 모의로 실측 먼저.
일봉 RSI는 칼잡기로 실패 — 5분봉 빠른반등만 유효.

진입: 5m RSI(14) < 20 (극단 과매도), 유동성 충족, 미보유/쿨다운 아님
청산: RSI ≥ 50 회복 / 손절 -5% / 24h 타임아웃
모의 기본(live_guard engine 'rsi' 미arm → notional). 실측 후 +α면 소액 실전.
포트 47231. 상태 data/rsi_pos.json | 로그 logs/rsi_trader.log
Run: python scripts/rsi_trader.py
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
    try: _sock.bind(("127.0.0.1", 47231))
    except OSError: print("[ERROR] rsi_trader 이미 실행 중 (포트 47231)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb import notify
from bithumb.live_guard import LiveGuard, live_status, load_config

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [RSI] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/rsi_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

RSI_ENTRY = 20         # 극단 과매도 진입
RSI_EXIT = 50          # 회복 매도
SL = 0.05              # 손절 -5%
TIMEOUT_H = 24
SLOTS = 5
TOPN = 60              # 거래대금 상위 감시
LIQ_FLOOR = 300_000_000
COOLDOWN_MIN = 120     # 청산 후 같은 코인 재진입 쿨다운
ENTRY_KRW_DRY = 200_000
CYCLE = 120            # 2분마다 (5m RSI는 느림)
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
POS = ROOT / "data" / "rsi_pos.json"


def rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        gains += max(d, 0); losses += max(-d, 0)
    ag = gains / period; al = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag * (period-1) + max(d, 0)) / period
        al = (al * (period-1) + max(-d, 0)) / period
    if al == 0: return 100.0
    return 100 - 100 / (1 + ag / al)


def is_live():
    ls = live_status(); return bool(ls.get("enabled")) and "rsi" in ls.get("armed", [])


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


def closes_5m(c, coin, n=40):
    try:
        k = c.get_candles(f"KRW-{coin}", unit=5, count=n)   # newest first
        return [x["trade_price"] for x in k[::-1]]          # oldest→newest
    except Exception:
        return None


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
                if val >= 10000: EXCLUDE.add(cur)   # 더스트(1만원 미만)는 제외 안함(거래허용)
    except Exception: pass
    mode = "🔴실전" if is_live() else "모의"
    wl = watchlist(c); wl_day = datetime.now(KST).date()
    log.info(f"RSI 반등 시작 [{mode}] — 5m RSI {RSI_ENTRY}밑 진입/RSI {RSI_EXIT} 회복 or -{SL*100:.0f}% 청산 | 감시{len(wl)} | 기존보유제외 {sorted(EXCLUDE-set(STABLE))}")
    try: notify.send(f"📉 RSI 과매도반등 시작 [{mode}] — 5m RSI 20밑 폭락 진입, RSI50 회복 매도. 검증된 첫 후보(t2.38), 모의 실측")
    except Exception: pass
    while True:
        try:
            if datetime.now(KST).date() != wl_day:
                wl_day = datetime.now(KST).date(); wl = watchlist(c)
            live = is_live()
            cap = load_config().get("engine_caps_krw", {}).get("rsi", 0)
            entry_krw = (cap / SLOTS) if (live and cap) else ENTRY_KRW_DRY
            # ── 청산 점검 ──
            for coin in list(pos.keys()):
                p = pos[coin]; cl = closes_5m(c, coin)
                if not cl: continue
                cur = cl[-1]; r = rsi(cl); pnl = cur / p["entry"] - 1
                exit_r = (r is not None and r >= RSI_EXIT); sl_hit = pnl <= -SL; to_hit = time.time() >= p["timeout"]
                if exit_r or sl_hit or to_hit:
                    reason = "RSI회복" if exit_r else ("손절-5%" if sl_hit else "타임아웃")
                    if live:
                        sell_vol = p["vol"]
                        try:
                            for a in c.get_balance(coin):
                                if a.get("currency") == coin:
                                    b = float(a.get("balance", 0) or 0)
                                    if b > 0: sell_vol = min(p["vol"], b)
                        except Exception: pass
                        g = LiveGuard("rsi"); g.execute_sell(c, f"KRW-{coin}", sell_vol, krw_hint=cur*sell_vol); g.record_realized((cur-p["entry"])*sell_vol)
                    log.warning(f"[{mode}] 청산 {coin} @{cur:,.4f} PnL={pnl*100:+.2f}% | {reason}(RSI{r:.0f})")
                    try: notify.send(f"📉 RSI반등 청산 {coin} {pnl*100:+.1f}% [{reason}] ({mode})")
                    except Exception: pass
                    cooldown[coin] = time.time() + COOLDOWN_MIN*60
                    del pos[coin]; save_pos(pos)
            # ── 진입 스캔 ──
            if len(pos) < SLOTS:
                for coin in wl:
                    if len(pos) >= SLOTS: break
                    if coin in pos or coin in EXCLUDE or cooldown.get(coin, 0) > time.time(): continue
                    cl = closes_5m(c, coin)
                    if not cl: continue
                    r = rsi(cl)
                    if r is None or r >= RSI_ENTRY: continue
                    cur = cl[-1]
                    if cur <= 0: continue
                    if live:
                        g = LiveGuard("rsi"); res = g.execute_buy(c, f"KRW-{coin}", entry_krw)
                        if res.get("dry"): log.info(f"진입 차단 {coin}: {res.get('reason')}"); continue
                        vol = entry_krw*(1-0.0004)/cur
                    else:
                        vol = entry_krw/cur
                    pos[coin] = {"entry": cur, "vol": vol, "rsi_in": r, "timeout": time.time()+TIMEOUT_H*3600, "entered": datetime.now(KST).isoformat()}
                    save_pos(pos)
                    log.warning(f"[{mode}] 진입 {coin} @{cur:,.4f} {entry_krw:,.0f}원 — RSI {r:.0f}(과매도)")
                    try: notify.send(f"📉 RSI반등 진입 {coin} (RSI{r:.0f} 과매도) [{mode}]")
                    except Exception: pass
            else:
                log.info(f"[{mode}] 슬롯 {len(pos)}/{SLOTS} 보유 {list(pos)}")
        except KeyboardInterrupt: break
        except Exception as e: log.error(f"루프오류: {e}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
