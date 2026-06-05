import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3

DB = r"c:\code\coinbase\data\trades.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# ── 스키마 확인 ──────────────────────────────────────────────
cur.execute("PRAGMA table_info(pump_log)")
cols = cur.fetchall()
print("=== pump_log 컬럼 ===")
for c in cols:
    print(f"  {c['cid']}: {c['name']} ({c['type']})")

# peak_at_sec / bounce_after 컬럼 존재 여부 확인
col_names = [c['name'] for c in cols]
has_peak = 'peak_at_sec' in col_names
has_bounce = 'bounce_after' in col_names
print(f"\npeak_at_sec 존재: {has_peak}")
print(f"bounce_after 존재: {has_bounce}")

if not has_peak:
    print("\n[오류] peak_at_sec 컬럼이 없습니다. 사용 가능한 컬럼:")
    for c in col_names:
        print(f"  {c}")
    conn.close()
    sys.exit(1)

# 전체 row 수 및 non-NULL 확인
cur.execute("SELECT COUNT(*) as total, COUNT(peak_at_sec) as non_null FROM pump_log")
r = cur.fetchone()
print(f"\n전체 행: {r['total']}, peak_at_sec non-NULL: {r['non_null']}")

if r['non_null'] == 0:
    print("[경고] peak_at_sec 값이 모두 NULL입니다.")
    conn.close()
    sys.exit(0)

# ── 1. peak_at_sec 분포 (percentile) ────────────────────────
print("\n=== 1. peak_at_sec 분포 (NULL 제외) ===")
cur.execute("""
    SELECT
        COUNT(*) as n,
        MIN(peak_at_sec) as min_val,
        MAX(peak_at_sec) as max_val,
        AVG(peak_at_sec) as avg_val,
        (SELECT peak_at_sec FROM pump_log WHERE peak_at_sec IS NOT NULL
         ORDER BY peak_at_sec LIMIT 1 OFFSET (SELECT COUNT(*) FROM pump_log WHERE peak_at_sec IS NOT NULL) * 25 / 100) as p25,
        (SELECT peak_at_sec FROM pump_log WHERE peak_at_sec IS NOT NULL
         ORDER BY peak_at_sec LIMIT 1 OFFSET (SELECT COUNT(*) FROM pump_log WHERE peak_at_sec IS NOT NULL) * 50 / 100) as p50,
        (SELECT peak_at_sec FROM pump_log WHERE peak_at_sec IS NOT NULL
         ORDER BY peak_at_sec LIMIT 1 OFFSET (SELECT COUNT(*) FROM pump_log WHERE peak_at_sec IS NOT NULL) * 75 / 100) as p75,
        (SELECT peak_at_sec FROM pump_log WHERE peak_at_sec IS NOT NULL
         ORDER BY peak_at_sec LIMIT 1 OFFSET (SELECT COUNT(*) FROM pump_log WHERE peak_at_sec IS NOT NULL) * 90 / 100) as p90
    FROM pump_log WHERE peak_at_sec IS NOT NULL
""")
r = cur.fetchone()
print(f"  N={r['n']}, min={r['min_val']:.1f}s, max={r['max_val']:.1f}s, avg={r['avg_val']:.1f}s")
print(f"  P25={r['p25']}s  P50(중앙값)={r['p50']}s  P75={r['p75']}s  P90={r['p90']}s")

