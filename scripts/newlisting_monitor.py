"""
신규상장 감지·캡처 (newlisting_monitor) — 원래 프로젝트 목표: 빗썸 신규상장 첫펌프.

백테스트 불가(상장날짜·상장직후 데이터 없음) → forward로만. 상장은 미래 이벤트라
"감지 즉시 알림 + 첫 몇 시간 가격궤적 캡처"로 데이터를 모은다. 몇 건 쌓이면 진입/청산 규칙 설계.

동작:
  - known_coins.json(영속) 대비 ticker ALL에 *새 코인* 출현 = 상장 감지
  - 즉시 텔레그램 알림 + data/newlisting_events.csv에 상장후 N시간 가격궤적 기록
  - 상장은 드물어(월 몇 건) 평소엔 대기. 자동매매 없음(데이터 먼저, 규칙은 나중).

격리·순수로깅. 포트 47229. watchdog 감시.
Run: python scripts/newlisting_monitor.py
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
    try: _sock.bind(("127.0.0.1", 47229))
    except OSError: print("[ERROR] newlisting_monitor 이미 실행 중 (포트 47229)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb import notify

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [NEWLIST] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/newlisting_monitor.log", encoding="utf-8")])
log = logging.getLogger(__name__)

KNOWN = ROOT / "data" / "known_coins.json"
CSV_PATH = ROOT / "data" / "newlisting_events.csv"
POLL = 10            # 10초마다 (상장 순간 빨리 잡게)
TRACK_HOURS = 6      # 상장 후 6시간 궤적 추적
SNAP_SEC = 60        # 궤적 기록 간격 60초


def load_known():
    try: return set(json.loads(KNOWN.read_text(encoding="utf-8")))
    except Exception: return set()


def save_known(s):
    try: KNOWN.write_text(json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8")
    except Exception as e: log.warning(f"known 저장 실패: {e}")


def logsnap(coin, mins, info):
    new = not CSV_PATH.exists()
    try:
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(["time","coin","mins_since_list","price","chg24h","vol_krw"])
            w.writerow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin, f"{mins:.0f}",
                        info.get("closing_price",""), info.get("fluctate_rate_24H",""), info.get("acc_trade_value_24H","")])
    except Exception as e:
        log.warning(f"궤적 기록 실패: {e}")


def main():
    c = BithumbClient()
    known = load_known()
    try:
        cur = {k for k in c.get_ticker("ALL") if k != "date"}
    except Exception:
        cur = set()
    # 시작 시 현재 상장코인을 baseline으로 조용히 병합(스테일 리스트 false-positive 방지).
    # 이후 *새로 출현*하는 코인만 진짜 신규상장으로 알림.
    base_new = cur - known
    if base_new:
        log.info(f"시작 baseline 병합(알림X): {sorted(base_new)}")
    known |= cur; save_known(known)
    tracking = {}        # coin -> detect_time
    last_snap = {}
    log.info(f"신규상장 감지 시작 — 등록 {len(known)}코인 감시 | 상장시 알림+{TRACK_HOURS}h 궤적캡처")
    try: notify.send(f"🆕 신규상장 감지봇 시작 — 새 코인 상장 즉시 알림+가격궤적 캡처 (현재 {len(known)}종 감시)")
    except Exception: pass
    while True:
        try:
            tk = c.get_ticker("ALL")
            cur = {k for k in tk if k != "date"}
            new = cur - known
            for coin in sorted(new):
                info = tk.get(coin, {})
                tracking[coin] = datetime.now(KST)
                px = info.get("closing_price", "?")
                log.warning(f"🆕🆕 신규상장 감지! {coin}/KRW @{px}원 — {TRACK_HOURS}h 추적 시작")
                try: notify.send(f"🆕🆕 신규상장! <b>{coin}</b> @{px}원\n빗썸 방금 상장. 첫펌프 주시 — 봇이 궤적 캡처 중. 직접 차트/호가 확인.")
                except Exception: pass
                logsnap(coin, 0, info)
            if new:
                known = cur; save_known(known)
            # 추적 중인 코인 궤적 기록
            now = datetime.now(KST)
            for coin, t0 in list(tracking.items()):
                mins = (now - t0).total_seconds() / 60
                if mins > TRACK_HOURS * 60:
                    del tracking[coin]; continue
                if (now - last_snap.get(coin, t0 - timedelta(seconds=SNAP_SEC))).total_seconds() >= SNAP_SEC:
                    logsnap(coin, mins, tk.get(coin, {})); last_snap[coin] = now
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
