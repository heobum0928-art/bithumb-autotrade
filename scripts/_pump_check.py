import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('data/trades.db')
total = conn.execute('SELECT COUNT(*) FROM pump_log').fetchone()[0]
rows = conn.execute('SELECT coin, detected_at, pump_pct, vol_mult, price_1m, price_3m, price_5m, pullback_2pct FROM pump_log ORDER BY detected_at DESC LIMIT 5').fetchall()
print(f'pump_log 총: {total}건')
for r in rows:
    print(r)
conn.close()
