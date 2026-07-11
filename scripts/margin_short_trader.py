"""
마진 숏 실전 트레이더 (margin_short_trader) — 거래량 폭발 급등주 숏.

검증(2026-07-11): 빗썸∩바이낸스마진숏 314코인 백테스트에서 모든 관문 통과 —
거래량 8~10배 폭발 + 1~2h +15~20% 급등 → 8h 무손절 숏.
2배 레버리지 정직계산(청산 3% 반영) 후 TE +11%/건(t2.9), 승률 73%, 상위3코인 제거·train/test 생존.
이 프로젝트 3개월 만의 첫 전관문 통과 엣지.

동작:
  ① 바이낸스 전 선물+마진 심볼 5분봉 폴링 → 거래량 VOL_MULT배 & 2h +PUMP% 감지
  ② margin_guard로 크로스마진 숏 진입(증거금 상한 내, 4중 관문)
  ③ HOLD_H 후 자동 청산(되사서 상환). 무손절(백테스트가 무손절 기준).
  ④ 손익 기록.

★ margin_guard OFF면 자동 dry. 실전은 data/margin_live_config.json arm 필요(현재 arm됨).
포지션 data/margin_short_pos.json | 기록 data/margin_short_trades.csv | 로그 logs/margin_short_trader.log
포트 47251. Run: python scripts/margin_short_trader.py
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
    try: _sock.bind(("127.0.0.1", 47251))
    except OSError: print("[ERROR] margin_short_trader 이미 실행 중 (포트 47251)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
from bithumb import notify
from bithumb.margin_guard import MarginGuard, live_status, get_margin_usdt, load_config, get_borrowed

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [MSHORT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/margin_short_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"; BASE = "https://api.binance.com"
ENGINE = "mshort"
POLL_SEC = 300

# ★ 2026-07-11 진입조건 전면교체 — 사용자 패턴이 봇 조건보다 우월함이 데이터로 확인됨.
# 기존 봇: 2h+20% & 거래량10배 → 3.2건/주, TE +16.6%(t1.6), 승76%, 청산2%
# 사용자식: 24h +40% (거래량 무관) → 8.1건/주, TE +17.6%(t2.3), 승72%, 청산1%  ★채택
#   신호 2.5배 + 통계 더 확실(t1.6→2.3) + 청산 더 낮음. RSI 필터 추가는 오히려 악화(t2.2)라 미적용.
# 근거: 사용자가 감으로 이긴 SKL(24h+55%)·PYR(24h+63%)이 기존 봇 조건엔 안 걸렸음 — 조건 자체가 틀렸던 것.
# "며칠에 걸쳐 누적 급등"(DEXE: 일봉 MA20이격+47%)도 24h 기준이라야 포착됨.
PUMP_PCT = 40.0            # 24시간 상승률 문턱
LOOKBACK = 288             # 24h = 5분 x 288
VOL_MULT = 0.0            # 거래량 필터 미사용 (검증 결과 불필요 — "많이 올랐다"는 사실 하나면 충분)
HOLD_H = 48
STOP_PCT = 40.0           # 진입가 대비 +40% 상승 시 손절(2배 청산선 +50% 안쪽)
COOLDOWN_H = 24           # 24h급등 기준이라 쿨다운도 확대(같은 코인 반복진입 방지)
MARGIN_PER_TRADE = 100.0  # 증거금(상한과 동일). 실제 사용은 min(잔고,상한)

BUF_PATH = ROOT / "data" / "margin_short_buf.json"
POS_PATH = ROOT / "data" / "margin_short_pos.json"
TRADES_PATH = ROOT / "data" / "margin_short_trades.csv"

# 유니버스: 바이낸스 마진 대출가능 코인 전체 (2026-07-11 확장 — 빗썸 교집합 제한 제거)
# ★ 발견1: 백테스트 314개 중 실제 빌릴 수 있는 건 절반뿐(급등 소형알트는 대출재고 없어 -3045 거부).
#   재검증: 엣지는 오히려 대출가능 쪽이 큼 → 대출가능만 감시.
# ★ 발견2: 빗썸 교집합으로 좁힐 이유 없음(거래는 바이낸스에서만 함). 제한 풀면 177→210개,
#   신호 1.8→3.2건/주로 78%↑, 승률76%·청산2%(오히려 개선). 단 신규분 TEST 수익은 약해
#   전체 TE +31%→+17%로 희석 — 기대치는 낮추되 표본이 2배라 실전검증이 빨라지는 게 더 중요.
BORROWABLE_PATH = ROOT / "data" / "_borrowable_all.txt"
BORROWABLE_REFRESH_H = 6

def refresh_borrowable():
    """바이낸스 마진 숏가능 + 실제 대출재고 있는 코인 전체 (klines 보유 여부 무관)."""
    from bithumb.margin_guard import _signed
    try:
        pairs = _signed("GET", "/sapi/v1/margin/allPairs").json()
        cands = sorted(set(p["symbol"].replace("USDT", "") for p in pairs
                           if p.get("quote") == "USDT" and p.get("isSellAllowed") and p.get("isMarginTrade")))
    except Exception as e:
        log.warning(f"마진쌍 조회 실패: {e}"); return []
    ok = []
    for coin in cands:
        try:
            r = _signed("GET", "/sapi/v1/margin/maxBorrowable", {"asset": coin})
            if r.status_code == 200 and float(r.json().get("amount", 0)) > 0:
                ok.append(coin)
        except Exception:
            pass
        time.sleep(0.1)
    if ok:
        BORROWABLE_PATH.write_text("\n".join(ok), encoding="utf-8")
        log.info(f"대출가능 유니버스 갱신: {len(ok)}/{len(cands)}개")
    return ok

def load_borrowable():
    try:
        return [c for c in BORROWABLE_PATH.read_text(encoding="utf-8").split() if c]
    except Exception:
        return []

UNIVERSE = load_borrowable()   # 비면 main()의 첫 refresh가 채움


def _load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception: return d
def _save(p, o):
    tmp = Path(p).with_suffix(".tmp"); tmp.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8"); tmp.replace(p)


def all_tickers():
    """전 심볼의 현재가 + 24시간 변동률 한 번에 (24h 급등 판정용).
    ★ 가격버퍼로 24h를 재려면 288샘플=24시간 대기 필요 → 바이낸스가 직접 주는 24h 변동률 사용."""
    r = requests.get(f"{BASE}/api/v3/ticker/24hr", timeout=15)
    r.raise_for_status()
    out = {}
    for x in r.json():
        try:
            out[x["symbol"]] = (float(x["lastPrice"]), float(x["priceChangePercent"]), float(x["quoteVolume"]))
        except Exception:
            pass
    return out


MIN_QUOTE_VOL = 2_000_000   # 24h 거래대금 최소 200만 USDT (유동성 — 체결·대출 가능성 확보)


def log_trade(row):
    new = not TRADES_PATH.exists()
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry_time","exit_time","symbol","pump_2h","vol_mult",
                                           "entry_price","exit_price","margin_usdt","pnl_pct","pnl_usdt","live","reason"])
        if new: w.writeheader()
        w.writerow(row)


def main():
    global UNIVERSE
    positions = _load(POS_PATH, {}); cooldown = {}
    last_refresh = 0.0
    ls = live_status()
    mode = "🔴실전" if (ls["enabled"] and ENGINE in ls["armed"]) else "🔵모의(dry)"
    log.info(f"마진숏 트레이더 시작 [{mode}] — 대출가능 {len(UNIVERSE)}코인, 24h+{PUMP_PCT:.0f}%급등→{HOLD_H}h숏+스탑{STOP_PCT:.0f}% "
             f"| 증거금상한 {ls['global_cap_usdt']}USDT {ls['leverage']}배 | 마진잔고 {get_margin_usdt():.1f}")
    try: notify.send(f"📉 마진숏 트레이더 시작 [{mode}] — 24h+{PUMP_PCT:.0f}% 급등주 숏, 대출가능 {len(UNIVERSE)}코인")
    except Exception: pass

    while True:
        try:
            now = time.time()
            # 대출가능 유니버스 주기 갱신 (재고가 수시로 바뀜 → 못 빌리는 코인에 주문 던지는 것 방지)
            if now - last_refresh >= BORROWABLE_REFRESH_H * 3600:
                last_refresh = now
                fresh = refresh_borrowable()
                if fresh: UNIVERSE = fresh
            tick = all_tickers()
            guard = MarginGuard(ENGINE)

            # 1) 신호 탐지 — 24h +PUMP_PCT% 급등 (거래량 필터 없음: 검증 결과 불필요)
            for coin in UNIVERSE:
                sym = f"{coin}USDT"
                t = tick.get(sym)
                if not t: continue
                px, chg24, qvol = t
                if px <= 0 or qvol < MIN_QUOTE_VOL: continue
                if sym in positions or cooldown.get(sym, 0) > now:
                    continue
                if chg24 < PUMP_PCT:
                    continue
                ret2h = chg24   # 기록용(24h 상승률)
                vr = 0.0
                # 누적 노출 상한 확인 (48h 홀딩이라 동시다발 진입 가능 → 전체상한 초과 방지)
                open_margin = sum(p["margin"] for p in positions.values())
                gcap = load_config().get("global_cap_usdt", 0)
                if open_margin + MARGIN_PER_TRADE > gcap:
                    log.info(f"진입 보류 {sym}(24h+{chg24:.0f}%): 누적노출 {open_margin:.0f}+{MARGIN_PER_TRADE:.0f}>전체상한 {gcap}")
                    continue
                # 진입
                margin = min(MARGIN_PER_TRADE, get_margin_usdt())
                res = guard.open_short(coin, margin)
                cooldown[sym] = now + COOLDOWN_H*3600
                if res.get("live"):
                    positions[sym] = {"coin": coin, "entry_ts": now, "entry_price": res.get("price", px),
                                      "qty": res["qty"], "margin": margin, "pump": round(ret2h,1), "vr": round(vr,1),
                                      "exit_ts": now + HOLD_H*3600, "entry_iso": datetime.now(KST).isoformat(), "live": True}
                    log.warning(f"★실전 마진숏 진입 {sym} 24h+{ret2h:.0f}% 증거금{margin:.0f} → {res['qty']}개")
                    try: notify.send(f"📉 마진숏 진입 {sym} 24h+{ret2h:.0f}% (증거금{margin:.0f}USDT)")
                    except Exception: pass
                else:
                    log.info(f"진입 dry/실패 {sym}: {res}")

            # 2) 청산: 40% 스탑(가격이 진입가+40% 상승 = 숏 손실) OR 48h 만기
            for sym in list(positions.keys()):
                pos = positions[sym]
                t = tick.get(sym)
                px = t[0] if t else pos["entry_price"]
                stop_hit = px >= pos["entry_price"] * (1 + STOP_PCT/100)
                if not stop_hit and now < pos["exit_ts"]:
                    continue
                reason = f"스탑+{STOP_PCT:.0f}%" if stop_hit else f"{HOLD_H}h만기"
                cres = guard.close_short(pos["coin"])
                pnl_pct = (1 - px/pos["entry_price"])*100
                pnl_usdt = pos["margin"] * load_config().get("leverage",2) * (pnl_pct/100)
                if pos["live"]: guard.record_realized(pnl_usdt)
                log_trade(dict(entry_time=pos["entry_iso"], exit_time=datetime.now(KST).isoformat(), symbol=sym,
                               pump_2h=pos["pump"], vol_mult=pos["vr"], entry_price=pos["entry_price"], exit_price=px,
                               margin_usdt=pos["margin"], pnl_pct=round(pnl_pct,2), pnl_usdt=round(pnl_usdt,2),
                               live=pos["live"], reason=reason))
                log.warning(f"★마진숏 청산 {sym} @{px:g} {reason} pnl={pnl_pct:+.2f}%({pnl_usdt:+.2f}USDT) → {cres.get('live') and '실청산' or cres}")
                try: notify.send(f"📈 마진숏 청산 {sym} {reason} pnl={pnl_pct:+.1f}% ({pnl_usdt:+.1f}USDT)")
                except Exception: pass
                del positions[sym]

            _save(POS_PATH, positions)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
