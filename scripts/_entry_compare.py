"""[검증 v2] 즉시진입 vs 재테스트 — 각자 '실제 진입방식'으로 공정 비교.
  재테스트: 지정가(되돌림 목표가) 진입 — retest_backtest.run_serial_limit
  즉시진입: 시장가(돌파 다음봉 시가) — alt_entry_backtest.run_serial
둘 다 트레일3% 청산 동일. walk-forward 60/40 test."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import scripts.retest_backtest as rb
import scripts.alt_entry_backtest as ab
from scripts.retest_backtest import signals_retest_limit, run_serial_limit
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, BARS_PER_DAY, summarize, run_serial,
)

TOPN, SL, TIMEOUT, COST, TRAIN = 50, -0.03, 288, 0.0016, 0.6
BREAKOUT, RETEST_PCT = 288, 0.005


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


def sig_immediate(candles, breakout_n):
    """돌파 봉 즉시 진입(되돌림 대기 없음). run_serial이 다음봉 시가로 체결."""
    sigs = []; N = len(candles)
    for i in range(breakout_n, N - 1):
        ph = max(c["high_price"] for c in candles[i - breakout_n:i])
        if candles[i]["trade_price"] > ph:
            sigs.append(i)
    return sigs


def report(label, tr):
    s = summarize(tr)
    if not tr:
        print(f"  {label:20} 0건"); return
    pnls = [t["pnl_pct"] for t in tr]; mx = max(pnls) * 100
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    avgw = sum(wins) / len(wins) * 100 if wins else 0
    avgl = sum(losses) / len(losses) * 100 if losses else 0
    print(f"  {label:20} {s['n']:3}건 승률{s['win_rate']:3.0f}% 누적{s['total_pct']:+7.1f}% "
          f"MDD{s['mdd_pct']:3.0f}% 최대+{mx:4.0f}% (이익+{avgw:.1f}/손실{avgl:.1f})")


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 코인 | TEST [0.6,1.0) | 트레일3% 청산 동일 | 비용 0.16%\n")

    trail = make_trail()
    rb.simulate_exit = trail   # 재테스트(지정가)용
    ab.simulate_exit = trail   # 즉시진입(시가)용

    print("=== 진입 방식 비교 (각자 실제 방식) ===")
    tr_re = run_serial_limit(cc, pit, TOPN,
                             {"breakout_n": BREAKOUT, "retest_pct": RETEST_PCT},
                             0.06, SL, TIMEOUT, COST, TRAIN, 1.0)
    report("재테스트(지정가)", tr_re)

    tr_im = run_serial(cc, pit, TOPN, sig_immediate, {"breakout_n": BREAKOUT},
                       0.06, SL, TIMEOUT, COST, TRAIN, 1.0)
    report("즉시진입(시장가)", tr_im)
