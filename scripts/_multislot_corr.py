"""[검증·확장] 멀티슬롯 상관·유효표본·MDD·보정t 측정.

운영자 반론 검증: "잡코인은 강세장이어도 제각각 움직인다 → 부분 독립 → 완전기각 과함".
측정 4종:
  1) 동시보유 거래쌍 pnl% 상관계수 (시간 겹침 기준)
  2) effective sample size (상관 보정 후 독립 정보량)
  3) 분산효과 MDD: 슬롯당 자본 1/N로 시간정렬 포트폴리오 자본곡선 → 1슬롯 vs N슬롯
  4) 상관보정 t값 (effective N 기반 + 클러스터 로버스트)

_multislot_test.py를 확장. 진입/청산 전략 동일(retest+트레일3%, SL-3%, 24h).
"""
import sys, statistics as st, math
from pathlib import Path
from datetime import datetime
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


def _dt(s):
    # kst string -> datetime
    return datetime.fromisoformat(s.replace("Z", ""))


def run_multi(cc, pit, nslots, lo, hi):
    """슬롯 N개. 각 거래에 진입/청산 시각·pnl 기록 (상관·MDD 분석용)."""
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
        trades.append({"coin": coin, "entry": kst, "exit": exit_kst,
                       "pnl": pnl * 100, "entry_dt": _dt(kst), "exit_dt": _dt(exit_kst)})
        slots[free] = exit_kst
    return trades


def overlap_pairs(trades, cross_coin_only=False):
    """시간 겹치는 거래쌍의 (pnl_i, pnl_j) 목록.
    cross_coin_only=True면 같은 코인 쌍 제외 (서로 다른 잡코인 간 상관만)."""
    pairs = []
    n = len(trades)
    for i in range(n):
        ti = trades[i]
        for j in range(i + 1, n):
            tj = trades[j]
            if cross_coin_only and ti["coin"] == tj["coin"]:
                continue
            # 겹침: i 시작 < j 끝  AND  j 시작 < i 끝
            if ti["entry_dt"] < tj["exit_dt"] and tj["entry_dt"] < ti["exit_dt"]:
                pairs.append((ti["pnl"], tj["pnl"]))
    return pairs


def pearson(pairs):
    if len(pairs) < 3:
        return None
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    # 대칭화: (x,y)와 (y,x) 둘 다 넣어 비대칭 라벨링 편향 제거
    xs2 = xs + ys; ys2 = ys + xs
    mx = sum(xs2) / len(xs2); my = sum(ys2) / len(ys2)
    num = sum((a - mx) * (b - my) for a, b in zip(xs2, ys2))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs2))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys2))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def avg_concurrency(trades):
    """평균 동시보유 슬롯 점유 수 (시간가중 근사: 거래별 겹침 카운트 평균)."""
    if not trades:
        return 0.0
    counts = []
    for i, ti in enumerate(trades):
        c = 1
        for j, tj in enumerate(trades):
            if i == j:
                continue
            if ti["entry_dt"] < tj["exit_dt"] and tj["entry_dt"] < ti["exit_dt"]:
                c += 1
        counts.append(c)
    return sum(counts) / len(counts)


def effective_n(n, rho, m):
    """클러스터/상관 보정 유효표본.
    동시보유 평균 m개, 평균상관 rho → 분산팽창계수 VIF ≈ 1 + (m-1)*rho.
    effN = n / VIF.  (rho<0이면 분산 축소 → effN 증가, 단 1+(m-1)rho>0 가정)"""
    rho = max(rho, 0.0)  # 보수: 음상관 분산이득은 effN에 안 줌 (MDD에서 따로 봄)
    vif = 1 + (m - 1) * rho
    vif = max(vif, 1e-9)
    return n / vif, vif


