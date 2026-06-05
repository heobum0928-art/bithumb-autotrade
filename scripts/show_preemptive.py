import sys; sys.path.insert(0, ".")
import sqlite3
from bithumb.db import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

# 선진입 식별: exit_reason에 '선진입' 포함 or 로그복원(XION), BSV/CRV 등
# 선진입 코드가 오늘 추가됐으므로 오늘 이후 거래 중 preemptive 태그된 것들
pre = con.execute("""
    SELECT coin, exited_at, pnl_krw, pnl_pct, exit_reason
    FROM trades
    WHERE exit_reason LIKE '%선진입%'
       OR exit_reason LIKE '%로그복원%'
    ORDER BY exited_at
""").fetchall()

all_today = con.execute("""
    SELECT coin, exited_at, pnl_krw, pnl_pct, exit_reason
    FROM trades WHERE date >= '2026-05-10'
    ORDER BY exited_at
""").fetchall()

con.close()

print(f"=== 선진입 명시 거래: {len(pre)}건 ===")
for r in pre:
    sign = "+" if r["pnl_krw"] >= 0 else ""
    print(f"  {str(r['exited_at'])[:16]}  {r['coin']:8s}  {sign}{r['pnl_krw']:,.0f}원 ({r['pnl_pct']:+.1f}%)")

print(f"\n=== 오늘 전체 거래: {len(all_today)}건 ===")
print("(BSV, XION, CRV, SHELL 등 선진입이나 일반이나 섞여 있음)")
print("→ entry_type을 DB에 저장하지 않아 구분 불가")
print("\n[개선 필요] exit_reason에 [선진입] 태그 추가하면 정확히 구분 가능")
