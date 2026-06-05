import sys; sys.path.insert(0, ".")
import sqlite3
from bithumb.db import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
r = con.execute(
    "SELECT id, exited_at, pnl_krw FROM trades WHERE coin='SUNDOG' AND pnl_krw < -100000"
).fetchone()
if r:
    print(f"찾음: id={r['id']}, exited_at={r['exited_at']}, pnl={r['pnl_krw']:,.0f}원")
    con.execute("DELETE FROM trades WHERE id=?", (r["id"],))
    con.commit()
    total = con.execute("SELECT SUM(pnl_krw) FROM trades WHERE date='2026-05-10'").fetchone()[0]
    cnt   = con.execute("SELECT COUNT(*) FROM trades WHERE date='2026-05-10'").fetchone()[0]
    print(f"삭제 완료. 수정 후 합계: {total:+,.0f}원 ({cnt}건)")
else:
    print("해당 기록 없음")
con.close()
