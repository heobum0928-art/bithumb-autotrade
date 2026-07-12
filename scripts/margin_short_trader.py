"""
마진 숏 실전 트레이더 (margin_short_trader) — 급등주 되돌림 숏 (바이낸스 크로스마진).

★ 최종 규칙(2026-07-12): 6시간 +40% 급등 → 48시간 숏 (+40% 스탑), 2배.
   시간대 전수 스위프(2h~72h × 문턱20~60%, 40조합, 날짜클러스터 t) 결과 최적:
   TEST +38.8%(t4.60), 승률 86%, 2배청산 0%, 4.5건/주. 대출가능 코인만 감시.
   (이전 24h+40%는 TE +18%/t1.69로 열등 — 짧은 시간대일수록 되돌림이 확실)

동작:
  ① 24h 변동률로 1차 스크리닝 → 후보만 5분봉으로 6h 상승률 정밀계산
  ② margin_guard로 크로스마진 숏 진입(증거금 상한 내, 4중 관문)
  ③ +40% 스탑 또는 48h 만기 시 자동 청산(되사서 상환)
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

# ★★ 2026-07-12 시간대 전수 스위프 (2h~72h × 문턱20~60%, 40조합, 날짜클러스터 t) — 24h는 나쁜 선택이었음.
#   짧은 시간대일수록 압도적으로 좋음. 길수록 급격히 악화(24h t1.69 → 48h t0.10 → 72h t0.06).
#   "짧은 시간에 급하게 오른 것"이 확실히 되돌아옴. 24h+에 걸쳐 서서히 오른 건 진짜 추세일 수 있어 숏이 위험.
#     24h+40%(이전): 8.2건/주, TE +18.0%(t1.69), 승73%, 청산1%
#   ★ 6h+40%(채택):  4.5건/주, TE +38.8%(t4.60), 승86%, 청산0%   ← 수익2배·승률+13%p·t 2.7배
#     4h+40%:        4.0건/주, TE +35.7%(t3.72), 승82%
#     6h+30%:        8.2건/주, TE +25.5%(t3.50), 승76%  (신호 많이 원하면 대안)
# 이전 반성: 2h vs 24h 둘만 비교하고 중간(4~6h)을 안 봐서 24h를 골랐던 것. 사용자가 "24h에 걸 필요 없다" 지적.
PUMP_PCT = 40.0            # 상승률 문턱
LOOKBACK_H = 6             # ★ 6시간 상승률 기준
LOOKBACK = 72              # 6h = 5분봉 72개
VOL_MULT = 0.0            # 거래량 필터 미사용 (검증 결과 불필요)
HOLD_H = 48
STOP_PCT = 40.0           # 진입가 대비 +40% 상승 시 손절(2배 청산선 +50% 안쪽)
COOLDOWN_H = 12           # 코인당 재진입 쿨다운
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
        log.warning(f"마진쌍 조회 실패: {e}")
        try: notify.send(f"⚠️ 마진숏봇: 대출가능목록 갱신 실패 — {e} (IP차단·API권한 문제 의심)")
        except Exception: pass
        return []
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
    """전 심볼의 현재가 + 24h 변동률 + 24h 거래대금 (유동성 필터·1차 스크리닝용)."""
    r = requests.get(f"{BASE}/api/v3/ticker/24hr", timeout=15)
    r.raise_for_status()
    out = {}
    for x in r.json():
        try:
            out[x["symbol"]] = (float(x["lastPrice"]), float(x["priceChangePercent"]), float(x["quoteVolume"]))
        except Exception:
            pass
    return out


def pump_6h(sym):
    """6시간 상승률 — 5분봉 73개 조회해 계산. (상승률, 현재가). 실패 시 (None, 0).
    ★ 6h는 24h와 달리 API가 바로 안 주므로 klines 계산 필요. 후보에만 호출(1차 스크리닝 통과분)."""
    try:
        r = requests.get(f"{BASE}/api/v3/klines", params={"symbol": sym, "interval": "5m", "limit": LOOKBACK + 1}, timeout=8)
        if r.status_code != 200: return None, 0
        k = r.json()
        if len(k) < LOOKBACK + 1: return None, 0
        past = float(k[0][4]); cur = float(k[-1][4])
        if past <= 0: return None, 0
        return (cur / past - 1) * 100, cur
    except Exception:
        return None, 0


# ★ 2026-07-12 유동성 필터 완화 (200만 → 20만): 200만 필터는 대출가능 248개 중 56개만 통과시켜
#   실제 감시대상이 백테스트(237코인)의 1/4로 쪼그라들어 있었음 → 신호도 1/4로 줄어드는 구조적 누락.
#   20만으로 낮추면 204개 감시 = 백테스트와 유사. 체결·슬리피지는 명목 200 USDT 소액이라 문제없음.
MIN_QUOTE_VOL = 200_000     # 24h 거래대금 최소 20만 USDT
PRESCREEN_24H = 15.0        # 1차 스크리닝: 24h가 이 미만이면 6h+40%일 리 없음 → klines 조회 절약


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
    api_fail = 0   # ★ 마진잔고 조회 연속실패 카운터 — IP차단 등으로 조용히 0 반환되는 걸 감지·알림
    ls = live_status()
    mode = "🔴실전" if (ls["enabled"] and ENGINE in ls["armed"]) else "🔵모의(dry)"
    log.info(f"마진숏 트레이더 시작 [{mode}] — 대출가능 {len(UNIVERSE)}코인, 6h+{PUMP_PCT:.0f}%급등→{HOLD_H}h숏+스탑{STOP_PCT:.0f}% "
             f"| 증거금상한 {ls['global_cap_usdt']}USDT {ls['leverage']}배 | 마진잔고 {get_margin_usdt():.1f}")
    try: notify.send(f"📉 마진숏 트레이더 시작 [{mode}] — 6h+{PUMP_PCT:.0f}% 급등주 숏, 대출가능 {len(UNIVERSE)}코인")
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

            # ★ 마진잔고 헬스체크 — get_margin_usdt()가 API장애 시 조용히 0.0을 반환하는 문제(오늘 IP차단 사건)
            #   감지: 연속 실패 시 알림, 복구 시 알림. (진짜 잔고 0은 300USDT 운용 규모상 사실상 안 일어남)
            bal = get_margin_usdt()
            if bal <= 0:
                api_fail += 1
                if api_fail in (2, 6) or api_fail % 12 == 0:
                    log.error(f"마진잔고 0/조회실패 {api_fail}회 연속 — IP차단·API권한 문제 의심")
                    try: notify.send(f"🚨 마진숏봇: 마진잔고 {api_fail}회 연속 0/조회실패 — IP차단 등 API 문제 의심, 확인 필요")
                    except Exception: pass
            else:
                if api_fail >= 2:
                    try: notify.send(f"✅ 마진숏봇: API 정상화 (잔고 {bal:.1f} USDT)")
                    except Exception: pass
                api_fail = 0

            # 1) 신호 탐지 — 6h +PUMP_PCT% 급등 (거래량 필터 없음: 검증 결과 불필요)
            #    1차: 24h 변동률로 후보 추림(6h+40%면 24h도 최소 15%↑) → 2차: 후보만 5분봉으로 6h 정밀계산
            for coin in UNIVERSE:
                sym = f"{coin}USDT"
                t = tick.get(sym)
                if not t: continue
                px, chg24, qvol = t
                if px <= 0 or qvol < MIN_QUOTE_VOL: continue
                if sym in positions or cooldown.get(sym, 0) > now:
                    continue
                if chg24 < PRESCREEN_24H:      # 1차 스크리닝 (klines 조회 절약)
                    continue
                ret6h, px6 = pump_6h(sym)      # 2차 정밀 — 6시간 상승률
                if ret6h is None or ret6h < PUMP_PCT:
                    continue
                if px6 > 0: px = px6
                ret2h = ret6h   # 기록용(6h 상승률)
                vr = 0.0
                # 누적 노출 상한 확인 (48h 홀딩이라 동시다발 진입 가능 → 전체상한 초과 방지)
                open_margin = sum(p["margin"] for p in positions.values())
                gcap = load_config().get("global_cap_usdt", 0)
                if open_margin + MARGIN_PER_TRADE > gcap:
                    log.info(f"진입 보류 {sym}(6h+{ret6h:.0f}%): 누적노출 {open_margin:.0f}+{MARGIN_PER_TRADE:.0f}>전체상한 {gcap}")
                    continue
                # 진입
                margin = min(MARGIN_PER_TRADE, get_margin_usdt())
                res = guard.open_short(coin, margin)
                cooldown[sym] = now + COOLDOWN_H*3600
                if res.get("live"):
                    positions[sym] = {"coin": coin, "entry_ts": now, "entry_price": res.get("price", px),
                                      "qty": res["qty"], "margin": margin, "pump": round(ret2h,1), "vr": round(vr,1),
                                      "exit_ts": now + HOLD_H*3600, "entry_iso": datetime.now(KST).isoformat(), "live": True}
                    log.warning(f"★실전 마진숏 진입 {sym} 6h+{ret2h:.0f}% 증거금{margin:.0f} → {res['qty']}개")
                    try: notify.send(f"📉 마진숏 진입 {sym} 6h+{ret2h:.0f}% (증거금{margin:.0f}USDT)")
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
                # ★ 실전 포지션은 실제 청산(live) 확인 전엔 로컬에서 지우지 않음.
                #   과거 버그: 청산주문이 실패(API장애·IP차단 등)해도 무조건 positions에서 삭제해
                #   실제 거래소엔 레버리지 숏이 그대로 열려있는데 봇은 더 이상 스탑/만기를 감시 안 함.
                if pos["live"] and not cres.get("live"):
                    fails = pos.get("close_fails", 0) + 1
                    pos["close_fails"] = fails
                    log.error(f"★청산 실패(포지션 유지, 다음루프 재시도) {sym} {reason} → {cres} (연속{fails}회)")
                    if fails in (1, 3) or fails % 10 == 0:
                        try: notify.send(f"🚨 마진숏 청산 실패 {sym} {reason} → {cres} (연속{fails}회) — 실거래소엔 포지션 열려있음! 확인 필요")
                        except Exception: pass
                    continue
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
