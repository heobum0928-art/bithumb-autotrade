"""
바이낸스 공지 감지지연 측정기 (binance_notice_monitor) — 순수 로깅, 매매 0.

목적(2026-07-03): "바이낸스 상장공지 → 빗썸 기상장코인 반응" 하이브리드 후보 검증.
백테스트(에이전트) 결론: 효과 실재하나 유효표본 4건(<5) → 판정 보류, 전향 검증 착수 권고.
업비트 버전과 차이: 반응이 30~60분까지 확산(업비트는 첫 60초 집중) — 감지지연 5분도
승자 케이스 +12~17% 순수익 유지. 감지 인프라 부담이 업비트보다 낮음.

동작: 바이낸스 CMS 공지 API(catalogId=48, New Cryptocurrency Listing) POLL_SEC마다 폴링
→ 신규 공지 id 감지 → 제목에서 코인 심볼 추출 → 빗썸 기상장 여부 확인 → 감지지연 기록.
"Will List"/"Will Add" 등 상장 키워드 공지는 텔레그램 알림, 나머지는 CSV만.

★ 순수 측정: 매매 API 미호출. 포트 47242.
상태 data/binance_notice_known.json | 기록 data/binance_notice_events.csv | 로그 logs/binance_notice_monitor.log
Run: python scripts/binance_notice_monitor.py
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
    try: _sock.bind(("127.0.0.1", 47242))
    except OSError: print("[ERROR] binance_notice_monitor 이미 실행 중 (포트 47242)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [BNNOTI] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/binance_notice_monitor.log", encoding="utf-8")])
log = logging.getLogger(__name__)

API = "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query"
HEADERS = {"User-Agent": "Mozilla/5.0"}
CATALOG_ID = 48       # New Cryptocurrency Listing
POLL_SEC = 20          # 업비트보다 반응이 느려(30~60분 확산) 폴링 여유 있음
KEYWORDS = ["Will List", "Will Add", "Adds", "Lists"]
LISTING_RE = re.compile(r"\(([A-Z0-9]{2,15})\)")   # 제목의 (SYMBOL) 패턴 추출
KNOWN = ROOT / "data" / "binance_notice_known.json"
CSV_PATH = ROOT / "data" / "binance_notice_events.csv"


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
        if new: w.writerow(["detected_at", "article_id", "release_at", "delay_sec", "is_listing", "symbol", "bithumb_listed", "title"])
        w.writerow(row)


def fetch_articles(page_size=15):
    r = requests.get(API, params={"type": 1, "pageNo": 1, "pageSize": page_size, "catalogId": CATALOG_ID},
                      headers=HEADERS, timeout=8)
    r.raise_for_status()
    catalogs = r.json().get("data", {}).get("catalogs", [])
    return catalogs[0].get("articles", []) if catalogs else []


def extract_symbol(title):
    m = LISTING_RE.search(title)
    return m.group(1) if m else ""


def main():
    c = BithumbClient()
    try:
        bithumb_coins = c.get_all_coins_v2()
    except Exception:
        bithumb_coins = set()
    known = load_known()
    first_run = not known
    log.info(f"바이낸스 공지 감지지연 측정기 시작 — {POLL_SEC}초 폴링 | 빗썸상장 {len(bithumb_coins)}코인 확인 | 순수측정(매매0)")
    try:
        from bithumb import notify
        notify.send(f"📡 바이낸스 공지 감지지연 측정기 시작 — {POLL_SEC}초 폴링. 상장공지 하이브리드 후보 검증용, 매매0")
    except Exception: pass

    while True:
        try:
            articles = fetch_articles()
            if first_run:
                known = {str(a["id"]) for a in articles}
                save_known(known)
                first_run = False
                log.info(f"baseline 등록 {len(known)}건 (알림 생략)")
                time.sleep(POLL_SEC); continue

            new_ones = [a for a in articles if str(a["id"]) not in known]
            for a in reversed(new_ones):
                aid = str(a["id"])
                known.add(aid)
                detected = datetime.now(KST)
                title = a.get("title", "")
                release_ms = a.get("releaseDate")
                delay = None
                release_str = ""
                if release_ms:
                    release_dt = datetime.fromtimestamp(release_ms / 1000, tz=KST)
                    release_str = release_dt.isoformat()
                    delay = (detected - release_dt).total_seconds()
                symbol = extract_symbol(title)
                is_listing = any(k in title for k in KEYWORDS)
                bithumb_listed = bool(symbol and symbol in bithumb_coins)
                logrow([detected.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], aid, release_str,
                        f"{delay:.1f}" if delay is not None else "", is_listing, symbol, bithumb_listed, title])
                tag = "🆕상장" if is_listing else "공지"
                log.warning(f"{tag} 감지 [{aid}] delay={delay if delay is None else f'{delay:.1f}s'} sym={symbol} 빗썸기상장={bithumb_listed} '{title[:60]}'")
                if is_listing and bithumb_listed:
                    try:
                        from bithumb import notify
                        notify.send(f"🆕 바이낸스 상장공지 감지! (빗썸 기상장 {symbol}) delay={delay:.1f}초\n{title}")
                    except Exception: pass
            if new_ones:
                save_known(known)

            # 빗썸 상장목록 하루 1회 갱신 (신규상장 반영)
            if datetime.now(KST).minute == 0 and datetime.now(KST).second < POLL_SEC:
                try: bithumb_coins = c.get_all_coins_v2()
                except Exception: pass
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
