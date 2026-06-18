"""[검증] 숏 전략 out-of-regime + 청산/펀딩 모델링 — 에이전트 합의 필수검증.
①장세분리: BTC가 200일선 위(BULL)/아래(BEAR)로 진입 분류 → 상승장 숏이 터지나?
②청산모델: 레버리지 L배면 +1/L 오를 때 청산(2배=+50%, 3배=+33%) → 마진손익.
③펀딩드래그: 보유일당 소액(숏 불리 가정 0.01%/일) 차감(보수).
숏-돈치안(20일저붕괴→20일고커버), 2년 일봉."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "candles_daily"
TRAIN = 0.6
COST = 0.0016
FUND_PER_DAY = 0.0001   # 0.01%/일 펀딩 드래그(숏 불리 보수가정)
N, M = 20, 20


def load():
    cc = {}
    for f in glob.glob(str(DAILY / "*_1d.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if len(d) >= 250:
            cc[Path(f).stem.replace("_1d", "")] = d
    return cc


def btc_regime():
    """date(YYYY-MM-DD) -> 'BULL'/'BEAR' (BTC 200일 SMA 기준)."""
    f = DAILY / "BTC_1d.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    closes = [x["trade_price"] for x in d]
    reg = {}
    for i in range(len(d)):
        if i < 200:
            continue
        sma = sum(closes[i-200:i]) / 200
        reg[d[i]["candle_date_time_kst"][:10]] = "BULL" if closes[i] > sma else "BEAR"
    return reg


def trades_detailed(candles, lo, hi):
    nlen = len(candles); a, b = int(nlen * lo), int(nlen * hi)
    out = []; i = max(N, M); in_pos = False
    while i < nlen - 1:
        c = candles[i]
        if not in_pos:
            if not (a <= i < b):
                i += 1; continue
            ll = min(x["low_price"] for x in candles[i - N:i])
            if c["trade_price"] < ll:
                in_pos = True; entry = c["trade_price"]; ei = i
                maxhi = c["high_price"]
        else:
            maxhi = max(maxhi, c["high_price"])
            hh = max(x["high_price"] for x in candles[i - M:i])
            if c["trade_price"] > hh:
                out.append({"date": candles[ei]["candle_date_time_kst"][:10],
                            "entry": entry, "exit": c["trade_price"],
                            "maxhi": maxhi, "days": i - ei}); in_pos = False
        i += 1
    return out


def stats(label, vals):
    n = len(vals)
    if not n:
        print(f"  {label:22} 0건"); return
    p = [v * 100 for v in vals]
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0
    t = avg / (sd / n ** 0.5) if sd else 0
    wr = sum(1 for x in p if x > 0) / n * 100
    print(f"  {label:22} {n:3}건 승률{wr:3.0f}% 거래당{avg:+.2f}% t{t:+.2f} 표본합{sum(p):+.0f}%")


if __name__ == "__main__":
    cc = load(); reg = btc_regime()
    alltr = []
    for coin, d in cc.items():
        alltr += trades_detailed(d, TRAIN, 1.0)
    print(f"데이터 {len(cc)}코인 | 숏 거래 {len(alltr)}건 | 숏-돈치안20/20\n")

    # ① 장세분리 (무레버리지 notional, 청산 무관)
    print("=== ① 장세별 (무레버리지) — 상승장 숏이 터지나? ===")
    for rgm in ("BULL", "BEAR", "전체"):
        vals = []
        for t in alltr:
            if rgm != "전체" and reg.get(t["date"]) != rgm:
                continue
            r = (t["entry"] - t["exit"]) / t["entry"] - COST - FUND_PER_DAY * t["days"]
            vals.append(r)
        stats(f"{rgm} 진입", vals)

    # ② 레버리지별 청산 모델 (마진 손익)
    print("\n=== ② 레버리지별 청산 모델 (마진 기준, +1/L 오르면 청산=-100%) ===")
    for L in (1, 2, 3):
        vals = []; nliq = 0
        for t in alltr:
            liq_px = t["entry"] * (1 + 1.0 / L)
            if t["maxhi"] >= liq_px:
                vals.append(-1.0); nliq += 1            # 마진 전손
            else:
                notional = (t["entry"] - t["exit"]) / t["entry"]
                margin = L * notional - L * COST - L * FUND_PER_DAY * t["days"]
                vals.append(margin)
        p = [v*100 for v in vals]; n = len(p)
        avg = sum(p)/n; sd = st.pstdev(p) if n>1 else 0
        tval = avg/(sd/n**0.5) if sd else 0
        print(f"  {L}배: 청산 {nliq}/{n}건({nliq/n*100:.0f}%) 마진거래당{avg:+.2f}% t{tval:+.2f} 누적{sum(p):+.0f}%")
