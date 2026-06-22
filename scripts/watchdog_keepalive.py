"""watchdog 자동복구 — 포트 47230 미점유(=watchdog 죽음) 시 watchdog를 분리 기동.

배경: 2026-06-22 전체 봇 다운 사건 — watchdog가 도구 세션에 묶여 같이 종료됐으나
아무 알림이 없었음(watchdog 죽으면 텔레그램도 침묵). watchdog는 봇을 살리지만
watchdog 자신을 살릴 주체가 없었음. 이 스크립트가 그 공백을 메운다.

Windows 작업 스케줄러에 'CoinbaseBot_Watchdog'로 5분마다 + 로그인 시 등록.
- 포트 47230이 이미 LISTEN(점유)이면 watchdog 정상 → 아무것도 안 함(watchdog 싱글톤이라
  중복 기동돼도 새 인스턴스가 스스로 종료하지만, 불필요한 churn 방지 위해 먼저 확인).
- 미점유면 DETACHED_PROCESS로 watchdog 기동 → 이 스크립트(작업)가 끝나도 watchdog는 생존.
"""
import socket, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 47230


def watchdog_alive() -> bool:
    """포트 47230 bind 시도 — 성공하면 미점유(죽음), 실패(주소 사용중)면 살아있음."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PORT))
        s.close()
        return False
    except OSError:
        return True


def main():
    if watchdog_alive():
        print("watchdog 정상 (포트 47230 점유 중)")
        return
    py = sys.executable  # 작업 스케줄러가 부른 pythonw/python
    # watchdog는 로깅에 stdout 쓰므로 pythonw가 아니라 python.exe로 띄움(단 창은 숨김)
    if py.lower().endswith("pythonw.exe"):
        cand = py[:-len("pythonw.exe")] + "python.exe"
        if Path(cand).exists(): py = cand
    flags = 0
    if sys.platform == "win32":
        # CREATE_NO_WINDOW: 창 숨김 + 콘솔은 있어 stdout 유효. NEW_PROCESS_GROUP: 부모 종료에 안 묶임.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [py, str(ROOT / "scripts" / "watchdog.py")],
        cwd=str(ROOT),
        creationflags=flags,
        close_fds=True,
    )
    print("watchdog 죽음 감지 → 창 숨김 기동 완료")


if __name__ == "__main__":
    main()
