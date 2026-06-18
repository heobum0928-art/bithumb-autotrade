"""[검증] 숏(공매도) 전략 — 약세장에서 떨어질 때 버는 방식. 2년 일봉.
빗썸 현물은 숏 불가 → 선물 필요. 단 데이터로 '숏이 이 하락장에 먹혔나' 먼저 0원 검증.
숏-돈치안 붕괴: 종가가 직전 N일 신저가 깨면 숏 진입 → M일 신고가 회복하면 커버(청산).
손익(숏) = (진입-청산)/진입 (가격 내리면 +). 코인별 독립, 비용 0.16/0.30% 왕복.
walk-forward TEST[0.6,1.0). (참고: 선물은 펀딩비 추가 — 미반영, 1차 근사.)"""
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
        if len(d) >= 250:
            cc[Path(f).stem.replace("_1d", "")] = d
    return cc


def short_donchian(candles, N, M, cost, lo, hi):
    """N일 신저가 붕괴 숏 → M일 신고가 회복 커버. 숏손익=(entry-exit)/entry."""
    nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
    trades = []; i = max(N, M)
    in_pos = False; entry = 0.0
    while i < nlen - 1:
        c = candles[i]
        if not in_pos:
            if not (a <= i < b):
                i += 1; continue
            ll = min(x["low_price"] for x in candles[i - N:i])
            if c["trade_price"] < ll:
                in_pos = True; entry = c["trade_price"]
        else:
            hh = max(x["high_price"] for x in candles[i - M:i])
            if c["trade_price"] > hh:                 # 반등 = 숏 커버
                pnl = (entry - c["trade_price"]) / entry - cost
                trades.append(pnl); in_pos = False
        i += 1
    if in_pos:
        pnl = (entry - candles[-1]["trade_price"]) / entry - cost
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
    print(f"데이터 {len(cc)}개 코인 (2년 일봉) | TEST[0.6,1.0) | 숏-돈치안(신저가붕괴→신고가커버)\n")
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for N, M in ((20, 10), (50, 20), (20, 20), (10, 5)):
            allt = []
            for coin, d in cc.items():
                allt += short_donchian(d, N, M, cost, TRAIN, 1.0)
            report(f"숏 {N}일저/{M}일고", allt)
        print()
