"""[검증] RT(돌파-재테스트)에 'BTC 장세 필터'를 붙이면 약세장 가짜돌파를 걸러
엣지가 살아나는지 백테스트. 가설: BTC 상승추세(가격>N봉 SMA)일 때만 진입.

- 청산은 live RT와 동일한 트레일3%(+1%발동) SL-3% 타임아웃24h.
- 운영 코드 미수정 — 기존 함수 재사용 + 직렬 루프에 regime_ok(kst) 게이트만 추가.
- point-in-time: 각 진입 시점의 BTC SMA는 그 봉까지의 종가만 사용(lookahead 없음).
- walk-forward TEST 구간[0.6,1.0)에서 필터 OFF vs ON(N=12/144/288) 비교."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.retest_backtest import signals_retest_limit
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, build_pit_volume as _b,
    is_topn, summarize, BARS_PER_DAY,
)

TOPN, SL, TIMEOUT, COST, TRAIN = 50, -0.03, 288, 0.0016, 0.6
PARAMS = {"breakout_n": 288, "retest_pct": 0.005}
TRAIL, ACT = 0.03, 0.01


def trail_exit(candles, idx, entry_px, sl, tb):
    sl_px = entry_px * (1 + sl); peak = entry_px; on = False
    end = min(idx + 1 + tb, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if not on and cj["low_price"] <= sl_px:
            return sl_px, "SL", j - idx
        if cj["high_price"] > peak: peak = cj["high_price"]
        if not on and (peak - entry_px) / entry_px >= ACT: on = True
        if on:
            stop = max(sl_px, peak * (1 - TRAIL))
            if cj["low_price"] <= stop:
                return stop, "TR", j - idx
    last = candles[min(end, len(candles)) - 1]
    return last["trade_price"], "TO", end - idx


def btc_regime(btc, n):
    """{kst: BTC가 n봉 SMA 위인가}. point-in-time(그 봉까지 종가만)."""
    closes = [c["trade_price"] for c in btc]
    out = {}
    s = 0.0
    for i, c in enumerate(btc):
        s += closes[i]
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            sma = s / n
            out[c["candle_date_time_kst"]] = closes[i] > sma
    return out


def run(coin_candles, pit, lo, hi, regime=None):
    events = []
    for coin, candles in coin_candles.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_retest_limit(candles, **PARAMS):
            if idx + 1 >= nlen or not (a <= idx < b):
                continue
            events.append((candles[idx]["candle_date_time_kst"], coin, idx, epx))
    events.sort()
    trades = []; busy = ""
    for kst, coin, idx, epx in events:
        if kst < busy:
            continue
        if regime is not None and not regime.get(kst, False):
            continue  # BTC 약세 — 진입 스킵
        if not is_topn(coin, kst, pit, TOPN):
            continue
        candles = coin_candles[coin]
        ex_px, reason, bars = trail_exit(candles, idx, epx, SL, TIMEOUT)
        pnl = (ex_px - epx) / epx - COST
        exit_kst = candles[min(idx + bars, len(candles) - 1)]["candle_date_time_kst"]
        trades.append({"coin": coin, "entry_kst": kst, "exit_kst": exit_kst, "pnl_pct": pnl})
        busy = exit_kst
    return trades


def stats(label, tr):
    s = summarize(tr); n = s["n"]
    if n == 0:
        print(f"  {label:18} 0건"); return
    p = [t["pnl_pct"] * 100 for t in tr]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0; adj = avg - 0.16
    tval = adj / (sd / n ** 0.5) if sd else 0
    print(f"  {label:18} {n:3}건 승률{s['win_rate']:3.0f}% 비용후평균{adj:+.2f}% "
          f"t{tval:+.2f} MDD{s['mdd_pct']:3.0f}% 누적{s['total_pct']:+7.1f}%")


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    btc = fetch_5m("BTC")
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 코인 + BTC | TEST[0.6,1.0) | 트레일3% | 비용0.16%\n")
    print("=== BTC 장세 필터: OFF vs ON(가격>N봉SMA) ===")
    stats("필터 OFF(기준)", run(cc, pit, TRAIN, 1.0, None))
    for n in (12, 144, 288):
        reg = btc_regime(btc, n)
        hrs = n * 5 / 60
        stats(f"ON N={n}({hrs:.0f}h)", run(cc, pit, TRAIN, 1.0, reg))
