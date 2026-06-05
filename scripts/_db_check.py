import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('data/trades.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('테이블:', [t[0] for t in tables])
# pump_log 있으면 컬럼도 확인
if any(t[0] == 'pump_log' for t in tables):
    cols = conn.execute('PRAGMA table_info(pump_log)').fetchall()
    print('pump_log 컬럼:', [c[1] for c in cols])
else:
    print('pump_log 테이블 없음!')
conn.close()
