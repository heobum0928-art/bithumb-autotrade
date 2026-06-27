"""CASCADE 슬리피지 스트레스 테스트.

실제 손절이 -1.5% 설정인데 -2.65%로 체결되는 현상 확인.
SL을 -1.5%, -2.0%, -2.5%, -3.0%로 스윕해서 실제 엣지가 살아있는지 확인.

walk-forward TEST[0.6,1.0) / 비용 0.30% / DROP-3% / VOL-2x (현재 live 파라미터)
"""
import sys, json, glob, statistics, math, os
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"

K = 5; DROP = -3.0; VOL = 2.0; COST = 0.30; HOLD = 24; TRAIL = 1.5

# ── 데이터 로드 ──
latest = {}
for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
    base = os.path.basename(f)
    if base.startswith("BTC_"): continue
    coin = base.split("_5m_90d_")[0]
    date = base.split("_5m_90d_")[1].replace(".json","")
    if coin not in latest or date > latest[coin][1]:
        latest[coin] = (f, date)

data = {}
signals = []
for coin, (path, _) in latest.items():
    try: d = json.load(open(path, encoding="utf-8"))
    except: continue
    cl = [x["trade_price"]   for x in d]
    op = [x["opening_price"] for x in d]
    vl = [float(x.get("candle_acc_trade_price",0)) for x in d]
    data[coin] = (cl, op, vl)
    n = len(cl); i = 25
    while i < n - HOLD:
        lh = max(cl[i-K:i+1])
        drop = (cl[i]/lh - 1)*100
        avgv = statistics.mean(vl[i-20:i]) if i >= 20 else 0
        vr = vl[i]/avgv if avgv > 0 else 0
        if drop <= DROP and vr >= VOL and cl[i] > op[i]:
            signals.append((coin, i, i/n))
            i += HOLD
        else:
            i += 1

print(f"진입신호: {len(signals)}건 (DROP<={DROP}% VOL>={VOL}배 반등캔들)")
print(f"현재 live 파라미터: DROP={DROP}%, VOL={VOL}x\n")

def simulate(sl_pct):
    out = []
    for coin, i, frac in signals:
        cl, op, vl = data[coin]
        entry = cl[i]; n = len(cl); high = entry
        ret = None
        for j in range(i+1, min(i+1+HOLD, n)):
            pr = (cl[j]/entry - 1)*100
            high = max(high, cl[j])
            hp = (high/entry - 1)*100
            if pr <= sl_pct: ret = sl_pct; break
            if hp >= TRAIL and pr <= hp - TRAIL: ret = pr; break
        if ret is None: ret = (cl[min(i+HOLD,n-1)]/entry-1)*100
        out.append((ret - COST, frac))
    return out

def report(rs, sl_label):
    if not rs: return
    v = [r[0] for r in rs]
    m = statistics.mean(v)
    sd = statistics.pstdev(v) if len(v)>1 else 0
    t_all = m/(sd/math.sqrt(len(v))) if sd>0 else 0
    wr = sum(1 for x in v if x>0)/len(v)

    te = [r for r in rs if r[1] >= 0.6]
    vt = [r[0] for r in te]
    mt = statistics.mean(vt) if vt else 0
    sdt = statistics.pstdev(vt) if len(vt)>1 else 0
    tt = mt/(sdt/math.sqrt(len(vt))) if sdt>0 else 0
    wrt = sum(1 for x in vt if x>0)/len(vt) if vt else 0

    verdict = "✅ 엣지 생존" if tt >= 2.0 else ("⚠️  얇음" if tt >= 1.5 else "❌ 엣지 소멸")
    print(f"  SL {sl_label:6s} | 전체 n={len(rs):3d} avg={m:+.3f}% t={t_all:+.2f} | "
          f"TEST n={len(te):3d} avg={mt:+.3f}% 승률={wrt*100:.0f}% t={tt:+.2f}  {verdict}")

print("=== 손절선별 백테스트 (비용 0.30% 차감, walk-forward TEST 기준) ===")
print(f"  {'SL':6s} | {'전체':30s} | {'TEST':35s}")

for sl in [-1.5, -2.0, -2.5, -2.65, -3.0]:
    rs = simulate(sl)
    report(rs, f"{sl}%")

print()
print("=== 해석 ===")
print("  -1.5%: 백테스트 가정값 (이상적 체결)")
print("  -2.65%: 실제 BLUE 손절 체결값 (관측된 최대 슬리피지)")
print("  -2.0~-2.5%: 현실적 평균 슬리피지 범위 추정")
print()
print("  t >= 2.0  → 슬리피지 흡수 가능, 모의 계속 의미 있음")
print("  t 1.5~2.0 → 엣지 얇아짐, 실거래 전환 조건 강화 필요")
print("  t < 1.5   → 실질 엣지 소멸 위험, 전략 재검토")
