"""[검증] RT 엣지의 비용 민감도. 진짜 per-trade 엣지가 얇으면(+0.5%대)
실전 마찰(슬리피지·체결불리)이 커질 때 0/음수로 붕괴하는지 확인.
비용 0.16%(이상)→0.30→0.40→0.50%로 올려가며 비용후평균·t값·누적을 비교.
청산은 live RT와 동일 트레일3%. 장세필터 OFF와 N=12(1h) 둘 다."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.retest_backtest import signals_retest_limit
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, summarize, BARS_PER_DAY,
)

TOPN, SL, TIMEOUT, TRAIN = 50, -0.03, 288, 0.6
PARAMS = {"breakout_n": 288, "retest_pct": 0.005}
TRAIL, ACT = 0.03, 0.01


def trail_exit(candles, idx, entry_px):
    sl_px = entry_px * (1 + SL); peak = entry_px; on = False
    end = min(idx + 1 + TIMEOUT, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if not on and cj["low_price"] <= sl_px:
            return sl_px, j - idx
        if cj["high_price"] > peak: peak = cj["high_price"]
        if not on and (peak - entry_px) / entry_px >= ACT: on = True
        if on:
            stop = max(sl_px, peak * (1 - TRAIL))
            if cj["low_price"] <= stop:
                return stop, j - idx
    last = candles[min(end, len(candles)) - 1]
    return last["trade_price"], end - idx


def btc_regime(btc, n):
    closes = [c["trade_price"] for c in btc]; out = {}; s = 0.0
    for i, c in enumerate(btc):
        s += closes[i]
        if i >= n: s -= closes[i - n]
        if i >= n - 1: out[c["candle_date_time_kst"]] = closes[i] > s / n
    return out


def gross_trades(coin_candles, pit, lo, hi, regime=None):
    """비용 0 기준 gross 수익률 거래 리스트(비용은 나중에 차감)."""
    events = []
    for coin, candles in coin_candles.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_retest_limit(candles, **PARAMS):
            if idx + 1 >= nlen or not (a <= idx < b): continue
            events.append((candles[idx]["candle_date_time_kst"], coin, idx, epx))
    events.sort()
    out = []; busy = ""
    for kst, coin, idx, epx in events:
        if kst < busy: continue
        if regime is not None and not regime.get(kst, False): continue
        if not is_topn(coin, kst, pit, TOPN): continue
        candles = coin_candles[coin]
        ex_px, bars = trail_exit(candles, idx, epx)
        gross = (ex_px - epx) / epx
        exit_kst = candles[min(idx + bars, len(candles) - 1)]["candle_date_time_kst"]
        out.append({"entry_kst": kst, "gross": gross, "exit_kst": exit_kst})
        busy = exit_kst
    return out


def stats_at_cost(gross, cost):
    tr = [{"pnl_pct": g["gross"] - cost, "entry_kst": g["entry_kst"]} for g in gross]
    s = summarize(tr); n = s["n"]
    if not n: return None
    p = [t["pnl_pct"] * 100 for t in tr]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    adj = avg  # cost 이미 차감됨
    tval = adj / (sd / n ** 0.5) if sd else 0
    return n, s["win_rate"], adj, tval, s["total_pct"]


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5: cc[c] = d
    btc = fetch_5m("BTC")
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 코인 | TEST[0.6,1.0) | 트레일3% | 비용 민감도\n")
    reg12 = btc_regime(btc, 12)
    g_off = gross_trades(cc, pit, TRAIN, 1.0, None)
    g_on = gross_trades(cc, pit, TRAIN, 1.0, reg12)
    for label, g in (("필터 OFF", g_off), ("필터 ON N=12(1h)", g_on)):
        print(f"=== {label} ({len(g)}건) ===")
        for cost in (0.0016, 0.0030, 0.0040, 0.0050):
            r = stats_at_cost(g, cost)
            if r:
                n, wr, adj, tv, tot = r
                flag = "살아있음" if adj > 0 and tv >= 1.0 else ("붕괴" if adj <= 0 else "약함")
                print(f"  비용{cost*100:.2f}%: 평균{adj:+.2f}% t{tv:+.2f} 누적{tot:+7.1f}%  → {flag}")
        print()
