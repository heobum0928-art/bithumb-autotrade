"""
모멘텀 홀딩 모의봇 (momentum_trader) — 강세장 트렌드 팔로잉.

근거(2026-06-30): 단타(CASCADE/LEAD)는 슬리피지 비용 못 이김. 강세장에서 진짜
수익은 모멘텀 초입 올라타서 며칠 들고 가기 (#42). 거래대금 상위 + 24H +5~30%
구간(초중반, 막차 제외) 진입 → 손절-7% / 고점+15%→트레일-10%로 길게 태움.

진입조건: 거래대금 30억+ ∩ 24H +5~30% ∩ 1H 상승 중 ∩ BTC -2% 이상
청산: 손절-7% / 트레일(고점+15%→고점-10%) / 72H 타임아웃
실거래 여부는 live_guard(engine='momentum')가 결정 — armed_engines에 없으면 항상 모의(2026-07-01).
사전등록 게이트 통과 전까지 절대 arm 금지. 포트 47236.
상태 data/momentum_pos.json | 거래기록 data/momentum_trades.csv
Run: python scripts/momentum_trader.py
"""
import sys, os, atexit, time, json, csv, socket, logging
from collections import deque
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
    try:
        _sock.bind(("127.0.0.1", 47236))
    except OSError:
        print("[ERROR] momentum_trader 포트 47236 충돌."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb.live_guard import LiveGuard, live_status, load_config

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MOM] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/momentum_trader.log", encoding="utf-8")
    ]
)
log = logging.getLogger(__name__)

# ── 파라미터 ──
# 실거래 여부는 live_guard(engine='momentum')가 결정 — armed_engines에 없으면 항상 모의(2026-07-01)
CHG_MIN = 5.0          # 24H 최소 상승률 %
CHG_MAX = 30.0         # 24H 최대 상승률 (막차 제외)
LIQ_FLOOR = 3_000_000_000  # 거래대금 30억+
SL = 7.0               # 손절 %
TRAIL_TRIGGER = 15.0   # 트레일 시작 기준 (고점+15% 달성 시)
TRAIL = 10.0           # 고점 대비 트레일 %
TIMEOUT_H = 72         # 최대 보유 시간
SLOTS = 3
ENTRY_KRW_DRY = 50_000 # 모의 기본 슬롯당 금액
CYCLE = 300            # 5분 폴링


def is_live():
    ls = live_status(); return bool(ls.get("enabled")) and "momentum" in ls.get("armed", [])
BTC_SL = -2.0          # BTC 이 이상 하락 중이면 신규 진입 안함
COOLDOWN_H = 24        # 청산 후 재진입 쿨다운
STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}

POS = ROOT / "data" / "momentum_pos.json"
TRADES = ROOT / "data" / "momentum_trades.csv"
COOLDOWN_F = ROOT / "data" / "momentum_cooldown.json"

_price_buf: dict[str, deque] = {}  # coin → deque[(ts, price)], 5분봉 15개(75분)


def load_json(path, default):
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except Exception: pass
    return default

def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)

def log_trade(row):
    new = not TRADES.exists()
    with open(TRADES, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["exit_time","coin","entry","exit","pnl_pct","reason","held_h","chg24h_at_entry"])
        w.writerow(row)

def get_tickers(c) -> dict:
    try:
        t = c.get_ticker("ALL")
        out = {}
        for coin, d in t.items():
            if coin == "date" or coin in STABLE or not isinstance(d, dict): continue
            try:
                price = float(d.get("closing_price") or 0)
                chg24 = float(d.get("fluctate_rate_24H") or 0)
                vol   = float(d.get("acc_trade_value_24H") or 0)
                if price > 0:
                    out[coin] = {"price": price, "chg24": chg24, "vol": vol}
            except Exception: continue
        return out
    except Exception as e:
        log.warning(f"티커 실패: {e}"); return {}

def update_buf(tickers: dict):
    now = time.time()
    for coin, d in tickers.items():
        if coin not in _price_buf:
            _price_buf[coin] = deque(maxlen=15)
        _price_buf[coin].append((now, d["price"]))

def hour_ago_price(coin: str) -> float | None:
    buf = _price_buf.get(coin)
    if not buf or len(buf) < 3: return None
    cutoff = time.time() - 3600
    old = None
    for ts, p in buf:
        if ts <= cutoff: old = p
    return old


