"""[검증] 이벤트(거래량 급증) 기반 진입 — RT(가격 신고가 돌파)와 다른 메커니즘.
가설: 뉴스·상장·세력 진입 등 '이벤트'는 거래량 급증으로 먼저 드러남.
직전 N봉 평균거래대금 대비 K배 폭증한 봉 = 이벤트 신호 → 진입.
(진짜 뉴스피드는 과거데이터 없어 백테 불가 → 이벤트의 데이터 흔적=거래량으로 대체)

- 진입: 거래대금(candle_acc_trade_price)이 직전 N봉 평균의 K배 이상 + 상승봉(덤프 제외).
- 청산: 트레일 2%(+1%발동) SL-3% 24h (RT 2%와 동일하게).
- RT와 동일: 상위거래대금 PIT 필터, 단일슬롯 직렬, walk-forward TEST[0.6,1.0). 비용 0.16/0.30%."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, summarize, BARS_PER_DAY,
)

TOPN, SL, TIMEOUT, TRAIN = 50, -0.03, 288, 0.6
TRAIL, ACT = 0.02, 0.01


def signals_volspike(candles, N, K):
    """거래대금이 직전 N봉 평균의 K배↑ + 상승봉 → (idx, 진입가=종가)."""
    out = []
    vol = [c.get("candle_acc_trade_price", 0) for c in candles]
    s = 0.0
    for i in range(len(candles) - 1):
        if i >= N:
            s += vol[i - 1] - vol[i - 1 - N] if i - 1 - N >= 0 else vol[i - 1]
        if i < N:
            continue
        avg = sum(vol[i - N:i]) / N
        if avg <= 0:
            continue
        up = candles[i]["trade_price"] > candles[i]["opening_price"]
        if vol[i] >= K * avg and up:
            out.append((i, candles[i]["trade_price"]))
    return out


def trail_exit(candles, idx, epx):
    sl_px = epx * (1 + SL); peak = epx; on = False
    end = min(idx + 1 + TIMEOUT, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if not on and cj["low_price"] <= sl_px:
            return (sl_px - epx) / epx, j - idx
        if cj["high_price"] > peak: peak = cj["high_price"]
        if not on and (peak - epx) / epx >= ACT: on = True
        if on:
            stop = max(sl_px, peak * (1 - TRAIL))
            if cj["low_price"] <= stop:
                return (stop - epx) / epx, j - idx
    last = candles[min(end, len(candles)) - 1]
    return (last["trade_price"] - epx) / epx, end - idx


def run(cc, pit, N, K, cost, lo, hi):
    events = []
    for coin, candles in cc.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_volspike(candles, N, K):
            if idx + 1 >= nlen or not (a <= idx < b):
                continue
            events.append((candles[idx]["candle_date_time_kst"], coin, idx, epx))
    events.sort()
    trades = []; busy = ""
    for kst, coin, idx, epx in events:
        if kst < busy:
            continue
        if not is_topn(coin, kst, pit, TOPN):
            continue
        candles = cc[coin]
        gross, bars = trail_exit(candles, idx, epx)
        pnl = gross - cost
        exit_kst = candles[min(idx + bars, len(candles) - 1)]["candle_date_time_kst"]
        trades.append({"coin": coin, "entry_kst": kst, "exit_kst": exit_kst, "pnl_pct": pnl})
        busy = exit_kst
    return trades


def report(label, tr):
    s = summarize(tr); n = s["n"]
    if not n:
        print(f"  {label:18} 0건"); return
    p = [t["pnl_pct"] * 100 for t in tr]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    tval = avg / (sd / n ** 0.5) if sd else 0
    print(f"  {label:18} {n:3}건 승률{s['win_rate']:3.0f}% 비용후평균{avg:+.2f}% "
          f"t{tval:+.2f} MDD{s['mdd_pct']:3.0f}% 누적{s['total_pct']:+7.1f}%")


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 | TEST[0.6,1.0) | 이벤트=거래량급증 진입, 트레일2% 청산\n")
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for N in (24, 72):     # 2h / 6h 평균 기준
            for K in (3, 5, 10):
                report(f"N{N*5//60}h K{K}배", run(cc, pit, N, K, cost, TRAIN, 1.0))
        print()
