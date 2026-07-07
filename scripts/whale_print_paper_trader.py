"""
고래발자국(바닥권) 반응 페이퍼 트레이더 — 모의매매, 매매 API 미호출.

목적(2026-07-07): `orderflow_logger.py`(#46)가 감지하는 고래발자국(중앙값 대비
8배+ 단일거래) 중 **박스권 바닥권(range_pos_2h<=0.3)에서 뜬 매수(bid)** 신호만
골라 페이퍼트레이딩. 배경: BLUR 사례 — 05:44 바닥권(30.04, 위치0.1)에서 뜬
고래발자국 매수 이후 15분 뒤 +7.6% 폭발, 고점 대비로는 +8.4%. n=1이라 아직
검증 전 — 이 스크립트로 forward 표본을 쌓는다.

★ 다른 근거로 이미 기각한 것과 구분: 온체인 웨일얼럿(지갑추적, R²~0.002~0.05로
기각)과 다르게 이건 거래소 체결 자체 + 기술적 위치(박스권 바닥)를 결합한 신호.

동작: `data/whale_print_events.csv`를 폴링해 신규 이벤트 중
  side=bid AND range_pos_2h<=0.3
조건 충족 시 즉시 모의진입. 출구는 캐스케이드/#45와 동일 비대칭 철학
(손절 짧게, 트레일만, 익절상한 없음).

출구 규칙:
  SL_PCT     = -3.0%
  TRAIL_ARM  = +5.0%
  TRAIL_PCT  = -5.0%pt
  MAX_HOLD_H = 24     (박스권 돌파형이라 #45의 72h보다 짧게 설정)

★ 순수 모의: 매매 API 미호출, live_guard 미연동. 포트 47247.
상태 data/whale_paper_pos.json | 처리이력 data/whale_paper_state.json
기록 data/whale_paper_trades.csv | 로그 logs/whale_print_paper_trader.log
Run: python scripts/whale_print_paper_trader.py
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
    try: _sock.bind(("127.0.0.1", 47247))
    except OSError: print("[ERROR] whale_print_paper_trader 이미 실행 중 (포트 47247)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [WPAPER] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/whale_print_paper_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

WHALE_CSV = ROOT / "data" / "whale_print_events.csv"
STATE = ROOT / "data" / "whale_paper_state.json"
POS = ROOT / "data" / "whale_paper_pos.json"
TRADES = ROOT / "data" / "whale_paper_trades.csv"
POLL_SEC = 20

RANGE_POS_MAX = 0.3   # 바닥권 기준
SL_PCT = -3.0
TRAIL_ARM = 5.0
TRAIL_PCT = -5.0
MAX_HOLD_H = 24
COIN_COOLDOWN_MIN = 120   # 같은 코인 재진입 쿨다운(중복 방지)


def load_json(p, default):
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return default


def save_json(p, obj):
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def read_csv_rows(p):
    if not p.exists(): return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def log_trade(row):
    new = not TRADES.exists()
    with open(TRADES, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["entry_time", "exit_time", "symbol", "range_pos", "whale_size", "whale_ratio",
                             "entry_price", "exit_price", "peak_price", "pnl_pct", "hold_min", "reason"])
        w.writerow(row)


def price(c, coin):
    try: return float(c.get_ticker(coin)["closing_price"])
    except Exception: return 0.0


def scan_new_events(state):
    seen = set(state.get("seen_keys", []))
    found = []
    for r in read_csv_rows(WHALE_CSV):
        if r.get("side") != "bid": continue
        try:
            rpos = float(r.get("range_pos_2h") or "")
        except Exception:
            continue
        if rpos > RANGE_POS_MAX: continue
        key = f"{r['time']}:{r['coin']}"
        if key in seen: continue
        seen.add(key)
        found.append((r["coin"], key, rpos, r.get("size_krw", ""), r.get("ratio_to_median", "")))
    state["seen_keys"] = sorted(seen)[-3000:]  # 무한증식 방지
    return found


def main():
    c = BithumbClient()
    state = load_json(STATE, {"seen_keys": []})
    pos = load_json(POS, {})
    cooldown = {}
    log.info(f"고래발자국(바닥권) 페이퍼 트레이더 시작 — 위치<={RANGE_POS_MAX} SL{SL_PCT}%/트레일가동+{TRAIL_ARM}%/"
             f"트레일폭{TRAIL_PCT}%pt/타임아웃{MAX_HOLD_H}h | 순수모의(매매0) | 보유중 {len(pos)}건")
    try:
        from bithumb import notify
        notify.send(f"📡 고래발자국(바닥권) 페이퍼 트레이더 시작 — 순수모의(매매0)")
    except Exception: pass

    while True:
        try:
            new_events = scan_new_events(state)
            save_json(STATE, state)
            for coin, key, rpos, size_krw, ratio in new_events:
                if key in pos: continue
                if cooldown.get(coin, 0) > time.time(): continue
                if any(p["symbol"] == coin for p in pos.values()): continue  # 이미 보유중
                cur = price(c, coin)
                if cur <= 0:
                    log.warning(f"진입 스킵 {coin} — 현재가 조회 실패"); continue
                cooldown[coin] = time.time() + COIN_COOLDOWN_MIN * 60
                pos[key] = {"symbol": coin, "range_pos": rpos, "whale_size": size_krw, "whale_ratio": ratio,
                            "entry_price": cur, "peak_price": cur,
                            "entry_time": datetime.now(KST).isoformat()}
                save_json(POS, pos)
                log.warning(f"[모의] 진입 {coin} @{cur:g} 위치={rpos:.2f} 고래규모={size_krw}원({ratio}배)")
                try:
                    from bithumb import notify
                    notify.send(f"🎯 고래발자국 모의진입 {coin} @{cur:g} 바닥권위치={rpos:.2f}")
                except Exception: pass

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
                    log_trade([p["entry_time"], datetime.now(KST).isoformat(), p["symbol"], f"{p['range_pos']:.2f}",
                                p["whale_size"], p["whale_ratio"], f"{entry:g}", f"{cur:g}", f"{peak:g}",
                                f"{pnl:+.2f}", f"{hold_h*60:.0f}", reason])
                    log.warning(f"[모의] 청산 {p['symbol']} @{cur:g} PnL={pnl:+.2f}% | {reason}")
                    try:
                        from bithumb import notify
                        notify.send(f"🩸 고래발자국 모의청산 {p['symbol']} {pnl:+.1f}% [{reason}]")
                    except Exception: pass
                    del pos[key]
            save_json(POS, pos)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
