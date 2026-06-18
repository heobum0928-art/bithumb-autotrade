"""[검증] 인트라데이 시즌성(KST 시간대 효과) — 가격을 쫓지 않는 다른 메커니즘.
가설: 특정 시간대(한국 활동시간 등)에 알트 수익이 체계적으로 몰린다.
- 시간단위 종가수익률을 KST 시(0~23)로 버킷.
- TRAIN에서 시간대별 평균수익 → 상/하위 시간 식별.
- TEST(out-of-sample): TRAIN이 고른 '좋은 시간'이 test에서도 좋은지 검증(과최적화 거름).
- 거래 = 해당 시간 시작에 매수 → 1시간 보유 → 매도. 비용 0.16%/0.30% 왕복.
- walk-forward: TRAIN[0,0.6) 선택 / TEST[0.6,1.0) 검증."""
import sys, statistics as st
from collections import defaultdict
from datetime import datetime
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.alt_entry_backtest import cached_coins, fetch_5m, BARS_PER_DAY

TRAIN = 0.6
COST = 0.0016


def hourly_returns(candles):
    """5분봉 → 시간단위 종가. (kst_dt, hour, ret) 리스트."""
    # 시(hour) 경계로 그룹: 각 시의 마지막 종가
    hour_close = {}
    order = []
    for c in candles:
        k = c["candle_date_time_kst"]      # 'YYYY-MM-DDTHH:MM:SS'
        hk = k[:13]                         # 시까지
        if hk not in hour_close:
            order.append(hk)
        hour_close[hk] = c["trade_price"]
    out = []
    for i in range(1, len(order)):
        prev, cur = hour_close[order[i-1]], hour_close[order[i]]
        if prev > 0:
            hr = int(order[i][11:13])
            out.append((order[i], hr, cur/prev - 1))
    return out


if __name__ == "__main__":
    coins = cached_coins(); cc = {}
    for c in coins:
        d = fetch_5m(c)
        if len(d) >= BARS_PER_DAY * 5:
            cc[c] = d
    # 전체 코인 시간단위 수익 모으기 (시간대별)
    all_rows = []
    for coin, d in cc.items():
        all_rows += hourly_returns(d)
    all_rows.sort()  # 시간순
    n = len(all_rows); cut = int(n * TRAIN)
    train, test = all_rows[:cut], all_rows[cut:]
    print(f"데이터 {len(cc)}개 코인 | 시간단위 관측 {n} (train {len(train)}/test {len(test)})\n")

    # TRAIN: 시간대별 평균
    tr_by_h = defaultdict(list)
    for _, h, r in train:
        tr_by_h[h].append(r)
    tr_mean = {h: sum(v)/len(v) for h, v in tr_by_h.items() if v}
    ranked = sorted(tr_mean.items(), key=lambda x: -x[1])
    print("=== TRAIN 시간대별 평균 시간수익(상위5/하위5) ===")
    for h, m in ranked[:5]:
        print(f"  {h:02d}시 {m*100:+.3f}%")
    print("  ...")
    for h, m in ranked[-5:]:
        print(f"  {h:02d}시 {m*100:+.3f}%")

    # TEST out-of-sample: train 상위 K시간대를 test에서 평가
    te_by_h = defaultdict(list)
    for _, h, r in test:
        te_by_h[h].append(r)
    te_overall = [r for _, _, r in test]
    base_avg = sum(te_overall)/len(te_overall)*100
    print(f"\n=== TEST(out-of-sample) 검증 — train 상위시간대가 test서도? ===")
    print(f"  [기준] test 전체시간 평균: {base_avg:+.4f}%/시간")
    for K in (1, 2, 3, 5):
        good_hours = [h for h, _ in ranked[:K]]
        rr = [r for _, h, r in test if h in good_hours]
        if not rr: continue
        avg = sum(rr)/len(rr); sd = st.pstdev(rr) if len(rr)>1 else 0
        adj = avg - COST  # 매 거래 왕복비용
        t = adj/(sd/len(rr)**0.5) if sd else 0
        wr = sum(1 for x in rr if x>0)/len(rr)*100
        print(f"  상위{K}시간({good_hours}): {len(rr)}건 시간평균{avg*100:+.3f}% "
              f"비용후{adj*100:+.3f}% t{t:+.2f} 승률{wr:.0f}%")
