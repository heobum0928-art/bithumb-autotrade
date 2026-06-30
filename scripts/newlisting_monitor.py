"""
신규상장 감지 + 자동 진입 (newlisting_monitor) — 빗썸 신규상장 첫 펌핑 실전.

근거(2026-06-30 에이전트 분석): 학술 연구 327건 기준 상장일 평균 +5.7%, 바이낸스 +41%.
0.25% 수수료를 압도하는 유일한 구간. 백테 불필요 — 상장 이벤트 자체가 엣지.

동작:
  1. 빗썸 전체 ticker 10초 폴링 → 새 코인 출현 = 신규상장 감지
  2. 첫 체결가 확인 후 즉시 시장가 매수 (ENTRY_KRW)
  3. 손절-SL% / 고점+TRAIL_TRIGGER% → 고점-TRAIL_PCT% 트레일 / TIMEOUT_MIN분 타임아웃
  4. 가격궤적 CSV 기록 + 텔레그램 알림

포트 47229. Run: python scripts/newlisting_monitor.py
"""
import sys, os, atexit, time, json, csv, socket, logging, threading
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
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("logs/newlisting_monitor.log", encoding="utf-8")])
log = logging.getLogger(__name__)

# ── 파라미터 ──
LIVE         = True
ENTRY_KRW    = 100_000    # 10만원 (신규상장 변동성 큼)
SL_PCT       = 5.0        # 손절 -5%
TRAIL_TRIGGER= 20.0       # 트레일 시작 기준 고점 +20%
TRAIL_PCT    = 10.0       # 고점 대비 트레일 %
TIMEOUT_MIN  = 30         # 30분 타임아웃
POLL         = 10         # 폴링 10초
TRACK_HOURS  = 6
SNAP_SEC     = 60
WAIT_FIRST_MAX = 90       # 첫 체결가 기다리는 최대 초

KNOWN    = ROOT / "data" / "known_coins.json"
CSV_PATH = ROOT / "data" / "newlisting_events.csv"
POS_PATH = ROOT / "data" / "newlisting_pos.json"

_pos_lock = threading.Lock()
_positions = {}   # coin -> {entry, highest, volume, entered_ts, live}


def load_known():
    try: return set(json.loads(KNOWN.read_text(encoding="utf-8")))
    except Exception: return set()

def save_known(s):
    try: KNOWN.write_text(json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8")
    except Exception as e: log.warning(f"known 저장 실패: {e}")

def save_pos():
    with _pos_lock:
        tmp = POS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_positions, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, POS_PATH)

def logsnap(coin, mins, info):
    new = not CSV_PATH.exists()
    try:
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(["time","coin","mins_since_list","price","chg24h","vol_krw"])
            w.writerow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), coin, f"{mins:.0f}",
                        info.get("closing_price",""), info.get("fluctate_rate_24H",""),
                        info.get("acc_trade_value_24H","")])
    except Exception as e:
        log.warning(f"궤적 기록 실패: {e}")

def log_trade(coin, entry, exit_p, pnl, reason, held_min):
    path = ROOT / "data" / "newlisting_trades.csv"
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["exit_time","coin","entry","exit","pnl_pct","reason","held_min"])
        w.writerow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                    coin, f"{entry:.4f}", f"{exit_p:.4f}", f"{pnl:+.2f}", reason, f"{held_min:.0f}"])


def enter_position(c: BithumbClient, coin: str, price: float):
    """신규상장 진입 (별도 스레드에서 호출)."""
    volume = 0.0
    if LIVE:
        try:
            c.market_buy(f"KRW-{coin}", ENTRY_KRW)
            volume = round(ENTRY_KRW / price * 0.9975, 8)
            log.info(f"[실전] 매수 완료 {coin} @{price:,.2f} ~{volume:.6f}개")
        except Exception as e:
            log.error(f"[실전] 매수 실패 {coin}: {e}"); return

    with _pos_lock:
        _positions[coin] = {
            "entry": price, "highest": price, "volume": volume,
            "entered_ts": time.time(),
            "entered": datetime.now(KST).isoformat(),
            "live": LIVE
        }
    save_pos()
    tag = "[실전]" if LIVE else "[모의]"
    log.info(f"{tag} 신규상장 진입 {coin} @{price:,.2f}")
    try:
        notify.send(f"🆕 신규상장 진입 {coin} @{price:,.0f}원 {tag}\n손절-{SL_PCT}% / 트레일+{TRAIL_TRIGGER}%→-{TRAIL_PCT}% / {TIMEOUT_MIN}분")
    except Exception: pass


