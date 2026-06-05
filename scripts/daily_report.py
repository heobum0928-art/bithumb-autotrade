"""매일 아침 실행하는 데이터 수집 현황 리포트."""

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.db import DB_PATH


def run():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    print("=" * 50)
    print(f"  빗썸 봇 일일 리포트  {today}")
    print("=" * 50)

    # ── 어제 펌핑 이벤트 ──
    cur.execute(
        "SELECT coin, pump_pct, detected_at FROM pump_log "
        "WHERE detected_at >= ? AND detected_at < ? ORDER BY detected_at",
        (yesterday + "T00:00", today + "T00:00"),
    )
    yesterday_pumps = cur.fetchall()

    print(f"\n어제 펌핑 감지: {len(yesterday_pumps)}건")
    if yesterday_pumps:
        for coin, pct, ts in yesterday_pumps:
            print(f"  {ts[11:16]}  {coin:>8s}  {pct:+.1f}%")
    else:
        print("  (없음)")

    # ── 어제 틱 데이터 ──
    cur.execute(
        "SELECT COUNT(*) FROM pump_ticks pt "
        "JOIN pump_log pl ON pt.pump_id = pl.id "
        "WHERE pl.detected_at >= ? AND pl.detected_at < ?",
        (yesterday + "T00:00", today + "T00:00"),
    )
    yesterday_ticks = cur.fetchone()[0]
    print(f"\n어제 틱 기록: {yesterday_ticks}건")

    # ── 누적 현황 ──
    cur.execute("SELECT COUNT(*) FROM pump_log")
    total_events = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM pump_ticks")
    total_ticks = cur.fetchone()[0]

    # 틱 있는 이벤트 수 (백테스트 가능한 이벤트)
    cur.execute("SELECT COUNT(DISTINCT pump_id) FROM pump_ticks")
    events_with_ticks = cur.fetchone()[0]

    goal = 30  # test 셋 최소 거래수 목표
    # train 70% / test 30% 분할 기준으로 필요한 총 이벤트 수
    needed_total = int(goal / 0.3) + 1  # 약 101건
    progress_pct = min(events_with_ticks / needed_total * 100, 100)

    print(f"\n누적 현황")
    print(f"  틱 있는 이벤트: {events_with_ticks}건 / 목표 약 {needed_total}건")
    bar_len = 30
    filled = int(bar_len * progress_pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"  [{bar}] {progress_pct:.0f}%")
    print(f"  총 틱: {total_ticks}건")

    # 남은 기간 추정
    cur.execute(
        "SELECT MIN(detected_at) FROM pump_ticks pt "
        "JOIN pump_log pl ON pt.pump_id = pl.id"
    )
    first_tick_ts = cur.fetchone()[0]
    if first_tick_ts and events_with_ticks > 0:
        first_dt = datetime.fromisoformat(first_tick_ts)
        days_elapsed = max((datetime.now() - first_dt).days, 1)
        rate = events_with_ticks / days_elapsed  # 하루 평균 이벤트
        remaining = max(needed_total - events_with_ticks, 0)
        if rate > 0:
            days_left = int(remaining / rate)
            print(f"  하루 평균: {rate:.1f}건 → 검증 가능까지 약 {days_left}일 남음")

    # ── 자주 나오는 코인 TOP 5 (틱 기준) ──
    cur.execute(
        "SELECT pl.coin, COUNT(DISTINCT pl.id) as cnt "
        "FROM pump_ticks pt JOIN pump_log pl ON pt.pump_id = pl.id "
        "GROUP BY pl.coin ORDER BY cnt DESC LIMIT 5"
    )
    top_coins = cur.fetchall()
    if top_coins:
        print(f"\n틱 있는 코인 TOP 5")
        for coin, cnt in top_coins:
            print(f"  {coin:>8s}  {cnt}건")

    # ── 봇 상태 ──
    import psutil
    lock = Path("data/bot.lock")
    pid = int(lock.read_text().strip()) if lock.exists() else None
    alive = psutil.pid_exists(pid) if pid else False
    status = "✓ 실행 중" if alive else "✗ 꺼짐 (start_bots.bat 실행 필요)"
    print(f"\n봇 상태: {status}")

    print("\n" + "=" * 50)
    conn.close()


if __name__ == "__main__":
    run()
