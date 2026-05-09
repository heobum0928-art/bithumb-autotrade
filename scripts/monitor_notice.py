"""
Bithumb notice board monitor — pre-detect upcoming listings.
Polls Bithumb's public announcement RSS/API every 60s.
Alerts when a new listing notice appears (e.g. 'XXX 원화 마켓 추가').

Run: python scripts/monitor_notice.py
"""
import sys
import time
import logging
import re
import requests
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/notice.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

NOTICE_URL = "https://feed.bithumb.com/notice"
KEYWORDS = ["원화 마켓", "신규 상장", "거래 지원", "마켓 추가", "상장 예정"]
POLL_SEC = 60


def fetch_notices() -> list[dict]:
    try:
        resp = requests.get(NOTICE_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("list", data.get("data", []))
    except Exception as e:
        log.debug(f"공지 조회 실패: {e}")
        return []


def extract_coins_from_title(title: str) -> list[str]:
    """Extract coin symbols like BTC, ETH from notice title."""
    return re.findall(r'\b([A-Z]{2,10})\b', title)


def run():
    log.info("=== 빗썸 신규 상장 공지 모니터 시작 ===")
    seen_ids: set = set()

    # 초기 로드 (기존 공지는 무시)
    for n in fetch_notices():
        seen_ids.add(n.get("id") or n.get("noticeId") or n.get("title", ""))
    log.info(f"기존 공지 {len(seen_ids)}건 로드 완료")

    while True:
        try:
            time.sleep(POLL_SEC)
            notices = fetch_notices()
            for n in notices:
                nid = n.get("id") or n.get("noticeId") or n.get("title", "")
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)
                title = n.get("title", "")
                url = n.get("url", n.get("link", ""))
                date_str = n.get("date", n.get("regDate", ""))

                # 상장 관련 키워드 필터
                if any(kw in title for kw in KEYWORDS):
                    coins = extract_coins_from_title(title)
                    log.warning(
                        f"[신규 상장 공지!] {title} | "
                        f"코인추정={coins} | {url}"
                    )
                else:
                    log.info(f"[공지] {title}")

        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"오류: {e}")
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    run()
