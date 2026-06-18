"""[검증 #23] 베어트랩 + 확인신호 겹치기(confluence) — '한 기법 말고 응용/조합'.
#22 베어트랩(지지선 깬뒤 회복)은 약세장 양수방향이나 얇음(t0.48).
가설: 진짜 손절사냥 반등만 가려내는 직교필터를 겹치면 약세장 엣지가 비용 넘는가?
필터A 거래량급증(회복캔들 vol≥N일평균×mult=투매확인), 필터B 강한회복(종가가 당일레인지 상단≥pos).
조합별 약세장 t 비교. 일봉, walk-forward TEST[0.6,1.0), 비용0.16%. 다중검정 주의(조합 많을수록 과적합)."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "candles_daily"


def load():
    cc = {}
    for f in glob.glob(str(DAILY / "*_1d.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if len(d) >= 250:
            cc[Path(f).stem.replace("_1d", "")] = d
    return cc


def btc_regime():
    d = json.loads((DAILY / "BTC_1d.json").read_text(encoding="utf-8"))
    closes = [x["trade_price"] for x in d]; reg = {}
    for i in range(len(d)):
        if i < 200: continue
        reg[d[i]["candle_date_time_kst"][:10]] = "BULL" if closes[i] > sum(closes[i-200:i])/200 else "BEAR"
    return reg


def trades(candles, N, volmult, closepos, cost):
    """N20 베어트랩 + (volmult>0면 거래량필터) + (closepos>0면 강한회복필터). trail5/sl-7/15d."""
    n = len(candles); a, b = int(n*0.6), n
    cl = [c["trade_price"] for c in candles]; hi_=[c["high_price"] for c in candles]
    lo_=[c["low_price"] for c in candles]
    vol=[c.get("candle_acc_trade_volume", c.get("candle_acc_trade_price",0)) for c in candles]
    out=[]; i=max(N,20)
    while i < n-1:
        if not (a<=i<b): i+=1; continue
        support=min(lo_[i-N:i]); sma20=sum(cl[i-20:i])/20
        trap = lo_[i]<support and cl[i]>support and cl[i]<sma20
        if trap and volmult>0:
            avgv=sum(vol[i-N:i])/N
            if not (avgv>0 and vol[i]>=avgv*volmult): trap=False
        if trap and closepos>0:
            rng=hi_[i]-lo_[i]
            if not (rng>0 and (cl[i]-lo_[i])/rng>=closepos): trap=False
        if trap:
            entry=cl[i]; peak=entry; end=min(i+1+15,n); exited=False
            edate=candles[i]["candle_date_time_kst"][:10]
            for j in range(i+1,end):
                if lo_[j]<=entry*0.93: out.append((-0.07-cost,edate)); i=j; exited=True; break
                peak=max(peak,hi_[j])
                if lo_[j]<=peak*0.95 and peak>entry*1.01:
                    out.append(((peak*0.95-entry)/entry-cost,edate)); i=j; exited=True; break
            if not exited: out.append(((cl[min(end,n)-1]-entry)/entry-cost,edate)); i=end
        else: i+=1
    return out


def stat(label, rows, reg, rgm="BEAR"):
    rows=[r for r in rows if reg.get(r[1])==rgm]
    n=len(rows)
    if not n: print(f"  {label:30} 0건"); return
    p=[r[0]*100 for r in rows]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:30} {n:3}건 승률{wr:3.0f}% 거래당{avg:+.2f}% t{t:+.2f}")


if __name__=="__main__":
    cc=load(); reg=btc_regime()
    print(f"보유 {len(cc)}코인 | #23 베어트랩+확인신호 조합 | 약세장(BEAR) 진입만 | TEST\n")
    combos=[("기본(#22)",0,0),("+거래량1.5x",1.5,0),("+거래량2.0x",2.0,0),
            ("+강한회복0.6",0,0.6),("+강한회복0.7",0,0.7),
            ("+거래량1.5+회복0.6",1.5,0.6),("+거래량2.0+회복0.7",2.0,0.7)]
    for lab,vm,cp in combos:
        allt=[]
        for coin,d in cc.items(): allt+=trades(d,20,vm,cp,0.0016)
        stat(lab,allt,reg)
