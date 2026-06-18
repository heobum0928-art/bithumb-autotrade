"""[검증] 스윙 추세추종 — 2년 일봉(data/candles_daily). 대장 #11(90일 부족) 재도전.
돈치안 채널: 종가가 직전 N일 신고가 돌파 시 매수 → 직전 M일 신저가 깨면 매도(추세이탈).
시간축이 일(day)이라 노이즈·수수료 비중 작음. 코인별 독립 포지션, 비용 0.16/0.30% 왕복.
walk-forward: 각 코인 히스토리의 앞 60% train(파라미터 감) / 뒤 40% test(판정)."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "candles_daily"
TRAIN = 0.6


def load():
    cc = {}
    for f in glob.glob(str(DAILY / "*_1d.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if len(d) >= 250:   # 최소 ~8개월
            coin = Path(f).stem.replace("_1d", "")
            cc[coin] = d
    return cc


def donchian_trades(candles, N, M, cost, lo, hi):
    """N일 신고가 돌파 매수 → M일 신저가 이탈 매도. 코인 내 비중복."""
    nlen = len(candles)
    a, b = int(nlen * lo), int(nlen * hi)
    trades = []
    i = max(N, 1)
    in_pos = False; entry = 0.0
    while i < nlen - 1:
        c = candles[i]
        if not in_pos:
            if not (a <= i < b):
                i += 1; continue
            hh = max(x["high_price"] for x in candles[i - N:i])
            if c["trade_price"] > hh:
                in_pos = True; entry = c["trade_price"]; ei = i
        else:
            ll = min(x["low_price"] for x in candles[i - M:i])
            if c["trade_price"] < ll:
                pnl = (c["trade_price"] - entry) / entry - cost
                trades.append(pnl); in_pos = False
        i += 1
    if in_pos:  # 마지막 미청산 → 마지막 종가로 정리
        pnl = (candles[-1]["trade_price"] - entry) / entry - cost
        trades.append(pnl)
    return trades


def report(label, allt):
    n = len(allt)
    if not n:
        print(f"  {label:16} 0건"); return
    p = [x * 100 for x in allt]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    t = avg / (sd / n ** 0.5) if sd else 0
    wr = sum(1 for x in p if x > 0) / n * 100
    print(f"  {label:16} {n:3}건 승률{wr:3.0f}% 거래당평균{avg:+.2f}% t{t:+.2f} 표본합{sum(p):+.0f}%")


if __name__ == "__main__":
    cc = load()
    print(f"데이터 {len(cc)}개 코인 (2년 일봉) | TEST[0.6,1.0) | 돈치안 추세추종\n")
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for N, M in ((20, 10), (50, 20), (20, 20), (50, 25)):
            allt = []
            for coin, d in cc.items():
                allt += donchian_trades(d, N, M, cost, TRAIN, 1.0)
            report(f"{N}일고/{M}일저", allt)
        print()
