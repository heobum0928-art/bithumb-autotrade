"""
Watchdog — keeps alt_monitor.py and tg_bot.py alive.
Restarts either process if it dies. Sends Telegram alert on restart.

Run: python scripts/watchdog.py
"""
import sys
import os
import atexit
import time
import subprocess
import logging
import requests
import yaml
import psutil
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


def kill_existing(name: str) -> None:
    keyword = f"{name}.py"
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if keyword in cmd and p.pid != os.getpid():
                log.warning(f"[{name}] 기존 PID {p.pid} 종료")
                p.terminate()
                try:
                    p.wait(timeout=5)
                except psutil.TimeoutExpired:
                    p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def start_bot(name: str, script: Path) -> subprocess.Popen:
    kill_existing(name)
    time.sleep(1)
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


def run_ai_analyze() -> None:
    out = ROOT / "docs" / f"ai_analysis_{date.today().isoformat()}.md"
    if out.exists():
        log.info("[ai_analyze] 오늘 분석 이미 존재, 스킵")
        return
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ai_analyze.py")],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", timeout=120,
        )
        if result.returncode == 0 and out.exists():
            summary = result.stdout[-600:].strip()
            send_tg(f"📊 AI 분석 완료\n\n{summary}")
            log.info("[ai_analyze] 완료")
        else:
            send_tg(f"❌ AI 분석 실패\n{result.stderr[-300:]}")
            log.warning(f"[ai_analyze] 실패: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        send_tg("❌ AI 분석 타임아웃 (120s)")
        log.warning("[ai_analyze] 타임아웃")
    except Exception as e:
        log.warning(f"[ai_analyze] 예외: {e}")


LOCKFILE = ROOT / "data" / "watchdog.pid"


def main() -> None:
    # 워치독 중복 실행 방지
    if LOCKFILE.exists():
        try:
            old_pid = int(LOCKFILE.read_text())
            if psutil.pid_exists(old_pid):
                log.error(f"워치독 이미 실행 중 (PID={old_pid}). 종료합니다.")
                sys.exit(1)
        except (ValueError, OSError):
            pass
    LOCKFILE.write_text(str(os.getpid()))
    atexit.register(lambda: LOCKFILE.unlink(missing_ok=True))

    log.info("=== 워치독 시작 ===")
    send_tg("🐕 워치독 시작 — 봇 자동 재시작 감시 중")

    # 시작 시 오늘 세션 로그 생성
    write_session()
    last_date = datetime.now(KST).date()

    last_analysis_date: date | None = None

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

        # 매일 00:00 KST AI 분석 자동 실행
        now_kst = datetime.now(KST)
        if (last_analysis_date != today
                and now_kst.hour == 0 and now_kst.minute < 1):
            run_ai_analyze()
            last_analysis_date = today

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
