"""[검증 #31] 점화 ML 분류 — '니가 학습해서 ALICE vs HPP 구분해라'.
손규칙 30개가 못한 구분을, 모델한테 학습시킨다(머신비전 엔지니어 형 요청).
진입시점 특징 12개(룩어헤드 없음) → 라벨=고정청산 PnL>0(이 펌핑이 먹혔나).
walk-forward: 각 코인 앞60% train / 뒤40% test. test에서 모델이 '먹힐것'만 골라 거래하면 양수?
비교: 전체 test 거래 vs 모델선별 거래. 비용0.30%."""
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
IG, VM = 0.04, 3.0
FEATS = ["ig_bar","surge15","volmult","trend1h","trend4h","vola","rangepos","greenstreak","bodyratio","upperwick","btcmove","hour"]


def load_5m(coin):
    fs = sorted(glob.glob(str(CACHE / f"{coin}_5m_90d_*.json")))
    if not fs: return None
    d = json.loads(Path(fs[-1]).read_text(encoding="utf-8"))
    return d if len(d) >= 2000 else None


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


def build(coins, bmap):
    rows=[]  # (coin, frac_pos, feats[], pnl)
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
            if not (bar>=IG and avgv>0 and vol[i]>=avgv*VM):
                i+=1; continue
            rng=max(hi[i-48:i])-min(lo[i-48:i]); rng=rng if rng>0 else 1e-9
            barrng=hi[i]-lo[i]; barrng=barrng if barrng>0 else 1e-9
            gs=0
            for k in range(i,max(i-10,0),-1):
                if cl[k]>op[k]: gs+=1
                else: break
            rets=[cl[k]/cl[k-1]-1 for k in range(i-12,i) if cl[k-1]>0]
            f=[bar, cl[i]/cl[i-3]-1, vol[i]/avgv, cl[i]/cl[i-12]-1, cl[i]/cl[i-48]-1,
               st.pstdev(rets) if len(rets)>1 else 0,
               (cl[i]-min(lo[i-48:i]))/rng, gs, (cl[i]-op[i])/barrng, (hi[i]-cl[i])/barrng,
               bmap.get(tk[i],0), int(tk[i][11:13])]
            pnl=fixed_exit_pnl(cl,hi,lo,i,n)
            rows.append((i/n, f, pnl)); i+=12
    return rows


def stats(label, pnls):
    n=len(pnls)
    if not n: print(f"  {label:24} 0건"); return
    p=[x*100 for x in pnls]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:24} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.3f}% t{t:+.2f}")


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    print(f"알트 {len(coins)}개 | 점화 ML분류 | feats {len(FEATS)} | 비용0.30%")
    rows=build(coins, btc_absmove_map())
    print(f"점화 이벤트 총 {len(rows)}건\n")
    tr=[r for r in rows if r[0]<0.6]; te=[r for r in rows if r[0]>=0.6]
    Xtr=np.array([r[1] for r in tr]); ytr=np.array([1 if r[2]>0 else 0 for r in tr])
    Xte=np.array([r[1] for r in te]); pnl_te=[r[2] for r in te]
    print(f"train {len(tr)} / test {len(te)} | train 양수비율 {ytr.mean()*100:.0f}%\n")
    clf=GradientBoostingClassifier(max_depth=3, n_estimators=120, learning_rate=0.05, subsample=0.8, random_state=1)
    clf.fit(Xtr,ytr)
    prob=clf.predict_proba(Xte)[:,1]
    print("=== TEST: 모델 확신도별 선별 거래 ===")
    stats("전체(모델없음)", pnl_te)
    for thr in (0.5,0.6,0.7):
        sel=[pnl_te[k] for k in range(len(te)) if prob[k]>=thr]
        stats(f"모델 P>={thr} 선별", sel)
    imp=sorted(zip(FEATS, clf.feature_importances_), key=lambda x:-x[1])
    print("\n특징 중요도:", ", ".join(f"{k}{v:.2f}" for k,v in imp[:6]))
