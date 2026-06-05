import sys; sys.path.insert(0, ".")
import sqlite3
from bithumb.db import DB_PATH
from collections import defaultdict

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT coin, exited_at, cost_krw, received_krw, pnl_krw, pnl_pct, exit_reason, hold_seconds
    FROM trades WHERE date >= '2026-05-10' ORDER BY exited_at
""").fetchall()
con.close()

print("=== 손해 패턴 분석 ===\n")

# 1. 진입 유형별
pre   = [r for r in rows if r["exit_reason"] and "선진입" in r["exit_reason"]]
reg   = [r for r in rows if not r["exit_reason"] or "선진입" not in r["exit_reason"]]
pre_t = [r for r in pre if r["exit_reason"] and "타임아웃" in r["exit_reason"]]

def stats(group, label):
    if not group: return
    wins  = [r for r in group if r["pnl_krw"] > 0]
    total = sum(r["pnl_krw"] for r in group)
    avg_h = sum(r["hold_seconds"] or 0 for r in group) / len(group) / 60
    print(f"[{label}] {len(group)}건 | 승률 {len(wins)}/{len(group)} = {len(wins)/len(group)*100:.0f}% | 합계 {total:+,.0f}원 | 평균보유 {avg_h:.0f}분")

stats(pre,   "선진입 전체")
stats(pre_t, "선진입 타임아웃")
stats([r for r in pre if r["exit_reason"] and "트레일링" in r["exit_reason"]], "선진입 트레일성공")
stats(reg,   "일반 진입")
print()

# 2. 손실 크기 분포
print("=== 손실 크기 분포 ===")
losses = sorted([r["pnl_pct"] for r in rows if r["pnl_krw"] < 0])
buckets = defaultdict(int)
for p in losses:
    if p > -1:   buckets["0~-1%"] += 1
    elif p > -2: buckets["-1~-2%"] += 1
    elif p > -3: buckets["-2~-3%"] += 1
    elif p > -5: buckets["-3~-5%"] += 1
    else:        buckets["-5% 이상"] += 1
for k, v in buckets.items():
    print(f"  {k}: {v}건")

# 3. 수익 크기 분포
print("\n=== 수익 크기 분포 ===")
wins = sorted([r["pnl_pct"] for r in rows if r["pnl_krw"] > 0])
for p in wins:
    print(f"  +{p:.1f}%", end="  ")
print()

# 4. 보유 시간 vs 손익
print("\n=== 보유시간 vs 결과 ===")
short = [r for r in rows if (r["hold_seconds"] or 0) < 600]
long_ = [r for r in rows if (r["hold_seconds"] or 0) >= 600]
stats(short, "10분 미만")
stats(long_, "10분 이상")

print("\n=== 전체 요약 ===")
stats(rows, "전체")
