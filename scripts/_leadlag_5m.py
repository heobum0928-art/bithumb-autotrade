"""[검증 #25] BTC 리드 / 알트 래그 (5분봉) — 진짜 미시도 인트라데이 메커니즘.
가설: BTC가 5분봉에서 확 오르면(N봉 +thr%↑), 아직 안 따라온 알트(자기 N봉 수익 < BTC×ratio)가
뒤늦게 따라온다 → 그 래그를 먹는다. BTC를 신호로, 알트를 진입대상으로.
청산 트레일/SL/타임아웃(봉). 90일 5m, 전구간 약세. walk-forward TEST[0.6,1.0). 비용0.16/0.30%."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"


def latest(coin):
    fs = sorted(glob.glob(str(CACHE / f"{coin}_5m_90d_*.json")))
    return fs[-1] if fs else None


def load_5m(coin):
    f = latest(coin)
    if not f: return None
    d = json.loads(Path(f).read_text(encoding="utf-8"))
    return d if len(d) >= 2000 else None


def btc_move_map(N):
    """kst_time -> 직전 N봉 BTC 수익률."""
    d = load_5m("BTC")
    if not d: return {}
    cl = [x["trade_price"] for x in d]; t = [x["candle_date_time_kst"] for x in d]
    m = {}
    for i in range(N, len(d)):
        if cl[i-N] > 0:
            m[t[i]] = cl[i]/cl[i-N]-1
    return m


def trades(coin, btc_map, N, thr, lag_ratio, trail, sl, to, cost):
    d = load_5m(coin)
    if not d: return []
    n = len(d); a = int(n*0.6)
    cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
    lo=[x["low_price"] for x in d]; tk=[x["candle_date_time_kst"] for x in d]
    out=[]; i=max(N,1)
    while i < n-1:
        if i < a: i+=1; continue
        bmove = btc_map.get(tk[i])
        if bmove is None or bmove < thr:
            i+=1; continue
        amove = cl[i]/cl[i-N]-1 if cl[i-N]>0 else 0
        if amove >= bmove*lag_ratio:   # 알트가 이미 따라왔으면 패스(래그 아님)
            i+=1; continue
        entry=cl[i]; peak=entry; end=min(i+1+to,n); exited=False
        for j in range(i+1,end):
            if lo[j]<=entry*(1+sl): out.append(sl-cost); i=j; exited=True; break
            peak=max(peak,hi[j])
            if lo[j]<=peak*(1-trail) and peak>entry*(1+trail*0.5):
                out.append((peak*(1-trail)-entry)/entry-cost); i=j; exited=True; break
        if not exited: out.append((cl[min(end,n)-1]-entry)/entry-cost); i=end
    return out


def rep(label, allt):
    n=len(allt)
    if not n: print(f"  {label:30} 0건"); return
    p=[x*100 for x in allt]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:30} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.3f}% t{t:+.2f}")


if __name__=="__main__":
    coins=[Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))]
    coins=sorted(set(c for c in coins if c!="BTC"))
    print(f"알트 {len(coins)}개 | BTC리드-알트래그 5m | TEST[0.6,1.0)\n")
    for N,thr in ((3,0.005),(3,0.010),(6,0.010)):
        bmap=btc_move_map(N)
        print(f"=== BTC {N}봉(={N*5}분) +{thr*100:.1f}%↑ 신호 | 트레일1.5%/SL-2%/12봉 ===")
        for lr in (0.3, 0.5):
            allt=[]
            for c in coins:
                allt+=trades(c, bmap, N, thr, lr, 0.015, -0.02, 12, 0.0016)
            rep(f"래그비율<{lr} 비용0.16%", allt)
        # 최강조합 0.30% 스트레스
        allt=[]
        for c in coins:
            allt+=trades(c, bmap, N, thr, 0.5, 0.015, -0.02, 12, 0.0030)
        rep("(래그<0.5) 비용0.30%", allt)
        print()
