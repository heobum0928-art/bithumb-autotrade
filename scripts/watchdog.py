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
    # "vb_trader" 제거 (2026-06-25): forward t=-4.29, 승률17% — 폐기 확정
    "retest_trader":         ROOT / "scripts" / "retest_trader.py",    # 돌파-재테스트 전략 B (모의 검증 중)
    "em_trader":             ROOT / "scripts" / "em_trader.py",        # #24 초기모멘텀 (약세장 전용·모의, forward 게이트 검증)
    "igniter_alert":         ROOT / "scripts" / "igniter_alert.py",    # #31 ML 점화 알림 (알림전용·주문없음, forward 추적)
    "ml_trader":             ROOT / "scripts" / "ml_trader.py",        # #31 ML 점화 모의매매 (게이트 검증)
    "core_trader":           ROOT / "scripts" / "core_trader.py",      # 코어 BTC 사이클타이밍 (검증엔진·모의 추적)
    "hybrid_trader":         ROOT / "scripts" / "hybrid_trader.py",    # 하이브리드 약세현금/강세 BTC50%+알트Top3 (강세 forward 검증·모의)
    "crossex_logger":        ROOT / "scripts" / "crossex_logger.py",   # 교차거래소 선행신호 로거 (순수로깅·매매0, 격리)
    "volume_radar":          ROOT / "scripts" / "volume_radar.py",     # 거래대금 급증 레이더 (순수로깅·매매0, 격리)
    "accum_trader":          ROOT / "scripts" / "accum_trader.py",     # 매집 단타 (live_guard 제어, 약세장 역추세 실전)
    "newlisting_monitor":    ROOT / "scripts" / "newlisting_monitor.py", # 신규상장 감지·궤적캡처 (순수로깅, 첫펌프 데이터)
    "rsi_trader":            ROOT / "scripts" / "rsi_trader.py",       # RSI 과매도반등 (검증된 첫 후보·모의 실측)
    "cascade_trader":        ROOT / "scripts" / "cascade_trader.py",   # 캐스케이드-반등 (#40, walk-forward 통과·모의 실측)
    "futures_logger":        ROOT / "scripts" / "futures_logger.py",   # Binance 선물 펀딩/OI/롱숏 폴링 (순수로깅·매매0, cascade 하이브리드 외부신호 #1, 청산WS 한국차단→REST대체)
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
    # vb_trader 제거됨
    "retest_trader":         ["--dry-run"],   # 합격선(모의30건+, 평균>0) 통과 전 실거래 금지
    "em_trader":             ["--dry-run"],   # #24 게이트(CLEAN n≥50, 비용0.30%후 t≥3, 강건성) 통과 전 실거래 금지
    "ml_trader":             ["--dry-run"],   # #31 게이트(CLEAN n≥30, 비용0.30%후 t≥2.5, 베이스라인 초과) 통과 전 실거래 금지
    "core_trader":           ["--dry-run"],   # 코어 BTC타이밍 모의 — 실거래는 사용자 승인
    "hybrid_trader":         ["--dry-run"],   # 하이브리드 모의 — 강세 forward 데이터 수집 전 실거래 금지
    "claude_intelligence":   [],              # 2026-06-10 dry-run 전환 — 검증 전 실거래 금지 원칙
}

# 인스턴스 식별용 kill 키워드 매핑
KILL_KEYWORDS: dict[str, str] = {
    "claude_screener_dry":   "--dry-run",
    "claude_screener_watch": "--watch-mode",
    # vb_trader: 키워드 제거 (2026-06-10) — "--dry-run" 키워드 잔존으로 --live 인스턴스를
    # 못 죽여 싱글톤 포트 충돌 → 재시작 크래시 루프 발생했던 버그 수정
}


def kill_existing(name: str) -> None:
    extra_kw = KILL_KEYWORDS.get(name)
    script_kw = "claude_screener.py" if "screener" in name else f"{name}.py"
    for p in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            if "python" not in (p.info["name"] or "").lower():
                continue  # 셸 래퍼 오인 종료 방지
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
    # CREATE_NO_WINDOW: 콘솔창 안 띄움(봇은 파일로 로깅 → stdout 불필요). 창 클러터 방지.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [sys.executable, str(script)] + extra,
        cwd=str(ROOT),
        creationflags=flags,
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


_wd_sock = None  # GC 방지


def _acquire_singleton() -> None:
    """포트 바인딩 싱글톤 — 이미 watchdog가 포트를 점유 중이면 새 인스턴스가
    스스로 종료한다(기존을 살림). 과거 '포트 못 잡으면 기존을 죽이고 차지' 방식은
    동시 기동 시 상호 킬 레이스로 watchdog가 여러 개 공존하는 버그가 있었음
    (2026-06-14: PC 재부팅 누적으로 watchdog 5개 → 각자 봇을 띄워 봇 중복 사건).
    PC 재부팅 시엔 OS가 포트를 회수하므로 새 watchdog가 정상적으로 점유한다."""
    import socket
    global _wd_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        s.bind(("127.0.0.1", 47230))  # watchdog 전용 포트
        s.listen(1)                   # listen까지 해서 확실히 점유 (LISTEN 상태로 가시화)
        _wd_sock = s
        atexit.register(s.close)
    except OSError:
        s.close()
        log.warning("포트 47230 이미 사용 중 — watchdog 이미 실행 중이므로 새 인스턴스 종료")
        sys.exit(0)


def main() -> None:
    _acquire_singleton()
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
