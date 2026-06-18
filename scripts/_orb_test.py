"""[검증] ORB(오프닝 레인지 돌파) — RT(24h 롤링 신고가)와 다른 시간앵커 메커니즘.
가설: 하루 초반 N분(오프닝 레인지)의 고가를 그날 안에 돌파하면 추세 시작.
- 세션앵커: KST 날짜 경계(00:00). 초반 K봉의 고가/저가 = 오프닝 레인지(OR).
- 진입: OR 확정 후, 그날 종가가 OR고가 돌파하는 첫 봉(상승 돌파).
- 청산: 트레일 2%(+1%발동) SL-3% 24h (RT 2%와 동일 — 진입만 다름).
- RT와 동일: 상위거래대금 PIT 필터, 단일슬롯 직렬, walk-forward TEST[0.6,1.0). 비용 0.16/0.30%."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, summarize, BARS_PER_DAY,
)

TOPN, SL, TIMEOUT, TRAIN = 50, -0.03, 288, 0.6
TRAIL, ACT = 0.02, 0.01


def signals_orb(candles, K):
    """KST 날짜별 초반 K봉 OR고가 돌파 첫 봉 → (idx, 진입가=종가). 하루 1신호."""
    out = []
    # 날짜별 인덱스 그룹
    by_day = {}
    for i, c in enumerate(candles):
        day = c["candle_date_time_kst"][:10]
        by_day.setdefault(day, []).append(i)
    for day, idxs in by_day.items():
        if len(idxs) <= K:
            continue
        or_hi = max(candles[i]["high_price"] for i in idxs[:K])
        # OR 확정 후 첫 상승 돌파
        for i in idxs[K:]:
            if candles[i]["trade_price"] > or_hi and candles[i]["trade_price"] > candles[i]["opening_price"]:
                out.append((i, candles[i]["trade_price"]))
                break
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


def run(cc, pit, K, cost, lo, hi):
    events = []
    for coin, candles in cc.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_orb(candles, K):
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
        print(f"  {label:16} 0건"); return
    p = [t["pnl_pct"] * 100 for t in tr]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    tval = avg / (sd / n ** 0.5) if sd else 0
    print(f"  {label:16} {n:3}건 승률{s['win_rate']:3.0f}% 비용후평균{avg:+.2f}% "
          f"t{tval:+.2f} MDD{s['mdd_pct']:3.0f}% 누적{s['total_pct']:+7.1f}%")


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 | TEST[0.6,1.0) | ORB(KST일 초반K봉 박스돌파) 진입, 트레일2%\n")
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for K in (6, 12, 18):   # 오프닝 30m / 1h / 90m
            report(f"OR {K*5}분", run(cc, pit, K, cost, TRAIN, 1.0))
        print()
