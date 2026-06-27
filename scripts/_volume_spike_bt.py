"""거래대금 급증 진입 백테 — "거래량 높은(터진) 종목을 사면 먹나?" 직접 검증.

사용자 반복 직관: "매일 거래대금 높은 급등주 나오는데 거기서 고르면 되잖아".
이걸 가장 단순하게: 봉 거래대금이 평소(20봉평균)의 K배로 터진 순간 진입 → N봉 후.
방향 무관(그냥 '거래량 터진 종목 추격'). cascade와 차이: 급락+반등 조건 없음 = 순수 거래량.

walk-forward TEST[0.6,1.0). 출구 cascade동일(SL-2%/트레일1.5%, 슬리피지반영). 비용0.30%.
양봉만 / 전체 둘 다.
"""
import sys, json, glob, statistics, math, os
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"
COST = 0.30; HOLD = 24; SL = -2.0; TRAIL = 1.5

latest = {}
for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
    base = os.path.basename(f); coin = base.split("_5m_90d_")[0]
    date = base.split("_5m_90d_")[1].replace(".json","")
    if coin not in latest or date > latest[coin][1]:
        latest[coin] = (f, date)

data = {}
for coin,(path,_) in latest.items():
    try: d = json.load(open(path, encoding="utf-8"))
    except: continue
    cl=[x["trade_price"] for x in d]; op=[x["opening_price"] for x in d]
    vl=[float(x.get("candle_acc_trade_price",0)) for x in d]
    data[coin]=(cl,op,vl)
print(f"{len(data)}종목 로드\n")

def simulate(sigs):
    out=[]
    for coin,i in sigs:
        cl,op,vl=data[coin]; entry=cl[i]; n=len(cl); high=entry; ret=None
        for j in range(i+1,min(i+1+HOLD,n)):
            pr=(cl[j]/entry-1)*100; high=max(high,cl[j]); hp=(high/entry-1)*100
            if pr<=SL: ret=SL; break
            if hp>=TRAIL and pr<=hp-TRAIL: ret=pr; break
        if ret is None: ret=(cl[min(i+HOLD,n-1)]/entry-1)*100
        out.append((ret-COST, i/n))
    return out

def report(rs, label):
    if not rs: print(f"  {label}: 신호없음"); return
    v=[r[0] for r in rs]; m=statistics.mean(v)
    te=[r for r in rs if r[1]>=0.6]; vt=[r[0] for r in te]
    if not vt: print(f"  {label}: TEST없음"); return
    mt=statistics.mean(vt); sdt=statistics.pstdev(vt) if len(vt)>1 else 0
    tt=mt/(sdt/math.sqrt(len(vt))) if sdt>0 else 0
    wrt=sum(1 for x in vt if x>0)/len(vt)
    verdict="✅" if tt>=2.0 else ("⚠️" if tt>=1.5 else "❌")
    print(f"  {label:30s} TEST n={len(te):4d} avg={mt:+.3f}% 승률={wrt*100:.0f}% t={tt:+.2f} {verdict}")

def get_sigs(kmult, green_only):
    sigs=[]
    for coin,(cl,op,vl) in data.items():
        n=len(cl); i=21
        while i<n-HOLD:
            avgv=statistics.mean(vl[i-20:i]) if i>=20 else 0
            vr=vl[i]/avgv if avgv>0 else 0
            if vr>=kmult and (not green_only or cl[i]>op[i]):
                sigs.append((coin,i)); i+=HOLD; continue
            i+=1
    return sigs

print("=== 거래대금 급증 진입 (방향무관) ===")
for k in [2,3,5,10]:
    report(simulate(get_sigs(k,False)), f"거래량 {k}배 급증")

print("\n=== 거래대금 급증 + 양봉만 ===")
for k in [2,3,5,10]:
    report(simulate(get_sigs(k,True)), f"거래량 {k}배 + 양봉")
