"""
업비트 공지 감지지연 측정기 (upbit_notice_monitor) — 순수 로깅, 매매 0.

목적(2026-07-02): "업비트 상장공지 → 빗썸 기상장코인 급등" 하이브리드 후보 검증.
백테스트(에이전트) 결론: 효과는 실재(5/5 반응, 60분 피크 평균+59%)하나 엣지는 공지 후
첫 60초에 집중 — 1분 지연되면 기대수익 소멸. 봇을 만들기 전에 "우리가 실제로 몇 초 만에
공지를 감지할 수 있는지"부터 측정해야 함(검증 우선 원칙).

동작: 업비트 공지 API(api-manager.upbit.com) POLL_SEC마다 폴링 → 신규 공지 id 감지 →
서버 표기시각(listed_at)과 우리 감지시각의 차이(delay_sec) 기록.
신규상장/거래지원 관련 공지는 텔레그램 알림(속도 체감용), 나머지는 CSV만.

★ 순수 측정: 매매 API 미호출. 포트 47241.
상태 data/upbit_notice_known.json | 기록 data/upbit_notice_events.csv | 로그 logs/upbit_notice_monitor.log
Run: python scripts/upbit_notice_monitor.py
"""
import sys, os, atexit, time, json, csv, socket, logging, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
KST = timezone(timedelta(hours=9))

_sock = None
def _single():
    global _sock
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM); _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try: _sock.bind(("127.0.0.1", 47241))
    except OSError: print("[ERROR] upbit_notice_monitor 이미 실행 중 (포트 47241)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [UPNOTI] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/upbit_notice_monitor.log", encoding="utf-8")])
log = logging.getLogger(__name__)

API = "https://api-manager.upbit.com/api/v1/announcements"
POLL_SEC = 3          # 폴링 주기 (측정 목적 — 실전에선 API 부담과 트레이드오프 판단 필요)
KEYWORDS = ["원화 마켓 추가", "거래지원 안내", "마켓 추가", "신규 거래지원", "KRW 마켓"]
KNOWN = ROOT / "data" / "upbit_notice_known.json"
CSV_PATH = ROOT / "data" / "upbit_notice_events.csv"


def load_known():
    try: return set(json.loads(KNOWN.read_text(encoding="utf-8")))
    except Exception: return set()


def save_known(s):
    tmp = KNOWN.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(s)), encoding="utf-8")
    os.replace(tmp, KNOWN)


def logrow(row):
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["detected_at", "notice_id", "listed_at", "delay_sec", "is_listing", "title"])
        w.writerow(row)


def fetch_notices(per_page=15):
    r = requests.get(API, params={"os": "web", "page": 1, "per_page": per_page, "category": "all"}, timeout=8)
    r.raise_for_status()
    return r.json().get("data", {}).get("notices", [])


def main():
    known = load_known()
    first_run = not known
    log.info(f"업비트 공지 감지지연 측정기 시작 — {POLL_SEC}초 폴링 | 기존공지 {len(known)}건 | 순수측정(매매0)")
    try:
        from bithumb import notify
        notify.send(f"📡 업비트 공지 감지지연 측정기 시작 — {POLL_SEC}초 폴링. 상장공지 하이브리드 후보 검증용, 매매0")
    except Exception: pass

    while True:
        try:
            notices = fetch_notices()
            if first_run:
                known = {str(n["id"]) for n in notices}
                save_known(known)
                first_run = False
                log.info(f"baseline 등록 {len(known)}건 (알림 생략)")
                time.sleep(POLL_SEC); continue

            new_ones = [n for n in notices if str(n["id"]) not in known]
            for n in reversed(new_ones):  # 오래된 것부터
                nid = str(n["id"])
                known.add(nid)
                detected = datetime.now(KST)
                title = n.get("title", "")
                try:
                    listed = datetime.fromisoformat(n.get("listed_at", ""))
                except Exception:
                    listed = None
                delay = (detected - listed).total_seconds() if listed else None
                is_listing = any(k in title for k in KEYWORDS)
                logrow([detected.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], nid,
                        n.get("listed_at", ""), f"{delay:.1f}" if delay is not None else "", is_listing, title])
                tag = "🆕상장" if is_listing else "공지"
                log.warning(f"{tag} 감지 [{nid}] delay={delay:.1f}s '{title}'" if delay is not None else f"{tag} 감지 [{nid}] '{title}'")
                if is_listing:
                    try:
                        from bithumb import notify
                        notify.send(f"🆕 업비트 상장관련 공지 감지! delay={delay:.1f}초\n{title}")
                    except Exception: pass
            if new_ones:
                save_known(known)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
