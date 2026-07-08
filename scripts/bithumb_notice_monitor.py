"""
빗썸 공지 로거 (bithumb_notice_monitor) — 순수 로깅, 매매 0.

목적(2026-07-09): 동결펌핑(유의지정+입출금정지) 이벤트 스터디의 forward 표본 축적.
급등 부검(90일 상위 30건)에서 유의지정 공지 발행 분에 점화하는 펌핑 부류 발견(GNO +87/+133%,
SPURS +71.5%). 그러나 업비트 공지 13.5개월 전수 이벤트 스터디 결과 유의지정 31건 중 74%가
상폐 수순(생존편향) — 무조건 진입 EV 0. 펌프/덤프 사전 판별자를 찾으려면 forward 표본 필요.
빗썸 공지 API는 최신 5건만 반환(이력 복구 불가) → 지금부터 로깅해야 표본이 쌓임(월 2~3건).

동작: api.bithumb.com/v1/notices (최신 5건) POLL_SEC마다 폴링 → 신규 공지 감지 →
카테고리·감지지연 기록. 거래유의/입출금/마켓추가 공지는 텔레그램 알림.

★ 순수 측정: 매매 API 미호출. 포트 47248.
상태 data/bithumb_notice_known.json | 기록 data/bithumb_notice_events.csv | 로그 logs/bithumb_notice_monitor.log
Run: python scripts/bithumb_notice_monitor.py
"""
import sys, os, atexit, time, json, csv, socket, logging
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
    try: _sock.bind(("127.0.0.1", 47248))
    except OSError: print("[ERROR] bithumb_notice_monitor 이미 실행 중 (포트 47248)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [BTNOTI] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/bithumb_notice_monitor.log", encoding="utf-8")])
log = logging.getLogger(__name__)

API = "https://api.bithumb.com/v1/notices"
POLL_SEC = 5
# 알림 대상 카테고리 (전체 공지는 CSV에 항상 기록)
ALERT_CATEGORIES = {"거래유의", "입출금", "마켓 추가"}
CAUTION_KEYWORDS = ["거래유의종목 지정", "유의촉구", "입출금 일시 중단", "입출금 일시 중지", "거래지원 종료"]
KNOWN = ROOT / "data" / "bithumb_notice_known.json"
CSV_PATH = ROOT / "data" / "bithumb_notice_events.csv"


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
        if new: w.writerow(["detected_at", "notice_url", "published_at", "delay_sec", "categories", "is_caution", "title"])
        w.writerow(row)


def fetch_notices(count=5):
    r = requests.get(API, params={"count": count}, timeout=8)
    r.raise_for_status()
    return r.json()


def main():
    known = load_known()
    first_run = not known
    log.info(f"빗썸 공지 로거 시작 — {POLL_SEC}초 폴링 | 기존공지 {len(known)}건 | 순수측정(매매0)")
    try:
        from bithumb import notify
        notify.send(f"📡 빗썸 공지 로거 시작 — 동결펌핑 forward 표본 축적용, 매매0")
    except Exception: pass

    while True:
        try:
            notices = fetch_notices()
            if first_run:
                known = {str(n["pc_url"]) for n in notices}
                save_known(known)
                first_run = False
                log.info(f"baseline 등록 {len(known)}건 (알림 생략)")
                time.sleep(POLL_SEC); continue

            new_ones = [n for n in notices if str(n.get("pc_url", "")) not in known]
            for n in reversed(new_ones):  # 오래된 것부터
                url = str(n.get("pc_url", ""))
                known.add(url)
                detected = datetime.now(KST)
                title = n.get("title", "")
                cats = "|".join(n.get("categories", []) or [])
                try:
                    pub = datetime.strptime(n.get("published_at", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                    delay = (detected - pub).total_seconds()
                except Exception:
                    delay = None
                is_caution = any(k in title for k in CAUTION_KEYWORDS)
                logrow([detected.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], url,
                        n.get("published_at", ""), f"{delay:.1f}" if delay is not None else "", cats, is_caution, title])
                alert = is_caution or any(c in ALERT_CATEGORIES for c in (n.get("categories") or []))
                tag = "⚠️유의/동결" if is_caution else ("📌" if alert else "공지")
                log.warning(f"{tag} 감지 [{cats}] delay={delay:.1f}s '{title}'" if delay is not None else f"{tag} 감지 [{cats}] '{title}'")
                if alert:
                    try:
                        from bithumb import notify
                        notify.send(f"{tag} 빗썸 공지 감지 [{cats}]\n{title}\ndelay={delay:.1f}초" if delay is not None else f"{tag} 빗썸 공지 감지 [{cats}]\n{title}")
                    except Exception: pass
            if new_ones:
                save_known(known)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
