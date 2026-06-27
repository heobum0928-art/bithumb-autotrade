"""섹터 순환매 백테스트 — "섹터가 강세일 때 후발주(덜 오른 종목) 진입이 수익인가?"

가설(2026-06-27 에이전트 분석): 오늘 급등 9종목 중 6개가 AI/DePIN 섹터. 리더(GRASS)가
먼저 가면 후발(ELF·ARX)이 따라옴 = 섹터 순환매. 종목 예측은 불가하나 "핫섹터의 후발주"는
잡을 수 있다는 가설.

로직: 각 5분봉 시점에서
  - 섹터 모멘텀 = 같은 섹터 다른 종목들의 최근 K봉 평균 수익률
  - 핫섹터(섹터모멘텀 >= HOT%) AND 이 종목은 후발(자기 모멘텀 < 섹터평균) AND 진입봉 양봉
  - 진입 → cascade와 동일 출구(손절-2%/트레일1.5%, 슬리피지 반영)
walk-forward TEST[0.6,1.0). 비교 baseline: 무작위 진입(섹터무관).

★ 섹터 태그는 거친 수동분류 — 1차 시그널 확인용. 양수면 정교화, 음수면 가설 약화.
"""
import sys, json, glob, statistics, math, os
from pathlib import Path
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"

# ── 섹터 태그 (거친 분류, 명확한 것만) ──
SECTORS = {
    "AI_DEPIN": {"GRASS","HNT","POKT","AI","AGI","FLOCK","VIRTUAL","PEAQ","GPS","KERNEL","SENT","PRL"},
    "GAMING":   {"AXS","SAND","ALICE","BORA","AGLD","ZTX","XTER","HOOK","B3","SPACE","CARV"},
    "MEME":     {"DOGE","SHIB","PEPE","TRUMP","SPX","PENGU","BABY","CHIP","TT"},
    "L1":       {"BTC","ETH","SOL","ADA","NEAR","SEI","SUI","HBAR","INJ","KAIA","TRX","BCH","WAVES","XLM","XRP","OSMO","MOVE","XION","CSPR","ASTER"},
    "DEFI":     {"UNI","ONDO","ENA","RPL","DEXE","SWAP","MMT","BLEND","PROS","GNO"},
}
COIN_SECTOR = {}
for sec, coins in SECTORS.items():
    for cc in coins: COIN_SECTOR[cc] = sec

K = 6              # 모멘텀 측정 봉수 (30분)
HOT = 1.5          # 섹터 평균 모멘텀 이 이상 = 핫섹터 %
COST = 0.30; HOLD = 24; SL = -2.0; TRAIL = 1.5  # cascade와 동일 출구(슬리피지 SL-2%)

# ── 데이터 로드: 코인별 (타임스탬프→종가, 종가배열, 시가배열) ──
latest = {}
for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
    base = os.path.basename(f)
    coin = base.split("_5m_90d_")[0]
    if coin not in COIN_SECTOR: continue   # 태그된 코인만
    date = base.split("_5m_90d_")[1].replace(".json","")
    if coin not in latest or date > latest[coin][1]:
        latest[coin] = (f, date)

data = {}      # coin -> (ts[], cl[], op[])
ts_to_idx = {} # coin -> {ts: idx}
for coin, (path, _) in latest.items():
    try: d = json.load(open(path, encoding="utf-8"))
    except: continue
    ts = [x["timestamp"] for x in d]
    cl = [x["trade_price"] for x in d]
    op = [x["opening_price"] for x in d]
    data[coin] = (ts, cl, op)
    ts_to_idx[coin] = {t:i for i,t in enumerate(ts)}

print(f"태그 코인 {len(data)}개 로드 (섹터별: " +
      ", ".join(f"{s}:{sum(1 for c in data if COIN_SECTOR[c]==s)}" for s in SECTORS) + ")")

def mom(coin, i):
    """coin의 i시점 최근 K봉 수익률 %."""
    _, cl, _ = data[coin]
    if i < K: return None
    return (cl[i]/cl[i-K]-1)*100 if cl[i-K] > 0 else None

# ── 신호 추출 ──
signals = []  # (coin, i, frac)
for coin, (ts, cl, op) in data.items():
    sec = COIN_SECTOR[coin]
    peers = [c for c in data if COIN_SECTOR[c]==sec and c != coin]
    n = len(cl); i = K
    while i < n - HOLD:
        t = ts[i]
        # 같은 섹터 peer들의 같은 시점 모멘텀
        peer_moms = []
        for p in peers:
            pi = ts_to_idx[p].get(t)
            if pi is not None:
                m = mom(p, pi)
                if m is not None: peer_moms.append(m)
        if len(peer_moms) >= 2:
            sec_mom = statistics.mean(peer_moms)
            my_mom = mom(coin, i)
            if (sec_mom >= HOT and my_mom is not None and my_mom < sec_mom
                    and cl[i] > op[i]):              # 핫섹터 + 후발 + 양봉
                signals.append((coin, i, i/n))
                i += HOLD; continue
        i += 1

print(f"섹터 순환매 신호: {len(signals)}건 (핫섹터≥{HOT}% + 후발 + 양봉)\n")

def simulate(sigs):
    out = []
    for coin, i, frac in sigs:
        _, cl, op = data[coin]; entry = cl[i]; n = len(cl); high = entry; ret = None
        for j in range(i+1, min(i+1+HOLD, n)):
            pr = (cl[j]/entry-1)*100; high = max(high, cl[j]); hp = (high/entry-1)*100
            if pr <= SL: ret = SL; break
            if hp >= TRAIL and pr <= hp-TRAIL: ret = pr; break
        if ret is None: ret = (cl[min(i+HOLD,n-1)]/entry-1)*100
        out.append((ret-COST, frac))
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
    print(f"  {label:28s} 전체n={len(rs):3d} avg={m:+.3f}% | TEST n={len(te):3d} avg={mt:+.3f}% 승률={wrt*100:.0f}% t={tt:+.2f} {verdict}")

print("=== 결과 (SL-2% 슬리피지반영, 비용0.30%) ===")
report(simulate(signals), "섹터순환매 후발주진입")

# HOT 임계 민감도
for hot in [1.0, 2.0, 3.0]:
    sigs2 = []
    for coin,(ts,cl,op) in data.items():
        sec=COIN_SECTOR[coin]; peers=[c for c in data if COIN_SECTOR[c]==sec and c!=coin]
        n=len(cl); i=K
        while i<n-HOLD:
            t=ts[i]; pm=[]
            for p in peers:
                pi=ts_to_idx[p].get(t)
                if pi is not None:
                    mm=mom(p,pi)
                    if mm is not None: pm.append(mm)
            if len(pm)>=2:
                sm=statistics.mean(pm); my=mom(coin,i)
                if sm>=hot and my is not None and my<sm and cl[i]>op[i]:
                    sigs2.append((coin,i,i/n)); i+=HOLD; continue
            i+=1
    report(simulate(sigs2), f"HOT임계 {hot}%")
