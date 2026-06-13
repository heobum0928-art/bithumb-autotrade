"""
[정식 검증] retest 진입 고정 + 청산방식 walk-forward 비교.
  TRAIN[0,0.6)에서 트레일폭 선택 → TEST[0.6,1.0)에서 TP6% vs 선택트레일 검증 + 편중 분해.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import scripts.retest_backtest as rb
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, BARS_PER_DAY, summarize,
)

TOPN       = 50
PARAMS     = {"breakout_n": 288, "retest_pct": 0.005}
SL         = -0.03
TIMEOUT    = 288
COST       = 0.0016
TRAIN_FRAC = 0.6
TRAILS     = [0.015, 0.02, 0.03, 0.05]

_orig_exit = rb.simulate_exit


def make_trail(trail, activate=0.01):
    def _exit(candles, idx, entry_px, tp, sl, timeout_bars):
        sl_px = entry_px * (1 + sl)
        peak = entry_px
        activated = False
        end = min(idx + 1 + timeout_bars, len(candles))
        for j in range(idx + 1, end):
            cj = candles[j]
            if not activated and cj["low_price"] <= sl_px:
                return {"exit": sl_px, "reason": "SL", "bars": j - idx}
            if cj["high_price"] > peak:
                peak = cj["high_price"]
            if not activated and (peak - entry_px) / entry_px >= activate:
                activated = True
            if activated:
                stop = max(sl_px, peak * (1 - trail))
                if cj["low_price"] <= stop:
                    return {"exit": stop, "reason": "TRAIL", "bars": j - idx}
        last = candles[min(end, len(candles)) - 1]
        return {"exit": last["trade_price"], "reason": "TIMEOUT", "bars": end - idx}
    return _exit


def stats(tr):
    s = summarize(tr)
    if not tr:
        return s, 0, 0, 0
    pnls = [t["pnl_pct"] for t in tr]
    maxw = max(pnls) * 100
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avgw = (sum(wins) / len(wins) * 100) if wins else 0
    avgl = (sum(losses) / len(losses) * 100) if losses else 0
    return s, maxw, avgw, avgl


def line(label, tr):
    s, maxw, avgw, avgl = stats(tr)
    rr = abs(avgw / avgl) if avgl else 0
    print(f"  {label:16} {s['n']:3}건 승률{s['win_rate']:3.0f}% 누적{s['total_pct']:+7.1f}% "
          f"MDD{s['mdd_pct']:3.0f}% | 최대+{maxw:5.1f}% 손익비{rr:.2f}")


if __name__ == "__main__":
    coins = cached_coins()
    cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 코인 | walk-forward 60/40 | 비용 0.16%\n")

    # TRAIN: 트레일폭 선택
    print("=== TRAIN [0,0.6) — 트레일폭 선택 ===")
    best, best_score = None, -1e9
    for trail in TRAILS:
        rb.simulate_exit = make_trail(trail)
        tr = rb.run_serial_limit(cc, pit, TOPN, PARAMS, 0.06, SL, TIMEOUT, COST, 0.0, TRAIN_FRAC)
        s = summarize(tr)
        mark = ""
        if s["n"] >= 10 and s["total_pct"] > best_score:
            best_score, best = s["total_pct"], trail
            mark = " <-"
        print(f"  트레일{trail*100:4.1f}%: {s['n']:3}건 누적{s['total_pct']:+7.1f}%{mark}")
    print(f"  => 선택: 트레일 {best*100:.1f}%\n")

    # TEST: 현재 TP6% vs 선택 트레일
    print("=== TEST [0.6,1.0) — out-of-sample 검증 ===")
    rb.simulate_exit = _orig_exit
    tp_tr = rb.run_serial_limit(cc, pit, TOPN, PARAMS, 0.06, SL, TIMEOUT, COST, TRAIN_FRAC, 1.0)
    line("TP+6%(현재)", tp_tr)

    rb.simulate_exit = make_trail(best)
    tl_tr = rb.run_serial_limit(cc, pit, TOPN, PARAMS, 0.06, SL, TIMEOUT, COST, TRAIN_FRAC, 1.0)
    line(f"트레일{best*100:.1f}%(선택)", tl_tr)

    # 편중 분해 (선택 트레일, test)
    print(f"\n=== 코인별 분해 (TEST, 트레일{best*100:.1f}%) — 편중 확인 ===")
    bycoin = {}
    for t in tl_tr:
        bycoin.setdefault(t["coin"], []).append(t["pnl_pct"])
    rows = sorted(bycoin.items(), key=lambda x: -sum(x[1]))
    total = sum(t["pnl_pct"] for t in tl_tr)
    for coin, pnls in rows[:8]:
        print(f"  {coin:8} {len(pnls):2}건 누적{sum(pnls)*100:+6.1f}%")
    if rows and total:
        top1 = sum(rows[0][1])
        print(f"  => 최대기여 {rows[0][0]}: 전체 수익의 {top1/total*100:.0f}%  (40%↑면 편중 의심)")
    rb.simulate_exit = _orig_exit
