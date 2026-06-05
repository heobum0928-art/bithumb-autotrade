import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/trades.db')

# 시간대별 신호 분포 (전체)
rows = conn.execute('''
    SELECT hour_kst, entry_type, skip_reason
    FROM signal_log ORDER BY entered_at
''').fetchall()

# 실제 진입된 거래 시간대
trades = conn.execute('''
    SELECT strftime('%H', entered_at) as h, coin, pnl_krw
    FROM trades ORDER BY entered_at
''').fetchall()
conn.close()

# 시간대별 진입 vs 차단
hour_enter = {}
hour_block = {}
for hour, etype, reason in rows:
    if hour is None:
        continue
    if etype in ('regular', 'newlisting', 'preemptive'):
        hour_enter[hour] = hour_enter.get(hour, 0) + 1
    else:
        hour_block[hour] = hour_block.get(hour, 0) + 1

print("시간대별 신호 현황 (전체 기간):")
print(f"{'시':<4} {'진입':>4} {'차단':>4}  거래 내역")
for h in range(24):
    e = hour_enter.get(h, 0)
    b = hour_block.get(h, 0)
    t_list = [(coin, pnl) for th, coin, pnl in trades if int(th) == h]
    t_str = ', '.join(f"{c}({'+'if (p or 0)>0 else ''}{(p or 0):,.0f})" for c, p in t_list)
    if e + b > 0 or t_list:
        print(f"{h:02d}시  {e:>4}  {b:>4}  {t_str}")
