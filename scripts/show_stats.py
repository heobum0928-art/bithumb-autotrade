import sys; sys.path.insert(0, ".")
import sqlite3
from bithumb.db import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

total = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
wins  = con.execute("SELECT COUNT(*) FROM trades WHERE pnl_krw > 0").fetchone()[0]
rows  = con.execute("SELECT pnl_krw FROM trades").fetchall()
pnls  = [r["pnl_krw"] for r in rows]

print(f"총 거래: {total}건  승률: {wins}/{total} = {wins/total*100:.0f}%" if total else "데이터 없음")
if pnls:
    avg_win  = sum(p for p in pnls if p > 0) / max(sum(1 for p in pnls if p > 0), 1)
    avg_loss = sum(p for p in pnls if p < 0) / max(sum(1 for p in pnls if p < 0), 1)
    print(f"평균 수익: +{avg_win:,.0f}원  평균 손실: {avg_loss:,.0f}원")
    print(f"손익비: {abs(avg_win/avg_loss):.2f} (1 이상이면 유리)")
    print(f"총 PnL: {sum(pnls):+,.0f}원")
    print(f"\n통계적으로 의미있는 비교: 최소 50건 필요 (현재 {total}건)")
    print(f"선진입 전략 비교: 최소 20건 필요 (오늘 시작)")

con.close()
