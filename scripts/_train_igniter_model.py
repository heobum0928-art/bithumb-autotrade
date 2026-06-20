"""점화 ML 모델 학습·저장 — 알림봇이 실시간 점수 매기게.
전체 점화 이벤트로 학습 → data/igniter_model.pkl 저장. 특징순서 동일(FEATS)."""
import sys, json, glob, statistics as st, pickle
from pathlib import Path
import numpy as np
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from sklearn.ensemble import GradientBoostingClassifier
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"
COST=0.0030; IG,VM=0.03,2.5
FEATS=["ig_bar","surge15","volmult","trend1h","trend4h","vola","rangepos","greenstreak","bodyratio","upperwick","btcmove","hour"]


def load_5m(coin):
    fs=sorted(glob.glob(str(CACHE/f"{coin}_5m_90d_*.json")))
    if not fs: return None
    d=json.loads(Path(fs[-1]).read_text(encoding="utf-8")); return d if len(d)>=2000 else None


def btc_map(K=12):
    d=load_5m("BTC")
    if not d: return {}
    cl=[x["trade_price"] for x in d]; t=[x["candle_date_time_kst"] for x in d]
    return {t[i]:abs(cl[i]/cl[i-K]-1) for i in range(K,len(d)) if cl[i-K]>0}


def pnl(cl,hi,lo,i,n,trail=0.03,sl=-0.03,to=48):
    e=cl[i]; pk=e; end=min(i+1+to,n)
    for j in range(i+1,end):
        if lo[j]<=e*(1+sl): return sl-COST
        pk=max(pk,hi[j])
        if lo[j]<=pk*(1-trail) and pk>e*(1+trail): return (pk*(1-trail)-e)/e-COST
    return (cl[min(end,n)-1]-e)/e-COST


def feats_at(cl,hi,lo,op,vol,tk,i,bmap):
    rng=max(hi[i-48:i])-min(lo[i-48:i]) or 1e-9; br=hi[i]-lo[i] or 1e-9
    gs=0
    for k in range(i,max(i-10,0),-1):
        if cl[k]>op[k]: gs+=1
        else: break
    rets=[cl[k]/cl[k-1]-1 for k in range(i-12,i) if cl[k-1]>0]
    return [cl[i]/op[i]-1, cl[i]/cl[i-3]-1, vol[i]/(sum(vol[i-20:i])/20),
            cl[i]/cl[i-12]-1, cl[i]/cl[i-48]-1, st.pstdev(rets) if len(rets)>1 else 0,
            (cl[i]-min(lo[i-48:i]))/rng, gs, (cl[i]-op[i])/br, (hi[i]-cl[i])/br,
            bmap.get(tk[i],0), int(tk[i][11:13])]


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json")))-{"BTC"})
    bmap=btc_map(); X=[]; y=[]
    for coin in coins:
        d=load_5m(coin)
        if not d: continue
        n=len(d)
        cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]; lo=[x["low_price"] for x in d]
        op=[x["opening_price"] for x in d]; vol=[x.get("candle_acc_trade_volume",0) for x in d]; tk=[x["candle_date_time_kst"] for x in d]
        i=48
        while i<n-49:
            bar=cl[i]/op[i]-1 if op[i]>0 else 0; avgv=sum(vol[i-20:i])/20
            if bar>=IG and avgv>0 and vol[i]>=avgv*VM:
                X.append(feats_at(cl,hi,lo,op,vol,tk,i,bmap)); y.append(1 if pnl(cl,hi,lo,i,n)>0 else 0); i+=12
            else: i+=1
    X=np.array(X); y=np.array(y)
    clf=GradientBoostingClassifier(max_depth=3,n_estimators=120,learning_rate=0.05,subsample=0.8,random_state=1)
    clf.fit(X,y)
    out=ROOT/"data"/"igniter_model.pkl"
    pickle.dump({"model":clf,"feats":FEATS,"ig":IG,"vm":VM}, open(out,"wb"))
    print(f"학습 완료: {len(X)}건 (양수 {y.mean()*100:.0f}%) → {out}")
