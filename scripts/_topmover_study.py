"""[실측 연구] '거래량 터진 1등'에 들어가면 그 다음 실제로 어떻게 되나 — 분포 직시.
형 주장: 매일 1등은 수십% 상승, 거래량으로 판단 가능. → 그게 사실이면 들어가서 먹어야.
방법: 큰 거래량급증+상승봉(점화)을 '1등 후보'로 보고, 진입 후 +30분/+1h/+4h 전방수익 분포.
       특히 극단급증(거래량 10배+)만 따로. 추론 아니라 실측. 90일 5m, 전구간."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"


def load_5m(coin):
    fs = sorted(glob.glob(str(CACHE / f"{coin}_5m_90d_*.json")))
    if not fs: return None
    d = json.loads(Path(fs[-1]).read_text(encoding="utf-8"))
    return d if len(d) >= 2000 else None


def events(coin, ig_pct, vol_mult):
    """점화 시점들의 (진입가, 진입후 경로) 수집."""
    d = load_5m(coin)
    if not d: return []
    cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
    op=[x["opening_price"] for x in d]; vol=[x.get("candle_acc_trade_volume",0) for x in d]
    ev=[]; n=len(d); i=20
    while i < n-50:
        bar = cl[i]/op[i]-1 if op[i]>0 else 0
        avgv=sum(vol[i-20:i])/20
        if bar>=ig_pct and avgv>0 and vol[i]>=avgv*vol_mult:
            e=cl[i]
            r30 = cl[i+6]/e-1
            r1h = cl[i+12]/e-1
            r4h = cl[i+48]/e-1
            mx4h = max(hi[i+1:i+49])/e-1   # 진입후 4h내 최고 도달
            ev.append((r30,r1h,r4h,mx4h)); i+=12   # 같은 점화 중복 줄이려 12봉 건너뜀
        else: i+=1
    return ev


def dist(label, vals):
    n=len(vals)
    if not n: print(f"  {label:22} 0건"); return
    p=[v*100 for v in vals]
    avg=sum(p)/n; med=st.median(p)
    up10=sum(1 for x in p if x>10)/n*100; up30=sum(1 for x in p if x>30)/n*100
    dn=sum(1 for x in p if x<0)/n*100
    print(f"  {label:22} {n:4}건 | 평균{avg:+.1f}% 중앙{med:+.1f}% | +10%이상{up10:.0f}% +30%이상{up30:.0f}% | 하락{dn:.0f}%")


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    print(f"알트 {len(coins)}개 | '점화 후 실제로 어떻게 되나' 실측 (전구간 5m)\n")
    for ig,vm,tag in ((0.04,3.0,"점화+4% 거래량3배"),(0.06,5.0,"점화+6% 거래량5배"),(0.10,10.0,"극단: +10% 거래량10배")):
        allev=[]
        for c in coins: allev+=events(c,ig,vm)
        print(f"=== {tag} ({len(allev)}건) — 진입 후 전방수익 분포 ===")
        dist("+30분 후", [e[0] for e in allev])
        dist("+1시간 후", [e[1] for e in allev])
        dist("+4시간 후", [e[2] for e in allev])
        dist("4h내 최고점 도달", [e[3] for e in allev])
        print()
