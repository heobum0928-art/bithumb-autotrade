"""[검증] 상대강도(횡단면 모멘텀) — 지금까지와 완전히 다른 메커니즘.
기존 전략: "이 코인이 신고가 뚫었나"(단일코인·시계열).
이 전략: "지금 전체 잡코인 중 최근 수익률 상위 K개를 사서 주기적 교체"(코인끼리 비교·횡단면).
가설: 약세장에도 '상대적 1등'은 늘 존재 → 장세를 덜 탐.

- 매 R봉마다 리밸런싱: 직전 L봉 수익률로 랭크 → 상위 K개 동일비중 보유.
- 거래대금 상위 PIT 필터(유동성). 비용=교체분 회전율×왕복%.
- walk-forward TEST[0.6,1.0). point-in-time(그 시점까지만)."""
import sys, statistics as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn, BARS_PER_DAY,
)

TOPN, TRAIN = 50, 0.6


def run(closes, master, pit, L, R, K, cost, lo, hi):
    n = len(master)
    a, b = int(n * lo), int(n * hi)
    idxs = [i for i in range(max(L, a), min(b, n - R), R)]
    prev = set(); period_rets = []
    for i in idxs:
        t, t_lb, t_fwd = master[i], master[i - L], master[i + R]
        cand = []
        for coin, cl in closes.items():
            if t in cl and t_lb in cl and cl[t_lb] > 0:
                if is_topn(coin, t, pit, TOPN):
                    cand.append((cl[t] / cl[t_lb] - 1.0, coin))
        if len(cand) < K:
            continue
        cand.sort(reverse=True)
        sel = [c for _, c in cand[:K]]
        # 보유분 forward 수익
        rets = []
        for coin in sel:
            cl = closes[coin]
            if t in cl and t_fwd in cl and cl[t] > 0:
                rets.append(cl[t_fwd] / cl[t] - 1.0)
        if not rets:
            continue
        gross = sum(rets) / len(rets)
        # 회전율 비용: 새로 편입된 비중만큼 왕복비용
        turnover = len(set(sel) - prev) / K
        period_rets.append(gross - turnover * cost)
        prev = set(sel)
    return period_rets


def report(label, pr):
    n = len(pr)
    if n == 0:
        print(f"  {label:22} 0기간"); return
    avg = sum(pr) / n * 100
    sd = st.pstdev(pr) * 100 if n > 1 else 0
    tval = avg / (sd / n ** 0.5) if sd else 0
    wr = sum(1 for x in pr if x > 0) / n * 100
    eq = 1.0; peak = 1.0; mdd = 0.0
    for x in pr:
        eq *= 1 + x; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    print(f"  {label:22} {n:3}기간 승률{wr:3.0f}% 기간평균{avg:+.2f}% "
          f"t{tval:+.2f} MDD{mdd*100:3.0f}% 누적{(eq-1)*100:+7.1f}%")


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    pit = build_pit_volume(cc)
    # 공통 타임라인 + 코인별 종가 사전
    allk = set()
    closes = {}
    for coin, d in cc.items():
        cl = {x["candle_date_time_kst"]: x["trade_price"] for x in d}
        closes[coin] = cl; allk |= set(cl.keys())
    master = sorted(allk)
    print(f"데이터 {len(cc)}개 | 마스터 타임라인 {len(master)}봉 | TEST[0.6,1.0)\n")
    print("=== 상대강도(횡단면) 모멘텀: 상위K 보유, R봉마다 교체 ===")
    # L=룩백, R=보유/리밸런싱, K=보유개수 (봉수: 72=6h, 288=24h)
    combos = [(L, R, K) for L in (72, 288) for R in (72, 288) for K in (1, 3)]
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"--- 비용 {ctag} ---")
        for L, R, K in combos:
            pr = run(closes, master, pit, L, R, K, cost, TRAIN, 1.0)
            report(f"L{L*5//60}h R{R*5//60}h K{K}", pr)
        print()
