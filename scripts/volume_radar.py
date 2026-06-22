"""
거래대금 레이더 — 평소 대비 거래대금이 비정상 급증한 코인 상시 포착 + 호가 캡처 (순수 로깅, 매매 0).

배경(2026-06-22): 사용자 "거래량 겁나 높은 코인 어떻게 확인하나". 점화봇은 5분 스파이크만 봐서
TAIKO·BICO 같은 *지속 고거래대금* 코인을 놓침. 그리고 절대 거래대금이 아니라 **평소 대비 배수**가
진짜 비정상의 척도(BICO 1618배 등). 핵심 함정: 고거래대금의 절반은 *던짐*(BICO -27%)이라
거래대금만으론 매수신호 불가 → 그 순간 호가(매집 vs 던짐)를 같이 찍어 데이터로 판별.

동작: RADAR_SEC마다 전 코인 24H거래대금 ÷ 직전20일 일거래대금 중앙값 = surge배수.
  - surge >= SURGE_MIN 코인 포착(코인당 COOLDOWN 쿨다운) → 호가 스냅샷 캡처 →
    data/volume_radar_events.csv (surge·등락·호가깊이불균형·매수체결비)
  - 상위 급증 코인 목록을 data/volume_radar_state.json에 기록(퀀트팀 브리핑이 표시 + 신선도 감시)

★ 격리: 매매 0, 독립 프로세스, 어떤 예외도 봇 못 죽임. watchdog 감시. 포트 47227.
Run: python scripts/volume_radar.py
"""
import sys, os, atexit, time, json, csv, glob, socket, logging, statistics as st
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
    try: _sock.bind(("127.0.0.1", 47227))
    except OSError: print("[ERROR] volume_radar 이미 실행 중 (포트 47227)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [RADAR] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/volume_radar.log", encoding="utf-8")])
log = logging.getLogger(__name__)

RADAR_SEC = 180          # 3분마다 스캔
SURGE_MIN = 20.0         # 평소 대비 20배+ = 비정상 거래대금
LIQ_FLOOR = 100_000_000  # 24H 거래대금 최소 1억(잡음 컷)
COOLDOWN_MIN = 30        # 같은 코인 30분 1회만 캡처
TOP_KEEP = 12
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
DAILY = ROOT / "data" / "candles_daily"
CSV_PATH = ROOT / "data" / "volume_radar_events.csv"
STATE = ROOT / "data" / "volume_radar_state.json"


def build_baselines():
    """코인별 직전 20일 일거래대금 중앙값 = 평소 거래대금."""
    out = {}
    for f in glob.glob(str(DAILY / "*_1d.json")):
        coin = os.path.basename(f).replace("_1d.json", "")
        try:
            cand = json.loads(Path(f).read_text(encoding="utf-8"))
            base = [float(x.get("candle_acc_trade_price", 0)) for x in cand[-21:-1]]
            if len(base) >= 20:
                m = st.median(base)
                if m > 0: out[coin] = m
        except Exception:
            pass
    return out


