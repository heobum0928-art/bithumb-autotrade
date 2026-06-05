import sys
sys.path.insert(0, ".")
import sqlite3
from bithumb.db import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT coin, entered_at, exited_at, cost_krw, received_krw, pnl_krw, pnl_pct
    FROM trades
    WHERE exited_at >= '2026-05-09 22:00:00'
    ORDER BY exited_at ASC
""").fetchall()

print(f"{'시간':16s}  {'코인':8s}  {'비용':>10s}  {'수령':>10s}  {'손익':>14s}")
print("-" * 70)

total = 0
real_total = 0
for r in rows:
    pnl  = r["pnl_krw"]
    recv = r["received_krw"] or 0
    total += pnl
    sign = "+" if pnl >= 0 else ""
    bug  = " ← 버그" if recv == 0 and pnl < -50000 else ""
    print(f"{r['exited_at'][:16]}  {r['coin']:8s}  {r['cost_krw']:>10,.0f}  {recv:>10,.0f}  {sign}{pnl:>10,.0f}원 ({r['pnl_pct']:+.1f}%){bug}")
    if not bug:
        real_total += pnl

print("-" * 70)
print(f"DB 합계:   {total:+,.0f}원  ({len(rows)}건)")
print(f"실제 추정: {real_total:+,.0f}원  (버그 건 제외)")
con.close()
