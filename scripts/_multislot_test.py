"""[검증] 멀티슬롯(동시 N개 보유) 비교 — 진입/청산 전략은 동일(retest+트레일3%),
동시 보유 슬롯 수만 1·2·3으로. 슬롯 늘리면 단일슬롯이 놓친 신호도 잡음.
거래당 평균수익이 유지되면 → 멀티슬롯으로 표본 가속 가능."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import scripts.retest_backtest as rb
from scripts.retest_backtest import signals_retest_limit
from scripts.alt_entry_backtest import cached_coins, fetch_5m, build_pit_volume, BARS_PER_DAY, is_topn

TOPN, SL, TIMEOUT, COST, TRAIN = 50, -0.03, 288, 0.0016, 0.6
PARAMS = {"breakout_n": 288, "retest_pct": 0.005}


def make_trail(trail=0.03, act=0.01):
    def _e(candles, idx, entry_px, tp, sl, tb):
        sl_px = entry_px * (1 + sl); peak = entry_px; on = False
        end = min(idx + 1 + tb, len(candles))
        for j in range(idx + 1, end):
            cj = candles[j]
            if not on and cj["low_price"] <= sl_px:
                return {"exit": sl_px, "reason": "SL", "bars": j - idx}
            if cj["high_price"] > peak: peak = cj["high_price"]
            if not on and (peak - entry_px) / entry_px >= act: on = True
            if on:
                stop = max(sl_px, peak * (1 - trail))
                if cj["low_price"] <= stop:
                    return {"exit": stop, "reason": "TR", "bars": j - idx}
        last = candles[min(end, len(candles)) - 1]
        return {"exit": last["trade_price"], "reason": "TO", "bars": end - idx}
    return _e


def run_multi(cc, pit, nslots, lo, hi):
    events = []
    for coin, candles in cc.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_retest_limit(candles, **PARAMS):
            if idx + 1 >= nlen or not (a <= idx < b):
                continue
            events.append((candles[idx]["candle_date_time_kst"], coin, idx, epx))
    events.sort()
    trades = []; slots = [""] * nslots
    for kst, coin, idx, epx in events:
        free = next((s for s in range(nslots) if kst >= slots[s]), None)
        if free is None:
            continue
        if not is_topn(coin, kst, pit, TOPN):
            continue
        candles = cc[coin]
        ex = rb.simulate_exit(candles, idx, epx, 0.06, SL, TIMEOUT)
        pnl = (ex["exit"] - epx) / epx - COST
        exit_kst = candles[min(idx + ex["bars"], len(candles) - 1)]["candle_date_time_kst"]
        trades.append(pnl * 100)
        slots[free] = exit_kst
    return trades


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 | TEST [0.6,1.0) | retest+트레일3% | 비용 0.16%\n")
    rb.simulate_exit = make_trail()
    print("=== 동시 보유 슬롯 수 비교 ===")
    for N in (1, 2, 3):
        tr = run_multi(cc, pit, N, TRAIN, 1.0)
        n = len(tr); avg = sum(tr) / n; sd = st.pstdev(tr) if n > 1 else 0
        tval = avg / (sd / n**0.5) if sd else 0
        wr = sum(1 for p in tr if p > 0) / n * 100
        tot = sum(tr)
        print(f"  {N}슬롯: {n:3}건  승률{wr:3.0f}%  거래당평균{avg:+.2f}%  t{tval:.2f}  "
              f"표본합{tot:+.0f}%")
