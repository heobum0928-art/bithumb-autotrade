"""[검증 #22] 실패한 붕괴(Failed Breakdown / 베어트랩) — 약세장 특화 롱.
평균회귀(#10/#16)는 확인없이 과매도 매수→떨어지는칼. 이건 다름:
직전 N일 신저가를 장중 깼다가(low<support) 종가가 그 위로 회복(close>support)할 때만 매수.
= '손절 사냥 후 반등' 확인 진입(칼 안 받음). 선택적 하락추세 필터(close<SMA20)로 '베어트랩'만.
일봉, walk-forward TEST[0.6,1.0), 비용0.16/0.30%, 장세분리. 청산=트레일/타임아웃."""
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
    f = DAILY / "BTC_1d.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    closes = [x["trade_price"] for x in d]; reg = {}
    for i in range(len(d)):
        if i < 200: continue
        sma = sum(closes[i-200:i]) / 200
        reg[d[i]["candle_date_time_kst"][:10]] = "BULL" if closes[i] > sma else "BEAR"
    return reg


def failbreak_trades(candles, N, trail, sl, timeout, downtrend, cost, lo, hi):
    n = len(candles); a, b = int(n*lo), int(n*hi)
    cl = [c["trade_price"] for c in candles]
    hi_ = [c["high_price"] for c in candles]
    lo_ = [c["low_price"] for c in candles]
    out = []; i = max(N, 20)
    while i < n - 1:
        if not (a <= i < b):
            i += 1; continue
        support = min(lo_[i-N:i])
        sma20 = sum(cl[i-20:i]) / 20
        cond_trap = (lo_[i] < support) and (cl[i] > support)      # 깼다가 회복
        cond_trend = (cl[i] < sma20) if downtrend else True       # 하락추세 한정(베어트랩)
        if cond_trap and cond_trend:
            entry = cl[i]; peak = entry; edate = candles[i]["candle_date_time_kst"][:10]
            end = min(i+1+timeout, n); exited = False
            for j in range(i+1, end):
                if lo_[j] <= entry*(1+sl):
                    out.append((( entry*(1+sl)-entry)/entry - cost, edate)); i = j; exited = True; break
                peak = max(peak, hi_[j])
                if lo_[j] <= peak*(1-trail) and peak > entry*1.01:
                    out.append(((peak*(1-trail)-entry)/entry - cost, edate)); i = j; exited = True; break
            if not exited:
                out.append(((cl[min(end,n)-1]-entry)/entry - cost, edate)); i = end
        else:
            i += 1
    return out


def stat(label, rows, reg=None, rgm=None):
    if reg is not None:
        rows = [r for r in rows if reg.get(r[1]) == rgm]
    n = len(rows)
    if not n:
        print(f"  {label:24} 0건"); return
    p = [r[0]*100 for r in rows]
    avg = sum(p)/n; sd = st.pstdev(p) if n>1 else 0
    t = avg/(sd/n**0.5) if sd else 0
    wr = sum(1 for x in p if x>0)/n*100
    print(f"  {label:24} {n:3}건 승률{wr:3.0f}% 거래당{avg:+.2f}% t{t:+.2f} 표본합{sum(p):+.0f}%")


if __name__ == "__main__":
    cc = load(); reg = btc_regime()
    print(f"보유 {len(cc)}코인 | 실패한붕괴(베어트랩) | TEST[0.6,1.0)\n")
    print("=== 파라미터 스캔 (비용0.16%, 하락추세필터 ON) ===")
    best = None
    for N in (10, 20, 30):
        for trail, sl, to in ((0.05,-0.07,15), (0.08,-0.10,20)):
            allt = []
            for coin, d in cc.items():
                allt += failbreak_trades(d, N, trail, sl, to, True, 0.0016, 0.6, 1.0)
            lab = f"N{N} trail{int(trail*100)}/sl{int(sl*100)}/{to}d"
            stat(lab, allt)
            # bear t 추적
            br = [r for r in allt if reg.get(r[1])=="BEAR"]
            if br:
                p=[r[0]*100 for r in br]; t=(sum(p)/len(p))/(st.pstdev(p)/len(p)**0.5) if len(p)>1 and st.pstdev(p) else 0
                if best is None or t > best[1]:
                    best = (lab, t, (N,trail,sl,to))
    print(f"\n=== 최고 약세장 t 조합으로 장세분리: {best[0]} ===")
    N,trail,sl,to = best[2]
    allt = []
    for coin, d in cc.items():
        allt += failbreak_trades(d, N, trail, sl, to, True, 0.0016, 0.6, 1.0)
    for rgm in ("BEAR","BULL"):
        stat(f"{rgm} 진입", allt, reg, rgm)
    print("\n=== 같은 조합, 하락추세필터 OFF (전체) & 비용0.30% ===")
    allt2 = []
    for coin, d in cc.items():
        allt2 += failbreak_trades(d, N, trail, sl, to, False, 0.0030, 0.6, 1.0)
    stat("필터OFF 0.30%", allt2)
