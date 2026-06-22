"""
교차거래소 선행신호 로거 — 빗썸 vs 업비트 vs 바이낸스 시세 발산 캡처 (순수 로깅, 매매 0).

배경(2026-06-22 팀 결정): 빗썸은 후행 시장 — 같은 코인이 업비트/바이낸스에서 *먼저* 움직이고
빗썸이 따라오는 경향. "지금 다른 거래소에서 터졌나?"는 호가(현재상태)보다 한 단계 위인 *선행* 신호.
forward 데이터는 소급 불가(perishable)라 지금부터 쌓는 게 정답. 매매는 계속 ML만(한 변수씩).

캡처: 60초마다 3개 거래소 일괄 시세 → 코인별 단기 모멘텀 발산 측정.
  - bh_chg/up_chg/bn_chg = 직전 폴링 대비 % 변화(우리가 직접 계산, 통화무관)
  - premium_up = 빗썸/업비트 가격차(둘 다 KRW, 김치 내부 프리미엄)
  - lead = max(타거래소 모멘텀) - 빗썸 모멘텀 (+면 타거래소 선행)
  - 트리거: 업비트/바이낸스가 +TRIG% 이상 팝 → 빗썸이 따라오는지 그 순간 기록
누적 → data/crossex_events.csv. 하트비트 → data/crossex_state.json(신선도 알람용).

★ 격리 원칙: 매매 루프와 완전 분리된 독립 프로세스. 어떤 예외도 봇을 못 죽임.
watchdog 감시. 포트 47226(rt21/em22/ml23/core24/hyb25).
Run: python scripts/crossex_logger.py
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
    try: _sock.bind(("127.0.0.1", 47226))
    except OSError: print("[ERROR] crossex_logger 이미 실행 중 (포트 47226)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [XEX] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/crossex_logger.log", encoding="utf-8")])
log = logging.getLogger(__name__)

TOPN = 80
TRIG = 1.0          # 타거래소 % 모멘텀 이 이상이면 기록(발산 순간)
CYCLE_SEC = 60
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
CSV_PATH = ROOT / "data" / "crossex_events.csv"
STATE = ROOT / "data" / "crossex_state.json"
UPBIT_MARKETS = "https://api.upbit.com/v1/market/all"
UPBIT_TICKER = "https://api.upbit.com/v1/ticker"
BINANCE_PRICE = "https://api.binance.com/api/v3/ticker/price"


def bithumb_top(c):
    """빗썸 24H 거래대금 상위 TOPN 코인."""
    try:
        t = c.get_ticker("ALL"); rows = []
        for coin, d in t.items():
            if coin == "date" or coin in STABLE: continue
            try: v = float(d.get("acc_trade_value_24H", 0))
            except Exception: continue
            rows.append((coin, v))
        rows.sort(key=lambda x: -x[1])
        return [x[0] for x in rows[:TOPN]]
    except Exception as e:
        log.warning(f"빗썸 watchlist 실패: {e}"); return []


def upbit_krw_set():
    try:
        r = requests.get(UPBIT_MARKETS, timeout=5).json()
        return {m["market"].split("-")[1] for m in r if m["market"].startswith("KRW-")}
    except Exception as e:
        log.warning(f"업비트 마켓 실패: {e}"); return set()


def bithumb_prices(c):
    try:
        t = c.get_ticker("ALL")
        return {k: float(v["closing_price"]) for k, v in t.items() if k != "date" and isinstance(v, dict) and v.get("closing_price")}
    except Exception as e:
        log.warning(f"빗썸 시세 실패: {e}"); return {}


def upbit_prices(coins):
    """coins 중 업비트 KRW 마켓 일괄 시세."""
    out = {}
    if not coins: return out
    markets = ",".join(f"KRW-{x}" for x in coins)
    try:
        r = requests.get(UPBIT_TICKER, params={"markets": markets}, timeout=5).json()
        for d in r:
            out[d["market"].split("-")[1]] = float(d["trade_price"])
    except Exception as e:
        log.warning(f"업비트 시세 실패: {e}")
    return out


def binance_prices():
    """전 심볼 USDT 가격 1콜 → {COIN: price}."""
    out = {}
    try:
        r = requests.get(BINANCE_PRICE, timeout=6).json()
        for d in r:
            s = d.get("symbol", "")
            if s.endswith("USDT"):
                out[s[:-4]] = float(d["price"])
    except Exception as e:
        log.warning(f"바이낸스 시세 실패(차단 가능): {e}")
    return out


def logrow(row):
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["time","coin","bh_price","up_price","bn_price","bh_chg","up_chg","bn_chg","premium_up","lead"])
        w.writerow(row)


def heartbeat(cycles, logged):
    try:
        STATE.write_text(json.dumps({"last_cycle": datetime.now(KST).isoformat(),
                                     "cycles": cycles, "rows_logged": logged}, indent=2), encoding="utf-8")
    except Exception:
        pass


def main():
    c = BithumbClient()
    wl = bithumb_top(c); up_set = upbit_krw_set(); wl_day = datetime.now(KST).date()
    prev = {}            # coin -> {"bh":, "up":, "bn":}
    cycles = logged = 0
    log.info(f"교차거래소 로거 시작 — 감시 {len(wl)}코인 | 업비트KRW {len(up_set)} | 트리거 {TRIG}% | 순수 로깅")
    try:
        from bithumb import notify; notify.send(f"📡 교차거래소 로거 시작 — 업비트/바이낸스 선행신호 캡처(순수로깅, 매매0). 트리거 {TRIG}%")
    except Exception: pass
    while True:
        try:
            if datetime.now(KST).date() != wl_day:
                wl_day = datetime.now(KST).date(); wl = bithumb_top(c); up_set = upbit_krw_set()
            bh = bithumb_prices(c)
            up = upbit_prices([x for x in wl if x in up_set])
            bn = binance_prices()
            now = datetime.now(KST)
            for coin in wl:
                bhp = bh.get(coin); upp = up.get(coin); bnp = bn.get(coin)
                if bhp is None: continue
                pv = prev.get(coin, {})
                def chg(cur, key):
                    p = pv.get(key)
                    return (cur / p - 1) * 100 if (cur and p and p > 0) else None
                bh_chg = chg(bhp, "bh"); up_chg = chg(upp, "up"); bn_chg = chg(bnp, "bn")
                prev[coin] = {"bh": bhp, "up": upp, "bn": bnp}
                # 트리거: 타거래소가 팝(+TRIG% 이상)한 순간만 기록
                pops = [x for x in (up_chg, bn_chg) if x is not None and x >= TRIG]
                if not pops: continue
                premium_up = (bhp / upp - 1) * 100 if (upp and upp > 0) else None
                others = [x for x in (up_chg, bn_chg) if x is not None]
                lead = (max(others) - bh_chg) if (others and bh_chg is not None) else None
                logrow([now.strftime("%Y-%m-%d %H:%M:%S"), coin,
                        f"{bhp:.4f}", f"{upp:.4f}" if upp else "", f"{bnp:.6f}" if bnp else "",
                        f"{bh_chg:+.2f}" if bh_chg is not None else "",
                        f"{up_chg:+.2f}" if up_chg is not None else "",
                        f"{bn_chg:+.2f}" if bn_chg is not None else "",
                        f"{premium_up:+.2f}" if premium_up is not None else "",
                        f"{lead:+.2f}" if lead is not None else ""])
                logged += 1
                _u = f"{up_chg:+.2f}" if up_chg is not None else "NA"
                _b = f"{bn_chg:+.2f}" if bn_chg is not None else "NA"
                _h = f"{bh_chg:+.2f}" if bh_chg is not None else "NA"
                _l = f"{lead:+.2f}" if lead is not None else "NA"
                log.info(f"{coin} 발산 — 업비트{_u}% 바이낸스{_b}% 빗썸{_h}% lead{_l}")
            cycles += 1
            heartbeat(cycles, logged)
        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main()
