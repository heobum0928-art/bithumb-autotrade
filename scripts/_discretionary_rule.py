"""[검증 #24] 재량 논리 규칙화 — '추격 말고 덜 익은 초기 모멘텀'.
재량콜(TRUST)에서 쓴 기준을 규칙으로: 오늘 +lo~hi% 상승(과펌핑 아님)
+ 직전 5일 누적 급등 아님(초기단계) → 매수. 트레일/SL/타임아웃 청산.
표본 100건+ 목표. 잡주 포함 전체 + 거래대금티어 분리. walk-forward TEST, 장세분리."""
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


def regime():
    d = json.loads((DAILY / "BTC_1d.json").read_text(encoding="utf-8"))
    cl = [x["trade_price"] for x in d]; r = {}
    for i in range(len(d)):
        if i < 200: continue
        r[d[i]["candle_date_time_kst"][:10]] = "BULL" if cl[i] > sum(cl[i-200:i])/200 else "BEAR"
    return r


def turnover(d):
    v = [(c.get("candle_acc_trade_price") or 0) for c in d[-60:]]
    return st.median(v)/1e8 if v else 0


def trades(candles, lo, hi, run5_cap, trail, sl, to, cost):
    n = len(candles); a, b = int(n*0.6), n
    cl=[c["trade_price"] for c in candles]; hi_=[c["high_price"] for c in candles]; low=[c["low_price"] for c in candles]
    out=[]; i=max(6,1)
    while i < n-1:
        if not (a<=i<b): i+=1; continue
        day = cl[i]/cl[i-1]-1
        run5 = cl[i]/cl[i-5]-1 if i>=5 else 0
        if lo <= day <= hi and run5 <= run5_cap:        # 초기 모멘텀(과펌핑/늦은추격 배제)
            entry=cl[i]; peak=entry; end=min(i+1+to,n); exited=False
            edate=candles[i]["candle_date_time_kst"][:10]
            for j in range(i+1,end):
                if low[j]<=entry*(1+sl): out.append((sl-cost,edate)); i=j; exited=True; break
                peak=max(peak,hi_[j])
                if low[j]<=peak*(1-trail) and peak>entry*(1+trail*0.5):
                    out.append(((peak*(1-trail)-entry)/entry-cost,edate)); i=j; exited=True; break
            if not exited: out.append(((cl[min(end,n)-1]-entry)/entry-cost,edate)); i=end
        else: i+=1
    return out


def rep(label, rows, reg=None, rgm=None):
    if reg is not None: rows=[r for r in rows if reg.get(r[1])==rgm]
    n=len(rows)
    if not n: print(f"  {label:24} 0건"); return
    p=[r[0]*100 for r in rows]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:24} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.2f}% t{t:+.2f}")


if __name__=="__main__":
    cc=load(); reg=regime()
    print(f"보유 {len(cc)}코인 | #24 재량규칙(덜익은 초기모멘텀) | TEST[0.6,1.0)\n")
    P=dict(lo=0.03,hi=0.12,run5_cap=0.25,trail=0.05,sl=-0.07,to=15)
    print("=== 전체(잡주 포함) | 비용0.16% / 0.30% ===")
    for cost,tag in ((0.0016,"0.16%"),(0.0030,"0.30%")):
        allt=[]
        for c,d in cc.items(): allt+=trades(d,P["lo"],P["hi"],P["run5_cap"],P["trail"],P["sl"],P["to"],cost)
        rep(f"전체 비용{tag}",allt)
    allt=[]
    for c,d in cc.items(): allt+=trades(d,P["lo"],P["hi"],P["run5_cap"],P["trail"],P["sl"],P["to"],0.0016)
    print("\n=== 장세분리 (비용0.16%) ===")
    rep("BEAR 진입",allt,reg,"BEAR"); rep("BULL 진입",allt,reg,"BULL")
    print("\n=== 거래대금 티어 (전체기간, 비용0.16%) — 잡주가 더 되나? ===")
    tiers={"고(≥50억)":[],"중(10~50억)":[],"잡주(<10억)":[]}
    for c,d in cc.items():
        tv=turnover(d); tr=trades(d,P["lo"],P["hi"],P["run5_cap"],P["trail"],P["sl"],P["to"],0.0016)
        key="고(≥50억)" if tv>=50 else "중(10~50억)" if tv>=10 else "잡주(<10억)"
        tiers[key]+=tr
    for k,v in tiers.items(): rep(k,v)
