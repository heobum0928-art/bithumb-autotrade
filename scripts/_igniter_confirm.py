"""[검증 #30] 점화 '확인 후 진입' — 스파이크 쫓지 말고, 안 무너지고 버틴 코인만 산다.
밤샘실측: 식는놈(HPP/RE)은 30분내 무너짐, 버티는놈(ALICE)은 +20%. 차이=진입후 다샀던것.
창의: 점화(bar i) → K봉 뒤에도 점화레벨 이상 '버티면' 그때 진입 → 식는놈 자동필터.
청산 트레일/타임아웃. 90일 5m, TEST[0.6,1.0), 비용0.30%."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"
COST = 0.0030


def load_5m(coin):
    fs = sorted(glob.glob(str(CACHE / f"{coin}_5m_90d_*.json")))
    if not fs: return None
    d = json.loads(Path(fs[-1]).read_text(encoding="utf-8"))
    return d if len(d) >= 2000 else None


def run(coin, ig_pct, vm, confirm_k, hold_ratio, trail, sl, to):
    """점화 bar i → i+K 종가가 점화봉종가×(1+hold_ratio) 이상(버팀)이면 i+K에 진입."""
    d=load_5m(coin)
    if not d: return []
    n=len(d); a=int(n*0.6)
    cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
    lo=[x["low_price"] for x in d]; op=[x["opening_price"] for x in d]
    vol=[x.get("candle_acc_trade_volume",0) for x in d]
    out=[]; i=20
    while i<n-1-confirm_k:
        if i<a: i+=1; continue
        bar=cl[i]/op[i]-1 if op[i]>0 else 0; avgv=sum(vol[i-20:i])/20
        if not (bar>=ig_pct and avgv>0 and vol[i]>=avgv*vm):
            i+=1; continue
        ig_level=cl[i]; ci=i+confirm_k
        # 확인: K봉 뒤에도 버티나
        if cl[ci] < ig_level*(1+hold_ratio):
            i=ci; continue   # 무너짐 → 스킵
        # 진입 (확인봉 종가)
        entry=cl[ci]; peak=entry; end=min(ci+1+to,n); exited=False
        for j in range(ci+1,end):
            if lo[j]<=entry*(1+sl): out.append(sl-COST); i=j; exited=True; break
            peak=max(peak,hi[j])
            if trail>0 and lo[j]<=peak*(1-trail) and peak>entry*(1+trail):
                out.append((peak*(1-trail)-entry)/entry-COST); i=j; exited=True; break
        if not exited: out.append((cl[min(end,n)-1]-entry)/entry-COST); i=end
    return out


def rep(label, allt):
    n=len(allt)
    if not n: print(f"  {label:32} 0건"); return
    p=[x*100 for x in allt]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:32} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.3f}% t{t:+.2f}")


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    print(f"알트 {len(coins)}개 | 점화 '확인후진입' | TEST[0.6,1.0) | 비용0.30%\n")
    for ig,vm in ((0.05,4.0),(0.08,5.0)):
        print(f"=== 점화 +{ig*100:.0f}% 거래량{vm:.0f}배 ===")
        for K in (6, 12):   # 30분, 60분 확인
            for hr in (0.0, 0.02):  # 그대로 버팀 / +2% 더 올라가며 버팀
                allt=sum((run(c,ig,vm,K,hr,0.04,-0.04,48) for c in coins),[])
                rep(f"확인{K*5}분(버팀+{hr*100:.0f}%)→트레일4%", allt)
        print()