def portfolio_mdd(trades, nslots):
    """슬롯당 자본 1/N로 시간정렬 포트폴리오 자본곡선 MDD.
    각 거래는 청산 시각에 자본의 (1/N)*pnl 만큼 손익 반영(단리 슬롯 합산 근사).
    이벤트=청산 시각 정렬, 누적 자본곡선의 최대낙폭."""
    if not trades:
        return 0.0, 0.0
    # 청산 시각 순으로 손익 반영
    evs = sorted(trades, key=lambda t: t["exit_dt"])
    cap = 1.0; peak = 1.0; mdd = 0.0
    w = 1.0 / nslots
    for t in evs:
        cap += w * (t["pnl"] / 100.0) * cap  # 복리·슬롯비중
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap) / peak)
    return (cap - 1) * 100, mdd * 100


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    print(f"데이터 {len(cc)}개 | TEST [0.6,1.0) | retest+트레일3% | 비용 0.16%\n")
    rb.simulate_exit = make_trail()

    results = {}
    for N in (1, 2, 3):
        tr = run_multi(cc, pit, N, TRAIN, 1.0)
        results[N] = tr

    print("=== 1. 기본 통계 + 동시보유 상관 ===")
    for N in (1, 2, 3):
        tr = results[N]
        pnls = [t["pnl"] for t in tr]
        n = len(pnls); avg = sum(pnls) / n; sd = st.pstdev(pnls) if n > 1 else 0
        tval = avg / (sd / n**0.5) if sd else 0
        wr = sum(1 for p in pnls if p > 0) / n * 100
        pairs = overlap_pairs(tr)
        rho = pearson(pairs)
        xpairs = overlap_pairs(tr, cross_coin_only=True)
        xrho = pearson(xpairs)
        m = avg_concurrency(tr)
        rho_s = f"{rho:+.3f}" if rho is not None else "n/a"
        xrho_s = f"{xrho:+.3f}" if xrho is not None else "n/a"
        print(f"  {N}슬롯: {n:3}건 승률{wr:3.0f}% 평균{avg:+.2f}% naive_t{tval:.2f} "
              f"| 동시쌍{len(pairs):4} ρ_전체={rho_s} ρ_타코인간={xrho_s}({len(xpairs)}) 동시보유{m:.2f}")

    print("\n=== 2. 유효표본 + 상관보정 t ===")
    print("  (A=전체상관 보수기준, B=타코인간상관 = 실제 멀티슬롯 분산 대상)")
    for N in (2, 3):
        tr = results[N]
        pnls = [t["pnl"] for t in tr]
        n = len(pnls); avg = sum(pnls) / n; sd = st.pstdev(pnls)
        m = avg_concurrency(tr)
        rho_all = pearson(overlap_pairs(tr)) or 0.0
        rho_x = pearson(overlap_pairs(tr, cross_coin_only=True)) or 0.0
        naive_t = avg / (sd / n**0.5)
        effA, vifA = effective_n(n, rho_all, m)
        effB, vifB = effective_n(n, rho_x, m)
        tA = avg / (sd / effA**0.5)
        tB = avg / (sd / effB**0.5)
        print(f"  {N}슬롯 naive_t={naive_t:.2f}")
        print(f"      A 전체ρ={rho_all:+.3f} VIF={vifA:.2f} effN={effA:.0f} → 보정t={tA:.2f}")
        print(f"      B 타코인ρ={rho_x:+.3f} VIF={vifB:.2f} effN={effB:.0f} → 보정t={tB:.2f}")

    print("\n=== 3. 분산효과 MDD (슬롯당 자본 1/N, 시간정렬 자본곡선) ===")
    for N in (1, 2, 3):
        tot, mdd = portfolio_mdd(results[N], N)
        print(f"  {N}슬롯: 포트수익{tot:+.1f}% MDD={mdd:.1f}%")

    # 1슬롯도 자본 1/1, 3슬롯은 1/3씩 → 분산되면 MDD 낮아야 운영자 맞음
    print("\n  (참고) 1슬롯 MDD 대비 3슬롯 MDD가 낮으면 분산효과 실재")
