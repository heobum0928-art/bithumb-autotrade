"""[검증] 일봉 평균회귀 — '쫓으면 죽는 되돌림 시장'이면 반대로 되돌림을 먹어야.
#10(5분봉 평균회귀)은 -72% 폐기였으나 그건 5m(떨어지는칼). 일봉은 과매도 반등 신뢰↑.
2년 일봉(data/candles_daily). 볼린저 하단(20일,k시그마) 매수 → 중심선(SMA20) 회귀 매도.
SL·타임아웃 포함. 코인별 독립, 비용 0.16/0.30% 왕복. walk-forward TEST[0.6,1.0)."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "candles_daily"
TRAIN = 0.6
SL, TIMEOUT = -0.10, 20   # 일봉: 손절 -10%, 최대 20일 보유


def load():
    cc = {}
    for f in glob.glob(str(DAILY / "*_1d.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if len(d) >= 250:
            cc[Path(f).stem.replace("_1d", "")] = d
    return cc


def mr_trades(candles, period, k, cost, lo, hi):
    nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
    closes = [c["trade_price"] for c in candles]
    trades = []; i = period
    while i < nlen - 1:
        if not (a <= i < b):
            i += 1; continue
        win = closes[i - period:i]
        sma = sum(win) / period; sd = st.pstdev(win)
        if sd <= 0:
            i += 1; continue
        lower = sma - k * sd
        if closes[i] <= lower:                 # 과매도 진입
            entry = closes[i]; sl_px = entry * (1 + SL)
            mid = sma
            end = min(i + 1 + TIMEOUT, nlen)
            exited = False
            for j in range(i + 1, end):
                cj = candles[j]
                if cj["low_price"] <= sl_px:
                    trades.append((sl_px - entry) / entry - cost); i = j; exited = True; break
                if cj["high_price"] >= mid:     # 중심선 회귀 익절
                    trades.append((mid - entry) / entry - cost); i = j; exited = True; break
            if not exited:
                last = candles[min(end, nlen) - 1]["trade_price"]
                trades.append((last - entry) / entry - cost); i = end
        else:
            i += 1
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
    print(f"데이터 {len(cc)}개 코인 (2년 일봉) | TEST[0.6,1.0) | 일봉 평균회귀(BB하단→중심선)\n")
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for period in (10, 20):
            for k in (2.0, 2.5):
                allt = []
                for coin, d in cc.items():
                    allt += mr_trades(d, period, k, cost, TRAIN, 1.0)
                report(f"BB{period}일 {k}시그마", allt)
        print()
