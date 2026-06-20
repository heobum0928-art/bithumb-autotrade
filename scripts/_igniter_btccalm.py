"""[검증 #27] 점화+빨리자르기 + BTC잠잠 게이트 — 두 발견 결합.
#26: 큰점화(+6~8%)+트레일2%가 본전(t0.1). #24: 알트엣지는 BTC 잠잠할때만 산다.
가설: 점화를 BTC가 안 휘저을 때만 사면 본전→양수로 넘어가나?
BTC 직전 K봉 |수익|<calm_thr 일 때만 진입. 90일 5m, TEST[0.6,1.0), 비용0.30%(현실)."""
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


def btc_absmove_map(K):
    d = load_5m("BTC")
    if not d: return {}
    cl=[x["trade_price"] for x in d]; t=[x["candle_date_time_kst"] for x in d]
    return {t[i]: abs(cl[i]/cl[i-K]-1) for i in range(K,len(d)) if cl[i-K]>0}


def trades(coin, ig_pct, vol_mult, sl, trail, to, cost, bmap=None, calm=None):
    d = load_5m(coin)
    if not d: return []
    n=len(d); a=int(n*0.6)
    cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
    lo=[x["low_price"] for x in d]; op=[x["opening_price"] for x in d]
    vol=[x.get("candle_acc_trade_volume",0) for x in d]; tk=[x["candle_date_time_kst"] for x in d]
    out=[]; i=20
    while i < n-1:
        if i < a: i+=1; continue
        bar = cl[i]/op[i]-1 if op[i]>0 else 0
        avgv = sum(vol[i-20:i])/20
        ok = bar>=ig_pct and avgv>0 and vol[i]>=avgv*vol_mult
        if ok and bmap is not None:
            bm = bmap.get(tk[i])
            if bm is None or bm >= calm: ok=False   # BTC가 크게 움직이면 패스
        if ok:
            entry=cl[i]; peak=entry; end=min(i+1+to,n); exited=False
            for j in range(i+1,end):
                if lo[j]<=entry*(1+sl): out.append(sl-cost); i=j; exited=True; break
                peak=max(peak,hi[j])
                if trail>0 and lo[j]<=peak*(1-trail) and peak>entry*(1+trail):
                    out.append((peak*(1-trail)-entry)/entry-cost); i=j; exited=True; break
            if not exited: out.append((cl[min(end,n)-1]-entry)/entry-cost); i=end
        else: i+=1
    return out


def rep(label, allt):
    n=len(allt)
    if not n: print(f"  {label:30} 0건"); return
    p=[x*100 for x in allt]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:30} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.3f}% t{t:+.2f}")


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    print(f"알트 {len(coins)}개 | 점화+트레일2% + BTC잠잠 게이트 | TEST[0.6,1.0) | 비용0.30%\n")
    for K in (6, 12):
        bmap=btc_absmove_map(K)
        print(f"=== BTC 직전 {K}봉(={K*5}분) 변동 기준 ===")
        for ig,vm in ((0.06,3.0),(0.08,5.0)):
            base=sum((trades(c,ig,vm,-0.03,0.02,48,0.003) for c in coins),[])
            rep(f"점화+{ig*100:.0f}% (게이트없음)", base)
            for calm in (0.005, 0.010):
                g=sum((trades(c,ig,vm,-0.03,0.02,48,0.003,bmap,calm) for c in coins),[])
                rep(f"  └ BTC<{calm*100:.1f}% 잠잠만", g)
        print()
