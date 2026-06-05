import sys, sqlite3
sys.path.insert(0, ".")
from bithumb.db import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT * FROM trades WHERE coin='ZION' ORDER BY exited_at DESC LIMIT 5"
).fetchall()
con.close()

if not rows:
    print("ZION 거래 기록 없음")
else:
    for r in rows:
        ep  = r['entry_price'] or 0
        xp  = r['exit_price']  or 0
        print(
            f"{str(r['exited_at'])[:16]}  "
            f"진입={ep:.3f}  청산={xp:.3f}  "
            f"PnL={r['pnl_krw']:+,.0f}원 ({r['pnl_pct']:+.1f}%)  "
            f"사유={r['exit_reason']}"
        )
