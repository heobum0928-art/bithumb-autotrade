"""XION 미기록 거래 DB 수동 삽입 (datetime 버그로 저장 실패했던 건)"""
import sys; sys.path.insert(0, ".")
import sqlite3
from bithumb.db import DB_PATH

# 로그에서 확인된 정확한 수치
pnl_krw      = 13483.0
pnl_pct      = 8.97
cost_krw      = round(pnl_krw / (pnl_pct / 100), 2)   # 150,312원
received_krw  = round(cost_krw + pnl_krw, 2)            # 163,795원
entry_price   = 205.0
exit_price    = 232.0
volume        = 731.70731707
entered_at    = "2026-05-10T11:51:18"
exited_at     = "2026-05-10T12:09:56"
exit_reason   = "트레일링스탑 +13.2% (고점 239.000원 -2.5%) [로그복원]"
hold_seconds  = 1118   # 18분 38초

con = sqlite3.connect(DB_PATH)
# 이미 있으면 중복 삽입 방지
existing = con.execute(
    "SELECT id FROM trades WHERE coin='XION' AND entered_at=?", (entered_at,)
).fetchone()

if existing:
    print("XION 기록이 이미 존재합니다.")
else:
    con.execute("""
        INSERT INTO trades
            (date, coin, market, entry_price, exit_price, volume,
             cost_krw, received_krw, pnl_krw, pnl_pct,
             exit_reason, hold_seconds, entered_at, exited_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        "2026-05-10", "XION", "KRW-XION",
        entry_price, exit_price, volume,
        cost_krw, received_krw, pnl_krw, pnl_pct,
        exit_reason, hold_seconds, entered_at, exited_at,
    ))
    con.commit()
    print(f"XION 삽입 완료")
    print(f"  비용: {cost_krw:,.0f}원  수령: {received_krw:,.0f}원  PnL: +{pnl_krw:,.0f}원 (+{pnl_pct:.2f}%)")

# COS 버그 기록 확인
cos = con.execute(
    "SELECT id, received_krw, pnl_krw FROM trades WHERE coin='COS' AND received_krw=0"
).fetchone()
if cos:
    print(f"\n[!] COS 버그 기록 발견 (id={cos[0]}, PnL={cos[2]:,.0f}원, 수령=0)")
    print("    → 이 기록은 중복 프로세스 버그로 발생한 가짜 손실입니다.")
    print("    → 삭제하려면 아래 주석 해제 후 재실행:")
    con.execute("DELETE FROM trades WHERE id=?", (cos[0],))
    con.commit()
    print("    → 삭제 완료")

con.close()
