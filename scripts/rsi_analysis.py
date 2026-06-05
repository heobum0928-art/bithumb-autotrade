"""RSI 구간별 outcome 분석.

signal_log의 RSI 값과 outcome_5m / outcome_30m의 관계를 분석한다.
현재 필터(RSI 45~90)의 최적성 검증 포함.

Usage:
    python scripts/rsi_analysis.py
"""
import sys
import sqlite3
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bithumb.db import DB_PATH

SEP = "=" * 60


def fmt_row(label: str, n: int, avg5: float | None, pos5: float | None,
            avg30: float | None = None, pos30: float | None = None) -> None:
    avg5_s  = f"{avg5:+.3f}%" if avg5  is not None else "   N/A "
    pos5_s  = f"{pos5*100:.1f}%"  if pos5  is not None else " N/A"
    avg30_s = f"{avg30:+.3f}%" if avg30 is not None else "   N/A "
    pos30_s = f"{pos30*100:.1f}%"  if pos30 is not None else " N/A"
    print(f"  {label:<20} {n:>5}건  5m: {avg5_s:>9} ({pos5_s:>5}양)  "
          f"30m: {avg30_s:>9} ({pos30_s:>5}양)")


def stats(rows: list[dict]) -> tuple:
    """(n, avg_5m, pos_rate_5m, avg_30m, pos_rate_30m) — None if no data."""
    v5  = [r["outcome_5m"]  for r in rows if r["outcome_5m"]  is not None]
    v30 = [r["outcome_30m"] for r in rows if r["outcome_30m"] is not None]
    avg5  = sum(v5)  / len(v5)  if v5  else None
    avg30 = sum(v30) / len(v30) if v30 else None
    pos5  = sum(1 for x in v5  if x > 0) / len(v5)  if v5  else None
    pos30 = sum(1 for x in v30 if x > 0) / len(v30) if v30 else None
    return len(rows), avg5, pos5, avg30, pos30


