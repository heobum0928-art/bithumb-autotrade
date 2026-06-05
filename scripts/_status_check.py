import sqlite3, time, json, sys
from pathlib import Path
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('data/trades.db')
rows = conn.execute(
    "SELECT coin, pnl_krw, exit_reason FROM trades WHERE date=? ORDER BY entered_at",
    (date.today().isoformat(),)
).fetchall()
stats = conn.execute(
    "SELECT COUNT(*), SUM(pnl_krw), SUM(CASE WHEN pnl_krw>0 THEN 1 ELSE 0 END) FROM trades"
).fetchone()
conn.close()

pos_f = Path("data/active_pos.json")
print("포지션:", "없음" if not pos_f.exists() else "있음")
print()
print(f"오늘 거래: {len(rows)}건")
for coin, pnl, reason in rows:
    pnl = pnl or 0
    print(f"  {'O' if pnl > 0 else 'X'} {coin}: {pnl:+,.0f}원")
print()
cnt, total_pnl, wins = stats
total_pnl = total_pnl or 0
wins = wins or 0
print(f"누적: {cnt}건 | 승률 {wins}/{cnt} ({wins/cnt*100:.0f}%) | PnL {total_pnl:+,.0f}원")

lcd = Path("data/loss_coins.json")
if lcd.exists():
    loss = json.loads(lcd.read_text())
    now = time.time()
    active = {c: v for c, v in loss.items() if v.get("until", 0) > now}
    if active:
        print("쿨다운:")
        for c, v in active.items():
            h = (v["until"] - now) / 3600
            print(f"  {c}: {h:.0f}h 남음")
    else:
        print("쿨다운: 없음")