def snapshot(c, coin, levels=15, ntrades=50):
    """점화/급증 순간 호가창+체결흐름 — igniter_alert.micro_snapshot와 동일 정의(동기화 유지)."""
    try:
        ob = c.get_orderbook(coin)
        bids = [(float(b["price"]), float(b["quantity"])) for b in ob.get("bids", [])]
        asks = [(float(a["price"]), float(a["quantity"])) for a in ob.get("asks", [])]
        if not bids or not asks: return None
        best_bid = max(p for p, _ in bids); best_ask = min(p for p, _ in asks)
        mid = (best_bid + best_ask) / 2 or 1e-9
        spread_pct = (best_ask - best_bid) / mid * 100
        bids_s = sorted(bids, key=lambda x: -x[0])[:levels]
        asks_s = sorted(asks, key=lambda x: x[0])[:levels]
        bid_krw = sum(p * q for p, q in bids_s); ask_krw = sum(p * q for p, q in asks_s)
        tot = bid_krw + ask_krw or 1e-9
        depth_imb = (bid_krw - ask_krw) / tot
        bid_wall = (max(p * q for p, q in bids_s) / bid_krw) if bid_krw > 0 else 0
        ask_wall = (max(p * q for p, q in asks_s) / ask_krw) if ask_krw > 0 else 0
    except Exception:
        return None
    buy_ratio = -1.0
    try:
        th = c.get_transaction_history(coin, count=ntrades)
        buy = sell = 0.0
        for t in th:
            val = float(t["units_traded"]) * float(t["price"])
            if t.get("type") == "bid": buy += val
            else: sell += val
        if buy + sell > 0: buy_ratio = buy / (buy + sell)
    except Exception:
        pass
    return {"spread_pct": round(spread_pct, 4), "depth_imb": round(depth_imb, 4),
            "bid_wall": round(bid_wall, 4), "ask_wall": round(ask_wall, 4), "buy_ratio": round(buy_ratio, 4)}


def logrow(row):
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["time","coin","surge","val_24h_eok","chg_24h","spread_pct","depth_imb","bid_wall","ask_wall","buy_ratio"])
        w.writerow(row)


def main():
    c = BithumbClient()
    base = build_baselines(); base_day = datetime.now(KST).date()
    last = {}; cycles = logged = 0
    log.info(f"거래대금 레이더 시작 — 기준 {len(base)}코인 | surge>={SURGE_MIN}배 캡처 | {RADAR_SEC}초 스캔 | 순수 로깅")
    try:
        from bithumb import notify; notify.send(f"📊 거래대금 레이더 시작 — 평소대비 {SURGE_MIN}배+ 급증코인 포착+호가캡처(순수로깅·매매0)")
    except Exception: pass
    while True:
        try:
            if datetime.now(KST).date() != base_day:
                base = build_baselines(); base_day = datetime.now(KST).date()
            t = c.get_ticker("ALL"); now = datetime.now(KST)
            surges = []
            for coin, d in t.items():
                if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
                b = base.get(coin)
                if not b: continue
                try:
                    val = float(d.get("acc_trade_value_24H", 0)); chg = float(d.get("fluctate_rate_24H", 0))
                except Exception: continue
                if val < LIQ_FLOOR: continue
                surges.append((coin, val / b, val, chg))
            surges.sort(key=lambda x: -x[1])
            # 상위 급증 코인 상태 기록(브리핑/신선도)
            top = [{"coin": co, "surge": round(s, 1), "chg": round(ch, 1)} for co, s, v, ch in surges[:TOP_KEEP]]
            # 포착: surge>=SURGE_MIN + 쿨다운 → 호가 캡처
            for coin, surge, val, chg in surges:
                if surge < SURGE_MIN: break
                cd = last.get(coin)
                if cd and (now - cd).total_seconds() < COOLDOWN_MIN * 60: continue
                m = snapshot(c, coin)
                if not m: continue
                logrow([now.strftime("%Y-%m-%d %H:%M:%S"), coin, f"{surge:.1f}", f"{val/1e8:.0f}", f"{chg:+.1f}",
                        m["spread_pct"], m["depth_imb"], m["bid_wall"], m["ask_wall"], m["buy_ratio"]])
                last[coin] = now; logged += 1
                tag = "매집?" if m["depth_imb"] > 0.1 and m["buy_ratio"] > 0.55 else ("던짐?" if m["depth_imb"] < -0.1 or chg < -3 else "중립")
                log.info(f"{coin} 거래대금 {surge:.0f}배({chg:+.1f}%) — 깊이{m['depth_imb']:+.2f} 매수비{m['buy_ratio']:.2f} [{tag}]")
            cycles += 1
            try:
                STATE.write_text(json.dumps({"last_cycle": now.isoformat(), "cycles": cycles,
                                             "rows_logged": logged, "top": top}, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(RADAR_SEC)


if __name__ == "__main__":
    main()
