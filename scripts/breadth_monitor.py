"""
시장 전체 로테이션(breadth) 모니터 — 순수 로깅, 매매 0.

목적(2026-07-05): ADA가 23:40경부터 단일 뉴스 없이 올랐는데, 원인 추적 결과
"시장 전체 리스크온 로테이션"(BTC/ETH/SOL/ZEC 등 동반 상승)이었음 — 코인 개별
촉매(#45)도, 캐스케이드 급락반등도 아닌 제3의 현상. 이런 "여러 코인이 동시에
같이 뜨는" 시장 무드를 실시간으로 포착할 도구가 없어서 신설.

★ 주의: 이건 "로테이션 감지=매수 신호"가 아니다.
  - #18(알트바구니 매수)이 전 장세에서 이미 음수로 폐기됨(관망=현금이 정답).
  - 캐스케이드 자체 breadth 상관관계 리서치(2026-07-02)에서 오히려 **breadth 낮을 때
    (시장 조용할 때) 캐스케이드가 더 잘 먹힘**(t+4.40)을 확인함 — breadth 높다고
    무언가를 더 사야 한다는 근거가 오히려 반대임.
  순수 배경 모니터로만 쓴다 — 나중에 기존 전략(캐스케이드 등) 파라미터를 breadth
  기준으로 조정할지 검토할 때의 참고자료. 이 모니터 자체가 매수 신호를 만들지 않음.

동작: BREADTH_SEC마다 유동성 상위 코인 순회 → 최근 1h/3h/6h 수익률 계산 →
  "N% 이상 코인이 양수"인 비율(breadth)을 계산해 로깅. 고breadth 돌파 시 알림(정보용).

★ 순수 로깅: 매매 API 미호출. 포트 47246.
기록 data/breadth_events.csv | 로그 logs/breadth_monitor.log
Run: python scripts/breadth_monitor.py
"""
import sys, os, atexit, time, csv, socket, logging, statistics
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
    try: _sock.bind(("127.0.0.1", 47246))
    except OSError: print("[ERROR] breadth_monitor 이미 실행 중 (포트 47246)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [BREADTH] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/breadth_monitor.log", encoding="utf-8")])
log = logging.getLogger(__name__)

STABLE = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD", "PYUSD", "USDS", "KRW"}
MIN_VOL_24H_KRW = 1_000_000_000
LOOP_SEC = 300           # 5분마다 (전체 유니버스 캔들조회라 API부담 고려)
HIGH_BREADTH_THRESH = 0.65   # 65%+ 코인이 양수면 "로테이션" 알림(정보용, 매수신호 아님)
NOTIFY_COOLDOWN_MIN = 60
CSV_PATH = ROOT / "data" / "breadth_events.csv"


def logrow(row):
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["time", "n_coins", "breadth_1h", "breadth_3h", "breadth_6h",
                             "avg_ret_1h", "avg_ret_3h", "avg_ret_6h"])
        w.writerow(row)


def build_universe(c):
    t = c.get_ticker("ALL")
    out = []
    for coin, d in t.items():
        if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
        try:
            vol = float(d.get("acc_trade_value_24H", 0))
        except Exception: continue
        if vol >= MIN_VOL_24H_KRW:
            out.append(coin)
    return out


def ret_over(c, coin, hours):
    try:
        cl = c.get_candles(f"KRW-{coin}", unit=60, count=hours + 1)
    except Exception:
        return None
    if len(cl) < 2: return None
    cl = list(reversed(cl))
    p0 = cl[0]["opening_price"]; p1 = cl[-1]["trade_price"]
    if p0 <= 0: return None
    return (p1 / p0 - 1) * 100


def main():
    c = BithumbClient()
    universe = build_universe(c)
    last_notify = 0
    log.info(f"시장 전체 로테이션(breadth) 모니터 시작 — 유동성{MIN_VOL_24H_KRW/1e8:.0f}억+ {len(universe)}코인 | "
             f"{LOOP_SEC}s 주기 | 순수로깅(매매0, 배경신호 전용)")
    try:
        from bithumb import notify
        notify.send(f"📡 시장 전체 로테이션(breadth) 모니터 시작 — {len(universe)}코인, 순수배경신호(매매0)")
    except Exception: pass

    while True:
        try:
            universe = build_universe(c)
            rets = {1: [], 3: [], 6: []}
            for coin in universe:
                for h in (1, 3, 6):
                    r = ret_over(c, coin, h)
                    if r is not None:
                        rets[h].append(r)
                time.sleep(0.3)

            row = {"time": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), "n_coins": len(universe)}
            summary = []
            for h in (1, 3, 6):
                vals = rets[h]
                if not vals:
                    row[f"breadth_{h}h"] = ""; row[f"avg_ret_{h}h"] = ""; continue
                breadth = sum(1 for v in vals if v > 0) / len(vals)
                avg = statistics.mean(vals)
                row[f"breadth_{h}h"] = f"{breadth:.3f}"
                row[f"avg_ret_{h}h"] = f"{avg:+.2f}"
                summary.append(f"{h}h breadth={breadth*100:.0f}% avg={avg:+.2f}%")
            logrow([row["time"], row["n_coins"], row.get("breadth_1h",""), row.get("breadth_3h",""),
                    row.get("breadth_6h",""), row.get("avg_ret_1h",""), row.get("avg_ret_3h",""), row.get("avg_ret_6h","")])
            log.info(f"n={len(universe)} | " + " | ".join(summary))

            b3 = float(row.get("breadth_3h") or 0)
            if b3 >= HIGH_BREADTH_THRESH and time.time() - last_notify > NOTIFY_COOLDOWN_MIN * 60:
                last_notify = time.time()
                log.warning(f"고breadth 감지 — 3h breadth={b3*100:.0f}% (시장 전체 로테이션 가능성, 매수신호 아님·참고용)")
                try:
                    from bithumb import notify
                    notify.send(f"🌊 시장 전체 로테이션 감지(참고용, 매수신호 아님) — 3h breadth {b3*100:.0f}% 코인이 양수")
                except Exception: pass
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(LOOP_SEC)


if __name__ == "__main__":
    main()
