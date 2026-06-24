"""캐스케이드 출구 최적화 — 진입(드롭+거래량+반등캔들) 고정, 익절/손절/트레일 조합 탐색.
walk-forward TEST에서 비용후 양수+t유의면 후보. 과적합 경계: 조합 다수 테스트 → TEST 기준만 신뢰."""
import sys, os, json, glob, statistics, math
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"
latest = {}
for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
    base = os.path.basename(f); coin = base.split("_5m_90d_")[0]
    date = base.split("_5m_90d_")[1].replace(".json", "")
    if coin not in latest or date > latest[coin][1]:
        latest[coin] = (f, date)

import os as _os
K = 5; DROP = float(_os.environ.get("DROP","-4.0")); VOL = float(_os.environ.get("VOL","3.0")); COST = 0.30; HOLD = 24  # 최대 2시간

# 진입 시그널 위치만 한 번 추출 (코인별 candle 배열 캐시)
signals = []  # (coin, idx, frac)
data = {}
for coin, (path, _) in latest.items():
    try: d = json.load(open(path, encoding="utf-8"))
    except Exception: continue
    cl=[x["trade_price"] for x in d]; op=[x["opening_price"] for x in d]
    vol=[float(x.get("candle_acc_trade_price",0)) for x in d]
    data[coin]=(cl,op,vol); n=len(cl); i=25
    while i < n-HOLD:
        lh=max(cl[i-K:i+1]); drop=(cl[i]/lh-1)*100
        avgv=statistics.mean(vol[i-20:i]) if i>=20 else 0
        vs=vol[i]/avgv if avgv>0 else 0
        if drop<=DROP and vs>=VOL and cl[i]>op[i]:
            signals.append((coin,i,i/n)); i+=HOLD
        else: i+=1
print(f"진입신호 {len(signals)}건 (드롭<={DROP}% 거래량>={VOL}배 반등)\n")

def simulate(tp, sl, trail):
    """trail: None이면 고정TP/SL. 숫자면 고점대비 트레일%(+SL 동시)."""
    out=[]  # (ret, frac)
    for coin,i,frac in signals:
        cl,op,vol=data[coin]; entry=cl[i]; n=len(cl); high=entry; ret=None
        for j in range(i+1, min(i+1+HOLD,n)):
            pr=(cl[j]/entry-1)*100; high=max(high,cl[j])
            hp=(high/entry-1)*100
            if tp and pr>=tp: ret=tp; break
            if pr<=sl: ret=sl; break
            if trail and hp>=trail and pr<=hp-trail: ret=pr; break
        if ret is None: ret=(cl[min(i+HOLD,n-1)]/entry-1)*100
        out.append((ret-COST, frac))
    return out

def st(rs):
    if not rs: return None
    v=[r[0] for r in rs]; m=statistics.mean(v)
    sd=statistics.pstdev(v) if len(v)>1 else 0
    t=m/(sd/math.sqrt(len(v))) if sd>0 else 0
    win=sum(1 for x in v if x>0)/len(v); return len(v),m,win,t

combos=[]
for tp in [1.0,1.5,2.0,3.0,5.0,None]:
    for sl in [-1.5,-2.0,-3.0,-5.0]:
        for trail in [None,1.0,1.5,2.0]:
            if tp is None and trail is None: continue
            rs=simulate(tp,sl,trail)
            tr=[r for r in rs if r[1]<0.6]; te=[r for r in rs if r[1]>=0.6]
            sa=st(rs); str_=st(tr); ste=st(te)
            if sa and str_ and ste:
                combos.append((tp,sl,trail,sa,str_,ste))

# TEST 기대값 기준 정렬, 상위 출력
combos.sort(key=lambda x: -x[5][1])
print("상위 출구조합 (TEST 비용후 기대값 순) — TP/SL/트레일 | 전체 | TRAIN | TEST")
print(f"{'TP':>5}{'SL':>6}{'TR':>5} | {'전체n/순%/승/t':>22} | {'TEST n/순%/승/t':>22}")
for tp,sl,trail,sa,str_,ste in combos[:12]:
    tpn=f"{tp}" if tp else "—"; trn=f"{trail}" if trail else "—"
    print(f"{tpn:>5}{sl:>6}{trn:>5} | n{sa[0]:>4} {sa[1]:+.3f}% {sa[2]*100:.0f}% t{sa[3]:+.2f} | n{ste[0]:>4} {ste[1]:+.3f}% {ste[2]*100:.0f}% t{ste[3]:+.2f}")
