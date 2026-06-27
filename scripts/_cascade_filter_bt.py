"""캐스케이드 하이브리드 필터 백테스트 — 두 필터 효과 검증.

필터 A: BTC 15분 레짐 (BTC 15m 변화율 <= 임계값이면 진입 스킵)
필터 B: 캔들 강도 (반등봉 몸통 >= range의 X%, 위꼬리 <= range의 Y%)
비교: 베이스라인 vs A vs B vs A+B

walk-forward TEST[0.6,1.0) 기준. 비용 0.30% 차감.
"""
import sys, json, glob, statistics, math
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"

# ── 파라미터 ──
K = 5; DROP = -3.0; VOL = 2.0; COST = 0.30; HOLD = 24  # 2h = 24봉×5min
SL = -1.5; TRAIL = 1.5

# ── BTC 5m 캔들 로드 (최신) ──
btc_files = sorted(glob.glob(str(CACHE / "BTC_5m_90d_*.json")))
btc_raw = json.load(open(btc_files[-1], encoding="utf-8"))
# timestamp → BTC close 맵핑 (5분봉 경계 정렬)
btc_map = {}
for x in btc_raw:
    btc_map[x["timestamp"]] = {
        "o": x["opening_price"], "h": x["high_price"],
        "l": x["low_price"],    "c": x["trade_price"]
    }
btc_ts_sorted = sorted(btc_map.keys())

def btc_at(ts, lookback_bars=3):
    """ts(ms) 시점에서 직전 lookback_bars개 5분봉 변화율(%)."""
    # ts보다 작거나 같은 가장 가까운 BTC 타임스탬프 찾기
    idx = None
    for i, t in enumerate(btc_ts_sorted):
        if t <= ts: idx = i
        else: break
    if idx is None or idx < lookback_bars: return 0.0
    prev = btc_map[btc_ts_sorted[idx - lookback_bars]]["c"]
    cur  = btc_map[btc_ts_sorted[idx]]["c"]
    return (cur / prev - 1) * 100 if prev > 0 else 0.0

# ── 알트 5m 캔들 로드 ──
import os
latest = {}
for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
    base = os.path.basename(f)
    if base.startswith("BTC_"): continue
    coin = base.split("_5m_90d_")[0]
    date = base.split("_5m_90d_")[1].replace(".json","")
    if coin not in latest or date > latest[coin][1]:
        latest[coin] = (f, date)

print(f"알트코인 {len(latest)}개 로드 중...")

# ── 진입 신호 추출 ──
signals = []  # (coin, idx, frac, ts, hi, lo, op, cl)
data = {}
for coin, (path, _) in latest.items():
    try: d = json.load(open(path, encoding="utf-8"))
    except: continue
    cl = [x["trade_price"]   for x in d]
    op = [x["opening_price"] for x in d]
    hi = [x.get("high_price", x["trade_price"]) for x in d]
    lo = [x.get("low_price",  x["trade_price"]) for x in d]
    ts = [x["timestamp"]     for x in d]
    vol= [float(x.get("candle_acc_trade_price",0)) for x in d]
    data[coin] = (cl, op, hi, lo, ts, vol)
    n = len(cl); i = 25
    while i < n - HOLD:
        lh = max(cl[i-K:i+1])
        drop = (cl[i]/lh - 1)*100
        avgv = statistics.mean(vol[i-20:i]) if i >= 20 else 0
        vr = vol[i]/avgv if avgv > 0 else 0
        if drop <= DROP and vr >= VOL and cl[i] > op[i]:
            signals.append((coin, i, i/n, ts[i], hi[i], lo[i], op[i], cl[i]))
            i += HOLD
        else:
            i += 1

print(f"베이스라인 진입신호: {len(signals)}건\n")

# ── 시뮬레이션 ──
def simulate(sigs):
    out = []
    for coin, i, frac, _, _, _, _, _ in sigs:
        cl, op, hi, lo, ts, vol = data[coin]
        entry = cl[i]; n = len(cl); high = entry
        ret = None
        for j in range(i+1, min(i+1+HOLD, n)):
            pr = (cl[j]/entry - 1)*100
            high = max(high, cl[j])
            hp = (high/entry - 1)*100
            if pr <= SL: ret = SL; break
            if hp >= TRAIL and pr <= hp - TRAIL: ret = pr; break
        if ret is None: ret = (cl[min(i+HOLD,n-1)]/entry - 1)*100
        out.append((ret - COST, frac))
    return out

def stats(rs, label):
    if not rs:
        print(f"  {label:20s} 신호없음"); return
    v = [r[0] for r in rs]
    m = statistics.mean(v)
    sd = statistics.pstdev(v) if len(v) > 1 else 0
    t = m / (sd/math.sqrt(len(v))) if sd > 0 else 0
    wr = sum(1 for x in v if x > 0)/len(v)
    te = [r for r in rs if r[1] >= 0.6]
    if te:
        vt = [r[0] for r in te]; mt = statistics.mean(vt)
        sdt = statistics.pstdev(vt) if len(vt)>1 else 0
        tt = mt/(sdt/math.sqrt(len(vt))) if sdt>0 else 0
        wrt = sum(1 for x in vt if x>0)/len(vt)
        print(f"  {label:25s} 전체n={len(rs):3d} avg={m:+.3f}% | TEST n={len(te):3d} avg={mt:+.3f}% 승률={wrt*100:.0f}% t={tt:+.2f}")
    else:
        print(f"  {label:25s} n={len(rs):3d} avg={m:+.3f}% t={t:+.2f} (TEST없음)")

# ── 필터 A: BTC 레짐 ──
# BTC 직전 15분(3봉) 변화율이 임계 이하면 스킵
BTC_THRESH_LIST = [-0.3, -0.5, -1.0, -1.5]

print("=== 필터 A: BTC 레짐 필터 (BTC 15m 변화율 임계) ===")
base_rs = simulate(signals)
stats(base_rs, "베이스라인 (필터없음)")
for thresh in BTC_THRESH_LIST:
    filtered = [(c,i,f,ts,h,l,o,cl_) for (c,i,f,ts,h,l,o,cl_) in signals
                if btc_at(ts) > thresh]
    rs = simulate(filtered)
    stats(rs, f"BTC 15m > {thresh}%")

# ── 필터 B: 캔들 강도 ──
print("\n=== 필터 B: 캔들 강도 필터 (몸통비율 임계) ===")
BODY_LIST = [0.3, 0.4, 0.5, 0.6]
for body_min in BODY_LIST:
    filtered = []
    for sig in signals:
        coin, i, frac, ts, h, l, o, cl_ = sig
        rng = h - l
        if rng <= 0: continue
        body = (cl_ - o) / rng  # 몸통 비율
        wick = (h - cl_) / rng   # 위꼬리 비율
        if body >= body_min and wick <= 0.3:
            filtered.append(sig)
    rs = simulate(filtered)
    stats(rs, f"몸통>={body_min*100:.0f}% 위꼬리<=30%")

# ── 필터 A+B 조합 ──
print("\n=== 필터 A+B 조합 ===")
for btc_t in [-0.5, -1.0]:
    for body_m in [0.4, 0.5]:
        filtered = []
        for sig in signals:
            coin, i, frac, ts, h, l, o, cl_ = sig
            if btc_at(ts) <= btc_t: continue
            rng = h - l
            if rng <= 0: continue
            body = (cl_ - o) / rng
            wick = (h - cl_) / rng
            if body >= body_m and wick <= 0.3:
                filtered.append(sig)
        rs = simulate(filtered)
        stats(rs, f"BTC>{btc_t}% + 몸통>={body_m*100:.0f}%")