# ── 2. peak_at_sec 구간별 반등률 ────────────────────────────
print("\n=== 2. peak_at_sec 구간별 반등률 ===")
if has_bounce:
    cur.execute("""
        SELECT
            CASE
                WHEN peak_at_sec <= 30  THEN '0~30초'
                WHEN peak_at_sec <= 60  THEN '30~60초'
                WHEN peak_at_sec <= 120 THEN '60~120초'
                ELSE '120초+'
            END as bucket,
            COUNT(*) as n,
            COUNT(bounce_after) as bounce_non_null,
            ROUND(AVG(CASE WHEN bounce_after IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100, 1) as bounce_rate_pct,
            ROUND(AVG(bounce_after), 4) as avg_bounce,
            ROUND(AVG(peak_at_sec), 1) as avg_peak_sec
        FROM pump_log
        WHERE peak_at_sec IS NOT NULL
        GROUP BY bucket
        ORDER BY MIN(peak_at_sec)
    """)
    rows = cur.fetchall()
    print(f"  {'구간':<10} {'N':>5} {'반등있음':>8} {'반등률%':>8} {'평균bounce':>12} {'평균peak_sec':>13}")
    print("  " + "-"*60)
    for row in rows:
        print(f"  {row['bucket']:<10} {row['n']:>5} {row['bounce_non_null']:>8} {row['bounce_rate_pct']:>8.1f} {str(row['avg_bounce'] or 'N/A'):>12} {row['avg_peak_sec']:>13.1f}")
else:
    # bounce_after 없으면 outcome 컬럼 탐색
    print("  bounce_after 컬럼 없음 — outcome/result 컬럼으로 대체 시도")
    outcome_col = None
    for c in col_names:
        if c in ('outcome', 'result', 'pnl_pct', 'pnl', 'profit_pct'):
            outcome_col = c
            break
    if outcome_col:
        print(f"  사용 컬럼: {outcome_col}")
        cur.execute(f"""
            SELECT
                CASE
                    WHEN peak_at_sec <= 30  THEN '0~30초'
                    WHEN peak_at_sec <= 60  THEN '30~60초'
                    WHEN peak_at_sec <= 120 THEN '60~120초'
                    ELSE '120초+'
                END as bucket,
                COUNT(*) as n,
                ROUND(AVG({outcome_col}), 4) as avg_outcome
            FROM pump_log
            WHERE peak_at_sec IS NOT NULL
            GROUP BY bucket
            ORDER BY MIN(peak_at_sec)
        """)
        for row in cur.fetchall():
            print(f"  {row['bucket']:<12} N={row['n']:>4}  avg_{outcome_col}={row['avg_outcome']}")
    else:
        print(f"  결과 컬럼을 찾을 수 없음. 전체 컬럼: {col_names}")

