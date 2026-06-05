"""
화이트리스트 전략 분석 — 반등률 높은 코인만 거래하는 전략의
기대수익(EV), 트레이드오프, 기회비용을 pump_log 데이터로 평가.
"""
import sys
import sqlite3
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"
FEE = 0.005  # 왕복 수수료 0.5%

def run():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # ── 0. 테이블 현황 ──────────────────────────────────────────
    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    print("=== DB 테이블 목록 ===")
    for t in tables:
        cnt = con.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        print(f"  {t[0]}: {cnt}건")
    print()

    # pump_log 기본 현황
    total = con.execute("SELECT COUNT(*) FROM pump_log").fetchone()[0]
    with_drop = con.execute(
        "SELECT COUNT(*) FROM pump_log WHERE max_drop_pct IS NOT NULL"
    ).fetchone()[0]
    print(f"pump_log 전체: {total}건  (max_drop_pct 있음: {with_drop}건)")
    print()

    if with_drop == 0:
        print("[경고] max_drop_pct 데이터 없음 — bounce_after 컬럼으로 대체 분석합니다.")
        _analyze_bounce(con)
        con.close()
        return

    # ── 1. 코인별 반등률 전체 ──────────────────────────────────
    print("=" * 60)
    print("1. 코인별 반등률 전체 (건수 포함)")
    print("=" * 60)
    q1 = """
        SELECT
            coin,
            COUNT(*)                                        AS total_events,
            ROUND(AVG(pump_pct), 2)                        AS avg_pump_pct,
            ROUND(AVG(max_drop_pct), 2)                    AS avg_drop_pct,
            ROUND(
                100.0 * SUM(CASE WHEN bounce_after = 1 THEN 1 ELSE 0 END)
                / COUNT(*), 1
            )                                              AS bounce_rate_pct,
            SUM(CASE WHEN bounce_after = 1 THEN 1 ELSE 0 END) AS bounce_cnt
        FROM pump_log
        WHERE max_drop_pct IS NOT NULL
        GROUP BY coin
        ORDER BY bounce_rate_pct DESC, total_events DESC
    """
    rows = con.execute(q1).fetchall()
    if not rows:
        print("  데이터 없음")
    else:
        print(f"  {'코인':<10} {'이벤트':>8} {'평균펌핑%':>10} {'평균낙폭%':>10} {'반등률%':>9} {'반등건':>7}")
        print("  " + "-" * 60)
        for r in rows:
            print(f"  {r['coin']:<10} {r['total_events']:>8} "
                  f"{r['avg_pump_pct']:>10.1f} {r['avg_drop_pct']:>10.1f} "
                  f"{r['bounce_rate_pct']:>9.1f} {r['bounce_cnt']:>7}")
    print()

    # ── 2. 유효 화이트리스트 후보 (반등률 >= 60%, 건수 >= 5) ───
    print("=" * 60)
    print("2. 화이트리스트 후보: 반등률 >= 60%, 이벤트 수 >= 5")
    print("=" * 60)
    q2 = """
        SELECT
            coin,
            COUNT(*)                                        AS total_events,
            ROUND(AVG(pump_pct), 2)                        AS avg_pump_pct,
            ROUND(AVG(max_drop_pct), 2)                    AS avg_drop_pct,
            ROUND(
                100.0 * SUM(CASE WHEN bounce_after = 1 THEN 1 ELSE 0 END)
                / COUNT(*), 1
            )                                              AS bounce_rate_pct,
            SUM(CASE WHEN bounce_after = 1 THEN 1 ELSE 0 END) AS bounce_cnt
        FROM pump_log
        WHERE max_drop_pct IS NOT NULL
        GROUP BY coin
        HAVING bounce_rate_pct >= 60 AND total_events >= 5
        ORDER BY bounce_rate_pct DESC
    """
    wl_rows = con.execute(q2).fetchall()
    wl_coins = [r['coin'] for r in wl_rows]
    if not wl_rows:
        print("  [없음] 조건(반등률>=60%, 건수>=5)을 만족하는 코인 없음")
        print("  → 임계값을 낮춰 반등률 >= 50%, 건수 >= 3 으로 재조회...")
        q2b = q2.replace("bounce_rate_pct >= 60 AND total_events >= 5",
                          "bounce_rate_pct >= 50 AND total_events >= 3")
        wl_rows = con.execute(q2b).fetchall()
        wl_coins = [r['coin'] for r in wl_rows]
        if not wl_rows:
            print("  [없음] 완화된 조건도 만족 코인 없음")
    if wl_rows:
        print(f"  {'코인':<10} {'이벤트':>8} {'평균펌핑%':>10} {'평균낙폭%':>10} {'반등률%':>9}")
        print("  " + "-" * 55)
        for r in wl_rows:
            print(f"  {r['coin']:<10} {r['total_events']:>8} "
                  f"{r['avg_pump_pct']:>10.1f} {r['avg_drop_pct']:>10.1f} "
                  f"{r['bounce_rate_pct']:>9.1f}")
    print()

    # ── 3. 화이트리스트 코인의 avg pump_pct, max_drop_pct ──────
    print("=" * 60)
    print("3. 화이트리스트 코인 평균 pump_pct / max_drop_pct")
    print("=" * 60)
    if wl_coins:
        placeholders = ",".join("?" * len(wl_coins))
        q3 = f"""
            SELECT
                ROUND(AVG(pump_pct), 2)     AS avg_pump_pct,
                ROUND(AVG(max_drop_pct), 2) AS avg_drop_pct,
                ROUND(MIN(pump_pct), 2)     AS min_pump_pct,
                ROUND(MAX(pump_pct), 2)     AS max_pump_pct,
                COUNT(*)                    AS total_events
            FROM pump_log
            WHERE coin IN ({placeholders}) AND max_drop_pct IS NOT NULL
        """
        r3 = con.execute(q3, wl_coins).fetchone()
        print(f"  대상 코인: {wl_coins}")
        print(f"  이벤트 수:   {r3['total_events']}건")
        print(f"  평균 펌핑:   {r3['avg_pump_pct']:+.2f}%")
        print(f"  평균 낙폭:   {r3['avg_drop_pct']:+.2f}%")
        print(f"  펌핑 범위:   {r3['min_pump_pct']:+.2f}% ~ {r3['max_pump_pct']:+.2f}%")
    else:
        print("  화이트리스트 코인 없음 — 전체 평균으로 대체")
        r3 = con.execute("""
            SELECT ROUND(AVG(pump_pct),2) AS avg_pump_pct,
                   ROUND(AVG(max_drop_pct),2) AS avg_drop_pct,
                   COUNT(*) AS total_events
            FROM pump_log WHERE max_drop_pct IS NOT NULL
        """).fetchone()
        print(f"  이벤트 수: {r3['total_events']}건")
        print(f"  평균 펌핑: {r3['avg_pump_pct']:+.2f}%")
        print(f"  평균 낙폭: {r3['avg_drop_pct']:+.2f}%")
    print()

    # ── 4. 하루 평균 이벤트 수 ────────────────────────────────
    print("=" * 60)
    print("4. 화이트리스트 코인 하루 평균 이벤트 수")
    print("=" * 60)
    if wl_coins:
        placeholders = ",".join("?" * len(wl_coins))
        q4 = f"""
            SELECT
                DATE(detected_at) AS day,
                COUNT(*)          AS events
            FROM pump_log
            WHERE coin IN ({placeholders}) AND max_drop_pct IS NOT NULL
            GROUP BY day
            ORDER BY day
        """
        day_rows = con.execute(q4, wl_coins).fetchall()
        if day_rows:
            print(f"  날짜별 이벤트:")
            for dr in day_rows:
                print(f"    {dr['day']}: {dr['events']}건")
            avg_per_day = sum(dr['events'] for dr in day_rows) / len(day_rows)
            print(f"\n  하루 평균: {avg_per_day:.1f}건 (관측 {len(day_rows)}일)")
        else:
            print("  날짜별 데이터 없음")
        wl_avg_per_day = avg_per_day if day_rows else 0
    else:
        # 전체 기준
        q4b = """
            SELECT DATE(detected_at) AS day, COUNT(*) AS events
            FROM pump_log WHERE max_drop_pct IS NOT NULL
            GROUP BY day ORDER BY day
        """
        day_rows = con.execute(q4b).fetchall()
        if day_rows:
            for dr in day_rows:
                print(f"    {dr['day']}: {dr['events']}건 (전체)")
            wl_avg_per_day = sum(dr['events'] for dr in day_rows) / len(day_rows)
        else:
            wl_avg_per_day = 0
    print()

    # ── 5. 전체 vs 화이트리스트 EV 계산 ──────────────────────
    print("=" * 60)
    print("5. 기대수익(EV) 분석 — 수수료 0.5% 포함")
    print("=" * 60)

    def compute_ev(cur, coins=None):
        """
        EV = bounce_rate * (avg_pump_on_bounce - fee) - (1-bounce_rate) * (avg_drop_when_no_bounce + fee)
        pump_pct 와 max_drop_pct 로 수익/손실 규모 추정.
        """
        if coins:
            ph = ",".join("?" * len(coins))
            base = f"FROM pump_log WHERE coin IN ({ph}) AND max_drop_pct IS NOT NULL"
            args = coins
        else:
            base = "FROM pump_log WHERE max_drop_pct IS NOT NULL"
            args = []

        total_q = f"SELECT COUNT(*) {base}"
        bounce_q = f"SELECT COUNT(*) {base} AND bounce_after = 1"
        avg_pump_bounce_q = f"SELECT AVG(pump_pct) {base} AND bounce_after = 1"
        avg_drop_no_bounce_q = f"SELECT AVG(max_drop_pct) {base} AND bounce_after = 0"

        n_total = cur.execute(total_q, args).fetchone()[0]
        n_bounce = cur.execute(bounce_q, args).fetchone()[0]
        avg_pump_w = cur.execute(avg_pump_bounce_q, args).fetchone()[0] or 0
        avg_drop_l = cur.execute(avg_drop_no_bounce_q, args).fetchone()[0] or 0

        if n_total == 0:
            return None

        p_win = n_bounce / n_total
        p_loss = 1 - p_win

        # 익절/손절 전략 적용: TP=+5%, SL=-3% 기준으로 clamp
        tp = 5.0
        sl = 3.0
        win_gain = min(avg_pump_w, tp) - FEE * 100   # % 단위
        loss_cost = min(abs(avg_drop_l), sl) + FEE * 100

        ev = p_win * win_gain - p_loss * loss_cost

        return {
            "n_total": n_total,
            "n_bounce": n_bounce,
            "p_win": p_win,
            "avg_pump_w": avg_pump_w,
            "avg_drop_l": avg_drop_l,
            "win_gain": win_gain,
            "loss_cost": loss_cost,
            "ev": ev,
        }

    cur2 = con.cursor()
    ev_all = compute_ev(cur2, coins=None)
    ev_wl  = compute_ev(cur2, coins=wl_coins) if wl_coins else None

    def print_ev(label, ev):
        if ev is None:
            print(f"  {label}: 데이터 없음")
            return
        print(f"  [{label}]")
        print(f"    이벤트 수:     {ev['n_total']}건")
        print(f"    반등 성공:     {ev['n_bounce']}건 ({ev['p_win']*100:.1f}%)")
        print(f"    평균 펌핑폭:   {ev['avg_pump_w']:+.2f}%  (반등 시)")
        print(f"    평균 낙폭:     {ev['avg_drop_l']:+.2f}%  (반등 실패 시)")
        print(f"    건당 수익 (TP 상한 {5}%, 수수료 후): {ev['win_gain']:+.2f}%")
        print(f"    건당 손실 (SL 상한 {3}%, 수수료 후): -{ev['loss_cost']:.2f}%")
        print(f"    건당 EV:       {ev['ev']:+.3f}%")
        ev_krw_per_10m = ev['ev'] / 100 * 10_000_000
        print(f"    EV (1000만원 기준): {ev_krw_per_10m:+,.0f}원/건")

    print_ev("전체 코인", ev_all)
    print()
    print_ev("화이트리스트 코인", ev_wl)
    print()

    # ── 6. 화이트리스트 vs 전체 비교 요약 ─────────────────────
    print("=" * 60)
    print("6. 화이트리스트 vs 전체 — 종합 비교")
    print("=" * 60)
    if ev_all and ev_wl:
        ev_diff = ev_wl['ev'] - ev_all['ev']
        wr_diff = ev_wl['p_win'] - ev_all['p_win']
        print(f"  승률 차이:  {ev_all['p_win']*100:.1f}%  →  {ev_wl['p_win']*100:.1f}%  ({wr_diff*100:+.1f}%p)")
        print(f"  EV 차이:    {ev_all['ev']:+.3f}%  →  {ev_wl['ev']:+.3f}%  ({ev_diff:+.3f}%p)")

        # 기회비용: 화이트리스트 코인만 거래하면 얼마나 놓치나
        all_day_rows = con.execute("""
            SELECT DATE(detected_at) AS day, COUNT(*) AS events
            FROM pump_log WHERE max_drop_pct IS NOT NULL
            GROUP BY day ORDER BY day
        """).fetchall()
        all_avg_day = (sum(r['events'] for r in all_day_rows) / len(all_day_rows)
                       if all_day_rows else 0)

        print(f"\n  하루 거래 기회:")
        print(f"    전체 코인:           {all_avg_day:.1f}건/일")
        print(f"    화이트리스트만:       {wl_avg_per_day:.1f}건/일")
        if all_avg_day > 0:
            coverage = wl_avg_per_day / all_avg_day * 100
            print(f"    기회 커버리지:        {coverage:.1f}%")

        print()
        if ev_wl['ev'] > ev_all['ev']:
            print("  [결론] 화이트리스트 전략이 EV 기준 유리 ✓")
            print(f"  EV {ev_diff:+.3f}%p 개선. 단, 거래 횟수 감소로 총 누적 수익은 별도 검증 필요.")
        elif abs(ev_diff) < 0.05:
            print("  [결론] 화이트리스트 전략의 EV 차이 미미 (0.05%p 미만)")
            print("  → 데이터 추가 축적 후 재평가 권장")
        else:
            print("  [결론] 화이트리스트 전략이 EV 기준 불리 ✗")
            print("  → 반등률 기준이 너무 엄격하거나 샘플이 부족할 수 있음")
    print()

    # ── 7. 권장 화이트리스트 ────────────────────────────────
    print("=" * 60)
    print("7. 권장 화이트리스트 코인 목록")
    print("=" * 60)
    if wl_coins:
        for c in wl_coins:
            print(f"  - {c}")
    else:
        print("  현재 데이터로는 조건 만족 코인 없음")
        print("  → 데이터 축적 후 재실행 권장 (목표: 코인당 10건 이상)")

    print()
    print("─" * 60)
    print("분석 완료. pump_log 기반 / 수수료 0.5% 반영.")
    con.close()


