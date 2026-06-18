"""[검증] 알트 바구니 매수 후 보유 — '여러 후보에 돈 넣고 관망' 아이디어.
매 시점 전체 알트 동일비중 매수 → H일 보유 → 매도. 장세(BTC 200일선)별 분리.
가설검증: 약세장엔 바구니가 새는가? 펌핑 1개가 하락 9개를 못 메우는가?
2년 일봉. 비용 0.16% 왕복 1회."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "candles_daily"
COST = 0.0016


def load():
    cc = {}
    for f in glob.glob(str(DAILY / "*_1d.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if len(d) >= 250:
            cc[Path(f).stem.replace("_1d", "")] = {x["candle_date_time_kst"][:10]: x["trade_price"] for x in d}
    return cc


def btc_regime():
    f = DAILY / "BTC_1d.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    closes = [x["trade_price"] for x in d]; reg = {}
    for i in range(len(d)):
        if i < 200: continue
        sma = sum(closes[i-200:i]) / 200
        reg[d[i]["candle_date_time_kst"][:10]] = "BULL" if closes[i] > sma else "BEAR"
    return reg


if __name__ == "__main__":
    cc = load(); reg = btc_regime()
    # 공통 날짜축 (BTC 기준)
    dates = sorted(reg.keys())
    print(f"데이터 {len(cc)}개 코인 | 분류일 {len(dates)}일 | 알트 바구니 매수후 H일 보유\n")
    for H in (5, 20, 60):
        # 각 진입일의 바구니 H일 수익
        by_reg = {"BULL": [], "BEAR": [], "전체": []}
        for di, day in enumerate(dates):
            if di + H >= len(dates): break
            fut = dates[di + H]
            rets = []
            for coin, px in cc.items():
                if day in px and fut in px and px[day] > 0:
                    rets.append(px[fut] / px[day] - 1)
            if len(rets) < 5: continue
            basket = sum(rets) / len(rets) - COST       # 동일비중 바구니 수익
            by_reg[reg[day]].append(basket); by_reg["전체"].append(basket)
        print(f"=== {H}일 보유 ===")
        for r in ("BULL", "BEAR", "전체"):
            v = by_reg[r]; n = len(v)
            if not n: continue
            avg = sum(v)/n*100; sd = st.pstdev(v)*100 if n>1 else 0
            wr = sum(1 for x in v if x>0)/n*100
            print(f"  {r:5} 진입 {n:4}회: 평균 {avg:+.2f}%  (승률 {wr:.0f}%, 변동성 {sd:.1f}%)")
        print()
