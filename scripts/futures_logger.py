"""
선물 과열/청산추론 로거 — Binance 선물 REST 폴링 (순수 로깅, 매매 0).

배경(2026-06-27): cascade #39 슬리피지 손절 문제 = "급락+거래량"만으론 '청산캐스케이드→
과매도→반등'(진짜)과 '악재 칼'(가짜) 구분 불가. 진짜 하이브리드 = 외부신호 결합.
1번 후보가 Binance 청산이었으나 **청산 WS(fstream)는 한국 IP 차단**(핸드셰이크만 되고
데이터 0). 청산 직접 REST(allForceOrders)도 폐지됨(400). → REST로 얻는 청산 *대체* 신호로 전환.

핵심 통찰: **OI(미결제약정) 5분 급감 = 그 구간에 강제청산 발생** = 청산 스트림의 대체재.
함께 수집:
  - funding   : 펀딩비 (롱과열 양수극단 / 숏과열 음수극단)
  - oi / oi_chg_5m : 미결제약정 + 5분 변화율(급감=청산)
  - ls_ratio  : 글로벌 롱숏 계정비 (군중 쏠림)
  - taker_ratio : taker 매수/매도 거래량비 (실제 시장가 압력)

cascade 신호 시점에 OI 급감(청산 동반)이 있었나? 있으면 진짜 캐스케이드(반등), 없으면 칼.
며칠 누적 후 cascade 신호와 상관분석 → 슬리피지 손절 회피 필터 후보.

데이터: Binance fapi REST (한국 IP 정상 확인). 빗썸 상장 ∩ Binance선물 코인만.
  누적 → data/futures_signals.csv. 하트비트 → data/futures_state.json.

★ 격리 원칙: 매매 루프와 완전 분리. 어떤 예외도 봇을 못 죽임. watchdog 감시. 포트 47233.
Run: python scripts/futures_logger.py
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
    try: _sock.bind(("127.0.0.1", 47233))
    except OSError: print("[ERROR] futures_logger 이미 실행 중 (포트 47233)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [FUT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/futures_logger.log", encoding="utf-8")])
log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
CSV_PATH = ROOT / "data" / "futures_signals.csv"
STATE = ROOT / "data" / "futures_state.json"
TOPN = 150              # 빗썸 거래대금 상위 N개만 (∩ Binance선물) — cascade 감시범위(150)와 정렬 (2026-07-01)
CYCLE_SEC = 300         # 5분 폴링 (OI 5m 봉에 맞춤)
OI_DUMP_PCT = -3.0      # OI 5분 변화 이 이하면 "청산 의심" 로그 알림


def bithumb_top(c):
    """빗썸 24H 거래대금 상위 TOPN 코인."""
    try:
        t = c.get_ticker("ALL"); rows = []
        for coin, d in t.items():
            if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
            try: v = float(d.get("acc_trade_value_24H", 0))
            except Exception: continue
            rows.append((coin, v))
        rows.sort(key=lambda x: -x[1])
        return [x[0] for x in rows[:TOPN]]
    except Exception as e:
        log.warning(f"빗썸 watchlist 실패: {e}"); return []


def binance_perp_set():
    """Binance USDT 무기한 선물 상장 심볼 → 코인 집합."""
    try:
        r = requests.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=10).json()
        out = set()
        for s in r.get("symbols", []):
            if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                out.add(s["baseAsset"])
        return out
    except Exception as e:
        log.warning(f"Binance 선물목록 실패: {e}"); return set()


def all_funding():
    """전체 심볼 펀딩비 1콜 → {COIN: funding%}."""
    out = {}
    try:
        r = requests.get(f"{FAPI}/fapi/v1/premiumIndex", timeout=10).json()
        for d in r:
            s = d.get("symbol", "")
            if s.endswith("USDT"):
                try: out[s[:-4]] = float(d["lastFundingRate"]) * 100
                except Exception: pass
    except Exception as e:
        log.warning(f"펀딩비 실패: {e}")
    return out


def oi_hist(coin):
    """5분봉 OI 최근 2개 → (oi_now_usd, oi_chg_pct_5m)."""
    try:
        r = requests.get(f"{FAPI}/futures/data/openInterestHist",
            params={"symbol": f"{coin}USDT", "period": "5m", "limit": 2}, timeout=8).json()
        if len(r) >= 2:
            prev = float(r[-2]["sumOpenInterestValue"]); now = float(r[-1]["sumOpenInterestValue"])
            chg = (now / prev - 1) * 100 if prev > 0 else 0
            return now, chg
        elif r:
            return float(r[-1]["sumOpenInterestValue"]), 0.0
    except Exception:
        pass
    return None, None


def ls_ratio(coin):
    try:
        r = requests.get(f"{FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": f"{coin}USDT", "period": "5m", "limit": 1}, timeout=8).json()
        if r: return float(r[-1]["longShortRatio"])
    except Exception: pass
    return None


def taker_ratio(coin):
    try:
        r = requests.get(f"{FAPI}/futures/data/takerlongshortRatio",
            params={"symbol": f"{coin}USDT", "period": "5m", "limit": 1}, timeout=8).json()
        if r: return float(r[-1]["buySellRatio"])
    except Exception: pass
    return None


def logrow(row):
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["time","coin","funding","oi_usd","oi_chg_5m","ls_ratio","taker_ratio"])
        w.writerow(row)


def main():
    c = BithumbClient()
    wl = bithumb_top(c)
    perp = binance_perp_set()
    targets = [x for x in wl if x in perp]   # 빗썸상위 ∩ Binance선물
    wl_day = datetime.now(KST).date()
    cycles = logged = 0
    log.info(f"선물 로거 시작 — 빗썸상위{len(wl)} ∩ Binance선물 = {len(targets)}코인 | {CYCLE_SEC}s 폴링 | OI급감={OI_DUMP_PCT}% | 순수로깅(매매0)")
    log.info(f"감시: {targets}")
    try:
        from bithumb import notify
        notify.send(f"📊 선물 로거 시작 — Binance 펀딩/OI/롱숏 폴링({len(targets)}코인). 청산WS 한국차단 → OI급감으로 청산추론. cascade 하이브리드용")
    except Exception: pass

    while True:
        try:
            if datetime.now(KST).date() != wl_day:
                wl_day = datetime.now(KST).date()
                wl = bithumb_top(c); perp = binance_perp_set()
                targets = [x for x in wl if x in perp]
            fund = all_funding()
            now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            dumps = []
            for coin in targets:
                oi, oi_chg = oi_hist(coin)
                lsr = ls_ratio(coin)
                tkr = taker_ratio(coin)
                fr = fund.get(coin)
                if oi is None and fr is None: continue
                logrow([now, coin,
                        f"{fr:+.4f}" if fr is not None else "",
                        f"{oi:.0f}" if oi is not None else "",
                        f"{oi_chg:+.2f}" if oi_chg is not None else "",
                        f"{lsr:.3f}" if lsr is not None else "",
                        f"{tkr:.3f}" if tkr is not None else ""])
                logged += 1
                if oi_chg is not None and oi_chg <= OI_DUMP_PCT:
                    dumps.append((coin, oi_chg))
                time.sleep(0.15)   # rate limit 여유
            cycles += 1
            for coin, chg in dumps:
                log.info(f"💥 {coin} OI {chg:+.1f}% 5분급감 — 청산 의심(과매도 후보)")
            try:
                STATE.write_text(json.dumps({
                    "last_cycle": now, "cycles": cycles, "rows_logged": logged,
                    "targets": len(targets), "oi_dumps_last": [d[0] for d in dumps],
                }, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception: pass
            if cycles % 12 == 1:  # 1시간마다 생존 로그
                log.info(f"누적 {logged}행 / {cycles}사이클 / 감시{len(targets)}코인")
        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main()
