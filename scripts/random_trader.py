"""
탐험 트레이더 2호 — 무작위 기준선 (random_trader) — 순수 모의, 실주문 0.

목적(2026-07-01): "패턴이 뭔지 알고 싶다"는 목표에 대한 대조군(control group).
거래대금 상위 코인 중 아무 기술적 판단 없이 무작위로 골라 진입 → 만약 이게 우연히도
수익이 난다면 "고거래대금 자체가 드리프트를 갖는다"는 뜻이고, 손실이면 "거래대금만으론
엣지 없음"이 다시 확인되는 것(#36 결론과 일치 여부 재검증).

진입: 거래대금 상위 TOPN 중 보유/쿨다운 아닌 코인을 매 사이클 무작위 1개 선택(슬롯 여유 시)
청산: 손절-5% / 트레일(고점+8%→-5%) / 6H 타임아웃 — cascade/momentum과 다른 범용값
기록: 진입 시점 RSI(14)·거래량배수·24H등락률·BTC등락률을 남겨 사후 패턴 분석용
★ 순수 모의: live_guard(engine='random') 미arm이면 항상 모의. 포트 47239.
상태 data/random_pos.json | 거래기록 data/random_trades.csv | 로그 logs/random_trader.log
Run: python scripts/random_trader.py
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
    try: _sock.bind(("127.0.0.1", 47239))
    except OSError: print("[ERROR] random_trader 이미 실행 중 (포트 47239)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb.live_guard import LiveGuard, live_status, load_config

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [RND] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/random_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

TOPN = 60
LIQ_FLOOR = 500_000_000
SL = 5.0
TRAIL_TRIGGER = 8.0
TRAIL = 5.0
TIMEOUT_H = 6
SLOTS = 3
ENTRY_KRW_DRY = 50_000
CYCLE = 600          # 10분마다 (무작위 진입 빈도 제한)
COOLDOWN_H = 6
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}

POS = ROOT / "data" / "random_pos.json"
TRADES = ROOT / "data" / "random_trades.csv"
COOLDOWN_F = ROOT / "data" / "random_cooldown.json"


def is_live():
    ls = live_status(); return bool(ls.get("enabled")) and "random" in ls.get("armed", [])


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
        if new: w.writerow(["exit_time","coin","entry","exit","pnl_pct","reason","held_h","chg24h_at_entry","rsi_at_entry","vol_ratio_at_entry"])
        w.writerow(row)


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


def rsi_of(c, coin, period=14):
    try:
        k = c.get_candles(f"KRW-{coin}", unit=5, count=period+2)[::-1]
        cl = [x["trade_price"] for x in k]
        if len(cl) < period + 1: return None
        seg = cl[-(period+1):]
        gains = losses = 0.0
        for i in range(1, len(seg)):
            d = seg[i] - seg[i-1]
            if d > 0: gains += d
            else: losses += -d
        avg_gain = gains/period; avg_loss = losses/period
        if avg_loss == 0: return 100.0
        rs = avg_gain/avg_loss
        return 100 - 100/(1+rs)
    except Exception:
        return None


def vol_ratio_of(c, coin):
    try:
        k = c.get_candles(f"KRW-{coin}", unit=5, count=21)[::-1]
        vl = [float(x.get("candle_acc_trade_price", 0)) for x in k]
        if len(vl) < 21: return None
        avgv = statistics.mean(vl[-21:-1])
        return vl[-1] / avgv if avgv > 0 else None
    except Exception:
        return None


def price(c, coin):
    try: return float(c.get_ticker(coin)["closing_price"])
    except Exception: return 0.0


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
    wl = watchlist(c); wl_day = datetime.now(KST).date()
    log.info(f"무작위 기준선 시작 [{mode}] — 거래대금상위{TOPN} 무작위선택 | 손절-{SL}% 트레일(고점+{TRAIL_TRIGGER}%→-{TRAIL}%) {TIMEOUT_H}H타임아웃 | 슬롯{SLOTS}")
    try:
        from bithumb import notify
        notify.send(f"🎲 무작위 기준선(탐험2호) 시작 [{mode}] — 거래대금 상위 코인 무작위 진입, 대조군용. 실주문0(미arm)")
    except Exception: pass

    while True:
        try:
            if datetime.now(KST).date() != wl_day:
                wl_day = datetime.now(KST).date(); wl = watchlist(c)
            cool = {k: v for k, v in cool.items() if v > time.time()}
            live = is_live()
            cap = load_config().get("engine_caps_krw", {}).get("random", 0)
            entry_krw = (cap / SLOTS) if (live and cap) else ENTRY_KRW_DRY

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
                        g = LiveGuard("random")
                        res = g.execute_sell(c, f"KRW-{coin}", p["volume"], krw_hint=cur*p["volume"])
                        if res.get("error"):
                            log.error(f"[실전] 매도 실패 {coin}: {res.get('error')} — 포지션 유지")
                            continue
                        g.record_realized((cur - p["entry"]) * p["volume"])
                    tag = "[실전]" if p.get("live") else "[모의]"
                    held_h = (time.time() - p["entered_ts"]) / 3600
                    log.info(f"{tag} 청산 {coin} @{cur:,.4f} PnL={pnl:+.2f}% | {reason} ({held_h:.1f}H보유)")
                    log_trade([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin, f"{p['entry']:.4f}", f"{cur:.4f}",
                               f"{pnl:+.2f}", reason, f"{held_h:.1f}", f"{p.get('chg24',0):+.1f}",
                               p.get("rsi", ""), p.get("vol_ratio", "")])
                    try:
                        from bithumb import notify
                        notify.send(f"🎲 무작위 청산 {coin} {pnl:+.1f}% [{reason}] {tag}")
                    except Exception: pass
                    cool[coin] = time.time() + COOLDOWN_H * 3600
                    del pos[coin]; save_json(POS, pos); save_json(COOLDOWN_F, cool)

            # ── 무작위 진입 ──
            if len(pos) < SLOTS:
                candidates = [x for x in wl if x not in pos and x not in EXCLUDE and cool.get(x, 0) <= time.time()]
                random.shuffle(candidates)
                for coin in candidates:
                    if len(pos) >= SLOTS: break
                    cur = price(c, coin)
                    if cur <= 0: continue
                    rsi = rsi_of(c, coin)
                    vr = vol_ratio_of(c, coin)
                    try:
                        chg24 = float(c.get_ticker(coin).get("fluctate_rate_24H", 0) or 0)
                    except Exception:
                        chg24 = 0.0
                    volume = 0.0
                    if live:
                        g = LiveGuard("random"); res = g.execute_buy(c, f"KRW-{coin}", entry_krw)
                        if res.get("dry"): continue
                        if res.get("error"):
                            log.error(f"[실전] 매수 실패 {coin}: {res.get('error')}"); continue
                        volume = entry_krw*(1-0.0004)/cur
                    pos[coin] = {"entry": cur, "highest": cur, "chg24": round(chg24,1),
                                 "rsi": round(rsi,1) if rsi is not None else None,
                                 "vol_ratio": round(vr,2) if vr is not None else None,
                                 "volume": volume, "entered_ts": time.time(),
                                 "timeout": time.time()+TIMEOUT_H*3600,
                                 "entered": datetime.now(KST).isoformat(), "live": live}
                    save_json(POS, pos)
                    tag = "[실전]" if live else "[모의]"
                    log.info(f"{tag} 무작위진입 {coin} @{cur:,.4f} — 24H{chg24:+.1f}% RSI{rsi} 거래량비{vr}")
                    try:
                        from bithumb import notify
                        notify.send(f"🎲 무작위 진입 {coin} 24H{chg24:+.1f}% {tag}")
                    except Exception: pass
                    break  # 사이클당 1개만 무작위 진입(연속 몰빵 방지)
            save_json(POS, pos)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
