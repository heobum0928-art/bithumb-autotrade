"""[검증 #28] 점화 + 분할청산(scale-out) — '꼭대기 못 맞혀도 오르는 길에 나눠 먹기'.
제약: 사람이 매번 못 봄 → 청산도 자동. 단 실패한 고정트레일/타깃 말고 분할청산.
실측: 점화후 +16~21% 고점 찍으나 빨리 round-trip → 오를때마다 조금씩 던지면 되돌림 회피?
여러 분할 스킴 비교. 90일 5m, TEST[0.6,1.0), 비용0.30%(왕복, 각 분할마다 차감)."""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"
COST = 0.0030  # 왕복, 분할분마다 비례 차감


def load_5m(coin):
    fs = sorted(glob.glob(str(CACHE / f"{coin}_5m_90d_*.json")))
    if not fs: return None
    d = json.loads(Path(fs[-1]).read_text(encoding="utf-8"))
    return d if len(d) >= 2000 else None


def scaleout_trade(cl, hi, lo, ei, n, ladder, sl, trail, to):
    """ladder=[(목표상승, 비중)...], 나머지비중은 트레일/타임아웃/SL. 진입 ei, 반환=가중수익."""
    entry=cl[ei]; peak=entry; end=min(ei+1+to, n)
    remaining=1.0; realized=0.0
    targets=[(entry*(1+g), w) for g,w in ladder]
    ti=0
    for j in range(ei+1, end):
        # SL (남은 전량)
        if lo[j] <= entry*(1+sl):
            realized += remaining*((entry*(1+sl)-entry)/entry - COST); remaining=0.0; return realized
        peak=max(peak, hi[j])
        # 사다리 목표 도달 분할 익절
        while ti < len(targets) and hi[j] >= targets[ti][0]:
            g=ladder[ti][0]; w=ladder[ti][1]
            take=min(w, remaining)
            realized += take*(g - COST); remaining-=take; ti+=1
        if remaining<=1e-9: return realized
        # 남은 분 트레일 (마지막 사다리 이후 활성)
        if trail>0 and ti>=1 and lo[j] <= peak*(1-trail) and peak>entry:
            realized += remaining*((peak*(1-trail)-entry)/entry - COST); remaining=0.0; return realized
    # 타임아웃: 남은 전량 종가청산
    realized += remaining*((cl[min(end,n)-1]-entry)/entry - COST)
    return realized


def run(coin, ig_pct, vm, ladder, sl, trail, to):
    d=load_5m(coin)
    if not d: return []
    n=len(d); a=int(n*0.6)
    cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
    lo=[x["low_price"] for x in d]; op=[x["opening_price"] for x in d]
    vol=[x.get("candle_acc_trade_volume",0) for x in d]
    out=[]; i=20
    while i<n-1:
        if i<a: i+=1; continue
        bar=cl[i]/op[i]-1 if op[i]>0 else 0; avgv=sum(vol[i-20:i])/20
        if bar>=ig_pct and avgv>0 and vol[i]>=avgv*vm:
            out.append(scaleout_trade(cl,hi,lo,i,n,ladder,sl,trail,to)); i+=12
        else: i+=1
    return out


def rep(label, allt):
    n=len(allt)
    if not n: print(f"  {label:34} 0건"); return
    p=[x*100 for x in allt]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    print(f"  {label:34} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.3f}% t{t:+.2f} 합{sum(p):+.0f}%")


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    print(f"알트 {len(coins)}개 | 점화+분할청산 | TEST[0.6,1.0) | 비용0.30%/분할\n")
    for ig,vm in ((0.05,4.0),(0.08,5.0)):
        print(f"=== 점화 +{ig*100:.0f}% 거래량{vm:.0f}배 ===")
        # 기준: 분할없이 트레일2% (=#26 본전)
        rep("기준(트레일2%, 분할X)", sum((run(c,ig,vm,[],-0.03,0.02,48) for c in coins),[]))
        # 분할 스킴들
        rep("절반+6% / 나머지트레일3%", sum((run(c,ig,vm,[(0.06,0.5)],-0.03,0.03,48) for c in coins),[]))
        rep("1/3+5% 1/3+12% 트레일5%", sum((run(c,ig,vm,[(0.05,0.34),(0.12,0.33)],-0.03,0.05,48) for c in coins),[]))
        rep("절반+5% / 나머지+20%or트레일4%", sum((run(c,ig,vm,[(0.05,0.5),(0.20,0.5)],-0.03,0.04,48) for c in coins),[]))
        rep("1/3+4% 1/3+10% 1/3+25%", sum((run(c,ig,vm,[(0.04,0.34),(0.10,0.33),(0.25,0.33)],-0.03,0,96) for c in coins),[]))
        print()
