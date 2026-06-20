"""[검증 #29] 점화 + '기세소진' 청산 — 사람의 재량 매도를 프로그램화.
고정트레일/타깃/분할(#26~28) 다 본전~음수. 사람은 그렇게 안 팜 — '기세 죽는 신호'에 던짐.
기세소진 청산: ①첫 음봉(전봉대비 하락) ②신고가 못뚫고 N봉 정체 ③거래량 급감.
이걸 규칙화하면 사람 흉내. 점화 진입 후 적용. 90일 5m, TEST[0.6,1.0), 비용0.30%."""
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


def run(coin, ig_pct, vm, mode, sl, to, stall=2, varg=0.4):
    d=load_5m(coin)
    if not d: return []
    n=len(d); a=int(n*0.6)
    cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
    lo=[x["low_price"] for x in d]; op=[x["opening_price"] for x in d]
    vol=[x.get("candle_acc_trade_volume",0) for x in d]
    out=[]; i=20
    while i<n-1:
        if i<a: i+=1; continue
        bar=cl[i]/op[i]-1 if op[i]>0 else 0; avgv=sum(vol[i-20:i])/20
        if not (bar>=ig_pct and avgv>0 and vol[i]>=avgv*vm):
            i+=1; continue
        entry=cl[i]; peak=hi[i]; igvol=vol[i]; last_nh=i; end=min(i+1+to,n); exited=False
        for j in range(i+1,end):
            if lo[j]<=entry*(1+sl):
                out.append((entry*(1+sl)-entry)/entry-COST); i=j; exited=True; break
            if hi[j]>peak: peak=hi[j]; last_nh=j
            inprofit = peak>entry*1.02
            sell=False
            if mode=="redbar" and inprofit and cl[j]<cl[j-1]:
                sell=True
            elif mode=="stall" and inprofit and (j-last_nh)>=stall:
                sell=True
            elif mode=="voldry" and inprofit and vol[j] < igvol*varg:
                sell=True
            elif mode=="red+stall" and inprofit and (cl[j]<cl[j-1] or (j-last_nh)>=stall):
                sell=True
            if sell:
                out.append((cl[j]-entry)/entry-COST); i=j; exited=True; break
        if not exited:
            out.append((cl[min(end,n)-1]-entry)/entry-COST); i=end
    return out


def rep(label, allt):
    n=len(allt)
    if not n: print(f"  {label:30} 0건"); return
    p=[x*100 for x in allt]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:30} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.3f}% t{t:+.2f}")


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    print(f"알트 {len(coins)}개 | 점화+기세소진청산 | TEST[0.6,1.0) | 비용0.30%\n")
    for ig,vm in ((0.05,4.0),(0.08,5.0)):
        print(f"=== 점화 +{ig*100:.0f}% 거래량{vm:.0f}배 ===")
        rep("첫음봉 청산", sum((run(c,ig,vm,"redbar",-0.03,48) for c in coins),[]))
        rep("정체2봉 청산", sum((run(c,ig,vm,"stall",-0.03,48,stall=2) for c in coins),[]))
        rep("정체3봉 청산", sum((run(c,ig,vm,"stall",-0.03,48,stall=3) for c in coins),[]))
        rep("거래량급감(<40%) 청산", sum((run(c,ig,vm,"voldry",-0.03,48,varg=0.4) for c in coins),[]))
        rep("음봉or정체3 청산", sum((run(c,ig,vm,"red+stall",-0.03,48,stall=3) for c in coins),[]))
        print()
