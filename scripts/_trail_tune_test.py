"""[검증] RT 청산(트레일) 튜닝 비교 — 진입은 동일(돌파-재테스트), 청산만 바꿈.
오늘 관찰: 고정3% 트레일이 잡코인 변동성에 헐거워 고점 수익을 게워냄.
후보: 고정3%(현재)·2%·1.5% / ATR 샹들리에(변동성적응) / 부분익절 하이브리드.
RT와 동일: 상위거래대금 PIT 필터, 단일슬롯 직렬, walk-forward TEST[0.6,1.0).
비용 0.16%/0.30%. 각 exit은 gross수익률 반환, 비용은 사후 차감."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.retest_backtest import signals_retest_limit
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, summarize, BARS_PER_DAY,
)

TOPN, SL, TIMEOUT, TRAIN = 50, -0.03, 288, 0.6
PARAMS = {"breakout_n": 288, "retest_pct": 0.005}
ATR_PERIOD = 14


def build_atr(candles, period=ATR_PERIOD):
    """ATR 배열(가격단위). TR=max(h-l, |h-pc|, |l-pc|)의 period 이동평균."""
    n = len(candles); tr = [0.0] * n
    for i in range(n):
        h, l = candles[i]["high_price"], candles[i]["low_price"]
        if i == 0:
            tr[i] = h - l
        else:
            pc = candles[i - 1]["trade_price"]
            tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    atr = [0.0] * n; s = 0.0
    for i in range(n):
        s += tr[i]
        if i >= period:
            s -= tr[i - period]
        atr[i] = s / min(i + 1, period)
    return atr


def exit_fixed_trail(candles, idx, epx, atr, trail, act):
    sl_px = epx * (1 + SL); peak = epx; on = False
    end = min(idx + 1 + TIMEOUT, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if not on and cj["low_price"] <= sl_px:
            return (sl_px - epx) / epx, j - idx
        if cj["high_price"] > peak: peak = cj["high_price"]
        if not on and (peak - epx) / epx >= act: on = True
        if on:
            stop = max(sl_px, peak * (1 - trail))
            if cj["low_price"] <= stop:
                return (stop - epx) / epx, j - idx
    last = candles[min(end, len(candles)) - 1]
    return (last["trade_price"] - epx) / epx, end - idx


def exit_atr_chandelier(candles, idx, epx, atr, mult):
    """샹들리에: stop = 고점 − ATR×mult (변동성 적응). 하한 SL -3% 유지."""
    sl_px = epx * (1 + SL); peak = epx
    end = min(idx + 1 + TIMEOUT, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if cj["high_price"] > peak: peak = cj["high_price"]
        stop = max(sl_px, peak - atr[j] * mult)
        if cj["low_price"] <= stop:
            return (stop - epx) / epx, j - idx
    last = candles[min(end, len(candles)) - 1]
    return (last["trade_price"] - epx) / epx, end - idx


def exit_partial(candles, idx, epx, atr, tp_level, tp_frac, trail, act):
    """부분익절: +tp_level 도달 시 tp_frac 매도, 나머지는 트레일."""
    sl_px = epx * (1 + SL); peak = epx; on = False
    tp_px = epx * (1 + tp_level); took = False; realized = 0.0; rem = 1.0
    end = min(idx + 1 + TIMEOUT, len(candles))
    for j in range(idx + 1, end):
        cj = candles[j]
        if not took and cj["high_price"] >= tp_px:
            realized += tp_frac * tp_level; rem -= tp_frac; took = True
        if not on and cj["low_price"] <= sl_px:
            return realized + rem * SL, j - idx
        if cj["high_price"] > peak: peak = cj["high_price"]
        if not on and (peak - epx) / epx >= act: on = True
        if on:
            stop = max(sl_px, peak * (1 - trail))
            if cj["low_price"] <= stop:
                return realized + rem * (stop - epx) / epx, j - idx
    last = candles[min(end, len(candles)) - 1]
    return realized + rem * (last["trade_price"] - epx) / epx, end - idx


def run(cc, atrs, pit, exit_fn, lo, hi):
    events = []
    for coin, candles in cc.items():
        nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
        for idx, epx in signals_retest_limit(candles, **PARAMS):
            if idx + 1 >= nlen or not (a <= idx < b):
                continue
            events.append((candles[idx]["candle_date_time_kst"], coin, idx, epx))
    events.sort()
    out = []; busy = ""
    for kst, coin, idx, epx in events:
        if kst < busy:
            continue
        if not is_topn(coin, kst, pit, TOPN):
            continue
        candles = cc[coin]
        gross, bars = exit_fn(candles, idx, epx, atrs[coin])
        exit_kst = candles[min(idx + bars, len(candles) - 1)]["candle_date_time_kst"]
        out.append({"entry_kst": kst, "gross": gross, "exit_kst": exit_kst})
        busy = exit_kst
    return out


def stats(label, g, cost):
    tr = [{"pnl_pct": x["gross"] - cost, "entry_kst": x["entry_kst"]} for x in g]
    s = summarize(tr); n = s["n"]
    if not n:
        print(f"  {label:20} 0건"); return
    p = [t["pnl_pct"] * 100 for t in tr]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    tval = avg / (sd / n ** 0.5) if sd else 0
    print(f"  {label:20} {n:3}건 승률{s['win_rate']:3.0f}% 비용후평균{avg:+.2f}% "
          f"t{tval:+.2f} MDD{s['mdd_pct']:3.0f}% 누적{s['total_pct']:+7.1f}%")


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    atrs = {c: build_atr(v) for c, v in cc.items()}
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 | TEST[0.6,1.0) | 진입=재테스트 동일, 청산만 변경\n")

    variants = [
        ("고정3%(현재)", lambda ca, i, e, a: exit_fixed_trail(ca, i, e, a, 0.03, 0.01)),
        ("고정2%",       lambda ca, i, e, a: exit_fixed_trail(ca, i, e, a, 0.02, 0.01)),
        ("고정1.5%",     lambda ca, i, e, a: exit_fixed_trail(ca, i, e, a, 0.015, 0.01)),
        ("ATR샹들리에x2", lambda ca, i, e, a: exit_atr_chandelier(ca, i, e, a, 2.0)),
        ("ATR샹들리에x3", lambda ca, i, e, a: exit_atr_chandelier(ca, i, e, a, 3.0)),
        ("부분익절3%반+트3%", lambda ca, i, e, a: exit_partial(ca, i, e, a, 0.03, 0.5, 0.03, 0.01)),
    ]
    runs = {name: run(cc, atrs, pit, fn, TRAIN, 1.0) for name, fn in variants}
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for name, _ in variants:
            stats(name, runs[name], cost)
        print()
