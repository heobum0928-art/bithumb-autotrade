"""[검증] RT + 시간대(KST) 필터 — 시즌성 신호를 RT 타이밍필터로.
시즌성 TRAIN 결과: 좋은시간 06,23,15,12,20 / 나쁜시간 19,08,21,01,03.
RT(돌파-재테스트, 트레일2%)에 진입시각 필터를 끼워 비용후엣지·t가 오르나 비교.
- 필터: 없음 / 나쁜시간 차단 / 좋은시간만 / (3개·5개 버전).
- RT와 동일: 상위거래대금 PIT, 단일슬롯 직렬, walk-forward TEST[0.6,1.0). 비용 0.16/0.30%.
- 시간대는 시즌성 TRAIN[0,0.6)서 선택 → RT TEST[0.6,1.0)서 평가 = out-of-sample."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.retest_backtest import signals_retest_limit
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, summarize, BARS_PER_DAY,
)

TOPN, SL, TIMEOUT, TRAIN = 50, -0.03, 288, 0.6
PARAMS = {"breakout_n": 288, "retest_pct": 0.005}
TRAIL, ACT = 0.02, 0.01

BEST5 = {6, 23, 15, 12, 20}
WORST5 = {19, 8, 21, 1, 3}
BEST3 = {6, 23, 15}
WORST3 = {19, 8, 21}


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


def run(cc, pit, hour_ok, cost, lo, hi):
    events = []
    for coin, candles in cc.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_retest_limit(candles, **PARAMS):
            if idx + 1 >= nlen or not (a <= idx < b):
                continue
            kst = candles[idx]["candle_date_time_kst"]
            events.append((kst, coin, idx, epx))
    events.sort()
    trades = []; busy = ""
    for kst, coin, idx, epx in events:
        if kst < busy:
            continue
        hour = int(kst[11:13])
        if hour_ok is not None and not hour_ok(hour):
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
        print(f"  {label:22} 0건"); return
    p = [t["pnl_pct"] * 100 for t in tr]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    tval = avg / (sd / n ** 0.5) if sd else 0
    print(f"  {label:22} {n:3}건 승률{s['win_rate']:3.0f}% 비용후평균{avg:+.2f}% "
          f"t{tval:+.2f} MDD{s['mdd_pct']:3.0f}% 누적{s['total_pct']:+7.1f}%")


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 | TEST[0.6,1.0) | RT(트레일2%) + 시간대필터\n")
    filters = [
        ("필터없음(RT기준)", None),
        ("나쁜시간 차단(3)", lambda h: h not in WORST3),
        ("나쁜시간 차단(5)", lambda h: h not in WORST5),
        ("좋은시간만(5)", lambda h: h in BEST5),
        ("좋은시간만(3)", lambda h: h in BEST3),
    ]
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for name, fn in filters:
            report(name, run(cc, pit, fn, cost, TRAIN, 1.0))
        print()
