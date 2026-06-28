"""교차거래소 리드-래그 백테 — "Upbit/Binance 선행 → 빗썸 따라오나?" (정보우위 검증).

가설(에이전트 분석 TOP1): 빗썸은 후행 시장. 같은 코인이 업비트/바이낸스에서 먼저 튀면
(lead = max(up_chg,bn_chg) - bh_chg 가 큼), 빗썸이 1~3분 뒤 따라온다. OHLCV 추격(t-19)과
정반대 — 빗썸 차트엔 아직 안 나온 움직임을 타거래소에서 미리 보는 정보 우위.

데이터: crossex_events.csv(6일, 125코인) 신호시점 → candles_cache 5분봉 forward 수익률.
lead 버킷별(0.5/1.0/1.5/2.0) T+5/15/30분 평균·승률·t값. 슬리피지 0.5% 차감.
양수 단조증가(lead 클수록 forward↑)면 진짜 엣지.
"""
import sys, csv, json, glob, os, statistics, math
from datetime import datetime
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"
import os as _os
SLIP = float(_os.environ.get("SLIP", "0.5"))   # 왕복 슬리피지+수수료 차감 % (메이커쿠폰=0.04)

# ── 캔들 캐시 로드: coin -> (kst키→idx, 종가배열) ──
latest = {}
for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
    base = os.path.basename(f); coin = base.split("_5m_90d_")[0]
    date = base.split("_5m_90d_")[1].replace(".json","")
    if coin not in latest or date > latest[coin][1]:
        latest[coin] = (f, date)

candles = {}
for coin,(path,_) in latest.items():
    try: d = json.load(open(path, encoding="utf-8"))
    except: continue
    kst_idx = {}; cl = []
    for i,x in enumerate(d):
        kst_idx[x["candle_date_time_kst"]] = i
        cl.append(x["trade_price"])
    candles[coin] = (kst_idx, cl)

def floor5_key(timestr):
    dt = datetime.strptime(timestr, "%Y-%m-%d %H:%M:%S")
    fm = (dt.minute // 5) * 5
    return dt.strftime("%Y-%m-%dT%H:") + f"{fm:02d}:00"

# ── crossex 신호 → forward 수익률 ──
rows = list(csv.DictReader(open(ROOT/"data"/"crossex_events.csv", encoding="utf-8")))
recs = []   # (lead, ret5, ret15, ret30)
matched = 0
for r in rows:
    coin = r["coin"]
    if coin not in candles: continue
    try:
        lead = float(r["lead"]) if r["lead"] else None
        bh = float(r["bh_price"]) if r["bh_price"] else None
    except Exception: continue
    if lead is None or bh is None or bh <= 0: continue
    kst_idx, cl = candles[coin]
    key = floor5_key(r["time"])
    i = kst_idx.get(key)
    if i is None: continue
    matched += 1
    def fwd(k):
        j = i + k
        if j < len(cl): return (cl[j]/bh - 1)*100 - SLIP
        return None
    recs.append((lead, fwd(1), fwd(3), fwd(6)))   # T+5/15/30분

print(f"crossex 신호 {len(rows)}행 중 캔들매칭 {matched}건\n")

def stats(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 5: return None
    m = statistics.mean(v); sd = statistics.pstdev(v) if len(v)>1 else 0
    t = m/(sd/math.sqrt(len(v))) if sd>0 else 0
    wr = sum(1 for x in v if x>0)/len(v)*100
    return len(v), m, wr, t

print("=== lead 버킷별 빗썸 forward 수익률 (슬리피지 0.5% 차감) ===")
print(f"{'lead구간':14s} {'T+5분':>22s} {'T+15분':>22s} {'T+30분':>22s}")
buckets = [(0.0,0.5),(0.5,1.0),(1.0,1.5),(1.5,2.0),(2.0,3.0),(3.0,99)]
for lo,hi in buckets:
    sub = [r for r in recs if lo <= r[0] < hi]
    label = f"{lo}~{hi if hi<99 else '∞'}"
    cells = []
    for k in (1,2,3):  # ret5,ret15,ret30 인덱스
        s = stats([r[k] for r in sub])
        if s: cells.append(f"n{s[0]:4d} {s[1]:+.2f}% 승{s[2]:.0f}% t{s[3]:+.1f}")
        else: cells.append("표본부족")
    print(f"{label:14s} {cells[0]:>22s} {cells[1]:>22s} {cells[2]:>22s}")

print("\n=== 해석 ===")
print("  lead 클수록 forward 수익률 단조증가 + 양수 + t유의 → 정보우위 진짜")
print("  전 구간 0근처/음수 → 빗썸 차트추격과 다를바 없음(래그≠캐치업)")