# ── 3. NULL vs non-NULL bounce_after 비율 ───────────────────
print("\n=== 3. bounce_after NULL 비율 ===")
if has_bounce:
    cur.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(bounce_after) as non_null,
            SUM(CASE WHEN bounce_after IS NULL THEN 1 ELSE 0 END) as null_count
        FROM pump_log
    """)
    r = cur.fetchone()
    total = r['total']
    nn = r['non_null']
    nc = r['null_count']
    print(f"  전체: {total}, non-NULL: {nn} ({nn/total*100:.1f}%), NULL: {nc} ({nc/total*100:.1f}%)")

    # bounce 값의 분포도
    cur.execute("""
        SELECT
            SUM(CASE WHEN bounce_after > 0   THEN 1 ELSE 0 END) as positive,
            SUM(CASE WHEN bounce_after <= 0  THEN 1 ELSE 0 END) as non_positive,
            AVG(bounce_after) as avg_b,
            MIN(bounce_after) as min_b,
            MAX(bounce_after) as max_b
        FROM pump_log WHERE bounce_after IS NOT NULL
    """)
    r = cur.fetchone()
    print(f"  bounce>0(반등): {r['positive']}, bounce<=0(반등없음): {r['non_positive']}")
    print(f"  avg={r['avg_b']:.4f}, min={r['min_b']:.4f}, max={r['max_b']:.4f}")
else:
    print("  bounce_after 컬럼 없음")

# ── 4. 코인별 평균 peak_at_sec ───────────────────────────────
print("\n=== 4. 코인별 평균 peak_at_sec (N≥2) ===")
coin_col = 'coin' if 'coin' in col_names else ('market' if 'market' in col_names else None)
if coin_col:
    cur.execute(f"""
        SELECT
            {coin_col} as coin,
            COUNT(*) as n,
            ROUND(AVG(peak_at_sec), 1) as avg_peak,
            MIN(peak_at_sec) as min_peak,
            MAX(peak_at_sec) as max_peak
        FROM pump_log
        WHERE peak_at_sec IS NOT NULL
        GROUP BY {coin_col}
        HAVING COUNT(*) >= 1
        ORDER BY avg_peak
        LIMIT 30
    """)
    rows = cur.fetchall()
    print(f"  {'코인':<12} {'N':>4} {'avg_peak':>10} {'min':>8} {'max':>8}")
    print("  " + "-"*46)
    for row in rows:
        print(f"  {row['coin']:<12} {row['n']:>4} {row['avg_peak']:>10.1f} {row['min_peak']:>8.1f} {row['max_peak']:>8.1f}")
else:
    print("  코인 식별 컬럼 없음")

# ── 5. 빠른 그룹(≤30s) vs 느린 그룹(≥120s) 반등률 비교 ─────
print("\n=== 5. 빠른 펌프(≤30s) vs 느린 펌프(≥120s) 비교 ===")
if has_bounce:
    for label, cond in [("빠른 펌프 (≤30초)", "peak_at_sec <= 30"), ("느린 펌프 (≥120초)", "peak_at_sec >= 120")]:
        cur.execute(f"""
            SELECT
                COUNT(*) as n,
                COUNT(bounce_after) as bounce_count,
                ROUND(AVG(CASE WHEN bounce_after IS NOT NULL THEN 1.0 ELSE 0.0 END)*100,1) as bounce_rate,
                ROUND(AVG(CASE WHEN bounce_after > 0 THEN 1.0 ELSE 0.0 END)*100, 1) as positive_bounce_rate,
                ROUND(AVG(bounce_after), 4) as avg_bounce,
                ROUND(AVG(peak_at_sec), 1) as avg_peak_sec
            FROM pump_log
            WHERE {cond} AND peak_at_sec IS NOT NULL
        """)
        r = cur.fetchone()
        print(f"\n  [{label}]")
        print(f"    N={r['n']}, bounce 데이터 있음: {r['bounce_count']}")
        print(f"    반등 데이터 비율: {r['bounce_rate']}%")
        print(f"    bounce>0 비율: {r['positive_bounce_rate']}%")
        print(f"    평균 bounce: {r['avg_bounce']}")
        print(f"    평균 peak_at_sec: {r['avg_peak_sec']}s")
else:
    print("  bounce_after 컬럼 없어 대체 분석 진행")
    # pump_pct 또는 peak_pct 등으로 대체
    alt_col = None
    for c in col_names:
        if 'pct' in c or 'pnl' in c or 'gain' in c:
            alt_col = c
            break
    if alt_col:
        print(f"  대체 지표: {alt_col}")
        for label, cond in [("빠른(≤30s)", "peak_at_sec <= 30"), ("느린(≥120s)", "peak_at_sec >= 120")]:
            cur.execute(f"""
                SELECT COUNT(*) as n, ROUND(AVG({alt_col}),4) as avg_val
                FROM pump_log WHERE {cond} AND peak_at_sec IS NOT NULL
            """)
            r = cur.fetchone()
            print(f"  {label}: N={r['n']}, avg_{alt_col}={r['avg_val']}")

# ── 추가: bounce_after 있을 경우 상세 분포 ────────────────────
if has_bounce:
    print("\n=== 추가: peak_at_sec vs bounce_after 상관 샘플 (최근 20건) ===")
    cur.execute("""
        SELECT coin, peak_at_sec, bounce_after, pump_pct
        FROM pump_log
        WHERE peak_at_sec IS NOT NULL AND bounce_after IS NOT NULL
        ORDER BY detected_at DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    if rows:
        print(f"  {'코인':<10} {'peak_sec':>10} {'bounce':>10} {'pump_pct':>10}")
        print("  " + "-"*44)
        for row in rows:
            print(f"  {row['coin']:<10} {row['peak_at_sec']:>10.1f} {row['bounce_after']:>10.4f} {row['pump_pct'] or 0:>10.4f}")

conn.close()
print("\n=== 분석 완료 ===")
