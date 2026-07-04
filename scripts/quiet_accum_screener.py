"""
조용한 매집 스크리너 (quiet_accum_screener) — 순수 로깅, 매매 0.

목적(2026-07-04): 사용자 "하이브리드 틈새 전략" 요청 — 세 조건 동시 충족 코인 포착.
  ① 거래대금 상위(관심 몰림, val_24h >= 10억)
  ② 아직 안 오름(chg_24h <= 5%, 이미 오른 코인 추격 아님)
  ③ 체결 매수우위(OFI >= +0.4, orderflow_logger가 로깅한 최근값 재사용)

근거: 순수 거래대금급증 백테스트(2026-07-04)에서 "이미 오른 코인(chg>10)"과
"초극단 surge(100배+)"는 24h -8%대로 확실히 나쁨(t-3~-4.6)이었지만, "아직 안 오른
코인(chg<=5)"만은 24h -2.28%(t-1.18)로 나쁘다고 확정 못 함 — 유일하게 애매한 버킷.
여기에 OFI(체결방향, orderflow_logger가 오늘 신설)를 겹쳐 "돈은 몰리는데 아직
안 터졌고 매수세가 실제로 쌓이는 중"인 후보만 추림. MPLX가 업비트 공지 전
이런 모습이었을 것이라는 가설.

한계: OFI도 소급조회 불가 → 이 조합도 지금부터 실시간 로깅으로 검증해야 함
(volume_radar/notice_monitor/orderflow_logger와 동일 패턴). candles_cache로
순방향수익률 매칭 후 절제백테 예정 — #42/#44/#45/#46와 동일 사전등록 게이트 원칙.

★ 순수 로깅: 매매 API 미호출. orderflow_logger의 CSV를 읽기만 함(중복 API호출 없음).
포트 47245. 기록 data/quiet_accum_events.csv | 로그 logs/quiet_accum_screener.log
Run: python scripts/quiet_accum_screener.py
"""
import sys, os, atexit, time, csv, socket, logging
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
    try: _sock.bind(("127.0.0.1", 47245))
    except OSError: print("[ERROR] quiet_accum_screener 이미 실행 중 (포트 47245)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [QACCUM] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/quiet_accum_screener.log", encoding="utf-8")])
log = logging.getLogger(__name__)

STABLE = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD", "PYUSD", "USDS", "KRW"}
MIN_VOL_24H_KRW = 1_000_000_000
CHG_MAX = 5.0          # 아직 안 오름
OFI_MIN = 0.4          # 체결 매수우위
OFI_MAX_AGE_SEC = 300  # OFI 판독이 5분 넘게 오래되면 무시(orderflow_logger 순환주기 90s 기준 여유)
COOLDOWN_MIN = 60      # 같은 코인 재포착 쿨다운
LOOP_SEC = 30
OFI_CSV = ROOT / "data" / "orderflow_events.csv"
OUT_CSV = ROOT / "data" / "quiet_accum_events.csv"


def latest_ofi_by_coin():
    """orderflow_events.csv에서 코인별 가장 최근 OFI 판독 1건씩. 파일이 커지므로 tail만 읽음."""
    out = {}
    if not OFI_CSV.exists(): return out
    try:
        with open(OFI_CSV, encoding="utf-8") as f:
            lines = f.readlines()
        header = lines[0].strip().split(",")
        # 최근 것이 뒤에 쌓이므로 뒤에서부터 스캔, 코인당 첫 등장(=가장 최근)만 채택
        for line in reversed(lines[-6000:]):  # 유니버스 51코인*몇 사이클 정도면 충분, 과도한 메모리 방지
            parts = line.rstrip("\n").split(",")
            if len(parts) != len(header) or parts[0] == "time": continue
            row = dict(zip(header, parts))
            coin = row["coin"]
            if coin in out: continue
            try:
                t = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                ofi = float(row["ofi"])
            except Exception:
                continue
            out[coin] = (ofi, t)
    except Exception as e:
        log.warning(f"OFI CSV 읽기 실패: {e}")
    return out


def logrow(row):
    new = not OUT_CSV.exists()
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["time", "coin", "price", "chg_24h", "val_24h_eok", "ofi", "ofi_age_sec"])
        w.writerow(row)


def main():
    c = BithumbClient()
    cooldown = {}
    log.info(f"조용한 매집 스크리너 시작 — val{MIN_VOL_24H_KRW/1e8:.0f}억+ chg<={CHG_MAX}% OFI>={OFI_MIN} | 순수로깅(매매0)")
    try:
        from bithumb import notify
        notify.send(f"📡 조용한 매집 스크리너 시작 — 거래대금상위+안오름+매수우위 겹침탐지, 순수로깅 매매0")
    except Exception: pass

    while True:
        try:
            ofi_map = latest_ofi_by_coin()
            t = c.get_ticker("ALL")
            now = datetime.now(KST)
            for coin, d in t.items():
                if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
                try:
                    val24 = float(d.get("acc_trade_value_24H", 0))
                    chg24 = float(d.get("fluctate_rate_24H", 0))
                    price = float(d.get("closing_price", 0))
                except Exception:
                    continue
                if val24 < MIN_VOL_24H_KRW: continue
                if chg24 > CHG_MAX or chg24 < -CHG_MAX: continue  # 아직 안 오름(양방향 노이즈는 배제, 큰 변동 없는 코인만)
                if coin not in ofi_map: continue
                ofi, ofi_time = ofi_map[coin]
                age = (now - ofi_time).total_seconds()
                if age > OFI_MAX_AGE_SEC: continue
                if ofi < OFI_MIN: continue
                if cooldown.get(coin, 0) > time.time(): continue
                cooldown[coin] = time.time() + COOLDOWN_MIN * 60
                logrow([now.strftime("%Y-%m-%d %H:%M:%S"), coin, f"{price:g}", f"{chg24:+.2f}",
                        f"{val24/1e8:.1f}", f"{ofi:+.3f}", f"{age:.0f}"])
                log.warning(f"후보 포착 {coin} 가격={price:g} chg24h={chg24:+.2f}% 거래대금={val24/1e8:.0f}억 OFI={ofi:+.2f}")
                try:
                    from bithumb import notify
                    notify.send(f"🔍 조용한매집 후보 {coin} chg24h={chg24:+.1f}% OFI={ofi:+.2f} 거래대금{val24/1e8:.0f}억")
                except Exception: pass
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(LOOP_SEC)


if __name__ == "__main__":
    main()
