"""캐스케이드-반등 백테스트 — 90일 5분봉 전종목.
리서치 가설(#1): 대형 투매(드롭 큼)+거래량+반등캔들 → 평균회귀 롱.
비용 0.30% 차감 후 walk-forward TEST에서 양수+유의(t)면 후보."""
import sys, os, json, glob, statistics, math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "candles_cache"

# 종목별 최신 5m_90d 파일
latest = {}
for f in glob.glob(str(CACHE / "*_5m_90d_*.json")):
    base = os.path.basename(f)
    coin = base.split("_5m_90d_")[0]
    date = base.split("_5m_90d_")[1].replace(".json", "")
    if coin not in latest or date > latest[coin][1]:
        latest[coin] = (f, date)

# 파라미터 (가설)
K = 5            # 낙폭 측정 윈도우 (25분)
DROP = -3.0      # 진입 드롭 임계 (%)
VOL = 2.0        # 거래량 급증 배수
COST = 0.30      # 왕복 비용 %
FWD = 6          # forward 30분(6캔들)
TP = 2.0         # 익절 +2%
SL = -3.0        # 손절 -3%
HOLD = 12        # 최대보유 60분

def backtest(coins, drop_th, vol_th):
    trades = []  # (coin, t_index_frac, ret%, drop, vs)
    for coin, (path, _) in coins.items():
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        cl = [x["trade_price"] for x in d]
        op = [x["opening_price"] for x in d]
        vol = [float(x.get("candle_acc_trade_price", 0)) for x in d]
        n = len(cl)
        i = 25
        while i < n - HOLD:
            local_high = max(cl[i-K:i+1])
            drop = (cl[i] / local_high - 1) * 100
            avgv = statistics.mean(vol[i-20:i]) if i >= 20 else 0
            vs = vol[i] / avgv if avgv > 0 else 0
            green = cl[i] > op[i]
            if drop <= drop_th and vs >= vol_th and green:
                entry = cl[i]
                ret = None
                for j in range(i+1, min(i+1+HOLD, n)):
                    pr = (cl[j]/entry - 1) * 100
                    if pr >= TP: ret = TP; break
                    if pr <= SL: ret = SL; break
                if ret is None:
                    ret = (cl[min(i+HOLD, n-1)]/entry - 1) * 100
                trades.append((coin, i/n, ret - COST, drop, vs))
                i += HOLD  # 중복 진입 방지
            else:
                i += 1
    return trades

def stats(trades):
    if not trades: return None
    rets = [t[2] for t in trades]
    m = statistics.mean(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0
    t = m / (sd/math.sqrt(len(rets))) if sd > 0 else 0
    win = sum(1 for r in rets if r > 0) / len(rets)
    return len(rets), m, win, t

# 전체
allc = latest
tr = backtest(allc, DROP, VOL)
print(f"=== 캐스케이드-반등 (드롭<={DROP}% 거래량>={VOL}배 반등캔들, TP+{TP}/SL{SL}/보유{HOLD*5}분, 비용{COST}%) ===")
print(f"종목 {len(allc)}개, 90일 5분봉\n")
s = stats(tr)
if s:
    print(f"전체: n={s[0]} 순기대값(비용후) {s[1]:+.3f}% 승률 {s[2]*100:.0f}% t={s[3]:.2f}")

# walk-forward: train[0,0.6) test[0.6,1]
train = [t for t in tr if t[1] < 0.6]
test = [t for t in tr if t[1] >= 0.6]
for nm, sub in [("TRAIN[0,0.6)", train), ("TEST[0.6,1.0)", test)]:
    s = stats(sub)
    if s: print(f"{nm}: n={s[0]} 순기대값 {s[1]:+.3f}% 승률 {s[2]*100:.0f}% t={s[3]:.2f}")

# 드롭 크기별 그리드
print("\n=== 드롭 임계별 (거래량>=2배 고정) ===")
for dth in [-3, -4, -5, -6, -8]:
    s = stats(backtest(allc, dth, 2.0))
    if s: print(f"드롭<={dth}%: n={s[0]} 순{s[1]:+.3f}% 승{s[2]*100:.0f}% t={s[3]:.2f}")

print("\n=== 거래량 배수별 (드롭<=-4% 고정) ===")
for vth in [2, 3, 5, 8]:
    s = stats(backtest(allc, -4, vth))
    if s: print(f"거래량>={vth}배: n={s[0]} 순{s[1]:+.3f}% 승{s[2]*100:.0f}% t={s[3]:.2f}")
