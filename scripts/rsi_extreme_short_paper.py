"""
RSI 극단 과매수 숏 페이퍼 트레이더 (rsi_extreme_short_paper) — 순수 모의, 실주문 0.

검증(2026-07-12, 기술지표 오토리서치 5각도 중 유일 생존):
  규칙: 5분봉 RSI(14) > 92 AND 해당봉 거래대금 > 직전20봉 평균의 3배
        AND 24h 상승률 < 40% (실전봇과 겹치지 않게)
        → 다음봉 시가에 SHORT, 4시간 홀딩
  적대검증(독립 재구현): TRAIN EV +1.28% → TEST +3.70%(t_day 4.12), OOS가 더 강함.
  2배 청산 0/202건(최대역행 43%), 무작위숏 대조군은 양쪽 마이너스(=베타 아님, 진짜 알파),
  상위3코인 제거 +2.36%, 최고주 제거 +2.87%, 분할점 5개 전부 유지.
  ★실전봇(24h+40% 숏)과 신호 겹침 9.9%뿐, 손익상관 ~0 → 완전히 독립적인 자리.
  판정: MARGINAL (통계는 강하나 표본 202건) → 실전 전 forward 모의로 표본 축적.

주의: 다른 정통 지표(RSI30/70, MACD, 볼린저, 스토캐스틱)는 106조합 전멸.
      거래량 미시구조·1분봉 추론도 전부 사망. 이것만 살아남음.

★ 순수 모의: 주문 API 미호출. 포트 47252.
포지션 data/rsi_short_pos.json | 기록 data/rsi_short_trades.csv | 로그 logs/rsi_extreme_short.log
"""
import sys, os, atexit, time, json, csv, socket, logging, statistics
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
    try: _sock.bind(("127.0.0.1", 47252))
    except OSError: print("[ERROR] rsi_extreme_short_paper 이미 실행 중 (포트 47252)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb import notify

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [RSISHORT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/rsi_extreme_short.log", encoding="utf-8")])
log = logging.getLogger(__name__)

BASE = "https://api.binance.com"
POLL_SEC = 300
RSI_MIN = 92.0            # 5분봉 RSI(14) 문턱
VOL_MULT = 3.0            # 신호봉 거래대금 / 직전20봉 평균
MAX_24H_CHG = 40.0        # 24h 상승률 이 미만만 (실전봇 영역과 분리)
HOLD_H = 4
COOLDOWN_H = 8
COST_PCT = 0.20 + 0.12/24*HOLD_H   # 왕복비용 + 4h 대출이자
MIN_QUOTE_VOL_5M = 10_000          # 유동성: 5분봉 거래대금 1만 USDT+ (검증서 이 필터가 오히려 개선)

BORROW_PATH = ROOT / "data" / "_borrowable_all.txt"
POS_PATH = ROOT / "data" / "rsi_short_pos.json"
TRADES_PATH = ROOT / "data" / "rsi_short_trades.csv"


def _load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception: return d
def _save(p, o):
    tmp = Path(p).with_suffix(".tmp"); tmp.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8"); tmp.replace(p)


def universe():
    try:
        return [c for c in BORROW_PATH.read_text(encoding="utf-8").split() if c]
    except Exception:
        return []


def wilder_rsi(closes, period=14):
    """Wilder RSI — 검증에 쓴 것과 동일 방식."""
    if len(closes) < period + 1:
        return None
    gains = []; losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag/al)


def check_signal(sym):
    """(신호?, rsi, 거래량배수, 현재가, 24h변동) — 5분봉 30개 조회."""
    try:
        r = requests.get(f"{BASE}/api/v3/klines", params={"symbol": sym, "interval": "5m", "limit": 30}, timeout=8)
        if r.status_code != 200: return False, 0, 0, 0, 0
        k = r.json()
        if len(k) < 25: return False, 0, 0, 0, 0
        cl = [float(x[4]) for x in k]
        vq = [float(x[7]) for x in k]
        rsi = wilder_rsi(cl)
        if rsi is None: return False, 0, 0, 0, 0
        avg20 = sum(vq[-21:-1]) / 20
        if avg20 <= 0 or vq[-1] < MIN_QUOTE_VOL_5M: return False, rsi, 0, cl[-1], 0
        vr = vq[-1] / avg20
        return (rsi > RSI_MIN and vr > VOL_MULT), rsi, vr, cl[-1], 0
    except Exception:
        return False, 0, 0, 0, 0


def log_trade(row):
    new = not TRADES_PATH.exists()
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry_time","exit_time","symbol","rsi","vol_mult","chg24",
                                           "entry_price","exit_price","pnl_pct","mfe_pct","mae_pct","reason"])
        if new: w.writeheader()
        w.writerow(row)


