"""
거래량 매집 단타 (volaccum_trader) — volume_radar 신호 기반 실전.

=== 2026-06-30 재분석 결과 (8일치 2779건) ===

레이블별 30분 후 가격 delta:
  [던짐?] avg+0.07%  (n=1895) ← 가장 좋음, 이름과 반대
  [중립]  avg-0.74%  (n=542)
  [매집?] avg-0.53%  (n=274) ← 오히려 음수, 기존 가정 틀림

매수비별:
  < 0.3   avg+0.04%  ← 낮을수록 좋음 (기존 >=0.57 필터 역효과)
  0.3~0.5 avg-0.08%
  0.5~0.7 avg-0.29%
  >= 0.7  avg-0.38%

거래대금배수별:
  20~50배  avg+0.15%  ← 최선
  50~100배 avg-0.34%
  100배+   avg-0.27%

깊이별:
  음수(<0) avg-0.08%  ← 상대적으로 나음
  0~0.3   avg-0.11%
  >=0.3   avg-0.30%

상위 코인 (avg_gain): MANTA+0.73%, ACE+0.65%, AXS+0.53%, TAIKO+0.29%, AGLD+0.24%, CARV+0.18%
음수 코인 (블랙리스트): LAYER-2.89%, HOOK-1.49%, STRAX-1.17%, VERONA-0.84%,
  BLUE-0.84%, POWR-0.77%, SYRUP-0.65%, TT-0.55%, BTR-0.55%, POKT-0.40%, BEL-0.30%

주의: 최대 avg_gain이 +0.73%에 불과 → 30분 스냅샷 기반으로는 TP5% 도달 어려움.
  현재 전략은 신호 탐지 → 호가 확인 → 진입 흐름이므로, 조건을 현실화:
  - 레이블 무관 (던짐?도 포함)
  - 매수비 낮게 (0.4 이하, 단 0.1 이상으로 최소 유동성 확보)
  - 거래대금 20~50배 구간 집중
  - 깊이 필터 완화

진입: 거래량 20배+ / 매수비 0.10~0.45 / 깊이 제한 없음 / 블랙리스트 제외
청산: +3% TP / -1.5% SL / 30분 타임아웃 (수익폭 현실화)
포트 47237. Run: python scripts/volaccum_trader.py
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
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try: _sock.bind(("127.0.0.1", 47237))
    except OSError: print("[ERROR] volaccum_trader 포트 47237 충돌."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb import notify

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [VACC] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("logs/volaccum_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

# ── 파라미터 ──
LIVE         = True
ENTRY_KRW    = 50_000
TP_PCT       = 3.0         # 5.0→3.0: avg_gain 최고 0.73% → 5% TP는 현실적으로 불가능
SL_PCT       = 1.5         # 2.0→1.5: avg_loss도 작으므로 SL 좁혀서 손실 제한
TIMEOUT_MIN  = 30
SLOTS        = 3
POLL         = 60          # 1분 폴링
VOL_MULT_MIN = 20          # 20배+: 20~50배 구간이 avg+0.15% (최선)
VOL_MULT_MAX = 80          # 신규: 100배+ 코인은 avg-0.27%로 오히려 나쁨 → 80배 상한
BUY_RATIO_MIN= 0.10        # 0.57→0.10: 매수비 낮을수록 좋음 (재분석), 최소 유동성만 확보
BUY_RATIO_MAX= 0.45        # 신규: 0.45 초과 시 avg 음수 → 상한 추가
DEPTH_MIN    = -1.0        # 0.0→-1.0: 깊이 음수가 오히려 좋음, 사실상 필터 해제
# 블랙리스트: avg_gain 음수 코인 (n>=10 기준)
# 제거: BICO(+0.09%), MANTA(+0.73%), ARK(+0.25%)  ← 재분석에서 양수 확인
# 추가: HOOK(-1.49%), STRAX(-1.17%), VERONA(-0.84%), BLUE(-0.84%), POWR(-0.77%),
#        SYRUP(-0.65%), TT(-0.55%), BTR(-0.55%), POKT(-0.40%), BEL(-0.30%),
#        LAYER(-2.89%), MAGIC(-0.46%), MET(-0.47%), MMT(-0.19%), CSPR(-0.20%)
BLACKLIST    = {
    "LAYER",   # avg-2.89% (n=9)
    "HOOK",    # avg-1.49% (n=61)
    "STRAX",   # avg-1.17% (n=19)
    "VERONA",  # avg-0.84% (n=92)
    "BLUE",    # avg-0.84% (n=68) ← 기존 화이트리스트였으나 실제로 음수
    "POWR",    # avg-0.77% (n=30)
    "SYRUP",   # avg-0.65% (n=19)
    "TT",      # avg-0.55% (n=110) ← 기존 화이트리스트였으나 실제로 음수
    "BTR",     # avg-0.55% (n=28)
    "POKT",    # avg-0.40% (n=54)
    "MAGIC",   # avg-0.46% (n=30)
    "BEL",     # avg-0.30% (n=252)
    "MET",     # avg-0.53% (n=22)
    "MMT",     # avg-0.19% (n=100)
    "CSPR",    # avg-0.20% (n=91)
    "XPLA",    # avg+0.10% (n=9, 불안정)
    "G",       # avg-0.08% (n=201) ← 기존 화이트리스트였으나 실제로 음수
}

POS_PATH   = ROOT / "data" / "volaccum_pos.json"
TRADE_PATH = ROOT / "data" / "volaccum_trades.csv"

_positions = {}


def load_pos():
    try: return json.loads(POS_PATH.read_text(encoding="utf-8"))
    except Exception: return {}

def save_pos(p):
    tmp = POS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, POS_PATH)

def log_trade(coin, entry, exit_p, pnl, reason, held_min):
    new = not TRADE_PATH.exists()
    with open(TRADE_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["exit_time","coin","entry","exit","pnl_pct","reason","held_min"])
        w.writerow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    coin, f"{entry:.4f}", f"{exit_p:.4f}", f"{pnl:+.2f}", reason, f"{held_min:.0f}"])

def get_tickers(c):
    try:
        t = c.get_ticker("ALL")
        out = {}
        for coin, d in t.items():
            if coin == "date" or not isinstance(d, dict): continue
            try:
                price = float(d.get("closing_price") or 0)
                vol   = float(d.get("acc_trade_value_24H") or 0)
                if price > 0: out[coin] = {"price": price, "vol": vol}
            except Exception: continue
        return out
    except Exception as e:
        log.warning(f"티커 실패: {e}"); return {}

def get_orderbook(c, coin):
    """매수비·깊이 계산."""
    try:
        ob = c.get_orderbook(coin)
        asks = ob.get("asks", [])
        bids = ob.get("bids", [])
        ask_vol = sum(float(a.get("quantity", 0)) for a in asks[:5])
        bid_vol = sum(float(b.get("quantity", 0)) for b in bids[:5])
        total = ask_vol + bid_vol
        buy_ratio = bid_vol / total if total > 0 else 0.5
        depth = (bid_vol - ask_vol) / total if total > 0 else 0
        return buy_ratio, depth
    except Exception:
        return 0.5, 0.0


def main():
    c = BithumbClient()
    pos = load_pos()
    vol_baseline = {}   # coin → 24H 거래대금 기준값

    log.info(f"거래량매집 단타 [실전] — VOL {VOL_MULT_MIN}~{VOL_MULT_MAX}배 매수비 {BUY_RATIO_MIN}~{BUY_RATIO_MAX} | TP+{TP_PCT}% SL-{SL_PCT}% {TIMEOUT_MIN}분 | {SLOTS}슬롯×{ENTRY_KRW//10000}만")
    try: notify.send(f"📊 거래량매집 실전 시작 (재분석 파라미터) — TP+{TP_PCT}% SL-{SL_PCT}% {TIMEOUT_MIN}분")
    except Exception: pass

    while True:
        try:
            now = time.time()
            tickers = get_tickers(c)
            if not tickers:
                time.sleep(POLL); continue

            # 베이스라인 초기화 (첫 실행 시)
            if not vol_baseline:
                for coin, d in tickers.items():
                    vol_baseline[coin] = d["vol"]
                log.info(f"베이스라인 설정 {len(vol_baseline)}코인")
                time.sleep(POLL); continue

            # ── 청산 ──
            for coin in list(pos.keys()):
                p = pos[coin]
                cur = tickers.get(coin, {}).get("price")
                if not cur or cur <= 0: continue
                p["highest"] = max(p.get("highest", cur), cur)
                pnl = (cur / p["entry"] - 1) * 100
                held_min = (now - p["entered_ts"]) / 60
                sl_hit = pnl <= -SL_PCT
                tp_hit = pnl >= TP_PCT
                to_hit = held_min >= TIMEOUT_MIN
                if sl_hit or tp_hit or to_hit:
                    reason = f"TP+{TP_PCT}%" if tp_hit else (f"SL-{SL_PCT}%" if sl_hit else f"타임아웃{TIMEOUT_MIN}분")
                    if LIVE and p.get("volume", 0) > 0:
                        try:
                            c.market_sell(f"KRW-{coin}", p["volume"])
                            log.info(f"[실전] 매도 완료 {coin} {p['volume']:.6f}개")
                        except Exception as e:
                            log.error(f"[실전] 매도 실패 {coin}: {e}")
                    log.info(f"[실전] 청산 {coin} @{cur:,.4f} PnL={pnl:+.2f}% | {reason} ({held_min:.0f}분)")
                    log_trade(coin, p["entry"], cur, pnl, reason, held_min)
                    try: notify.send(f"📊 매집단타 청산 {coin} {pnl:+.1f}% [{reason}]")
                    except Exception: pass
                    del pos[coin]; save_pos(pos)

            # ── 진입 ──
            if len(pos) < SLOTS:
                for coin, d in tickers.items():
                    if len(pos) >= SLOTS: break
                    if coin in pos or coin in BLACKLIST: continue
                    base = vol_baseline.get(coin, 0)
                    if base <= 0: continue
                    mult = d["vol"] / base
                    # 20~80배: 100배+ 코인은 avg-0.27%로 나쁨
                    if mult < VOL_MULT_MIN or mult > VOL_MULT_MAX: continue

                    # 호가 확인: 매수비 0.10~0.45 (낮을수록 avg_gain 좋음, 재분석 결과)
                    buy_ratio, depth = get_orderbook(c, coin)
                    if buy_ratio < BUY_RATIO_MIN or buy_ratio > BUY_RATIO_MAX: continue
                    if depth <= DEPTH_MIN: continue

                    price = d["price"]
                    volume = 0.0
                    if LIVE:
                        try:
                            c.market_buy(f"KRW-{coin}", ENTRY_KRW)
                            volume = round(ENTRY_KRW / price * 0.9975, 8)
                            log.info(f"[실전] 매수 완료 {coin} ~{volume:.6f}개")
                        except Exception as e:
                            log.error(f"[실전] 매수 실패 {coin}: {e}"); continue

                    pos[coin] = {"entry": price, "highest": price, "volume": volume,
                                 "entered_ts": now, "entered": datetime.now(KST).isoformat(),
                                 "mult": mult, "buy_ratio": buy_ratio}
                    save_pos(pos)
                    log.info(f"[실전] 진입 {coin} @{price:,.4f} — 거래량{mult:.0f}배 매수비{buy_ratio:.2f} 깊이{depth:+.2f}")
                    try: notify.send(f"📊 매집단타 진입 {coin} @{price:,.0f}원 — {mult:.0f}배 매수비{buy_ratio:.2f}")
                    except Exception: pass

            # 베이스라인 서서히 갱신 (1시간 EMA)
            for coin, d in tickers.items():
                if coin in vol_baseline:
                    vol_baseline[coin] = vol_baseline[coin] * 0.999 + d["vol"] * 0.001

            save_pos(pos)

        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
