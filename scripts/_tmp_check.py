import sqlite3

con = sqlite3.connect('data/trades.db')
cur = con.cursor()

# 승 vs 패: 진입 시점 지표 비교
print("=== 진입 시점 지표: 승 vs 패 ===")
cur.execute("""
    SELECT
        CASE WHEN t.pnl_krw > 0 THEN '승리' ELSE '손실' END as result,
        COUNT(*) as cnt,
        ROUND(AVG(s.price_chg_pct),1) as avg_price_chg,
        ROUND(AVG(s.vol_mult),1) as avg_vol,
        ROUND(AVG(s.rsi),1) as avg_rsi
    FROM trades t
    LEFT JOIN signal_log s ON t.coin=s.coin
        AND ABS(julianday(t.entered_at) - julianday(s.entered_at))*86400 < 90
        AND s.entry_type='regular'
    GROUP BY result
""")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}건 | 진입시 가격변화 +{r[2]}% | 거래량 {r[3]}x | RSI {r[4]}")

# price_chg 구간별 실거래 승률
print()
print("=== 진입 시점 price_chg 구간별 ===")
cur.execute("""
    SELECT
        CASE
            WHEN s.price_chg_pct < 5 THEN 'A +3~5%'
            WHEN s.price_chg_pct < 7 THEN 'B +5~7%'
            WHEN s.price_chg_pct < 10 THEN 'C +7~10%'
            ELSE 'D +10%이상'
        END as bucket,
        COUNT(*) as cnt,
        SUM(CASE WHEN t.pnl_krw>0 THEN 1 ELSE 0 END) as wins,
        ROUND(SUM(t.pnl_krw),0) as pnl
    FROM trades t
    JOIN signal_log s ON t.coin=s.coin
        AND ABS(julianday(t.entered_at) - julianday(s.entered_at))*86400 < 90
        AND s.entry_type='regular'
    WHERE s.price_chg_pct IS NOT NULL
    GROUP BY bucket ORDER BY bucket
""")
for r in cur.fetchall():
    wr = r[2]/r[1]*100 if r[1] else 0
    print(f"  {r[0]}: {r[1]}건 승률{wr:.0f}% | {r[3]:+,.0f}원")

# max_pnl_pct 분석: 진입 후 최고점이 어느 수준이었나
print()
print("=== 진입 후 최고 수익률 분포 (손실 거래) ===")
cur.execute("""
    SELECT
        CASE
            WHEN max_pnl_pct IS NULL THEN '기록없음'
            WHEN max_pnl_pct < 0 THEN '한번도 안오름'
            WHEN max_pnl_pct < 1 THEN '0~1% 상승후 손절'
            WHEN max_pnl_pct < 2 THEN '1~2% 상승후 손절'
            WHEN max_pnl_pct < 3 THEN '2~3% 상승후 손절'
            ELSE '3%이상 상승후 손절'
        END as bucket,
        COUNT(*) as cnt,
        ROUND(SUM(pnl_krw),0) as pnl
    FROM trades WHERE pnl_krw < 0
    GROUP BY bucket ORDER BY MIN(COALESCE(max_pnl_pct,-999))
""")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}건 | {r[2]:+,.0f}원")

# hold_seconds vs 결과
print()
print("=== 보유 시간 구간별 승률 ===")
cur.execute("""
    SELECT
        CASE
            WHEN hold_seconds < 60 THEN '1분미만'
            WHEN hold_seconds < 180 THEN '1~3분'
            WHEN hold_seconds < 300 THEN '3~5분'
            WHEN hold_seconds < 600 THEN '5~10분'
            ELSE '10분이상'
        END as bucket,
        COUNT(*) as cnt,
        SUM(CASE WHEN pnl_krw>0 THEN 1 ELSE 0 END) as wins,
        ROUND(SUM(pnl_krw),0) as pnl
    FROM trades WHERE hold_seconds IS NOT NULL
    GROUP BY bucket ORDER BY MIN(hold_seconds)
""")
for r in cur.fetchall():
    wr = r[2]/r[1]*100 if r[1] else 0
    print(f"  {r[0]}: {r[1]}건 승률{wr:.0f}% | {r[3]:+,.0f}원")

con.close()
