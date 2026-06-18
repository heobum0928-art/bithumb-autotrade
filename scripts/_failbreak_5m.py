"""[검증 #23-5m] 베어트랩+확인신호를 5분봉으로 — 표본 확대 검증.
일봉은 약세장 투매-반등이 2년 31번뿐(t0.93 정체). 5분봉 90일(전구간 약세장)이면 표본 수백~수천.
가설: 신호가 진짜면 표본 늘수록 t가 오른다(통계 확신). 가짜면 0 근처로 수렴.
실패한붕괴(N봉 지지 깬뒤 회복) + 거래량급증 + 강한회복. 청산 트레일/SL/타임아웃(봉).
walk-forward TEST[0.6,1.0), 비용0.16/0.30%. 코인별 최신 5m 스냅샷."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"


def load_5m():
    """코인별 최신 5m_90d 스냅샷."""
    latest = {}
    for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
        name = Path(f).name
        coin = name.split("_5m_90d_")[0]
        date = name.split("_5m_90d_")[1].replace(".json", "")
        if coin not in latest or date > latest[coin][1]:
            latest[coin] = (f, date)
    cc = {}
    for coin, (f, _) in latest.items():
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if len(d) >= 2000:
            cc[coin] = d
    return cc


def trades(candles, N, volmult, closepos, trail, sl, to_bars, cost):
    n = len(candles); a, b = int(n*0.6), n
    cl=[c["trade_price"] for c in candles]; hi_=[c["high_price"] for c in candles]
    lo_=[c["low_price"] for c in candles]; vol=[c.get("candle_acc_trade_volume",0) for c in candles]
    out=[]; i=max(N,20)
    while i < n-1:
        if not (a<=i<b): i+=1; continue
        support=min(lo_[i-N:i])
        sma=sum(cl[i-N:i])/N
        trap = lo_[i]<support and cl[i]>support and cl[i]<sma
        if trap and volmult>0:
            avgv=sum(vol[i-N:i])/N
            if not (avgv>0 and vol[i]>=avgv*volmult): trap=False
        if trap and closepos>0:
            rng=hi_[i]-lo_[i]
            if not (rng>0 and (cl[i]-lo_[i])/rng>=closepos): trap=False
        if trap:
            entry=cl[i]; peak=entry; end=min(i+1+to_bars,n); exited=False
            for j in range(i+1,end):
                if lo_[j]<=entry*(1+sl): out.append(sl-cost); i=j; exited=True; break
                peak=max(peak,hi_[j])
                if lo_[j]<=peak*(1-trail) and peak>entry*(1+trail*0.5):
                    out.append((peak*(1-trail)-entry)/entry-cost); i=j; exited=True; break
            if not exited: out.append((cl[min(end,n)-1]-entry)/entry-cost); i=end
        else: i+=1
    return out


def report(label, allt):
    n=len(allt)
    if not n: print(f"  {label:34} 0건"); return
    p=[x*100 for x in allt]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:34} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.3f}% t{t:+.2f} 표본합{sum(p):+.0f}%")


if __name__=="__main__":
    cc=load_5m()
    print(f"5m 코인 {len(cc)} | 90일 전구간 약세장 | 베어트랩+confluence | TEST[0.6,1.0)\n")
    print("=== 비용0.16% | 트레일2%/SL-3%/타임아웃48봉(4h) ===")
    for lab,vm,cp in [("기본(필터없음)",0,0),("+거래량2x",2.0,0),("+강한회복0.7",0,0.7),
                       ("+거래량2x+회복0.7",2.0,0.7),("+거래량3x+회복0.7",3.0,0.7)]:
        allt=[]
        for coin,d in cc.items(): allt+=trades(d,48,vm,cp,0.02,-0.03,48,0.0016)
        report(lab,allt)
    print("\n=== 최강필터(거래량3x+회복0.7) 비용 스트레스 ===")
    for cost,tag in ((0.0016,"0.16%"),(0.0030,"0.30%"),(0.0050,"0.50%")):
        allt=[]
        for coin,d in cc.items(): allt+=trades(d,48,3.0,0.7,0.02,-0.03,48,cost)
        report(f"비용{tag}",allt)
