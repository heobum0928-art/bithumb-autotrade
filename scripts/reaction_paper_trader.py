"""
상장공지 반응 페이퍼 트레이더 (reaction_paper_trader) — 모의매매, 매매 API 미호출.

목적(2026-07-04): "업비트/바이낸스 상장공지 → 빗썸 기상장코인 반응(스필오버)" 가설을
실제 자본 없이 전향(forward) 검증. 배경: MPLX/NEX 건(업비트 상장공지 2026-07-03 12:00,
감지지연 6.7초)에서 진입가 대비 +40%+까지 상승 — 기존 백테스트(업비트 n=5, 바이낸스 n=4)
표본이 너무 작아 판정 보류 상태였던 가설을 실제 케이스로 재점화.

철학(사용자 지시, 2026-07-04): "손실은 안 날 수는 없으나 크게 먹는 전략" — 캐스케이드와
동일한 비대칭 출구(손절 짧게+트레일만, 익절상한 없음)를 이 신호에 적용.

동작: upbit_notice_events.csv / binance_notice_events.csv 를 폴링해 새로운
is_listing=True 이벤트를 찾고, 빗썸 기상장 코인이면 즉시 모의 진입(현재가 매수 기록).
이후 각 포지션을 SL/트레일/타임아웃 규칙으로 관리하다 모의 청산.

출구 규칙:
  SL_PCT     = -3.0%   (하드 손절)
  TRAIL_ARM  = +5.0%   (이 수익률 넘으면 트레일 가동)
  TRAIL_PCT  = -5.0%pt (고점 대비 트레일 폭)
  MAX_HOLD_H = 72       (타임아웃, 익절상한 없음)

★ 순수 모의: 매매 API 미호출, live_guard 미연동. 포트 47243.
상태 data/reaction_paper_pos.json | 처리이력 data/reaction_paper_state.json
기록 data/reaction_paper_trades.csv | 로그 logs/reaction_paper_trader.log
Run: python scripts/reaction_paper_trader.py
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
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM); _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try: _sock.bind(("127.0.0.1", 47243))
    except OSError: print("[ERROR] reaction_paper_trader 이미 실행 중 (포트 47243)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [RXPAPER] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/reaction_paper_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

UPBIT_CSV = ROOT / "data" / "upbit_notice_events.csv"
BINANCE_CSV = ROOT / "data" / "binance_notice_events.csv"
STATE = ROOT / "data" / "reaction_paper_state.json"
POS = ROOT / "data" / "reaction_paper_pos.json"
TRADES = ROOT / "data" / "reaction_paper_trades.csv"
POLL_SEC = 20

SL_PCT = -3.0
TRAIL_ARM = 5.0
TRAIL_PCT = -5.0
MAX_HOLD_H = 72
MIN_VOL_24H_KRW = 300_000_000  # 캐스케이드와 동일 유동성 하한 — 너무 얇은 코인은 모의진입해도 현실성 없음

SYMBOL_RE = re.compile(r"\(([A-Z0-9]{2,15})\)")


def load_json(p, default):
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return default


def save_json(p, obj):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def read_csv_rows(p):
    if not p.exists(): return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def log_trade(row):
    new = not TRADES.exists()
    with open(TRADES, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["entry_time", "exit_time", "symbol", "source", "notice_delay_sec",
                             "entry_price", "exit_price", "peak_price", "pnl_pct", "hold_min", "reason"])
        w.writerow(row)


def price(c, coin):
    try: return float(c.get_ticker(coin)["closing_price"])
    except Exception: return 0.0


def trade_value_24h(c, coin):
    try: return float(c.get_ticker(coin).get("acc_trade_value_24H", 0))
    except Exception: return 0.0


def scan_new_events(state, bithumb_coins):
    """두 CSV에서 새 상장이벤트를 찾아 (symbol, source, event_key, delay) 리스트로 반환."""
    found = []
    # 업비트: 여러 코인이 한 공지에 같이 나올 수 있음 (예: MPLX, NEX 동시)
    seen_up = set(state.get("upbit_seen_ids", []))
    for r in read_csv_rows(UPBIT_CSV):
        if r.get("is_listing") != "True": continue
        nid = r.get("notice_id", "")
        if nid in seen_up: continue
        seen_up.add(nid)
        try: delay = float(r.get("delay_sec") or 0)
        except Exception: delay = 0
        if delay > 3600: continue  # 백필 쓰레기(초대형 지연) 제외 — 실시간 감지만 대상
        for sym in SYMBOL_RE.findall(r.get("title", "")):
            if sym in bithumb_coins:
                found.append((sym, "upbit", f"up:{nid}:{sym}", delay))
    state["upbit_seen_ids"] = sorted(seen_up)

    # 바이낸스: bithumb_listed 컬럼 이미 계산됨
    seen_bn = set(state.get("binance_seen_ids", []))
    for r in read_csv_rows(BINANCE_CSV):
        if r.get("is_listing") != "True" or r.get("bithumb_listed") != "True": continue
        aid = r.get("article_id", "")
        if aid in seen_bn: continue
        seen_bn.add(aid)
        try: delay = float(r.get("delay_sec") or 0)
        except Exception: delay = 0
        if delay > 3600: continue
        sym = r.get("symbol", "")
        if sym: found.append((sym, "binance", f"bn:{aid}:{sym}", delay))
    state["binance_seen_ids"] = sorted(seen_bn)

    return found


def main():
    c = BithumbClient()
    state = load_json(STATE, {"upbit_seen_ids": [], "binance_seen_ids": []})
    pos = load_json(POS, {})
    log.info(f"상장공지 반응 페이퍼 트레이더 시작 — SL{SL_PCT}%/트레일가동+{TRAIL_ARM}%/트레일폭{TRAIL_PCT}%pt/"
             f"타임아웃{MAX_HOLD_H}h | 순수모의(매매0) | 보유중 {len(pos)}건")
    try:
        from bithumb import notify
        notify.send(f"📡 상장공지 반응 페이퍼 트레이더 시작 — 순수 모의(매매0). 손절{SL_PCT}%/트레일만, 익절상한 없음")
    except Exception: pass

    try:
        bithumb_coins = c.get_all_coins_v2()
    except Exception:
        bithumb_coins = set()

    while True:
        try:
            if datetime.now(KST).minute % 30 == 0:
                try: bithumb_coins = c.get_all_coins_v2()
                except Exception: pass

            new_events = scan_new_events(state, bithumb_coins)
            save_json(STATE, state)
            for sym, source, key, delay in new_events:
                if key in pos: continue
                cur = price(c, sym)
                if cur <= 0:
                    log.warning(f"진입 스킵 {sym}({source}) — 현재가 조회 실패")
                    continue
                vol24 = trade_value_24h(c, sym)
                if vol24 < MIN_VOL_24H_KRW:
                    log.warning(f"진입 스킵 {sym}({source}) — 유동성 부족(24H거래대금 {vol24:,.0f}<{MIN_VOL_24H_KRW:,.0f})")
                    continue
                pos[key] = {"symbol": sym, "source": source, "delay": delay,
                            "entry_price": cur, "peak_price": cur,
                            "entry_time": datetime.now(KST).isoformat()}
                save_json(POS, pos)
                log.warning(f"[모의] 진입 {sym}({source}) @{cur:g} 공지감지지연={delay:.1f}s")
                try:
                    from bithumb import notify
                    notify.send(f"🎯 상장반응 모의진입 {sym}({source}) @{cur:g}")
                except Exception: pass

            # ── 포지션 관리 ──
            for key in list(pos.keys()):
                p = pos[key]
                cur = price(c, p["symbol"])
                if cur <= 0: continue
                if cur > p["peak_price"]: p["peak_price"] = cur
                entry = p["entry_price"]; peak = p["peak_price"]
                pnl = (cur / entry - 1) * 100
                peak_pnl = (peak / entry - 1) * 100
                hold_h = (datetime.now(KST) - datetime.fromisoformat(p["entry_time"])).total_seconds() / 3600

                reason = None
                if pnl <= SL_PCT:
                    reason = f"손절{SL_PCT}%"
                elif peak_pnl >= TRAIL_ARM and pnl <= peak_pnl + TRAIL_PCT:
                    reason = f"트레일(고점+{peak_pnl:.1f}%)"
                elif hold_h >= MAX_HOLD_H:
                    reason = f"타임아웃{MAX_HOLD_H}h"

                if reason:
                    log_trade([p["entry_time"], datetime.now(KST).isoformat(), p["symbol"], p["source"],
                                f"{p['delay']:.1f}", f"{entry:g}", f"{cur:g}", f"{peak:g}",
                                f"{pnl:+.2f}", f"{hold_h*60:.0f}", reason])
                    log.warning(f"[모의] 청산 {p['symbol']}({p['source']}) @{cur:g} PnL={pnl:+.2f}% | {reason}")
                    try:
                        from bithumb import notify
                        notify.send(f"🩸 상장반응 모의청산 {p['symbol']} {pnl:+.1f}% [{reason}]")
                    except Exception: pass
                    del pos[key]
            save_json(POS, pos)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