def main():
    positions = _load(POS_PATH, {}); cooldown = {}
    uni = universe()
    log.info(f"RSI극단 숏 페이퍼 시작 — 대출가능 {len(uni)}코인, RSI>{RSI_MIN:.0f} & 거래량{VOL_MULT:.0f}배 & 24h<{MAX_24H_CHG:.0f}% → {HOLD_H}h숏 | 순수모의(주문0)")
    try: notify.send(f"📉 RSI극단 숏 페이퍼 시작 — RSI>{RSI_MIN:.0f}+거래량{VOL_MULT:.0f}배 숏, 모의(주문0)")
    except Exception: pass

    while True:
        try:
            now = time.time()
            uni = universe() or uni
            # 24h 변동률 (실전봇 영역 제외용)
            chg24 = {}
            try:
                for x in requests.get(f"{BASE}/api/v3/ticker/24hr", timeout=15).json():
                    chg24[x["symbol"]] = float(x["priceChangePercent"])
            except Exception:
                pass

            # 1) 신호 탐지
            for coin in uni:
                sym = f"{coin}USDT"
                if sym in positions or cooldown.get(sym, 0) > now:
                    continue
                c24 = chg24.get(sym, 0)
                if c24 >= MAX_24H_CHG:   # 실전봇이 잡는 영역 → 스킵(중복 방지)
                    continue
                hit, rsi, vr, px, _ = check_signal(sym)
                if not hit or px <= 0:
                    continue
                positions[sym] = {"entry_ts": now, "entry_price": px, "rsi": round(rsi,1), "vr": round(vr,1),
                                  "chg24": round(c24,1), "exit_ts": now + HOLD_H*3600,
                                  "min_p": px, "max_p": px, "entry_iso": datetime.now(KST).isoformat()}
                cooldown[sym] = now + COOLDOWN_H*3600
                log.warning(f"숏 진입(모의) {sym} @{px:g} RSI{rsi:.0f} 거래량{vr:.1f}배 24h{c24:+.0f}% → {HOLD_H}h후 청산")
                try: notify.send(f"📉 RSI극단 숏 진입(모의) {sym} RSI{rsi:.0f} 거래량{vr:.0f}배 @{px:g}")
                except Exception: pass

            # 2) 추적 + 만기 청산
            if positions:
                try:
                    prices = {x["symbol"]: float(x["price"]) for x in requests.get(f"{BASE}/api/v3/ticker/price", timeout=10).json()}
                except Exception:
                    prices = {}
                for sym in list(positions.keys()):
                    p = positions[sym]
                    px = prices.get(sym)
                    if px and px > 0:
                        p["min_p"] = min(p["min_p"], px); p["max_p"] = max(p["max_p"], px)
                    if now < p["exit_ts"]:
                        continue
                    exit_px = px if (px and px > 0) else p["entry_price"]
                    entry = p["entry_price"]
                    pnl = (1 - exit_px/entry)*100 - COST_PCT
                    mfe = (1 - p["min_p"]/entry)*100
                    mae = (1 - p["max_p"]/entry)*100
                    log_trade(dict(entry_time=p["entry_iso"], exit_time=datetime.now(KST).isoformat(), symbol=sym,
                                   rsi=p["rsi"], vol_mult=p["vr"], chg24=p["chg24"],
                                   entry_price=entry, exit_price=exit_px, pnl_pct=round(pnl,2),
                                   mfe_pct=round(mfe,2), mae_pct=round(mae,2), reason=f"{HOLD_H}h만기"))
                    log.warning(f"숏 청산(모의) {sym} @{exit_px:g} pnl={pnl:+.2f}% (최대유리+{mfe:.1f}% 최대역행{mae:+.1f}%)")
                    try: notify.send(f"📈 RSI극단 숏 청산(모의) {sym} pnl={pnl:+.1f}%")
                    except Exception: pass
                    del positions[sym]

            _save(POS_PATH, positions)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
