"""[검증 #26] 펌핑 점화 + 비대칭 페이오프 — '빨리 자르고 멀리 태운다'.
창의적 재추론: 25개 실패는 진입이 아니라 '승자를 일찍 잘라서'일 수 있다.
급등의 돈은 두꺼운 우상단 꼬리(+30~50%)에 있는데 트레일1.5~5%가 그걸 잘랐다.
가설: 점화(5m +X%+거래량) 진입 → 손절 빡빡 → 승자는 안 자르고 멀리(넓은트레일/큰타깃) 태우면
       꼬리 몇 방이 작은 손실 다수를 메워 순익 +?
청산 구조를 여러개 비교(타이트 vs 와이드 vs 무제한). 90일 5m, walk-forward TEST. 비용0.30%(현실)."""
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


def trades(coin, ig_pct, vol_mult, sl, trail, target, to, cost):
    """점화: 1봉 +ig_pct% AND 거래량 vol_mult배. 청산: SL / 트레일(0이면 미사용) / 타깃(0이면 미사용) / 타임아웃."""
    d = load_5m(coin)
    if not d: return []
    n=len(d); a=int(n*0.6)
    cl=[x["trade_price"] for x in d]; hi=[x["high_price"] for x in d]
    lo=[x["low_price"] for x in d]; op=[x["opening_price"] for x in d]
    vol=[x.get("candle_acc_trade_volume",0) for x in d]
    out=[]; i=20
    while i < n-1:
        if i < a: i+=1; continue
        bar = cl[i]/op[i]-1 if op[i]>0 else 0
        avgv = sum(vol[i-20:i])/20
        if bar >= ig_pct and avgv>0 and vol[i] >= avgv*vol_mult:
            entry=cl[i]; peak=entry; end=min(i+1+to,n); exited=False
            for j in range(i+1,end):
                if lo[j] <= entry*(1+sl): out.append(sl-cost); i=j; exited=True; break
                peak=max(peak,hi[j])
                if target>0 and hi[j] >= entry*(1+target):
                    out.append(target-cost); i=j; exited=True; break
                if trail>0 and lo[j] <= peak*(1-trail) and peak>entry*(1+trail):
                    out.append((peak*(1-trail)-entry)/entry-cost); i=j; exited=True; break
            if not exited: out.append((cl[min(end,n)-1]-entry)/entry-cost); i=end
        else: i+=1
    return out


def rep(label, allt):
    n=len(allt)
    if not n: print(f"  {label:28} 0건"); return
    p=[x*100 for x in allt]; avg=sum(p)/n; sd=st.pstdev(p) if n>1 else 0
    t=avg/(sd/n**0.5) if sd else 0; wr=sum(1 for x in p if x>0)/n*100
    mx=max(p); print(f"  {label:28} {n:4}건 승률{wr:3.0f}% 거래당{avg:+.2f}% t{t:+.2f} 최대승{mx:+.0f}% 합{sum(p):+.0f}%")


if __name__=="__main__":
    coins=sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(CACHE/"*_5m_90d_*.json"))) - {"BTC"})
    print(f"알트 {len(coins)}개 | 점화+비대칭페이오프 5m | TEST[0.6,1.0) | 비용0.30%\n")
    for ig,vm in ((0.04,3.0),(0.06,3.0),(0.08,5.0)):
        print(f"=== 점화: 1봉 +{ig*100:.0f}% + 거래량{vm:.0f}배 ===")
        # 청산 구조 비교 (같은 진입, 다른 출구)
        rep("타이트(트레일2%)", sum((trades(c,ig,vm,-0.03,0.02,0,48,0.003) for c in coins),[]))
        rep("와이드(트레일10%)", sum((trades(c,ig,vm,-0.03,0.10,0,48,0.003) for c in coins),[]))
        rep("무제한(SL만,48봉)", sum((trades(c,ig,vm,-0.03,0,0,48,0.003) for c in coins),[]))
        rep("빡손절(-1.5%)+와이드", sum((trades(c,ig,vm,-0.015,0.10,0,48,0.003) for c in coins),[]))
        rep("타깃+30%/SL-3%", sum((trades(c,ig,vm,-0.03,0,0.30,96,0.003) for c in coins),[]))
        print()