def wait_and_enter(c: BithumbClient, coin: str):
    """첫 체결가 기다린 후 진입."""
    deadline = time.time() + WAIT_FIRST_MAX
    while time.time() < deadline:
        try:
            tk = c.get_ticker(coin)
            price = float(tk.get("closing_price", 0) or 0)
            if price > 0:
                enter_position(c, coin, price)
                return
        except Exception: pass
        time.sleep(2)
    log.warning(f"{coin} 첫 체결가 {WAIT_FIRST_MAX}초 내 못 잡음 — 진입 포기")


def check_exits(c: BithumbClient, tk_all: dict):
    """포지션 청산 체크."""
    with _pos_lock:
        coins = list(_positions.keys())

    for coin in coins:
        with _pos_lock:
            p = _positions.get(coin)
        if p is None: continue

        info = tk_all.get(coin, {})
        try: cur = float(info.get("closing_price", 0) or 0)
        except Exception: continue
        if cur <= 0: continue

        with _pos_lock:
            p["highest"] = max(p.get("highest", cur), cur)
            highest = p["highest"]

        pnl     = (cur / p["entry"] - 1) * 100
        hp      = (highest / p["entry"] - 1) * 100
        held_min= (time.time() - p["entered_ts"]) / 60

        sl_hit    = pnl <= -SL_PCT
        trail_hit = (hp >= TRAIL_TRIGGER) and (pnl <= hp - TRAIL_PCT)
        to_hit    = held_min >= TIMEOUT_MIN

        if not (sl_hit or trail_hit or to_hit):
            continue

        reason = (f"손절-{SL_PCT}%" if sl_hit else
                  f"트레일(고점+{hp:.1f}%→현재{pnl:+.1f}%)" if trail_hit else
                  f"타임아웃{TIMEOUT_MIN}분")

        if LIVE and p.get("volume", 0) > 0:
            try:
                c.market_sell(f"KRW-{coin}", p["volume"])
                log.info(f"[실전] 매도 완료 {coin} {p['volume']:.6f}개")
            except Exception as e:
                log.error(f"[실전] 매도 실패 {coin}: {e}")

        tag = "[실전]" if p.get("live") else "[모의]"
        log.info(f"{tag} 청산 {coin} @{cur:,.2f} PnL={pnl:+.2f}% | {reason} ({held_min:.0f}분보유)")
        log_trade(coin, p["entry"], cur, pnl, reason, held_min)
        try:
            notify.send(f"🆕 신규상장 청산 {coin} {pnl:+.1f}% [{reason}] {tag}")
        except Exception: pass

        with _pos_lock:
            _positions.pop(coin, None)
        save_pos()


def main():
    c = BithumbClient()
    known = load_known()
    try:
        cur = {k for k in c.get_ticker("ALL") if k != "date"}
    except Exception:
        cur = set()
    base_new = cur - known
    if base_new:
        log.info(f"시작 baseline 병합(알림X): {sorted(base_new)}")
    known |= cur; save_known(known)
    tracking = {}
    last_snap = {}
    tag = "[실전]" if LIVE else "[모의]"
    log.info(f"신규상장 감지+자동진입 {tag} — 등록 {len(known)}코인 | 진입{ENTRY_KRW//10000}만 손절-{SL_PCT}% 트레일+{TRAIL_TRIGGER}%→-{TRAIL_PCT}% {TIMEOUT_MIN}분")
    try: notify.send(f"🆕 신규상장 자동진입 시작 {tag} — {len(known)}종 감시. 상장 감지 즉시 {ENTRY_KRW//10000}만원 매수.")
    except Exception: pass

    while True:
        try:
            tk = c.get_ticker("ALL")
            cur = {k for k in tk if k != "date"}
            new = cur - known

            for coin in sorted(new):
                info = tk.get(coin, {})
                px = info.get("closing_price", "?")
                log.warning(f"🆕🆕 신규상장 감지! {coin}/KRW @{px}원")
                try: notify.send(f"🆕🆕 신규상장 감지! {coin} @{px}원 — 즉시 진입 시도 중...")
                except Exception: pass
                tracking[coin] = datetime.now(KST)
                logsnap(coin, 0, info)
                threading.Thread(target=wait_and_enter, args=(c, coin), daemon=True).start()

            if new:
                known = cur; save_known(known)

            # 궤적 기록
            now_kst = datetime.now(KST)
            for coin, t0 in list(tracking.items()):
                mins = (now_kst - t0).total_seconds() / 60
                if mins > TRACK_HOURS * 60:
                    del tracking[coin]; continue
                last = last_snap.get(coin, t0 - timedelta(seconds=SNAP_SEC))
                if (now_kst - last).total_seconds() >= SNAP_SEC:
                    logsnap(coin, mins, tk.get(coin, {})); last_snap[coin] = now_kst

            # 청산 체크
            check_exits(c, tk)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