def run() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # ── 1. 스키마 / 기본 통계 ────────────────────────────────────────
    print(f"\n{SEP}")
    print(" 1. signal_log 스키마 및 기본 통계")
    print(SEP)
    rows_schema = con.execute("PRAGMA table_info(signal_log)").fetchall()
    print(f"  {'cid':<3} {'name':<16} {'type':<8} {'notnull':<8} {'default'}")
    for r in rows_schema:
        print(f"  {r['cid']:<3} {r['name']:<16} {r['type']:<8} {r['notnull']:<8} {r['dflt_value']}")

    total_all = con.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
    rsi_not_null = con.execute(
        "SELECT COUNT(*) FROM signal_log WHERE rsi IS NOT NULL").fetchone()[0]
    outcome_cnt = con.execute(
        "SELECT COUNT(*) FROM signal_log WHERE outcome_5m IS NOT NULL").fetchone()[0]
    rsi_min, rsi_max, rsi_avg = con.execute(
        "SELECT MIN(rsi), MAX(rsi), AVG(rsi) FROM signal_log WHERE rsi IS NOT NULL"
    ).fetchone()
    print(f"\n  전체 레코드    : {total_all}건")
    print(f"  RSI 비NULL     : {rsi_not_null}건")
    print(f"  outcome_5m 있음: {outcome_cnt}건")
    if rsi_min is not None:
        print(f"  RSI 범위       : {rsi_min:.1f} ~ {rsi_max:.1f}  (평균 {rsi_avg:.1f})")

    # outcome 있는 행만 분석
    all_rows = con.execute(
        "SELECT rsi, coin, outcome_5m, outcome_30m FROM signal_log "
        "WHERE outcome_5m IS NOT NULL"
    ).fetchall()
    all_rows = [dict(r) for r in all_rows]

    if not all_rows:
        print("\n  [!] outcome_5m 데이터 없음 — 봇이 5분 후 업데이트를 아직 기록 안 했거나 "
              "signal_log에 결과 추적 레코드가 없습니다.")
        print("      rsi 컬럼은 있지만 결과 없이는 구간 분석 불가.\n")
        _rsi_only_summary(con)
        con.close()
        return

    # ── 2. RSI 구간별 outcome_5m ────────────────────────────────────
    print(f"\n{SEP}")
    print(" 2. RSI 구간별 outcome_5m / outcome_30m")
    print(SEP)
    print(f"  {'구간':<20} {'건수':>5}    {'avg_5m':>9}   pos_5m    {'avg_30m':>9}  pos_30m")
    print(f"  {'-'*58}")

    BANDS = [
        ("RSI < 45",     lambda x: x < 45),
        ("RSI 45~55",    lambda x: 45 <= x < 55),
        ("RSI 55~65",    lambda x: 55 <= x < 65),
        ("RSI 65~75",    lambda x: 65 <= x < 75),
        ("RSI 75~85",    lambda x: 75 <= x < 85),
        ("RSI 85~90",    lambda x: 85 <= x < 90),
        ("RSI >= 90",    lambda x: x >= 90),
    ]

    band_stats: dict = {}
    for label, cond in BANDS:
        subset = [r for r in all_rows if r["rsi"] is not None and cond(r["rsi"])]
        n, avg5, pos5, avg30, pos30 = stats(subset)
        band_stats[label] = (n, avg5, pos5, avg30, pos30)
        fmt_row(label, n, avg5, pos5, avg30, pos30)

    # ── 3. 현재 필터 범위 (45~90) 평가 ────────────────────────────
    print(f"\n{SEP}")
    print(" 3. 현재 필터 RSI 45~90 평가")
    print(SEP)
    in_filter  = [r for r in all_rows if r["rsi"] is not None and 45 <= r["rsi"] < 90]
    out_filter = [r for r in all_rows if r["rsi"] is not None and
                  (r["rsi"] < 45 or r["rsi"] >= 90)]
    no_rsi     = [r for r in all_rows if r["rsi"] is None]

    n_in, avg5_in, pos5_in, avg30_in, pos30_in     = stats(in_filter)
    n_out, avg5_out, pos5_out, avg30_out, pos30_out = stats(out_filter)

    print(f"  {'범위':<20} {'건수':>5}    {'avg_5m':>9}   pos_5m    {'avg_30m':>9}  pos_30m")
    print(f"  {'-'*58}")
    fmt_row("통과 (45~90)",    n_in,  avg5_in,  pos5_in,  avg30_in,  pos30_in)
    fmt_row("차단 (<45 or ≥90)", n_out, avg5_out, pos5_out, avg30_out, pos30_out)
    fmt_row("RSI 없음 (N/A)", len(no_rsi),
            *stats(no_rsi)[1:])

    # ── 4. 차단된 신호 검증 (<45 or >=90) ──────────────────────────
    print(f"\n{SEP}")
    print(" 4. 차단된 신호 상세 (RSI < 45 or >= 90)")
    print(SEP)
    if not out_filter:
        print("  차단된 신호 없음 (outcome_5m 있는 데이터 기준)")
    else:
        for label, cond in [("RSI < 45 (과매도)", lambda x: x < 45),
                             ("RSI >= 90 (극단 과매수)", lambda x: x >= 90)]:
            subset = [r for r in out_filter if cond(r["rsi"])]
            if subset:
                n, avg5, pos5, avg30, pos30 = stats(subset)
                fmt_row(label, n, avg5, pos5, avg30, pos30)
                # 상세 샘플 (최대 10건)
                sample = subset[:10]
                for s in sample:
                    print(f"    → {s['coin']:<8}  RSI={s['rsi']:5.1f}  "
                          f"5m={s['outcome_5m']:+.2f}%  "
                          f"30m={s['outcome_30m']:+.2f}%" if s['outcome_30m'] is not None
                          else f"    → {s['coin']:<8}  RSI={s['rsi']:5.1f}  "
                               f"5m={s['outcome_5m']:+.2f}%  30m=N/A")

    # ── 5. pump_log JOIN — bounce_after=1 이벤트의 RSI 분포 ─────────
    print(f"\n{SEP}")
    print(" 5. pump_log JOIN — bounce_after=1 이벤트의 RSI 분포")
    print(SEP)
    bounce_rows = con.execute("""
        SELECT s.rsi, s.outcome_5m, s.outcome_30m, s.coin
        FROM signal_log s
        JOIN pump_log p ON s.coin = p.coin
                       AND substr(s.entered_at,1,16) = substr(p.detected_at,1,16)
        WHERE p.bounce_after = 1
          AND s.outcome_5m IS NOT NULL
    """).fetchall()
    bounce_rows = [dict(r) for r in bounce_rows]

    if not bounce_rows:
        # bounce_after=1인 데이터가 있는지 먼저 확인
        n_bounce = con.execute(
            "SELECT COUNT(*) FROM pump_log WHERE bounce_after=1").fetchone()[0]
        print(f"  bounce_after=1인 pump_log 이벤트 수: {n_bounce}건")
        if n_bounce == 0:
            print("  → bounce_after=1 이벤트 자체가 없음 (아직 눌림목 반등 감지 없음)")
        else:
            print("  → signal_log JOIN 결과 없음 (시간 매칭 실패 또는 RSI/outcome_5m NULL)")
        # 매칭 없이 RSI 분포만 보여주기
        bounce_rsi = con.execute("""
            SELECT s.rsi, s.outcome_5m, s.outcome_30m, s.coin
            FROM signal_log s
            JOIN pump_log p ON s.coin = p.coin
            WHERE p.bounce_after = 1
              AND s.rsi IS NOT NULL
        """).fetchall()
        bounce_rsi = [dict(r) for r in bounce_rsi]
        if bounce_rsi:
            print(f"\n  (시간 무시 coin JOIN 결과 {len(bounce_rsi)}건)")
            for label, cond in BANDS:
                subset = [r for r in bounce_rsi if r["rsi"] is not None and cond(r["rsi"])]
                if subset:
                    n, avg5, pos5, avg30, pos30 = stats(subset)
                    fmt_row(label, n, avg5, pos5, avg30, pos30)
    else:
        print(f"  bounce_after=1 매칭 신호: {len(bounce_rows)}건")
        print(f"  {'구간':<20} {'건수':>5}    {'avg_5m':>9}   pos_5m    {'avg_30m':>9}  pos_30m")
        print(f"  {'-'*58}")
        for label, cond in BANDS:
            subset = [r for r in bounce_rows if r["rsi"] is not None and cond(r["rsi"])]
            if subset:
                n, avg5, pos5, avg30, pos30 = stats(subset)
                fmt_row(label, n, avg5, pos5, avg30, pos30)

    # ── 6. 요약 및 권고 ─────────────────────────────────────────────
    print(f"\n{SEP}")
    print(" 6. 분석 요약 및 권고")
    print(SEP)

    # 최고 승률 구간 찾기
    best_label = None
    best_pos5  = -1.0
    for label, (n, avg5, pos5, avg30, pos30) in band_stats.items():
        if pos5 is not None and n >= 3 and pos5 > best_pos5:
            best_pos5  = pos5
            best_label = label

    if best_label:
        print(f"  ▶ 5m 양수 비율 최고 구간: {best_label}  ({best_pos5*100:.1f}%)")
    else:
        print("  ▶ 통계 산출 가능 구간 없음 (outcome_5m 데이터 부족)")

    # 필터 비교
    if n_in > 0 and n_out > 0 and avg5_in is not None and avg5_out is not None:
        if avg5_out > avg5_in:
            print(f"  ▶ 차단 구간의 avg_5m({avg5_out:+.3f}%)이 통과 구간({avg5_in:+.3f}%)보다 높음")
            print("    → 현재 필터가 좋은 신호를 차단하고 있을 가능성 있음 — 범위 재검토 필요")
        else:
            print(f"  ▶ 통과 구간 avg_5m({avg5_in:+.3f}%) > 차단 구간({avg5_out:+.3f}%)")
            print("    → 현재 45~90 필터 방향은 올바름")
        if pos5_in is not None and pos5_out is not None:
            if pos5_out > pos5_in:
                print(f"  ▶ 차단 구간 양수 비율({pos5_out*100:.1f}%) > 통과 구간({pos5_in*100:.1f}%)")
                print("    → 필터를 좁히는 것이 오히려 역효과일 수 있음")
            else:
                print(f"  ▶ 통과 구간 양수 비율({pos5_in*100:.1f}%) ≥ 차단 구간({pos5_out*100:.1f}%)")
                print("    → 현재 RSI 필터 유효")
    else:
        print("  ▶ outcome 데이터 불충분으로 필터 효과 비교 불가")
        print("    신호 발생 후 5분 지나야 outcome_5m 채워짐 — 데이터 축적 대기")

    # 코인별 RSI 범위 차이
    print(f"\n  [코인별 RSI 중앙값 (outcome_5m 있는 신호)]")
    coin_rows: dict = {}
    for r in all_rows:
        if r["rsi"] is not None:
            coin_rows.setdefault(r["coin"], []).append(r["rsi"])
    if coin_rows:
        for coin, rsi_vals in sorted(coin_rows.items()):
            med = sorted(rsi_vals)[len(rsi_vals)//2]
            mn, mx = min(rsi_vals), max(rsi_vals)
            print(f"    {coin:<8} {len(rsi_vals):>3}건  중앙={med:.0f}  범위={mn:.0f}~{mx:.0f}")
    else:
        print("    데이터 없음")

    print(f"\n{SEP}\n")
    con.close()


def _rsi_only_summary(con: sqlite3.Connection) -> None:
    """outcome_5m 없어도 RSI 분포만 보여줌."""
    print(f"\n{SEP}")
    print(" [대체] RSI 분포 (outcome 무관, 전체 signal_log)")
    print(SEP)
    rows = con.execute(
        "SELECT rsi, coin FROM signal_log WHERE rsi IS NOT NULL"
    ).fetchall()
    rows = [dict(r) for r in rows]
    if not rows:
        print("  RSI 데이터도 없음")
        return
    BANDS = [
        ("RSI < 45",  lambda x: x < 45),
        ("RSI 45~55", lambda x: 45 <= x < 55),
        ("RSI 55~65", lambda x: 55 <= x < 65),
        ("RSI 65~75", lambda x: 65 <= x < 75),
        ("RSI 75~85", lambda x: 75 <= x < 85),
        ("RSI 85~90", lambda x: 85 <= x < 90),
        ("RSI >= 90", lambda x: x >= 90),
    ]
    for label, cond in BANDS:
        subset = [r for r in rows if cond(r["rsi"])]
        if subset:
            coins = list({r["coin"] for r in subset})
            print(f"  {label:<14} {len(subset):>4}건  코인: {', '.join(coins[:8])}")


if __name__ == "__main__":
    run()
