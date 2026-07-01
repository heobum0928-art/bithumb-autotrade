"""
탐험 트레이더 3호 — 느슨한 다각도 기록봇 (multiangle_trader) — 순수 모의, 실주문 0.

목적(2026-07-01): cascade(급락반등)·momentum(초입추세)보다 훨씬 느슨한 조건으로 진입해서
"어떤 진입이 이겼는지" 사후에 역으로 캐낼 수 있는 풍부한 특징(feature) 데이터를 쌓는다.
사전에 패턴을 정의하지 않고, 최대한 다양한 상황에서 진입해 결과와 특징을 같이 기록한다.

진입: 거래대금 상위권 + 24H등락률 -10~+40%(광범위) + BTC 급락 아닐 때. 사실상 넓은 그물.
청산: 손절-6% / 트레일(고점+10%→-6%) / 4H 타임아웃 — cascade/momentum과 또 다른 범용값(비교용)
기록: RSI(14)·MA20이격·거래량배수·24H등락률·BTC등락률 — 승패와 함께 CSV 축적 → 사후 상관분석
★ 순수 모의: live_guard(engine='multiangle') 미arm이면 항상 모의. 포트 47240.
상태 data/multiangle_pos.json | 거래기록 data/multiangle_trades.csv | 로그 logs/multiangle_trader.log
Run: python scripts/multiangle_trader.py
"""
import sys, os, atexit, time, json, csv, socket, random, logging, statistics
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
    try: _sock.bind(("127.0.0.1", 47240))
    except OSError: print("[ERROR] multiangle_trader 이미 실행 중 (포트 47240)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb.live_guard import LiveGuard, live_status, load_config

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [MULT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/multiangle_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

TOPN = 80
LIQ_FLOOR = 500_000_000
CHG_MIN = -10.0
CHG_MAX = 40.0
BTC_SL = -3.0
SL = 6.0
TRAIL_TRIGGER = 10.0
TRAIL = 6.0
TIMEOUT_H = 4
SLOTS = 3
ENTRY_KRW_DRY = 50_000
CYCLE = 300
COOLDOWN_H = 4
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}

POS = ROOT / "data" / "multiangle_pos.json"
TRADES = ROOT / "data" / "multiangle_trades.csv"
COOLDOWN_F = ROOT / "data" / "multiangle_cooldown.json"


def is_live():
    ls = live_status(); return bool(ls.get("enabled")) and "multiangle" in ls.get("armed", [])


def load_json(path, default):
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except Exception: pass
    return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def log_trade(row):
    new = not TRADES.exists()
    with open(TRADES, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["exit_time","coin","entry","exit","pnl_pct","reason","held_h",
                             "chg24h_at_entry","rsi_at_entry","vol_ratio_at_entry","ma20_gap_pct","btc_chg24_at_entry"])
        w.writerow(row)


def candles_5m(c, coin, n=25):
    try:
        k = c.get_candles(f"KRW-{coin}", unit=5, count=n)[::-1]
        return ([x["trade_price"] for x in k], [float(x.get("candle_acc_trade_price", 0)) for x in k])
    except Exception:
        return None, None


def features(c, coin):
    cl, vl = candles_5m(c, coin)
    if not cl or len(cl) < 21: return None
    period = 14
    seg = cl[-(period+1):]
    gains = losses = 0.0
    for i in range(1, len(seg)):
        d = seg[i] - seg[i-1]
        if d > 0: gains += d
        else: losses += -d
    avg_gain = gains/period; avg_loss = losses/period
    rsi = 100.0 if avg_loss == 0 else 100 - 100/(1+avg_gain/avg_loss)
    ma20 = sum(cl[-20:]) / 20
    ma20_gap = (cl[-1]/ma20 - 1) * 100
    avgv = statistics.mean(vl[-21:-1]) if len(vl) >= 21 else 0
    vr = vl[-1]/avgv if avgv > 0 else None
    return {"rsi": round(rsi,1), "ma20_gap": round(ma20_gap,2), "vol_ratio": round(vr,2) if vr else None}


def price(c, coin):
    try: return float(c.get_ticker(coin)["closing_price"])
    except Exception: return 0.0


def watchlist(c):
    try:
        t = c.get_ticker("ALL"); rows = []
        for coin, d in t.items():
            if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
            try:
                v = float(d.get("acc_trade_value_24H", 0)); chg = float(d.get("fluctate_rate_24H", 0))
            except Exception: continue
            if v >= LIQ_FLOOR and CHG_MIN <= chg <= CHG_MAX: rows.append((coin, v, chg))
        rows.sort(key=lambda x: -x[1]); return [(x[0], x[2]) for x in rows[:TOPN]]
    except Exception as e:
        log.warning(f"watchlist 실패: {e}"); return []


def main():
    c = BithumbClient(); pos = load_json(POS, {}); cool = load_json(COOLDOWN_F, {})
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
    log.info(f"다각도 기록봇 시작 [{mode}] — 24H{CHG_MIN}~{CHG_MAX}% 광범위진입 | 손절-{SL}% 트레일(고점+{TRAIL_TRIGGER}%→-{TRAIL}%) {TIMEOUT_H}H | 슬롯{SLOTS}")
    try:
        from bithumb import notify
        notify.send(f"🔬 다각도 기록봇(탐험3호) 시작 [{mode}] — 느슨한 필터+풍부한 특징기록, 사후패턴분석용. 실주문0(미arm)")
    except Exception: pass

    while True:
        try:
            cool = {k: v for k, v in cool.items() if v > time.time()}
            live = is_live()
            cap = load_config().get("engine_caps_krw", {}).get("multiangle", 0)
            entry_krw = (cap / SLOTS) if (live and cap) else ENTRY_KRW_DRY

            btc_chg = 0.0
            try: btc_chg = float(c.get_ticker("BTC").get("fluctate_rate_24H", 0) or 0)
            except Exception: pass

            # ── 청산 ──
            for coin in list(pos.keys()):
                p = pos[coin]; cur = price(c, coin)
                if cur <= 0: continue
                p["highest"] = max(p.get("highest", cur), cur)
                pnl = (cur / p["entry"] - 1) * 100
                hp = (p["highest"] / p["entry"] - 1) * 100
                sl_hit = pnl <= -SL
                trail_hit = (hp >= TRAIL_TRIGGER) and (pnl <= hp - TRAIL)
                to_hit = time.time() >= p["timeout"]
                if sl_hit or trail_hit or to_hit:
                    reason = f"손절-{SL}%" if sl_hit else (f"트레일(고점+{hp:.1f}%)" if trail_hit else f"타임아웃{TIMEOUT_H}H")
                    if p.get("live") and p.get("volume", 0) > 0:
                        g = LiveGuard("multiangle")
                        res = g.execute_sell(c, f"KRW-{coin}", p["volume"], krw_hint=cur*p["volume"])
                        if res.get("error"):
                            log.error(f"[실전] 매도 실패 {coin}: {res.get('error')} — 포지션 유지"); continue
                        g.record_realized((cur - p["entry"]) * p["volume"])
                    tag = "[실전]" if p.get("live") else "[모의]"
                    held_h = (time.time() - p["entered_ts"]) / 3600
                    log.info(f"{tag} 청산 {coin} @{cur:,.4f} PnL={pnl:+.2f}% | {reason} ({held_h:.1f}H보유)")
                    log_trade([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin, f"{p['entry']:.4f}", f"{cur:.4f}",
                               f"{pnl:+.2f}", reason, f"{held_h:.1f}", f"{p.get('chg24',0):+.1f}",
                               p.get("rsi",""), p.get("vol_ratio",""), p.get("ma20_gap",""), f"{p.get('btc_chg',0):+.1f}"])
                    try:
                        from bithumb import notify
                        notify.send(f"🔬 다각도 청산 {coin} {pnl:+.1f}% [{reason}] {tag}")
                    except Exception: pass
                    cool[coin] = time.time() + COOLDOWN_H * 3600
                    del pos[coin]; save_json(POS, pos); save_json(COOLDOWN_F, cool)

            # ── 진입 ──
            if len(pos) < SLOTS and btc_chg >= BTC_SL:
                candidates = [(coin, chg) for coin, chg in watchlist(c)
                              if coin not in pos and coin not in EXCLUDE and cool.get(coin, 0) <= time.time()]
                random.shuffle(candidates)
                for coin, chg24 in candidates:
                    if len(pos) >= SLOTS: break
                    cur = price(c, coin)
                    if cur <= 0: continue
                    f = features(c, coin)
                    if f is None: continue
                    volume = 0.0
                    if live:
                        g = LiveGuard("multiangle"); res = g.execute_buy(c, f"KRW-{coin}", entry_krw)
                        if res.get("dry"): continue
                        if res.get("error"):
                            log.error(f"[실전] 매수 실패 {coin}: {res.get('error')}"); continue
                        volume = entry_krw*(1-0.0004)/cur
                    pos[coin] = {"entry": cur, "highest": cur, "chg24": round(chg24,1),
                                 "rsi": f["rsi"], "vol_ratio": f["vol_ratio"], "ma20_gap": f["ma20_gap"],
                                 "btc_chg": round(btc_chg,1), "volume": volume, "entered_ts": time.time(),
                                 "timeout": time.time()+TIMEOUT_H*3600,
                                 "entered": datetime.now(KST).isoformat(), "live": live}
                    save_json(POS, pos)
                    tag = "[실전]" if live else "[모의]"
                    log.info(f"{tag} 진입 {coin} @{cur:,.4f} — 24H{chg24:+.1f}% RSI{f['rsi']} MA20이격{f['ma20_gap']:+.1f}% 거래량비{f['vol_ratio']}")
                    try:
                        from bithumb import notify
                        notify.send(f"🔬 다각도 진입 {coin} 24H{chg24:+.1f}% RSI{f['rsi']} {tag}")
                    except Exception: pass
                    break  # 사이클당 1개만
            save_json(POS, pos)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
