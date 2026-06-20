"""[검증 #31 로버스트] 점화 ML 분류 — P>=0.7 신호가 우연인가 진짜인가.
①데이터확대: 점화 +3%/거래량2.5배로 이벤트↑. ②워크포워드 3폴드(과거학습→미래검증, 누적).
③여러시드 평균(모델 안정성). ④순열검정: 라벨 섞으면 모델이 엣지 못 만들어야(가짜 아님 확인).
out-of-sample 고확신 거래만 풀링해 t값. 비용0.30%."""
import sys, json, glob, statistics as st
from pathlib import Path
import numpy as np
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from sklearn.ensemble import GradientBoostingClassifier
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"
COST = 0.0030
IG, VM = 0.03, 2.5
FEATS = ["ig_bar","surge15","volmult","trend1h","trend4h","vola","rangepos","greenstreak","bodyratio","upperwick","btcmove","hour"]


def load_5m(coin):
    fs = sorted(glob.glob(str(CACHE / f"{coin}_5m_90d_*.json")))
    if not fs: return None
    d=json.loads(Path(fs[-1]).read_text(encoding="utf-8")); return d if len(d)>=2000 else None


def btc_absmove_map(K=12):
    d=load_5m("BTC")
    if not d: return {}
    cl=[x["trade_price"] for x in d]; t=[x["candle_date_time_kst"] for x in d]
    return {t[i]: abs(cl[i]/cl[i-K]-1) for i in range(K,len(d)) if cl[i-K]>0}


def fixed_exit_pnl(cl,hi,lo,i,n,trail=0.03,sl=-0.03,to=48):
    entry=cl[i]; peak=entry; end=min(i+1+to,n)
    for j in range(i+1,end):
        if lo[j]<=entry*(1+sl): return sl-COST
        peak=max(peak,hi[j])
        if lo[j]<=peak*(1-trail) and peak>entry*(1+trail): return (peak*(1-trail)-entry)/entry-COST
    return (cl[min(end,n)-1]-entry)/entry-COST


def build(coins,bmap):
    rows=[]
    for coin in coins:
        d=load_5m(coin)
        if not d: continue
        n=len(d)
        cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
        lo=[x["low_price"] for x in d]; op=[x["opening_price"] for x in d]
        vol=[x.get("candle_acc_trade_volume",0) for x in d]; tk=[x["candle_date_time_kst"] for x in d]
        i=48
        while i<n-49:
            bar=cl[i]/op[i]-1 if op[i]>0 else 0; avgv=sum(vol[i-20:i])/20
            if not (bar>=IG and avgv>0 and vol[i]>=avgv*VM): i+=1; continue
            rng=max(hi[i-48:i])-min(lo[i-48:i]) or 1e-9; br=hi[i]-lo[i] or 1e-9
            gs=0
            for k in range(i,max(i-10,0),-1):
                if cl[k]>op[k]: gs+=1
                else: break
            rets=[cl[k]/cl[k-1]-1 for k in range(i-12,i) if cl[k-1]>0]
            f=[bar,cl[i]/cl[i-3]-1,vol[i]/avgv,cl[i]/cl[i-12]-1,cl[i]/cl[i-48]-1,
               st.pstdev(rets) if len(rets)>1 else 0,(cl[i]-min(lo[i-48:i]))/rng,gs,
               (cl[i]-op[i])/br,(hi[i]-cl[i])/br,bmap.get(tk[i],0),int(tk[i][11:13])]
            rows.append((i/n,f,fixed_exit_pnl(cl,hi,lo,i,n))); i+=12
    return rows


def tstat(p):
    n=len(p)
    if not n: return 0,0,0
    m=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    return m,(m/(sd/n**0.5) if sd else 0), n


def walkforward(rows, seeds=(1,2,3,4,5), shuffle=False):
    folds=[(0.0,0.5,0.65),(0.0,0.65,0.8),(0.0,0.8,1.0)]
    sel_all=[]
    for s in seeds:
        rng=np.random.RandomState(s)
        for lo_,cut,hi_ in folds:
            tr=[r for r in rows if r[0]<cut]; te=[r for r in rows if cut<=r[0]<hi_]
            if len(tr)<50 or len(te)<10: continue
            Xtr=np.array([r[1] for r in tr]); ytr=np.array([1 if r[2]>0 else 0 for r in tr])
            if shuffle: ytr=rng.permutation(ytr)
            Xte=np.array([r[1] for r in te]); pte=[r[2] for r in te]
            clf=GradientBoostingClassifier(max_depth=3,n_estimators=120,learning_rate=0.05,subsample=0.8,random_state=s)
            clf.fit(Xtr,ytr)
            prob=clf.predict_proba(Xte)[:,1]
            for k in range(len(te)):
                if prob[k]>=0.7: sel_all.append(pte[k])
    return sel_all


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    rows=build(coins, btc_absmove_map())
    base=[r[2] for r in rows]
    bm,bt,bn=tstat([x*100 for x in base])
    print(f"점화 이벤트 {len(rows)}건 (기준 +{IG*100:.0f}%/거래량{VM}배) | 전체 거래당{bm:+.2f}% t{bt:+.2f}\n")
    print("=== 워크포워드 3폴드 × 5시드, 모델 P>=0.7 선별 (out-of-sample 풀링) ===")
    real=walkforward(rows)
    m,t,nn=tstat([x*100 for x in real])
    print(f"  실제 라벨:  {nn}건 거래당{m:+.3f}% t{t:+.2f}  (전체 {bm:+.2f}% 대비)")
    print("\n=== 순열검정: 라벨 섞었을 때 (진짜면 여기선 엣지 사라져야) ===")
    sh=walkforward(rows, shuffle=True)
    ms,ts,ns=tstat([x*100 for x in sh])
    print(f"  섞은 라벨:  {ns}건 거래당{ms:+.3f}% t{ts:+.2f}")
    print(f"\n판정: 실제 t{t:+.2f} vs 섞음 t{ts:+.2f} → ", end="")
    print("모델이 진짜 구조 학습 (실제>>섞음)" if (m>0 and m>ms+0.3) else "구분 불명확 (실제≈섞음 = 우연일 수도)")
