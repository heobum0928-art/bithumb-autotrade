"""[검증 #19] 일봉 이동평균 골든크로스 추세추종 — 기존 돈치안(#11 스윙)과 다른 신호계열.
단기SMA(s)가 장기SMA(l) 상향돌파 시 매수 → 하향이탈(데드크로스) 매도. 코인별 독립.
장세(BTC 200일선 BULL/BEAR) 분리로 '추세는 강세장 전용인가' 확인.
2년 일봉. walk-forward TEST[0.6,1.0). 비용 0.16/0.30% 왕복."""
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


def btc_regime():
    f = DAILY / "BTC_1d.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    closes = [x["trade_price"] for x in d]; reg = {}
    for i in range(len(d)):
        if i < 200: continue
        sma = sum(closes[i-200:i]) / 200
        reg[d[i]["candle_date_time_kst"][:10]] = "BULL" if closes[i] > sma else "BEAR"
    return reg


def sma(closes, i, n):
    return sum(closes[i-n:i]) / n


def macross_trades(candles, s, l, cost, lo, hi):
    nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
    closes = [c["trade_price"] for c in candles]
    trades = []; i = l + 1; in_pos = False; entry = 0.0
    while i < nlen - 1:
        if i < l + 1:
            i += 1; continue
        ss, ll = sma(closes, i, s), sma(closes, i, l)
        ss_p, ll_p = sma(closes, i-1, s), sma(closes, i-1, l)
        if not in_pos:
            if not (a <= i < b):
                i += 1; continue
            if ss_p <= ll_p and ss > ll:            # 골든크로스
                in_pos = True; entry = closes[i]; edate = candles[i]["candle_date_time_kst"][:10]
        else:
            if ss_p >= ll_p and ss < ll:            # 데드크로스 청산
                trades.append(((closes[i] - entry) / entry - cost, edate)); in_pos = False
        i += 1
    if in_pos:
        trades.append(((closes[-1] - entry) / entry - cost, edate))
    return trades


def report(label, allt):
    n = len(allt)
    if not n:
        print(f"  {label:18} 0건"); return
    p = [x[0] * 100 for x in allt]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    t = avg / (sd / n ** 0.5) if sd else 0
    wr = sum(1 for x in p if x > 0) / n * 100
    print(f"  {label:18} {n:3}건 승률{wr:3.0f}% 거래당{avg:+.2f}% t{t:+.2f} 표본합{sum(p):+.0f}%")


if __name__ == "__main__":
    cc = load(); reg = btc_regime()
    print(f"데이터 {len(cc)}개 코인 (2년 일봉) | TEST[0.6,1.0) | 일봉 MA 골든크로스 추세추종\n")
    for cost, ctag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        print(f"=== 비용 {ctag} ===")
        for s, l in ((5, 20), (10, 30), (20, 60), (10, 50)):
            allt = []
            for coin, d in cc.items():
                allt += macross_trades(d, s, l, cost, TRAIN, 1.0)
            report(f"SMA{s}/{l}", allt)
        print()
    # 장세분리 (대표 파라미터 SMA10/30, 비용0.16%)
    print("=== 장세분리 (SMA10/30, 비용0.16%) ===")
    allt = []
    for coin, d in cc.items():
        allt += macross_trades(d, 10, 30, 0.0016, TRAIN, 1.0)
    for rgm in ("BULL", "BEAR", "전체"):
        vals = [x for x in allt if rgm == "전체" or reg.get(x[1]) == rgm]
        report(f"{rgm} 진입", vals)
