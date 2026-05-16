import sqlite3, sys
from datetime import date
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/trades.db')
total = conn.execute('SELECT COUNT(*) FROM signal_log').fetchone()[0]
with_5m = conn.execute('SELECT COUNT(*) FROM signal_log WHERE outcome_5m IS NOT NULL').fetchone()[0]
with_30m = conn.execute('SELECT COUNT(*) FROM signal_log WHERE outcome_30m IS NOT NULL').fetchone()[0]
today = conn.execute("SELECT COUNT(*) FROM signal_log WHERE date(entered_at)=?", (date.today().isoformat(),)).fetchone()[0]
trades = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
conn.close()

print(f"신호 기록 총 {total}건")
print(f"  outcome_5m  수집: {with_5m}건")
print(f"  outcome_30m 수집: {with_30m}건")
print(f"  오늘 신호:        {today}건")
print(f"거래 기록 총 {trades}건")
