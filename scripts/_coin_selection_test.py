"""[검증] '자주 오르는 코인만 골라 집중' — 코인별 펌핑 성격이 지속되는가?
TRAIN(앞60%)에서 펌핑빈도(일간 +10%↑ 비율)로 코인 랭킹 →
TEST(뒤40%)에서 ① 펌핑빈도가 유지되나(지속성) ② 상위코인 돌파-재테스트가 더 좋나.
지속되면 '몇 종목 골라 집중'이 근거 있음. 아니면 과거펌핑=미래예측 불가(착시).
주의: 보유 37코인은 생존자(현재 상장·고거래량)라 생존편향 내재. 2년 일봉."""
import sys, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategy_lab as lab


def pump_freq(candles, lo, hi, thr=0.10):
    """구간 [lo,hi) 일간수익 > thr 비율(펌핑빈도) + 일평균수익."""
    n = len(candles); a, b = int(n * lo), int(n * hi)
    cl = [c["trade_price"] for c in candles]
    rets = [cl[i] / cl[i - 1] - 1 for i in range(max(a, 1), b) if cl[i - 1] > 0]
    if not rets:
        return 0.0, 0.0
    pf = sum(1 for r in rets if r > thr) / len(rets) * 100
    return pf, st.mean(rets) * 100


def main():
    cc = lab.load_candles(); reg = lab.btc_regime()
    rows = []
    for coin, d in cc.items():
        pf_tr, mr_tr = pump_freq(d, 0.0, 0.6)
        pf_te, mr_te = pump_freq(d, 0.6, 1.0)
        rows.append((coin, pf_tr, pf_te, mr_tr, mr_te))

    # ① 지속성: train 펌핑빈도 vs test 펌핑빈도 상관
    xs = [r[1] for r in rows]; ys = [r[2] for r in rows]
    n = len(xs)
    mx, my = st.mean(xs), st.mean(ys)
    cov = sum((xs[i]-mx)*(ys[i]-my) for i in range(n)) / n
    sx, sy = st.pstdev(xs), st.pstdev(ys)
    corr = cov/(sx*sy) if sx and sy else 0
    print(f"보유 {n}코인 | TRAIN[0,0.6] 펌핑빈도로 선별 → TEST[0.6,1.0] 검증\n")
    print(f"=== ① 지속성: TRAIN 펌핑빈도 ↔ TEST 펌핑빈도 상관 = {corr:+.2f} ===")
    print("  (1에 가까울수록 '펌핑코인은 계속 펌핑' / 0이면 과거펌핑이 미래와 무관)\n")

    # TRAIN 펌핑빈도 상위/하위 절반
    rows.sort(key=lambda r: r[1], reverse=True)
    half = n // 2
    top = [r[0] for r in rows[:half]]; bot = [r[0] for r in rows[half:]]
    print(f"  TRAIN 상위펌핑 {half}코인: {', '.join(top[:8])}...")
    print(f"  → 이들의 TEST 평균 펌핑빈도 {st.mean([r[2] for r in rows[:half]]):.1f}% "
          f"vs 하위 {st.mean([r[2] for r in rows[half:]]):.1f}%\n")

    # ② 상위코인만 돌파-재테스트(근사) 했을 때 TEST 성과
    spec = {"name": "돌파20+트레일5",
            "entry": {"type": "breakout", "params": {"n": 20}},
            "exit": {"type": "trail", "params": {"trail": 0.05, "activate": 0.01, "sl": -0.10, "timeout": 30}},
            "filter": {"regime": "any"}}
    print("=== ② TRAIN상위코인만 vs 하위코인만 — TEST 돌파-재테스트 성과 ===")
    for label, sel in (("상위펌핑 집중", {k: cc[k] for k in top}),
                       ("하위펌핑", {k: cc[k] for k in bot}),
                       ("전체", cc)):
        r = lab.backtest(spec, cost=0.0016, candles_by_coin=sel, regime=reg)
        print(f"  {label:10}: {r['n']:3}건 승률{r['wr']:3.0f}% 거래당{r['avg']:+.2f}% t{r['t']:+.2f} 표본합{r['sum']:+.0f}%")


if __name__ == "__main__":
    main()
