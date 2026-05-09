"""
Daily scheduler — runs alongside auto_trade.py.
At midnight: runs daily_learn.py to tune parameters.
At 09:00: prints daily status report.

Run: python scripts/scheduler.py  (separate terminal)
"""
import sys
import time
import logging
import subprocess
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.db import init_db, get_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/scheduler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def run_daily_learn():
    log.info("=== 일별 학습 엔진 실행 ===")
    result = subprocess.run(
        [sys.executable, "scripts/daily_learn.py"],
        capture_output=True, text=True, encoding="utf-8"
    )
    if result.stdout:
        log.info(result.stdout.strip())
    if result.returncode != 0:
        log.error(f"학습 실패: {result.stderr}")


def print_morning_report():
    stats = get_stats(days=1)
    if stats.get("count", 0) == 0:
        log.info("[모닝 리포트] 어제 거래 없음")
    else:
        log.info(
            f"[모닝 리포트] 어제 | "
            f"거래={stats['count']}건 승률={stats['win_rate']*100:.1f}% "
            f"PnL={stats['total_pnl']:+,.0f}원"
        )


def run():
    init_db()
    log.info("=== 스케줄러 시작 ===")

    last_learn_date = None
    last_report_date = None

    while True:
        now = datetime.now()
        today = date.today()

        # 자정 (00:00~00:01): 학습 엔진 실행
        if now.hour == 0 and now.minute == 0 and last_learn_date != today:
            run_daily_learn()
            last_learn_date = today

        # 오전 9시 (09:00~09:01): 모닝 리포트
        if now.hour == 9 and now.minute == 0 and last_report_date != today:
            print_morning_report()
            last_report_date = today

        time.sleep(60)  # 1분마다 체크


if __name__ == "__main__":
    run()
