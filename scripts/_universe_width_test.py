"""[검증] 거래량 상위 범위 넓히기 — '저거래량 잡코인이 더 펌핑한다' 가설.
보유 일봉 코인을 거래대금(turnover) 기준 고/중/저 티어로 3분할 →
각 티어에서 돌파+트레일(RT 근사) 엣지 비교. 저티어가 더 좋으면 universe 확대 근거.
주의: 백테는 슬리피지 미반영 — 저거래량 결과는 현실서 더 깎임(상한 추정).
2년 일봉, walk-forward TEST[0.6,1.0), 비용 0.16/0.30%."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategy_lab as lab

DAILY = lab.DAILY


def turnover(d):
    """최근 60일 일평균 거래대금(원) 중앙값."""
    vals = []
    for c in d[-60:]:
        tp = c.get("candle_acc_trade_price")
        if tp is None:
            tp = c.get("candle_acc_trade_volume", 0) * c.get("trade_price", 0)
        vals.append(tp)
    return st.median(vals) if vals else 0


def main():
    cc = lab.load_candles()
    reg = lab.btc_regime()
    # 티어 분할
    ranked = sorted(cc.items(), key=lambda kv: turnover(kv[1]), reverse=True)
    n = len(ranked)
    t1 = dict(ranked[: n // 3])                 # 고거래량
    t2 = dict(ranked[n // 3: 2 * n // 3])       # 중
    t3 = dict(ranked[2 * n // 3:])              # 저거래량
    spec = {
        "name": "돌파20+트레일5",
        "entry": {"type": "breakout", "params": {"n": 20}},
        "exit": {"type": "trail", "params": {"trail": 0.05, "activate": 0.01, "sl": -0.10, "timeout": 30}},
        "filter": {"regime": "any"},
    }
    print(f"보유 {n}코인 | 거래대금 3분할 | RT근사(돌파20+트레일5%) | TEST[0.6,1.0)\n")
    for label, tier in (("고거래량 1/3", t1), ("중거래량 1/3", t2), ("저거래량 1/3", t3), ("전체", cc)):
        med = st.median([turnover(d) for d in tier.values()]) / 1e8
        print(f"=== {label}  (코인 {len(tier)}, 일평균거래대금 중앙값 ~{med:.0f}억) ===")
        for cost, tag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
            r = lab.backtest(spec, cost=cost, candles_by_coin=tier, regime=reg)
            print(f"  비용{tag}: {r['n']:3}건 승률{r['wr']:3.0f}% 거래당{r['avg']:+.2f}% t{r['t']:+.2f} 표본합{r['sum']:+.0f}%")
        print()


if __name__ == "__main__":
    main()