def _analyze_bounce(con):
    """max_drop_pct 없을 때 bounce_after 로만 분석."""
    print("=== bounce_after 기반 분석 (max_drop_pct 미수집) ===")
    rows = con.execute("""
        SELECT coin, COUNT(*) AS cnt,
               SUM(bounce_after) AS bounces,
               ROUND(100.0*SUM(bounce_after)/COUNT(*),1) AS bounce_pct,
               ROUND(AVG(pump_pct),2) AS avg_pump
        FROM pump_log
        GROUP BY coin ORDER BY bounce_pct DESC, cnt DESC
    """).fetchall()
    print(f"{'코인':<10} {'이벤트':>8} {'반등건':>7} {'반등률%':>9} {'평균펌핑%':>10}")
    print("-" * 50)
    for r in rows:
        print(f"{r['coin']:<10} {r['cnt']:>8} {r['bounces']:>7} {r['bounce_pct']:>9.1f} {r['avg_pump']:>10.1f}")

    # 전체 통계
    total = con.execute("SELECT COUNT(*), SUM(bounce_after), ROUND(AVG(pump_pct),2) FROM pump_log").fetchone()
    print(f"\n전체: {total[0]}건, 반등 {total[1]}건 ({100*total[1]/total[0]:.1f}%), 평균펌핑 {total[2]}%")
    print("\n[주의] max_drop_pct 가 수집되면 낙폭 기반 EV 계산이 가능해집니다.")


if __name__ == "__main__":
    run()
