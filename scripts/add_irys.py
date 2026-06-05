import sqlite3
from bithumb.db import DB_PATH

# IRYS 매도 정산 기준 수동 입력
# 거래금액 292,149 / 수량 5567.928 = exit_price 52.47
cost_krw     = 300000.0
received_krw = 291419.0
entry_price  = 53.88
exit_price   = round(292149 / 5567.92873051225, 4)
volume       = 5567.92873051225
pnl_krw      = received_krw - cost_krw
pnl_pct      = pnl_krw / cost_krw * 100

con = sqlite3.connect(DB_PATH)
con.execute("""
    INSERT INTO trades
        (date, coin, market, entry_price, exit_price, volume,
         cost_krw, received_krw, pnl_krw, pnl_pct,
         exit_reason, hold_seconds, entered_at, exited_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", (
    "2026-05-10", "IRYS", "KRW-IRYS",
    entry_price, exit_price, volume,
    cost_krw, received_krw, pnl_krw, pnl_pct,
    "트레일링스탑 (앱 정산 기준)",
    None,
    "2026-05-10T01:10:31",
    "2026-05-10T04:00:00",  # 정확한 시간 미상
))
con.commit()
con.close()

print(f"IRYS 기록 완료")
print(f"  진입: {entry_price:.3f}원  청산: {exit_price:.3f}원")
print(f"  원금: {cost_krw:,.0f}원  수령: {received_krw:,.0f}원")
print(f"  손익: {pnl_krw:+,.0f}원 ({pnl_pct:+.2f}%)")
