"""pump_pct 구간별 반등률 분석 — 진입 최적 펌핑 크기 도출"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "trades.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

# ── 0. 스키마 확인 ──────────────────────────────────────────────
print("=" * 60)
print("0. 테이블 및 컬럼 확인")
print("=" * 60)
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)
for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = cur.fetchall()
    print(f"\n[{t}]")
    for c in cols:
        print(f"  cid={c[0]}  name={c[1]}  type={c[2]}")

# ── 1. pump_pct 컬럼 위치 파악 후 분석 대상 테이블 결정 ────────
# pump_log 또는 trades 테이블에 pump_pct 가 있을 가능성
target_table = None
pump_col = None
rebound_col = None

for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = {c[1]: c[2] for c in cur.fetchall()}
    col_names = list(cols.keys())
    # pump_pct 컬럼 탐색
    for cand in ["pump_pct", "price_chg", "pct", "chg_pct"]:
        if cand in col_names:
            pump_col = cand
            target_table = t
            break
    # 반등률 컬럼 탐색
    for cand in ["rebound_pct", "max_rebound", "pnl_pct", "outcome_pct", "t5m_chg", "t30m_chg"]:
        if cand in col_names:
            rebound_col = cand
            break
    if pump_col:
        break

print(f"\n분석 테이블: {target_table}, pump_col={pump_col}, rebound_col={rebound_col}")

if not target_table or not pump_col:
    print("\n[pump_pct 관련 컬럼을 찾지 못했습니다. 모든 테이블 전체 데이터 샘플을 출력합니다]")
    for t in tables:
        cur.execute(f"SELECT * FROM {t} LIMIT 5")
        rows = cur.fetchall()
        cur.execute(f"PRAGMA table_info({t})")
        col_names = [c[1] for c in cur.fetchall()]
        print(f"\n[{t}] 샘플 (컬럼: {col_names})")
        for r in rows:
            print(" ", r)
    conn.close()
    sys.exit(0)

# ── 2. 전체 레코드 수 ───────────────────────────────────────────
cur.execute(f"SELECT COUNT(*) FROM {target_table} WHERE {pump_col} IS NOT NULL")
total = cur.fetchone()[0]
print(f"\n총 분석 대상 레코드: {total}건")

if total == 0:
    print("데이터가 없습니다.")
    conn.close()
    sys.exit(0)

# ── 3. pump_pct 구간별 반등률 ───────────────────────────────────
print("\n" + "=" * 60)
print("1. pump_pct 구간별 통계")
print("=" * 60)

# rebound_col 이 없으면 대체 컬럼 탐색
cur.execute(f"PRAGMA table_info({target_table})")
all_cols = {c[1]: c[2] for c in cur.fetchall()}

if not rebound_col:
    # fallback: pnl_pct, outcome, t5m, t30m 중 있는 것
    for cand in all_cols.keys():
        if any(k in cand for k in ["rebound", "pnl", "outcome", "chg", "drop", "rise"]):
            rebound_col = cand
            break

print(f"반등률 컬럼: {rebound_col}")

BINS = [
    ("3~5%",   3, 5),
    ("5~7%",   5, 7),
    ("7~10%",  7, 10),
    ("10~15%", 10, 15),
    ("15%+",   15, 9999),
]

# 반등률 컬럼이 있을 때
if rebound_col:
    print(f"\n{'구간':<10} {'건수':>6} {'평균반등%':>10} {'중간값%':>10} {'양전환%':>8}")
    print("-" * 50)
    for label, lo, hi in BINS:
        if hi == 9999:
            q = f"SELECT COUNT(*), AVG({rebound_col}), {rebound_col} FROM {target_table} WHERE {pump_col}>={lo} AND {pump_col} IS NOT NULL AND {rebound_col} IS NOT NULL"
        else:
            q = f"SELECT COUNT(*), AVG({rebound_col}), {rebound_col} FROM {target_table} WHERE {pump_col}>={lo} AND {pump_col}<{hi} AND {rebound_col} IS NOT NULL"

        cur.execute(q.split(" AND {rebound_col}")[0].replace(", {rebound_col}", "").replace("{rebound_col}", rebound_col).replace("{pump_col}", pump_col).replace("{target_table}", target_table))

        # rebuild cleanly
        if hi == 9999:
            sql = (f"SELECT COUNT(*), AVG({rebound_col}) "
                   f"FROM {target_table} "
                   f"WHERE {pump_col}>={lo} AND {pump_col} IS NOT NULL AND {rebound_col} IS NOT NULL")
        else:
            sql = (f"SELECT COUNT(*), AVG({rebound_col}) "
                   f"FROM {target_table} "
                   f"WHERE {pump_col}>={lo} AND {pump_col}<{hi} AND {rebound_col} IS NOT NULL")
        cur.execute(sql)
        cnt, avg_r = cur.fetchone()

        # 양전환 비율 (rebound > 0)
        if hi == 9999:
            sql2 = (f"SELECT COUNT(*) FROM {target_table} "
                    f"WHERE {pump_col}>={lo} AND {rebound_col}>0")
        else:
            sql2 = (f"SELECT COUNT(*) FROM {target_table} "
                    f"WHERE {pump_col}>={lo} AND {pump_col}<{hi} AND {rebound_col}>0")
        cur.execute(sql2)
        pos_cnt = cur.fetchone()[0]

        # 중간값
        if cnt > 0:
            if hi == 9999:
                sql3 = (f"SELECT {rebound_col} FROM {target_table} "
                        f"WHERE {pump_col}>={lo} AND {rebound_col} IS NOT NULL "
                        f"ORDER BY {rebound_col}")
            else:
                sql3 = (f"SELECT {rebound_col} FROM {target_table} "
                        f"WHERE {pump_col}>={lo} AND {pump_col}<{hi} AND {rebound_col} IS NOT NULL "
                        f"ORDER BY {rebound_col}")
            cur.execute(sql3)
            vals = [r[0] for r in cur.fetchall()]
            median = vals[len(vals)//2] if vals else None
        else:
            median = None

        avg_str = f"{avg_r:+.2f}%" if avg_r is not None else "  N/A"
        med_str = f"{median:+.2f}%" if median is not None else "  N/A"
        pos_str = f"{pos_cnt/cnt*100:.1f}%" if cnt > 0 else "  N/A"
        print(f"{label:<10} {cnt:>6} {avg_str:>10} {med_str:>10} {pos_str:>8}")

# pump_pct 만 있을 때도 분포는 보여줌
print("\n" + "=" * 60)
print("pump_pct 구간별 단순 건수 분포")
print("=" * 60)
print(f"\n{'구간':<10} {'건수':>6}")
print("-" * 20)
for label, lo, hi in BINS:
    if hi == 9999:
        sql = f"SELECT COUNT(*) FROM {target_table} WHERE {pump_col}>={lo}"
    else:
        sql = f"SELECT COUNT(*) FROM {target_table} WHERE {pump_col}>={lo} AND {pump_col}<{hi}"
    cur.execute(sql)
    cnt = cur.fetchone()[0]
    print(f"{label:<10} {cnt:>6}")

# ── 4. 코인별 최적 pump_pct 구간 ──────────────────────────────
print("\n" + "=" * 60)
print("2. 코인별 통계 (건수 3건 이상)")
print("=" * 60)

coin_col = None
for cand in ["coin", "market", "symbol"]:
    if cand in all_cols:
        coin_col = cand
        break

if coin_col and rebound_col:
    sql = (f"SELECT {coin_col}, COUNT(*) as cnt, "
           f"AVG({pump_col}) as avg_pump, AVG({rebound_col}) as avg_rebound, "
           f"MIN({pump_col}) as min_pump, MAX({pump_col}) as max_pump "
           f"FROM {target_table} "
           f"WHERE {pump_col} IS NOT NULL AND {rebound_col} IS NOT NULL "
           f"GROUP BY {coin_col} HAVING cnt >= 3 "
           f"ORDER BY avg_rebound DESC LIMIT 20")
    cur.execute(sql)
    rows = cur.fetchall()
    print(f"\n{'코인':<12} {'건수':>5} {'평균펌핑%':>10} {'평균반등%':>10} {'min펌핑':>8} {'max펌핑':>8}")
    print("-" * 60)
    for r in rows:
        print(f"{r[0]:<12} {r[1]:>5} {r[2]:>+9.1f}% {r[3]:>+9.1f}% {r[4]:>7.1f}% {r[5]:>7.1f}%")
elif coin_col:
    sql = (f"SELECT {coin_col}, COUNT(*) as cnt, "
           f"AVG({pump_col}) as avg_pump, MIN({pump_col}), MAX({pump_col}) "
           f"FROM {target_table} WHERE {pump_col} IS NOT NULL "
           f"GROUP BY {coin_col} HAVING cnt >= 2 ORDER BY avg_pump DESC LIMIT 20")
    cur.execute(sql)
    rows = cur.fetchall()
    print(f"\n{'코인':<12} {'건수':>5} {'평균펌핑%':>10} {'min':>8} {'max':>8}")
    print("-" * 50)
    for r in rows:
        print(f"{r[0]:<12} {r[1]:>5} {r[2]:>+9.1f}% {r[3]:>7.1f}% {r[4]:>7.1f}%")

# ── 5. pump_pct vs max_drop_pct 상관관계 ──────────────────────
print("\n" + "=" * 60)
print("3. pump_pct vs drop/loss 상관관계")
print("=" * 60)

drop_col = None
for cand in ["max_drop_pct", "drop_pct", "loss_pct", "min_pnl", "lowest_pct"]:
    if cand in all_cols:
        drop_col = cand
        break

if drop_col:
    sql = (f"SELECT {pump_col}, {drop_col} FROM {target_table} "
           f"WHERE {pump_col} IS NOT NULL AND {drop_col} IS NOT NULL")
    cur.execute(sql)
    rows = cur.fetchall()
    if rows:
        pumps = [r[0] for r in rows]
        drops = [r[1] for r in rows]
        n = len(pumps)
        mean_p = sum(pumps)/n
        mean_d = sum(drops)/n
        cov = sum((pumps[i]-mean_p)*(drops[i]-mean_d) for i in range(n)) / n
        std_p = (sum((x-mean_p)**2 for x in pumps)/n)**0.5
        std_d = (sum((x-mean_d)**2 for x in drops)/n)**0.5
        if std_p > 0 and std_d > 0:
            corr = cov / (std_p * std_d)
            print(f"\npump_pct vs {drop_col} 상관계수: {corr:.4f}")
            if corr > 0.3:
                print("  → 양의 상관: 펌핑이 클수록 낙폭도 큰 경향")
            elif corr < -0.3:
                print("  → 음의 상관: 펌핑이 클수록 낙폭이 작은 경향")
            else:
                print("  → 상관관계 미약")

        # 구간별 평균 drop
        print(f"\n{'구간':<10} {'건수':>6} {'평균낙폭%':>10}")
        print("-" * 30)
        for label, lo, hi in BINS:
            if hi == 9999:
                sql2 = f"SELECT COUNT(*), AVG({drop_col}) FROM {target_table} WHERE {pump_col}>={lo} AND {drop_col} IS NOT NULL"
            else:
                sql2 = f"SELECT COUNT(*), AVG({drop_col}) FROM {target_table} WHERE {pump_col}>={lo} AND {pump_col}<{hi} AND {drop_col} IS NOT NULL"
            cur.execute(sql2)
            cnt, avg_d = cur.fetchone()
            avg_str = f"{avg_d:+.2f}%" if avg_d is not None else "  N/A"
            print(f"{label:<10} {cnt:>6} {avg_str:>10}")
else:
    print(f"\n낙폭 컬럼 없음. 사용 가능한 컬럼: {list(all_cols.keys())}")

# ── 6. PRICE_THRESH=5% 적절성 평가 ────────────────────────────
print("\n" + "=" * 60)
print("4. 현재 PRICE_THRESH=5% 기준 평가")
print("=" * 60)

if rebound_col:
    # 5% 미만 vs 5% 이상 비교
    for label, cond in [("5% 미만 (기준 미달)", f"{pump_col}<5"),
                         ("5%~10% (현재 통과)", f"{pump_col}>=5 AND {pump_col}<10"),
                         ("10%+ (과열 구간)", f"{pump_col}>=10")]:
        sql = (f"SELECT COUNT(*), AVG({pump_col}), AVG({rebound_col}), "
               f"SUM(CASE WHEN {rebound_col}>0 THEN 1 ELSE 0 END) "
               f"FROM {target_table} WHERE {cond} AND {rebound_col} IS NOT NULL")
        cur.execute(sql)
        cnt, avg_p, avg_r, pos = cur.fetchone()
        if cnt and cnt > 0:
            win_r = pos/cnt*100 if pos else 0
            print(f"\n{label}")
            print(f"  건수={cnt}, 평균펌핑={avg_p:+.1f}%, 평균반등={avg_r:+.2f}%, 양전환율={win_r:.1f}%")
        else:
            print(f"\n{label}: 데이터 없음")

# ── 7. 샘플 데이터 출력 ────────────────────────────────────────
print("\n" + "=" * 60)
print("5. 최근 데이터 샘플 (최대 10행)")
print("=" * 60)
cur.execute(f"SELECT * FROM {target_table} ORDER BY rowid DESC LIMIT 10")
rows = cur.fetchall()
cur.execute(f"PRAGMA table_info({target_table})")
col_names = [c[1] for c in cur.fetchall()]
print(f"컬럼: {col_names}")
for r in rows:
    print(r)

conn.close()
print("\n분석 완료.")
