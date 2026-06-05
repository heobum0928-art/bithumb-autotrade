"""DB 수정: SUNDOG 00:44 버그 삭제 + COS 실제 손실 재추가"""
import sys; sys.path.insert(0, ".")
import sqlite3
from bithumb.db import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

# 1. SUNDOG 00:44 버그 기록 삭제
sundog_bug = con.execute(
    "SELECT id, pnl_krw FROM trades WHERE coin='SUNDOG' AND exited_at LIKE '2026-05-10T00:44%'"
).fetchone()
if sundog_bug:
    con.execute("DELETE FROM trades WHERE id=?", (sundog_bug["id"],))
    print(f"SUNDOG 00:44 삭제 (id={sundog_bug['id']}, pnl={sundog_bug['pnl_krw']:,.0f}원)")
else:
    print("SUNDOG 00:44 기록 없음 (이미 삭제됨)")

# 2. COS 실제 손실 재추가
cos_existing = con.execute(
    "SELECT id FROM trades WHERE coin='COS' AND date='2026-05-10'"
).fetchone()
if cos_existing:
    print("COS 기록 이미 존재 - 스킵")
else:
    cost_krw     = 250625.0
    received_krw = 241085.0
    pnl_krw      = received_krw - cost_krw   # -9,540원
    pnl_pct      = pnl_krw / cost_krw * 100  # -3.81%
    con.execute("""
        INSERT INTO trades
            (date, coin, market, entry_price, exit_price, volume,
             cost_krw, received_krw, pnl_krw, pnl_pct,
             exit_reason, hold_seconds, entered_at, exited_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        "2026-05-10", "COS", "KRW-COS",
        1.835, 1.774, 136239.78200000,
        cost_krw, received_krw, pnl_krw, pnl_pct,
        "트레일링스탑 (앱 정산 기준)", 447,
        "2026-05-10T00:34:51", "2026-05-10T00:42:05",
    ))
    print(f"COS 재추가: 비용={cost_krw:,.0f}원 수령={received_krw:,.0f}원 PnL={pnl_krw:+,.0f}원 ({pnl_pct:+.2f}%)")

con.commit()

# 최종 합계
rows = con.execute("SELECT pnl_krw FROM trades WHERE date='2026-05-10'").fetchall()
total = sum(r["pnl_krw"] for r in rows)
print(f"\n수정 후 오늘 합계: {total:+,.0f}원 ({len(rows)}건)")
con.close()