def main():
    c = BithumbClient()
    pos  = load_json(POS, {})
    cool = load_json(COOLDOWN_F, {})
    mode = "🔴실전" if is_live() else "모의"

    log.info(
        f"모멘텀 홀딩 시작 [{mode}] — 24H+{CHG_MIN}~{CHG_MAX}% 거래대금{LIQ_FLOOR/1e8:.0f}억+ | "
        f"손절-{SL}% 트레일(고점+{TRAIL_TRIGGER}%→-{TRAIL}%) 타임{TIMEOUT_H}H | "
        f"슬롯{SLOTS}×{ENTRY_KRW_DRY//10000}만"
    )
    try:
        from bithumb import notify
        notify.send(
            f"📈 모멘텀 홀딩 시작 (#42) [{mode}] — 강세장 트렌드팔로잉. "
            f"24H+{CHG_MIN}~{CHG_MAX}%, 손절-{SL}%, 트레일-{TRAIL}%, 72H보유. 실전은 live_guard 게이트 통과시만"
        )
    except Exception: pass

    while True:
        try:
            now     = time.time()
            now_kst = datetime.now(KST)

            # 쿨다운 만료 정리
            cool = {k: v for k, v in cool.items() if v > now}

            tickers = get_tickers(c)
            if not tickers:
                time.sleep(CYCLE); continue

            update_buf(tickers)
            btc_chg = tickers.get("BTC", {}).get("chg24", 0)

            # ── 청산 ──
            for coin in list(pos.keys()):
                p   = pos[coin]
                cur = tickers.get(coin, {}).get("price")
                if not cur or cur <= 0: continue

                p["highest"] = max(p.get("highest", cur), cur)
                pnl     = (cur / p["entry"] - 1) * 100
                hp      = (p["highest"] / p["entry"] - 1) * 100
                held_h  = (now - p["entered_ts"]) / 3600

                sl_hit    = pnl <= -SL
                trail_hit = (hp >= TRAIL_TRIGGER) and (pnl <= hp - TRAIL)
                to_hit    = held_h >= TIMEOUT_H

                if sl_hit or trail_hit or to_hit:
                    reason = (
                        f"손절-{SL}%" if sl_hit else
                        f"트레일(고점+{hp:.1f}%→현재{pnl:+.1f}%)" if trail_hit else
                        f"타임아웃{TIMEOUT_H}H"
                    )
                    if p.get("live") and p.get("volume", 0) > 0:
                        g = LiveGuard("momentum")
                        res = g.execute_sell(c, f"KRW-{coin}", p["volume"], krw_hint=cur*p["volume"])
                        if res.get("error"):
                            log.error(f"[실전] 매도 실패 {coin}: {res.get('error')} — 포지션 유지")
                            try:
                                from bithumb import notify
                                notify.send(f"🚨 모멘텀 매도 실패 {coin} [{reason}] {res.get('error')} — 포지션 유지")
                            except Exception: pass
                            continue
                        g.record_realized((cur - p["entry"]) * p["volume"])
                        log.info(f"[실전] 매도 완료 {coin} {p['volume']:.6f}개")
                    tag = "[실전]" if p.get("live") else "[모의]"
                    log.info(f"{tag} 청산 {coin} @{cur:,.4f} PnL={pnl:+.2f}% | {reason} ({held_h:.1f}H보유)")
                    log_trade([
                        now_kst.strftime("%Y-%m-%d %H:%M:%S"),
                        coin,
                        f"{p['entry']:.4f}",
                        f"{cur:.4f}",
                        f"{pnl:+.2f}",
                        reason,
                        f"{held_h:.1f}",
                        f"{p.get('chg24', 0):+.1f}"
                    ])
                    try:
                        from bithumb import notify
                        notify.send(f"📈 모멘텀 청산 {coin} {pnl:+.1f}% [{reason}] ({held_h:.0f}H보유) {tag}")
                    except Exception: pass
                    cool[coin] = now + COOLDOWN_H * 3600
                    del pos[coin]
                    save_json(POS, pos)
                    save_json(COOLDOWN_F, cool)

            # ── 진입 ──
            if len(pos) < SLOTS and btc_chg >= BTC_SL:
                candidates = [
                    (coin, d) for coin, d in tickers.items()
                    if d["vol"] >= LIQ_FLOOR
                    and CHG_MIN <= d["chg24"] <= CHG_MAX
                    and coin not in pos
                    and coin not in cool
                ]
                candidates.sort(key=lambda x: -x[1]["vol"])

                for coin, d in candidates:
                    if len(pos) >= SLOTS: break
                    price  = d["price"]
                    chg24  = d["chg24"]
                    h1_p   = hour_ago_price(coin)
                    if h1_p is not None and price <= h1_p:
                        continue  # 1H 동안 횡보/하락 → 패스

                    live = is_live()
                    cap = load_config().get("engine_caps_krw", {}).get("momentum", 0)
                    entry_krw = (cap / SLOTS) if (live and cap) else ENTRY_KRW_DRY
                    volume = 0.0
                    if live:
                        g = LiveGuard("momentum"); res = g.execute_buy(c, f"KRW-{coin}", entry_krw)
                        if res.get("dry"): log.info(f"진입 차단 {coin}: {res.get('reason')}"); continue
                        if res.get("error"):
                            log.error(f"[실전] 매수 실패 {coin}: {res.get('error')} — 포지션 미생성")
                            try:
                                from bithumb import notify
                                notify.send(f"🚨 모멘텀 매수 실패 {coin} {res.get('error')}")
                            except Exception: pass
                            continue
                        volume = round(entry_krw / price * 0.9975, 8)
                        log.info(f"[실전] 매수 완료 {coin} ~{volume:.6f}개")

                    pos[coin] = {
                        "entry": price, "highest": price,
                        "chg24": chg24, "volume": volume,
                        "entered_ts": now,
                        "entered": now_kst.isoformat(),
                        "live": live
                    }
                    save_json(POS, pos)
                    tag = "[실전]" if live else "[모의]"
                    log.info(
                        f"{tag} 진입 {coin} @{price:,.4f} — "
                        f"24H{chg24:+.1f}% 거래대금{d['vol']/1e8:.0f}억 (BTC{btc_chg:+.1f}%)"
                    )
                    try:
                        from bithumb import notify
                        notify.send(
                            f"📈 모멘텀 진입 {coin} 24H{chg24:+.1f}% "
                            f"거래대금{d['vol']/1e8:.0f}억 {tag}"
                        )
                    except Exception: pass

            save_json(POS, pos)

        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")

        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
