"""[검증] 스윙 모멘텀 — 단타와 같은 '돌파' 엣지를 시간축만 길게.
5분봉을 4시간봉으로 묶어, N일 신고가 돌파 시 진입 → 며칠 보유,
넓은 트레일링(+발동/트레일%)으로 청산. 가설: 시간축이 길면 노이즈·경쟁이
줄어 얇은 모멘텀 엣지가 살아남는다(수수료 비중도 작아짐).

- RT와 동일: 상위거래대금 PIT 필터, 단일슬롯 직렬, walk-forward TEST[0.6,1.0).
- 비용 0.16%/0.30%. point-in-time(그 봉까지만)."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, summarize, BARS_PER_DAY,
)

TOPN, TRAIN = 50, 0.6
BARS_4H = 48  # 5분봉 48개 = 4시간


def resample_4h(c5):
    """5분봉 → 4시간봉(인덱스 48개씩 묶음)."""
    out = []
    for i in range(0, len(c5) - BARS_4H + 1, BARS_4H):
        chunk = c5[i:i + BARS_4H]
        out.append({
            "candle_date_time_kst": chunk[0]["candle_date_time_kst"],
            "opening_price": chunk[0]["opening_price"],
            "high_price": max(x["high_price"] for x in chunk),
            "low_price": min(x["low_price"] for x in chunk),
            "trade_price": chunk[-1]["trade_price"],
        })
    return out


def signals_breakout(candles, n):
    """종가가 직전 n봉 신고가 돌파 → (idx, 진입가=다음봉 시가 대용=종가)."""
    out = []
    for i in range(n, len(candles) - 1):
        ph = max(c["high_price"] for c in candles[i - n:i])
        if candles[i]["trade_price"] > ph:
            out.append((i, candles[i]["trade_price"]))
    return out


def trail_exit(candles, idx, entry_px, trail, act, sl, tb):
    sl_px = entry_px * (1 + sl); peak = entry_px; on = False
    end = min(idx + 1 + tb, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if not on and cj["low_price"] <= sl_px:
            return sl_px, j - idx
        if cj["high_price"] > peak: peak = cj["high_price"]
        if not on and (peak - entry_px) / entry_px >= act: on = True
        if on:
            stop = max(sl_px, peak * (1 - trail))
            if cj["low_price"] <= stop:
                return stop, j - idx
    last = candles[min(end, len(candles)) - 1]
    return last["trade_price"], end - idx


def run(cc4, pit, n, trail, act, sl, tb, cost, lo, hi):
    events = []
    for coin, candles in cc4.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_breakout(candles, n):
            if not (a <= idx < b):
                continue
            events.append((candles[idx]["candle_date_time_kst"], coin, idx, epx))
    events.sort()
    trades = []; busy = ""
    for kst, coin, idx, epx in events:
        if kst < busy:
            continue
        if not is_topn(coin, kst, pit, TOPN):
            continue
        candles = cc4[coin]
        ex_px, bars = trail_exit(candles, idx, epx, trail, act, sl, tb)
        pnl = (ex_px - epx) / epx - cost
        exit_kst = candles[min(idx + bars, len(candles) - 1)]["candle_date_time_kst"]
        trades.append({"coin": coin, "entry_kst": kst, "exit_kst": exit_kst, "pnl_pct": pnl})
        busy = exit_kst
    return trades


def report(label, tr):
    s = summarize(tr); n = s["n"]
    if not n:
        print(f"  {label:26} 0건"); return
    p = [t["pnl_pct"] * 100 for t in tr]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    tval = avg / (sd / n ** 0.5) if sd else 0
    print(f"  {label:26} {n:3}건 승률{s['win_rate']:3.0f}% 비용후평균{avg:+.2f}% "
          f"t{tval:+.2f} MDD{s['mdd_pct']:3.0f}% 누적{s['total_pct']:+7.1f}%")


if __name__ == "__main__":
    coins = cached_coins(); cc4 = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc4[c] = resample_4h(d)
    pit = build_pit_volume({c: fetch_5m(c) for c in cc4})  # PIT은 5분봉 기준
    print(f"데이터 {len(cc4)}개 코인 | 4H봉 | TEST[0.6,1.0) | 스윙 모멘텀(N일 돌파+트레일)\n")
    # n: 4H봉 개수 (30=5일, 42=7일), 트레일/SL은 스윙용 넓게, 타임아웃 84봉=14일
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for n, days in ((30, 5), (42, 7)):
            for trail in (0.08, 0.12):
                report(f"{days}일돌파 트레일{trail*100:.0f}%",
                       run(cc4, pit, n, trail, 0.03, -0.08, 84, cost, TRAIN, 1.0))
        print()
