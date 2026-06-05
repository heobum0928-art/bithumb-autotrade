import sqlite3, sys
from datetime import date
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/trades.db')
rows = conn.execute(
    "SELECT coin, entry_type, skip_reason, rsi, bb_pct, vol_mult FROM signal_log WHERE date(entered_at)=? ORDER BY entered_at",
    (date.today().isoformat(),)
).fetchall()
conn.close()

skip = {}
entered = []
for coin, etype, reason, rsi, bb, vol in rows:
    if etype in ('regular', 'newlisting', 'preemptive'):
        entered.append(coin)
    else:
        k = reason or 'unknown'
        skip[k] = skip.get(k, 0) + 1

print(f"오늘 신호 총 {len(rows)}건")
print(f"진입: {len(entered)}건 {entered}")
print("차단 사유:")
for k, v in sorted(skip.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}건")
