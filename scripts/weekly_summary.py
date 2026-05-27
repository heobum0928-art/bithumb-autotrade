"""
Rolling 7-day summary writer.
DB에서 최근 7일 데이터를 읽어 docs/WEEKLY.md 자동 생성.
watchdog.py가 날짜 변경 시 자동 호출.
"""
import sqlite3
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT     = Path(__file__).parent.parent
DB_PATH  = ROOT / "data" / "trades.db"
OUT_FILE = ROOT / "docs" / "WEEKLY.md"


def run() -> None:
    if not DB_PATH.exists():
        return

    now_kst = datetime.now(KST)
    today   = now_kst.date()
    week_ago = (today - timedelta(days=6)).isoformat()  # 오늘 포함 7일
    today_s  = today.isoformat()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # ── 1. 일별 거래 요약 ──────────────────────────────────────────────
    cur.execute("""
        SELECT date, COUNT(*) as cnt,
               SUM(CASE WHEN pnl_krw > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pnl_krw), 0) as pnl
        FROM trades
        WHERE date >= ? AND date <= ?
        GROUP BY date ORDER BY date
    """, (week_ago, today_s))
    daily_trades = cur.fetchall()  # (date, cnt, wins, pnl)

    # 7일 합계
    total_cnt  = sum(r[1] for r in daily_trades)
    total_wins = sum(r[2] for r in daily_trades)
    total_pnl  = sum(r[3] for r in daily_trades)
    win_rate   = total_wins / total_cnt * 100 if total_cnt else 0

    # ── 2. 차단 사유 TOP5 ─────────────────────────────────────────────
    cur.execute("""
        SELECT skip_reason, COUNT(*) as cnt
        FROM signal_log
        WHERE date(entered_at) >= ? AND skip_reason IS NOT NULL
        GROUP BY skip_reason ORDER BY cnt DESC LIMIT 5
    """, (week_ago,))
    block_reasons = cur.fetchall()

    # ── 3. pump_log 분포 ──────────────────────────────────────────────
    cur.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN peak_at_sec >= 100 THEN 1 ELSE 0 END) as long_peak,
               SUM(CASE WHEN peak_at_sec > 0 AND peak_at_sec < 100 THEN 1 ELSE 0 END) as short_peak,
               ROUND(AVG(CASE WHEN peak_at_sec > 0 THEN peak_at_sec END), 0) as avg_peak
        FROM pump_log
        WHERE date(detected_at) >= ?
    """, (week_ago,))
    pump_stat = cur.fetchone()  # (total, long, short, avg)

    # ── 4. 주목 코인 (신호 많이 잡힌 상위 5) ─────────────────────────
    cur.execute("""
        SELECT coin, COUNT(*) as cnt,
               SUM(CASE WHEN entry_type='regular' THEN 1 ELSE 0 END) as entered
        FROM signal_log
        WHERE date(entered_at) >= ?
        GROUP BY coin ORDER BY cnt DESC LIMIT 5
    """, (week_ago,))
    top_coins = cur.fetchall()

    # ── 5. 손실 코인 TOP3 ─────────────────────────────────────────────
    cur.execute("""
        SELECT coin, COUNT(*) as cnt, ROUND(SUM(pnl_krw), 0) as pnl
        FROM trades
        WHERE date >= ? AND pnl_krw < 0
        GROUP BY coin ORDER BY pnl ASC LIMIT 3
    """, (week_ago,))
    loss_coins = cur.fetchall()

    # ── 6. oversold_log 현황 ──────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM oversold_log")
    os_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM oversold_log WHERE entered=1")
    os_entered = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*), ROUND(AVG(outcome_5m), 2)
        FROM oversold_log WHERE entered=1 AND outcome_5m IS NOT NULL
    """)
    os_resolved = cur.fetchone()  # (cnt, avg_5m)

    # ── 7. 누적 전체 ──────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN pnl_krw>0 THEN 1 ELSE 0 END), ROUND(SUM(pnl_krw),0) FROM trades")
    all_stat = cur.fetchone()

    # ── 8. 패턴 분석 (누적 전체 pump_log 기반) ───────────────────────

    # pump_pct 구간별 WIN율 (peak≥100s 기준)
    cur.execute("""
        SELECT ROUND(pump_pct, 0) as pct,
               COUNT(*) as total,
               SUM(CASE WHEN peak_at_sec >= 100 THEN 1 ELSE 0 END) as win,
               ROUND(AVG(CASE WHEN peak_at_sec >= 100 THEN 1.0 ELSE 0.0 END)*100, 0) as wr
        FROM pump_log
        WHERE peak_at_sec IS NOT NULL AND peak_at_sec > 0
          AND pump_pct BETWEEN 3 AND 12
        GROUP BY pct HAVING total >= 5
        ORDER BY pct
    """)
    pump_pct_dist = cur.fetchall()

    # 시간대별 WIN율 (건수 5건 이상만)
    cur.execute("""
        SELECT CAST(strftime('%H', detected_at, 'localtime') AS INTEGER) as hr,
               COUNT(*) as total,
               ROUND(AVG(CASE WHEN peak_at_sec >= 100 THEN 1.0 ELSE 0.0 END)*100, 0) as wr
        FROM pump_log
        WHERE peak_at_sec IS NOT NULL AND peak_at_sec > 0
        GROUP BY hr HAVING total >= 5
        ORDER BY hr
    """)
    hour_dist = cur.fetchall()

    # signal_log outcome_5m 기반 RSI 구간별 수익률
    cur.execute("""
        SELECT CASE
                 WHEN rsi < 50 THEN '<50'
                 WHEN rsi < 60 THEN '50~60'
                 WHEN rsi < 70 THEN '60~70'
                 WHEN rsi < 80 THEN '70~80'
                 ELSE '80+'
               END as rsi_bucket,
               COUNT(*) as total,
               ROUND(AVG(outcome_5m), 2) as avg5m,
               SUM(CASE WHEN outcome_5m > 0 THEN 1 ELSE 0 END) as pos
        FROM signal_log
        WHERE outcome_5m IS NOT NULL AND rsi IS NOT NULL
        GROUP BY rsi_bucket HAVING total >= 10
        ORDER BY MIN(rsi)
    """)
    rsi_dist = cur.fetchall()

    con.close()

    # ── 마크다운 생성 ─────────────────────────────────────────────────
    lines = []
    lines.append(f"# WEEKLY — 최근 7일 요약")
    lines.append(f"")
    lines.append(f"*갱신: {now_kst.strftime('%Y-%m-%d %H:%M')} KST*")
    lines.append(f"")

    # 7일 성과
    pnl_sign = f"{total_pnl:+,.0f}"
    lines.append(f"## 7일 성과 ({week_ago} ~ {today_s})")
    lines.append(f"")
    lines.append(f"| 항목 | 값 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 거래 | {total_cnt}건 (승{total_wins} 패{total_cnt-total_wins}) |")
    lines.append(f"| 승률 | {win_rate:.0f}% |")
    lines.append(f"| PnL  | **{pnl_sign}원** |")
    lines.append(f"")

    # 일별 상세
    lines.append(f"### 일별 상세")
    lines.append(f"")
    lines.append(f"| 날짜 | 거래 | 승/패 | PnL |")
    lines.append(f"|------|------|-------|-----|")
    # 7일 전체 날짜 채우기 (거래 없는 날도 표시)
    trade_map = {r[0]: r for r in daily_trades}
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        r = trade_map.get(d)
        if r:
            sign = "+" if r[3] >= 0 else ""
            lines.append(f"| {d} | {r[1]}건 | {r[2]}승{r[1]-r[2]}패 | {sign}{r[3]:,.0f}원 |")
        else:
            lines.append(f"| {d} | 0건 | — | — |")
    lines.append(f"")

    # 누적 전체
    if all_stat[0]:
        all_wr = all_stat[1] / all_stat[0] * 100
        lines.append(f"### 누적 전체")
        lines.append(f"")
        lines.append(f"- {all_stat[0]}건 | 승률 {all_wr:.0f}% | PnL **{all_stat[2]:+,.0f}원**")
        lines.append(f"")

    # 차단 사유
    lines.append(f"## 신호 차단 TOP5")
    lines.append(f"")
    lines.append(f"| 사유 | 건수 |")
    lines.append(f"|------|------|")
    for r in block_reasons:
        lines.append(f"| {r[0]} | {r[1]}건 |")
    lines.append(f"")

    # pump_log
    p = pump_stat
    lines.append(f"## 펌프 패턴 (7일)")
    lines.append(f"")
    lines.append(f"- 감지: {p[0]}건 | peak≥100s(WIN패턴): {p[1]}건 | peak<100s(LOSE패턴): {p[2]}건")
    if p[3]:
        lines.append(f"- 평균 peak_at_sec: {p[3]:.0f}s")
    lines.append(f"")

    # 주목 코인
    lines.append(f"## 신호 상위 코인 (7일)")
    lines.append(f"")
    lines.append(f"| 코인 | 신호 | 진입 |")
    lines.append(f"|------|------|------|")
    for r in top_coins:
        lines.append(f"| {r[0]} | {r[1]}건 | {r[2]}건 |")
    lines.append(f"")

    # 손실 코인
    if loss_coins:
        lines.append(f"## 손실 코인 TOP3 (7일)")
        lines.append(f"")
        lines.append(f"| 코인 | 거래 | PnL |")
        lines.append(f"|------|------|-----|")
        for r in loss_coins:
            lines.append(f"| {r[0]} | {r[1]}건 | {r[2]:,.0f}원 |")
        lines.append(f"")

    # oversold_log
    lines.append(f"## 과매도 반등 전략 데이터 수집 현황")
    lines.append(f"")
    lines.append(f"- watching 감지: {os_total}건 | 진입: {os_entered}건")
    if os_resolved[0]:
        avg5 = f"{os_resolved[1]:+.1f}%" if os_resolved[1] is not None else "집계중"
        lines.append(f"- 5m 결과 있음: {os_resolved[0]}건 | 평균 5m 수익: {avg5}")
    lines.append(f"- 목표: 20건+ 진입 후 승률 분석 (현재 {os_entered}/20)")
    lines.append(f"")

    # 전략 동결 상태
    lines.append(f"## 전략 현황")
    lines.append(f"")
    lines.append(f"- 4주 파라미터 동결: ~2026-06-22")
    lines.append(f"- 메인 필터: RSI 45~75 | MIN_PUMP_AGE_SEC=100s | DEAD_HOURS={{6,7,11~15,18}}")
    lines.append(f"- 과매도 반등: RSI<25 watching → RSI≥30×2캔들 + MACD↑ + 거래량1.5x → 진입")
    lines.append(f"- 즉시진입: META, WNCG")
    lines.append(f"")

    # ── 패턴 분석 (누적 데이터 기반) ────────────────────────────────
    lines.append(f"## 패턴 분석 (누적 pump_log 전체 기반)")
    lines.append(f"")

    # pump_pct vs WIN율
    lines.append(f"### 펌프 강도(pump_pct) vs WIN율")
    lines.append(f"")
    lines.append(f"| pump_pct | 건수 | WIN(peak≥100s) | WIN율 |")
    lines.append(f"|----------|------|----------------|-------|")
    for r in pump_pct_dist:
        marker = " ★" if r[3] >= 60 else (" ✗" if r[3] <= 25 else "")
        lines.append(f"| +{r[0]:.0f}% | {r[1]}건 | {r[2]}건 | {r[3]:.0f}%{marker} |")
    lines.append(f"")

    # 시간대별 WIN율
    lines.append(f"### 시간대별 WIN율")
    lines.append(f"")
    lines.append(f"| 시간 | 건수 | WIN율 | 판정 |")
    lines.append(f"|------|------|-------|------|")
    dead = {6, 7, 11, 12, 13, 14, 15, 18}
    for r in hour_dist:
        hr, total, wr = r
        if wr >= 70:
            verdict = "✅ 좋음"
        elif wr <= 25:
            verdict = "❌ 나쁨"
        else:
            verdict = "—"
        blocked = " [차단중]" if hr in dead else ""
        lines.append(f"| {hr:02d}시{blocked} | {total}건 | {wr:.0f}% | {verdict} |")
    lines.append(f"")

    # RSI 구간별 outcome_5m
    if rsi_dist:
        lines.append(f"### RSI 구간별 신호 후 5m 수익률")
        lines.append(f"")
        lines.append(f"| RSI 구간 | 건수 | 평균 5m | 양수비율 |")
        lines.append(f"|----------|------|---------|----------|")
        for r in rsi_dist:
            pos_rate = r[3] / r[1] * 100 if r[1] else 0
            lines.append(f"| {r[0]} | {r[1]}건 | {r[2]:+.1f}% | {pos_rate:.0f}% |")
        lines.append(f"")

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"WEEKLY.md 갱신 완료 ({now_kst.strftime('%Y-%m-%d %H:%M')} KST)")


if __name__ == "__main__":
    run()
