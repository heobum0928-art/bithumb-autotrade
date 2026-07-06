"""
체결방향 불균형 로거 (orderflow_logger) — 순수 로깅, 매매 0.

목적(2026-07-04): 사용자 "오토리서치" 요청 → 에이전트 리서치 결론 — 기존에 죽인
"거래량 급증(총량)" 가설과 다른 메커니즘인 **체결 방향성 불균형**(누가 공격적으로
사는지 vs 파는지, OFI/VPIN류)이 미시도 후보로 남음. 학술 근거: 방향 자체는 있으나
초단기(<1분)엔 비용을 못 이긴다는 경고 존재 — 5분~수십분 홀딩에서도 유효한지 실측 필요.

한계: 빗썸 체결이력 API가 소급조회 불가(최근 20건만) → 백테스트 불가능한 가설이라
지금부터 실시간 로깅으로 데이터를 쌓아야 함(다른 순수측정 도구들과 동일 패턴).

동작: LOOP_SEC마다 유동성 상위 코인 순회 → 최근 체결 20건에서
  ofi = (매수체결액 - 매도체결액) / 전체체결액   (범위 -1~+1, +면 매수우위)
계산해 현재가·24H거래대금과 함께 CSV에 기록. 추후 candles_cache로 순방향 수익률
매칭해 절제백테 예정(volume_radar/premium_guard와 동일 검증 절차).

★ 순수 로깅: 매매 API 미호출. 포트 47244.
기록 data/orderflow_events.csv | 로그 logs/orderflow_logger.log
Run: python scripts/orderflow_logger.py
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
    try: _sock.bind(("127.0.0.1", 47244))
    except OSError: print("[ERROR] orderflow_logger 이미 실행 중 (포트 47244)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [OFLOW] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/orderflow_logger.log", encoding="utf-8")])
log = logging.getLogger(__name__)

STABLE = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD", "PYUSD", "USDS", "KRW"}
MIN_VOL_24H_KRW = 1_000_000_000  # 유동성 상위만(1분 새 20건 체결이 몰릴 정도) — 얇은 코인은 20건이 몇시간치라 신호 무의미
LOOP_SEC = 90            # 전체 유니버스 한 바퀴 목표 주기
UNIVERSE_REFRESH_MIN = 10   # 2026-07-06: 30→10분(BLUR 폭발 사례 — 갱신주기 탓에 유동성기준 넘고도 30분간 감시목록 밖이었음)
CSV_PATH = ROOT / "data" / "orderflow_events.csv"

# 고래 발자국(대형 개별거래) 감지 (2026-07-06)
WHALE_RATIO_MIN = 8.0        # 같은 20건 내 중앙값 대비 최소 배율
WHALE_MIN_KRW = 3_000_000    # 절대 최소액(너무 작은 코인의 사소한 튐 배제)
WHALE_COOLDOWN_MIN = 10
WHALE_CSV = ROOT / "data" / "whale_print_events.csv"


def logrow(row):
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["time", "coin", "ofi", "n_trades", "span_sec", "price", "val_24h_eok"])
        w.writerow(row)


def logwhale(row):
    new = not WHALE_CSV.exists()
    with open(WHALE_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["time", "coin", "side", "size_krw", "ratio_to_median", "price", "val_24h_eok", "range_pos_2h"])
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


def range_position(c, coin, bars=24):
    """최근 N개 5분봉(기본 2시간) 고저 대비 현재가 위치 (0=바닥권, 1=고점권).

    2026-07-07: BLUR 사례 — 고래발자국이 박스권 바닥(30.04, 위치≈0.1)에서 뜨고 +8%
    올랐음. 같은 배율의 고래발자국이라도 이미 고점권(위치≈0.9)에서 뜨면 추격매수
    위험이 커서, 기술적 위치를 같이 봐야 신호 품질을 구분할 수 있음.
    """
    try:
        cl = c.get_candles(f"KRW-{coin}", unit=5, count=bars)
    except Exception:
        return None
    if not cl or len(cl) < 5:
        return None
    highs = [x["high_price"] for x in cl]
    lows = [x["low_price"] for x in cl]
    hi, lo = max(highs), min(lows)
    if hi <= lo:
        return None
    cur = cl[0]["trade_price"]  # 최신봉(newest-first)
    return (cur - lo) / (hi - lo)


def compute_ofi(c, coin):
    try:
        trades = c.get_transaction_history(coin, count=20)
    except Exception:
        return None
    if not trades or len(trades) < 5:
        return None
    buy_krw = sum(float(t_["total"]) for t_ in trades if t_.get("type") == "bid")
    sell_krw = sum(float(t_["total"]) for t_ in trades if t_.get("type") == "ask")
    total = buy_krw + sell_krw
    if total <= 0: return None
    ofi = (buy_krw - sell_krw) / total
    try:
        newest = datetime.strptime(trades[0]["transaction_date"], "%Y-%m-%d %H:%M:%S")
        oldest = datetime.strptime(trades[-1]["transaction_date"], "%Y-%m-%d %H:%M:%S")
        span = abs((newest - oldest).total_seconds())
    except Exception:
        span = 0
    # 대형 개별거래("고래 발자국") 감지 — 같은 20건 안에서 중앙값 대비 최대 단일거래 배율
    # (2026-07-06: 온체인 웨일얼럿은 이미 기각됐으나, 이건 지갑이동이 아니라 거래소 체결
    #  자체를 보는 거라 지연·모호함이 없어 별도 검증 대상)
    sizes = sorted(float(t_["total"]) for t_ in trades)
    median_size = sizes[len(sizes) // 2]
    max_trade = max(trades, key=lambda t_: float(t_["total"]))
    max_size = float(max_trade["total"])
    whale_ratio = (max_size / median_size) if median_size > 0 else 0
    whale_side = max_trade.get("type")
    return ofi, len(trades), span, whale_ratio, max_size, whale_side


def main():
    c = BithumbClient()
    universe = build_universe(c)
    last_universe_refresh = time.time()
    whale_cooldown = {}
    log.info(f"체결방향 불균형 로거 시작 — 유동성{MIN_VOL_24H_KRW/1e8:.0f}억+ {len(universe)}코인 | 순환주기~{LOOP_SEC}s | "
             f"고래발자국 배율{WHALE_RATIO_MIN}x+/{WHALE_MIN_KRW/1e6:.0f}백만원+ | 순수로깅(매매0)")
    try:
        from bithumb import notify
        notify.send(f"📡 체결방향 불균형(OFI)+고래발자국 로거 시작 — {len(universe)}코인 순수로깅, 매매0")
    except Exception: pass

    while True:
        try:
            if time.time() - last_universe_refresh > UNIVERSE_REFRESH_MIN * 60:
                universe = build_universe(c)
                last_universe_refresh = time.time()
                log.info(f"유니버스 갱신 — {len(universe)}코인")

            per_coin_sleep = max(LOOP_SEC / max(len(universe), 1), 0.6)
            t_all = c.get_ticker("ALL")
            for coin in universe:
                res = compute_ofi(c, coin)
                if res is None:
                    time.sleep(per_coin_sleep); continue
                ofi, n, span, whale_ratio, whale_size, whale_side = res
                d = t_all.get(coin, {})
                try:
                    price = float(d.get("closing_price", 0))
                    val24 = float(d.get("acc_trade_value_24H", 0))
                except Exception:
                    price = 0; val24 = 0
                logrow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin,
                        f"{ofi:.4f}", n, f"{span:.0f}", f"{price:g}", f"{val24/1e8:.1f}"])
                if abs(ofi) >= 0.7:
                    log.info(f"극단OFI {coin} ofi={ofi:+.2f} n={n} span={span:.0f}s 현재가={price:g}")

                if (whale_ratio >= WHALE_RATIO_MIN and whale_size >= WHALE_MIN_KRW
                        and whale_cooldown.get(coin, 0) <= time.time()):
                    whale_cooldown[coin] = time.time() + WHALE_COOLDOWN_MIN * 60
                    side_kr = "매수" if whale_side == "bid" else "매도"
                    rpos = range_position(c, coin)
                    rpos_str = f"{rpos:.2f}" if rpos is not None else ""
                    zone = ""
                    if rpos is not None:
                        zone = "바닥권" if rpos <= 0.3 else ("고점권" if rpos >= 0.7 else "중간")
                    logwhale([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin, whale_side,
                              f"{whale_size:.0f}", f"{whale_ratio:.1f}", f"{price:g}", f"{val24/1e8:.1f}", rpos_str])
                    log.warning(f"고래발자국 {coin} {side_kr} {whale_size:,.0f}원(중앙값 {whale_ratio:.1f}배) "
                                f"현재가={price:g} 위치={zone}({rpos_str})")
                    try:
                        from bithumb import notify
                        notify.send(f"🐋 고래발자국 {coin} {side_kr} {whale_size/1e6:.1f}백만원({whale_ratio:.0f}배) "
                                    f"현재가={price:g} [{zone}]")
                    except Exception: pass
                time.sleep(per_coin_sleep)
        except Exception as e:
            log.error(f"루프오류: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
