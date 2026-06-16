"""[검증] 평균회귀(mean-reversion) 백테스트 — RT(돌파)와 정반대.
가설: 가격이 볼린저밴드 하단(평균-k*표준편차) 아래로 과도하게 빠지면
다시 평균(중심선)으로 되돌아온다 → 하단에서 사서 중심선에서 판다.
횡보장에서 작동하는 전략. RT가 굶는 구간을 보완하는지 본다.

- 진입: 종가 <= 하단밴드(BB k시그마). 그 가격에 지정가 매수 가정.
- 청산: 고가 >= 중심선(SMA) → 익절 / 저가 <= 진입*(1-SL) → 손절 / 타임아웃.
- RT와 동일: 상위거래대금 PIT 필터, 단일슬롯 직렬, walk-forward TEST[0.6,1.0).
- 비용 0.16%/0.30% 둘 다. point-in-time(그 봉까지 데이터만, lookahead 없음)."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, summarize, BARS_PER_DAY,
)

TOPN, SL, TIMEOUT, TRAIN = 50, -0.03, 288, 0.6


def bb_signals(candles, period, k):
    """종가가 하단밴드 이하로 내려간 봉 → (idx, 진입가=하단밴드, mid=중심선)."""
    out = []
    closes = [c["trade_price"] for c in candles]
    for i in range(period, len(candles) - 1):
        win = closes[i - period:i]
        sma = sum(win) / period
        sd = st.pstdev(win)
        lower = sma - k * sd
        if closes[i] <= lower and sd > 0:
            out.append((i, lower, sma))
    return out


def simulate_exit(candles, idx, entry_px, mid):
    """중심선 도달=익절 / SL / 타임아웃."""
    sl_px = entry_px * (1 + SL)
    end = min(idx + 1 + TIMEOUT, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if cj["low_price"] <= sl_px:
            return sl_px, "SL", j - idx
        if cj["high_price"] >= mid:
            return mid, "TP", j - idx
    last = candles[min(end, len(candles)) - 1]
    return last["trade_price"], "TO", end - idx


def run(coin_candles, pit, period, k, cost, lo, hi):
    events = []
    for coin, candles in coin_candles.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx, mid in bb_signals(candles, period, k):
            if not (a <= idx < b):
                continue
            events.append((candles[idx]["candle_date_time_kst"], coin, idx, epx, mid))
    events.sort()
    trades = []; busy = ""
    for kst, coin, idx, epx, mid in events:
        if kst < busy:
            continue
        if not is_topn(coin, kst, pit, TOPN):
            continue
        candles = coin_candles[coin]
        ex_px, reason, bars = simulate_exit(candles, idx, epx, mid)
        pnl = (ex_px - epx) / epx - cost
        exit_kst = candles[min(idx + bars, len(candles) - 1)]["candle_date_time_kst"]
        trades.append({"coin": coin, "entry_kst": kst, "exit_kst": exit_kst,
                       "pnl_pct": pnl, "reason": reason})
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
    print(f"데이터 {len(cc)}개 코인 | TEST[0.6,1.0) | 평균회귀(BB하단매수→중심선매도)\n")
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for period in (20, 48):
            for k in (2.0, 2.5):
                hrs = period * 5 / 60
                report(f"BB{period}({hrs:.0f}h) {k}시그마", run(cc, pit, period, k, cost, TRAIN, 1.0))
        print()
