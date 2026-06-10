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
    "tg_bot":                ROOT / "scripts" / "tg_bot.py",
    "claude_intelligence":   ROOT / "scripts" / "claude_intelligence.py",  # CI Mode
    "swing_monitor":         ROOT / "scripts" / "swing_monitor.py",    # 스윙 MA 알림
    "vb_trader":             ROOT / "scripts" / "vb_trader.py",        # 변동성 돌파 실거래
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


EXTRA_ARGS: dict[str, list[str]] = {
    "claude_screener_dry":   ["--dry-run"],
    "claude_screener_watch": ["--watch-mode"],
    "swing_monitor":         ["--loop"],
    "vb_trader":             ["--live"],      # 실거래 모드
    "claude_intelligence":   [],              # 2026-06-10 dry-run 전환 — 검증 전 실거래 금지 원칙
}

# 인스턴스 식별용 kill 키워드 매핑
KILL_KEYWORDS: dict[str, str] = {
    "claude_screener_dry":   "--dry-run",
    "claude_screener_watch": "--watch-mode",
    "vb_trader":             "--dry-run",    # vb_trader.py --dry-run 프로세스 식별용
}


def kill_existing(name: str) -> None:
    extra_kw = KILL_KEYWORDS.get(name)
    script_kw = "claude_screener.py" if "screener" in name else f"{name}.py"
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            parts = p.info["cmdline"] or []
            # 스크립트 파일명이 독립 인자로 있을 때만 매칭 (문자열 내부 포함 방지)
            match = any(part.endswith(script_kw) for part in parts)
            if extra_kw:
                match = match and extra_kw in parts
            if match and p.pid != os.getpid():
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
    time.sleep(3)  # 기존 프로세스 완전 종료 + lockfile 정리 대기
    # lockfile 강제 삭제 (atexit 미실행으로 남은 경우)
    if name == "alt_monitor":
        for lf in [ROOT / "data" / "alt_monitor.pid", ROOT / "data" / "bot.lock"]:
            try:
                lf.unlink(missing_ok=True)
            except Exception:
                pass
    log.info(f"[{name}] 시작")
    extra = EXTRA_ARGS.get(name, [])
    proc = subprocess.Popen(
        [sys.executable, str(script)] + extra,
        cwd=str(ROOT),
    )
    time.sleep(5)  # 새 프로세스가 lockfile 쓸 시간 확보
    return proc


def write_weekly() -> None:
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "weekly_summary.py")],
            cwd=str(ROOT), timeout=30,
        )
    except Exception as e:
        log.warning(f"[weekly_summary] 실패: {e}")


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
    # 기존 워치독 프로세스 모두 종료 후 인수인계 (중복 재시작 루프 방지)
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            parts = p.info["cmdline"] or []
            if any(str(c).endswith("watchdog.py") for c in parts) and p.pid != os.getpid():
                log.warning(f"기존 워치독 PID {p.pid} 종료")
                p.terminate()
                try:
                    p.wait(timeout=5)
                except psutil.TimeoutExpired:
                    p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    LOCKFILE.write_text(str(os.getpid()))
    atexit.register(lambda: LOCKFILE.unlink(missing_ok=True))

    log.info("=== 워치독 시작 ===")
    send_tg("🐕 워치독 시작 — 봇 자동 재시작 감시 중")

    # 시작 시 오늘 세션 로그 + 주간 요약 생성
    write_session()
    write_weekly()
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
            write_weekly()                         # 7일 롤링 요약 갱신
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
