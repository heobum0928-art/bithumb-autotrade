"""
전략 B (돌파 후 재테스트) 정밀 백테스트 — 지정가(maker) 체결 가정.

alt_entry_backtest.py에서 B가 조건부 GO(+60.9% @0.08%, -5.7% @슬립0.5%)로 나옴.
B는 "돌파 레벨로 되돌아온 자리"에서 사는 전략이라 추격이 아니라 대기 →
시장가가 아니라 지정가로 그 레벨에 미리 주문을 깔 수 있다.

이 스크립트의 차이 (alt_entry_backtest.py 대비):
  1. 체결 모델 = 지정가: 진입가 = 재테스트 목표 레벨(돌파 레벨 × (1+retest_pct))로 고정.
     "다음 봉 시가"가 아니라 내가 지정한 가격 → 슬리피지 0 가정이 정당.
     단, 봉의 저가가 그 레벨에 실제로 닿아야 체결(미체결이면 신호 무효).
  2. 비용 케이스 3개: 0.08%(쿠폰 테이커), 0.0%(메이커 리워드로 상쇄), 0.16%(보수).
     지정가 진입 + 지정가 청산이면 양쪽 메이커 → 실효 수수료 0에 근접.
  3. 코인별 분해 출력 — 수익이 소수 코인 편중인지(생존편향 잔존) 확인.

규율: lookahead 금지, point-in-time 거래대금 상위 N, walk-forward train/test,
      단일 슬롯 직렬 복리. (alt_entry_backtest.py 함수 재사용)

Run:
  python -u -X utf8 scripts/retest_backtest.py --topn 30
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# alt_entry_backtest.py의 데이터/유틸 재사용
from scripts.alt_entry_backtest import (
    cached_coins, fetch_5m, build_pit_volume, is_topn,
    simulate_exit, summarize, BARS_PER_DAY,
)

TRAIN_FRAC = 0.60

# 비용 케이스 (왕복)
COST_CASES = {
    "0.00%(양방향메이커)": 0.0,
    "0.08%(쿠폰테이커)":   0.0008,
    "0.16%(보수)":         0.0016,
}

# B 그리드 (alt_entry_backtest와 동일 후보)
GRID = [
    {"breakout_n": bn, "retest_pct": rt}
    for bn in (48, 96, 288)
    for rt in (0.005, 0.015)
]
EXITS = [
    {"tp": 0.04, "sl": -0.025, "timeout_bars": 288},
    {"tp": 0.06, "sl": -0.03,  "timeout_bars": 288},
]


def signals_retest_limit(candles, breakout_n, retest_pct):
    """
    B 신호 + 지정가 진입가. 반환: [(entry_idx, entry_px), ...]
    돌파 후 가격이 (레벨 × (1+retest_pct))까지 내려오면 그 지정가에 체결 가정.
      - 돌파: 종가 > 직전 breakout_n봉 신고가
      - 진입 목표가 = 레벨 × (1+retest_pct)  (레벨 살짝 위 = 지지 매수)
      - 체결: 이후 봉의 저가가 목표가 이하로 내려온 첫 봉에서 목표가에 체결
      - 단 종가가 레벨 밑으로 깨지면(지지 실패) 그 봉은 체결 안 하고 무효
      - 돌파 후 24h(288봉) 내 미체결이면 무효
    """
    n = breakout_n
    out = []
    i = n
    N = len(candles)
    while i < N - 1:
        prior_high = max(c["high_price"] for c in candles[i - n:i])
        if candles[i]["trade_price"] > prior_high:
            level = prior_high
            target = level * (1 + retest_pct)
            for j in range(i + 1, min(i + 1 + BARS_PER_DAY, N)):
                cj = candles[j]
                if cj["low_price"] <= target:
                    # 지지 실패 거름: 종가가 레벨 밑이면 진입 안 함
                    if cj["trade_price"] > level:
                        out.append((j, target))
                    i = j
                    break
            else:
                pass
        i += 1
    # 중복 제거 (entry_idx 기준)
    seen = set()
    uniq = []
    for idx, px in out:
        if idx not in seen:
            seen.add(idx)
            uniq.append((idx, px))
    return uniq


def run_serial_limit(coin_candles, pit, topn, params, tp, sl, timeout_bars,
                     cost, lo_frac, hi_frac):
    """단일 슬롯 직렬 — 지정가 진입가 사용 버전."""
    events = []
    for coin, candles in coin_candles.items():
        nlen = len(candles)
        a, b = int(nlen * lo_frac), int(nlen * hi_frac)
        for entry_idx, entry_px in signals_retest_limit(candles, **params):
            if entry_idx + 1 >= nlen:
                continue
            if not (a <= entry_idx < b):
                continue
            kst = candles[entry_idx]["candle_date_time_kst"]
            events.append((kst, coin, entry_idx, entry_px))
    events.sort()

    trades = []
    busy_until = ""
    for kst, coin, idx, entry_px in events:
        if kst < busy_until:
            continue
        if not is_topn(coin, kst, pit, topn):
            continue
        candles = coin_candles[coin]
        # 청산은 진입 봉(idx) 다음부터 — 진입은 idx 봉 안에서 지정가 체결됨
        ex = simulate_exit(candles, idx, entry_px, tp, sl, timeout_bars)
        gross = (ex["exit"] - entry_px) / entry_px
        pnl = gross - cost
        exit_kst = candles[min(idx + ex["bars"], len(candles) - 1)]["candle_date_time_kst"]
        trades.append({
            "coin": coin, "entry_kst": kst, "exit_kst": exit_kst,
            "entry": entry_px, "exit": ex["exit"], "pnl_pct": pnl,
            "reason": ex["reason"], "bars": ex["bars"],
        })
        busy_until = exit_kst
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", type=int, default=30)
    args = ap.parse_args()

    coins = cached_coins()
    print("=== 전략 B 정밀 백테스트 (지정가 체결) ===")
    print(f"유니버스: 캐시 {len(coins)}개 | 거래대금 상위 {args.topn} | "
          f"walk-forward {TRAIN_FRAC*100:.0f}/{(1-TRAIN_FRAC)*100:.0f}")
    print(f"체결: 지정가(재테스트 목표가) — 슬리피지 0 가정\n")

    coin_candles = {}
    for c in coins:
        cc = fetch_5m(c)
        if len(cc) >= BARS_PER_DAY * 5:
            coin_candles[c] = cc
    print(f"데이터: {len(coin_candles)}개 코인")
    pit = build_pit_volume(coin_candles)
    print("PIT 거래대금 인덱스 완료\n")

    # train: 0.16% 보수 비용으로 파라미터 선택
    sel_cost = COST_CASES["0.16%(보수)"]
    best, best_score = None, -1e9
    print("--- TRAIN (파라미터 선택, 0.16% 기준) ---")
    for params in GRID:
        for ex in EXITS:
            tr = run_serial_limit(coin_candles, pit, args.topn, params,
                                  ex["tp"], ex["sl"], ex["timeout_bars"],
                                  sel_cost, 0.0, TRAIN_FRAC)
            s = summarize(tr)
            if s["n"] < 10:
                continue
            if s["total_pct"] > best_score:
                best_score = s["total_pct"]
                best = (params, ex)
    if not best:
        print("train 표본 부족 — 판정 불가")
        return
    params, ex = best
    ts = summarize(run_serial_limit(coin_candles, pit, args.topn, params,
                                    ex["tp"], ex["sl"], ex["timeout_bars"],
                                    sel_cost, 0.0, TRAIN_FRAC))
    print(f"  선택: {params} TP{ex['tp']*100:+.0f}% SL{ex['sl']*100:+.0f}%")
    print(f"  train: {ts['n']}건 승률{ts['win_rate']:.0f}% 누적{ts['total_pct']:+.1f}% MDD{ts['mdd_pct']:.1f}%\n")

    # test: 3개 비용 케이스
    print("--- TEST (out-of-sample) ---")
    test_trades = None
    for cname, cost in COST_CASES.items():
        tr = run_serial_limit(coin_candles, pit, args.topn, params,
                              ex["tp"], ex["sl"], ex["timeout_bars"],
                              cost, TRAIN_FRAC, 1.0)
        s = summarize(tr)
        if test_trades is None:
            test_trades = tr
        verdict = " [표본<30 보류]" if s["n"] < 30 else (" GO" if s["total_pct"] > 0 else " NO-GO")
        print(f"  [{cname:18s}] {s['n']:3}건 승률{s['win_rate']:5.0f}% "
              f"평균{s['avg_pct']:+.2f}% 누적{s['total_pct']:+7.1f}% MDD{s['mdd_pct']:5.1f}%{verdict}")

    # 코인별 분해 (편중 확인)
    by_coin = {}
    for t in test_trades:
        by_coin.setdefault(t["coin"], []).append(t["pnl_pct"])
    print("\n--- 코인별 분해 (test, 0.08% 기준) — 편중 확인 ---")
    rows = sorted(by_coin.items(), key=lambda x: -sum(x[1]))
    for coin, pnls in rows:
        print(f"  {coin:8s} {len(pnls):2}건 누적{sum(pnls)*100:+7.1f}% 평균{sum(pnls)/len(pnls)*100:+.2f}%")
    if rows:
        top = rows[0]
        total = sum(sum(p) for _, p in rows)
        if total != 0:
            share = sum(top[1]) / total * 100
            print(f"\n  최대 기여 코인 {top[0]}: 전체 수익의 {share:.0f}%")
            print(f"  → 상위 1코인 의존도가 높으면 생존편향 의심")


if __name__ == "__main__":
    main()
