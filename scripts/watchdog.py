"""
Watchdog — keeps alt_monitor.py and tg_bot.py alive.
Restarts either process if it dies. Sends Telegram alert on restart.

Run: python scripts/watchdog.py
"""
import sys
import time
import subprocess
import logging
import requests
import yaml
from pathlib import Path
from datetime import datetime, date, timezone, timedelta

KST = timezone(timedelta(hours=9))

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WD][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "watchdog.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

CHECK_INTERVAL = 30  # 초마다 프로세스 확인

BOTS = {
    "alt_monitor": ROOT / "scripts" / "alt_monitor.py",
    "tg_bot":      ROOT / "scripts" / "tg_bot.py",
}


def send_tg(text: str) -> None:
    try:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        tg = cfg.get("telegram", {})
        token = tg.get("bot_token", "")
        chat_id = str(tg.get("chat_id", ""))
        if token and chat_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
    except Exception:
        pass


def start_bot(name: str, script: Path) -> subprocess.Popen:
    log.info(f"[{name}] 시작")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(ROOT),
    )
    return proc


def write_session(target_date: str | None = None) -> None:
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "session_writer.py")]
            + ([target_date] if target_date else []),
            cwd=str(ROOT), timeout=30,
        )
    except Exception as e:
        log.warning(f"[session_writer] 실패: {e}")


def main() -> None:
    log.info("=== 워치독 시작 ===")
    send_tg("🐕 워치독 시작 — 봇 자동 재시작 감시 중")

    # 시작 시 오늘 세션 로그 생성
    write_session()
    last_date = datetime.now(KST).date()

    procs: dict[str, subprocess.Popen] = {}
    for name, script in BOTS.items():
        procs[name] = start_bot(name, script)
        time.sleep(2)

    while True:
        time.sleep(CHECK_INTERVAL)

        # 날짜 바뀌면 전날 마무리 + 오늘 파일 생성
        today = datetime.now(KST).date()
        if today != last_date:
            write_session(last_date.isoformat())  # 전날 최종 기록
            write_session()                        # 오늘 새 파일
            last_date = today
            log.info(f"[session] 날짜 변경 → {today} 세션 생성")

        for name, script in BOTS.items():
            proc = procs[name]
            if proc.poll() is not None:  # 프로세스 종료됨
                log.warning(f"[{name}] 죽음 감지 (exit={proc.returncode}) → 재시작")
                send_tg(f"⚠️ <b>{name}</b> 종료됨 → 자동 재시작")
                write_session()  # 재시작 시점 기록 업데이트
                time.sleep(2)
                procs[name] = start_bot(name, script)


if __name__ == "__main__":
    main()
