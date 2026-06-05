import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('data/trades.db')
after = conn.execute("SELECT COUNT(*) FROM signal_log WHERE entered_at > '2026-05-16T14:47'").fetchone()[0]
pump = conn.execute("SELECT COUNT(*) FROM pump_log WHERE detected_at > '2026-05-16T14:47'").fetchone()[0]
print(f'재시작(14:47) 이후 signal_log: {after}건')
print(f'재시작(14:47) 이후 pump_log: {pump}건')
conn.close()
