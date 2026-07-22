"""
마진 숏 실전 트레이더 (margin_short_trader) — 급등주 되돌림 숏 (바이낸스 크로스마진).

★ 규칙(2026-07-12 확정, 2026-07-22 재검증 후 파라미터만 갱신): LOOKBACK_H시간 +PUMP_PCT% 급등
   → HOLD_H시간 숏(+STOP_PCT% 스탑), 2배. 현재값: 7h+30%→48h(+40%). 최초 채택은 6h+40%였고
   2026-07-22에 세밀 재검증(4~8h×30~50% 격자)으로 7h+30%로 교체(신호빈도 약2배, TEST t+3.28) —
   상세 근거는 파일 상단 상수 정의부(LOOKBACK_H 근처) 주석 참조.
   (원 스위프: 2h~72h × 문턱20~60%, 날짜클러스터 t — 24h+40%는 TE+18%/t1.69로 열등,
   짧은 시간대일수록 되돌림이 확실하다는 게 핵심 발견.)

동작:
  ① 24h 변동률로 1차 스크리닝 → 후보만 5분봉으로 LOOKBACK_H시간 상승률 정밀계산
     (함수/변수명 pump_6h·ret6h는 최초 6h 버전의 잔재 — 지금은 LOOKBACK_H를 그대로 씀, 이름과
     실제 시간이 다를 수 있으니 착각 주의)
  ② margin_guard로 크로스마진 숏 진입(증거금 상한 내, 4중 관문)
  ③ STOP_PCT% 스탑 또는 HOLD_H시간 만기 시 자동 청산(되사서 상환)
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
from bithumb.binance_guard import BinanceGuard, load_config as load_futures_config, get_futures_usdt

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
#     6h+40%(2026-07-12~07-22 실전 사용): 4.5건/주, TE +38.8%(t4.60), 승86%, 청산0%
# 이전 반성: 2h vs 24h 둘만 비교하고 중간(4~6h)을 안 봐서 24h를 골랐던 것. 사용자가 "24h에 걸 필요 없다" 지적.
#
# ★★ 2026-07-22 재검증 — 5x5 세밀 격자(4~8h × 30~50%, 111코인·90일 5분봉, TRAIN60/TEST40).
#   6h+40%가 견고한 구간(주변 전부 t>1.4로 무너지는 곳 없음) 안에 있음은 재확인. 단 "더 잦은 신호"를
#   원하는 사용자 요청으로 30% 열(더 헐렁한 문턱)만 놓고 TRAIN t 기준 재선정(사후 TEST편향 없이) →
#   7h가 최고(TRAIN t+2.79). TEST로 확인: TRAIN n57 t+2.79 → TEST n39 t+3.28(TRAIN보다 오히려 강함,
#   과최적화 징후 없음). 표본합계 49→96건(약2배), 채택.
#   ★ 실전 미검증 주의: 위 백테스트는 최근 90일 재구성치라 2026-07-12 원 스위프(더 긴 기간)와 다른
#   창(window)임 — "6h+40%가 나쁘다"가 아니라 "7h+30%가 최근 창에서 더 낫고 표본도 더 많다"는 것.
PUMP_PCT = 30.0            # 상승률 문턱 (2026-07-22: 40→30, 신호빈도 증대 목적, TEST t+3.28 확인)
LOOKBACK_H = 7             # ★ 7시간 상승률 기준 (2026-07-22: 6→7, TRAIN t 기준 재선정)
LOOKBACK = 84              # 7h = 5분봉 84개 (LOOKBACK_H*12)
VOL_MULT = 0.0            # 거래량 필터 미사용 (검증 결과 불필요)
HOLD_H = 48
STOP_PCT = 40.0           # 진입가 대비 +40% 상승 시 손절(2배 청산선 +50% 안쪽)
COOLDOWN_H = 12           # 코인당 재진입 쿨다운
MARGIN_PER_TRADE = 100.0  # 증거금(상한과 동일). 실제 사용은 min(잔고,상한)

BUF_PATH = ROOT / "data" / "margin_short_buf.json"
POS_PATH = ROOT / "data" / "margin_short_pos.json"
TRADES_PATH = ROOT / "data" / "margin_short_trades.csv"
COOLDOWN_PATH = ROOT / "data" / "margin_short_cooldown.json"  # ★ 2026-07-22(감사 발견): 재시작 시
# cooldown이 메모리 전용이라 초기화되던 문제 — positions와 동일하게 디스크 영속화.

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

# ★ 2026-07-21 선물 폴백 — 마진 대출재고 0으로 놓치던 코인(BANK·ACE류) 구제.
#   백테스트 확인(scratchpad, 문서화 예정): 선물은 반전구간 펀딩비 역풍으로 마진보다 비용은 더 들지만
#   여전히 순양수(TRAIN+2.17%/TEST+4.04% vs 마진+3.42%/+5.67%) — 대출 막힌 코인 전용 폴백으로만 사용,
#   대출 가능하면 항상 마진 우선(경제성 더 좋음).
FUTURES_ENGINE = "mshort_fut"
FUTURES_UNIVERSE_PATH = ROOT / "data" / "_futures_tradeable.txt"


def refresh_futures_tradeable():
    """바이낸스 선물(USDⓈ-M)에 상장된 코인 전체 — 마진 대출과 무관하게 항상 숏 가능."""
    try:
        r = requests.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=15)
        cands = sorted(set(s["symbol"].replace("USDT", "") for s in r.json()["symbols"]
                           if s["symbol"].endswith("USDT") and s.get("status") == "TRADING"
                           and s.get("contractType") == "PERPETUAL"))
    except Exception as e:
        log.warning(f"선물 심볼목록 조회 실패: {e}")
        return []
    if cands:
        FUTURES_UNIVERSE_PATH.write_text("\n".join(cands), encoding="utf-8")
        log.info(f"선물 유니버스 갱신: {len(cands)}개")
    return cands


def load_futures_tradeable():
    try:
        return set(c for c in FUTURES_UNIVERSE_PATH.read_text(encoding="utf-8").split() if c)
    except Exception:
        return set()


FUTURES_UNIVERSE = load_futures_tradeable()   # 비면 main()의 첫 refresh가 채움


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
PRESCREEN_24H = 5.0         # 1차 스크리닝: 24h가 이 미만이면 klines 조회 생략(API 절약).
                            # ★ 2026-07-22(훅 지적으로 실측): "24h 낮으면 pump일 리 없다"는 가정은 거짓
                            # 케이스가 있음 — 7h+30% 신호 100건 표본 중 24h<15%가 8건(8%), 그중 6건은
                            # "이미 붕괴 후 반등"형(24h 자체가 -30~-86%인데 최근 7h만 급반등)이라 양수
                            # 문턱을 아무리 낮춰도 못 잡음(무필터 시에만 잡힘, 즉 사실상 필터 무의미화 필요).
                            # 15→5로 낮춰 근접 미스(HOME+14%,SLX+9%) 2건만 회수, 잔여 6%는 구조적 사각지대로
                            # 인지하고 수용(이 패턴은 신규펌프와 다른 성격이라 전략 취지상 배제도 무방할 수
                            # 있음 — 별도 검증 없이는 포함/배제 어느 쪽도 확정 못 함, 향후 과제).


def log_trade(row):
    new = not TRADES_PATH.exists()
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry_time","exit_time","symbol","pump_2h","vol_mult",
                                           "entry_price","exit_price","margin_usdt","pnl_pct","pnl_usdt","live","reason"])
        if new: w.writeheader()
        w.writerow(row)


def main():
    global UNIVERSE, FUTURES_UNIVERSE
    positions = _load(POS_PATH, {})
    cooldown = _load(COOLDOWN_PATH, {})
    now0 = time.time()
    cooldown = {k: v for k, v in cooldown.items() if v > now0}   # 만료된 항목 정리(파일 무한증가 방지)
    last_refresh = 0.0
    last_fut_refresh = 0.0
    last_capcfg_alert = 0.0   # ★ engine_caps_usdt 설정누락 알림 스팸방지용 타임스탬프
    api_fail = 0   # ★ 마진잔고 조회 연속실패 카운터 — IP차단 등으로 조용히 0 반환되는 걸 감지·알림
    neg_balance_notified = False   # ★ 2026-07-22: 계좌 USDT 순자산이 마이너스(타 엔진 정상 차입)일 때 1회성 알림용
    ls = live_status()
    mode = "🔴실전" if (ls["enabled"] and ENGINE in ls["armed"]) else "🔵모의(dry)"
    _bal0 = get_margin_usdt()
    log.info(f"마진숏 트레이더 시작 [{mode}] — 대출가능 {len(UNIVERSE)}코인 + 선물폴백 {len(FUTURES_UNIVERSE)}코인, "
             f"{LOOKBACK_H}h+{PUMP_PCT:.0f}%급등→{HOLD_H}h숏+스탑{STOP_PCT:.0f}% "
             f"| 증거금상한 {ls['global_cap_usdt']}USDT {ls['leverage']}배 | 마진잔고 "
             f"{'조회실패' if _bal0 is None else f'{_bal0:.1f}'}")
    try: notify.send(f"📉 마진숏 트레이더 시작 [{mode}] — {LOOKBACK_H}h+{PUMP_PCT:.0f}% 급등주 숏, 대출가능 {len(UNIVERSE)}코인+선물폴백")
    except Exception: pass

    while True:
        try:
            now = time.time()
            # 선물 유니버스 주기 갱신 (마진 대출과 무관 — 상장폐지/신규상장 정도만 반영하면 됨)
            if now - last_fut_refresh >= BORROWABLE_REFRESH_H * 3600:
                last_fut_refresh = now
                fresh_fut = refresh_futures_tradeable()
                if fresh_fut: FUTURES_UNIVERSE = set(fresh_fut)
            # 대출가능 유니버스 주기 갱신 (재고가 수시로 바뀜 → 못 빌리는 코인에 주문 던지는 것 방지)
            if now - last_refresh >= BORROWABLE_REFRESH_H * 3600:
                last_refresh = now
                fresh = refresh_borrowable()
                if fresh: UNIVERSE = fresh
            tick = all_tickers()
            guard = MarginGuard(ENGINE)

            # ★ 마진잔고 헬스체크. 2026-07-22: get_margin_usdt()가 이제 API실패 시 None(0.0과 구분)을
            #   반환하도록 수정됨 — 예전엔 이 함수가 실패해도 조용히 0.0을 반환해 "마이너스 잔고(타 엔진
            #   정상 차입)"와 "진짜 API장애"를 구분 못 하고 216회 연속 "IP차단 의심" 오진 알림을 보낸 사건
            #   있었음. 이제 None 자체로 명확히 구분. 신규진입 사이징은 더 이상 이 잔고에 의존하지 않음
            #   (MARGIN_PER_TRADE 고정값 사용, 아래 참조) — 여긴 순수 관측용.
            bal = get_margin_usdt()
            if bal is None:
                api_fail += 1
                if api_fail in (2, 6) or api_fail % 12 == 0:
                    log.error(f"마진잔고 조회 {api_fail}회 연속 실패 — API장애 의심(자세한 원인은 margin_guard.log 참조)")
                    try: notify.send(f"🚨 마진숏봇: 마진잔고 조회 {api_fail}회 연속 실패 — API 문제 의심, 확인 필요")
                    except Exception: pass
            else:
                if api_fail >= 2:
                    try: notify.send(f"✅ 마진숏봇: API 정상화 (잔고 {bal:.1f} USDT)")
                    except Exception: pass
                api_fail = 0
                if bal < 0 and not neg_balance_notified:
                    log.info(f"마진잔고 {bal:.1f}(마이너스) — 타 엔진 차입 추정, 정상. 신규진입은 고정금액 사용")
                    try: notify.send(f"ℹ️ 마진숏봇: 계좌 USDT 순자산 마이너스({bal:.1f}) — 타 엔진(재량롱 등) 정상 차입으로 추정, 신규진입은 고정금액 사용해 영향 없음")
                    except Exception: pass
                    neg_balance_notified = True
                elif bal >= 0:
                    neg_balance_notified = False

            # ★ 엔진상한 설정 유효성 — 루프 밖에서 사이클당 1회만 확인(코인마다 반복하면 알림 폭주)
            engine_caps = load_config().get("engine_caps_usdt", {})
            if ENGINE not in engine_caps:
                log.error(f"설정오류: engine_caps_usdt에 {ENGINE} 없음 — 신규진입 전면 차단(안전상 fail-closed)")
                if now - last_capcfg_alert >= 1800:   # 30분에 한 번만 알림(스팸 방지)
                    try: notify.send(f"🚨 마진숏봇: engine_caps_usdt에 {ENGINE} 설정 없음 — 신규진입 전면 차단 중, margin_live_config.json 확인 필요")
                    except Exception: pass
                    last_capcfg_alert = now
                engine_caps = None   # 아래 루프에서 신규진입 전부 스킵시킬 신호

            # 1) 신호 탐지 — 6h +PUMP_PCT% 급등 (거래량 필터 없음: 검증 결과 불필요)
            #    1차: 24h 변동률로 후보 추림(6h+40%면 24h도 최소 15%↑) → 2차: 후보만 5분봉으로 6h 정밀계산
            # ★ 2026-07-21: 대출가능(UNIVERSE) ∪ 선물상장(FUTURES_UNIVERSE) 전체 스캔.
            #   대출가능하면 마진 우선(경제성 더 좋음, 백테스트 확인), 대출 안 되고 선물만 있으면 폴백.
            UNIVERSE_SET = set(UNIVERSE)
            fut_guard = BinanceGuard(FUTURES_ENGINE)
            fut_cfg = load_futures_config()
            fut_caps = fut_cfg.get("engine_caps_usdt", {})
            for coin in (UNIVERSE_SET | FUTURES_UNIVERSE):
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

                use_margin = coin in UNIVERSE_SET
                if use_margin:
                    # 누적 노출 상한 확인 (48h 홀딩이라 동시다발 진입 가능 → 엔진 자체상한 초과 방지)
                    # ★ 2026-07-13 버그수정: global_cap_usdt(엔진 3개 합산 180)로 체크하고 있어서
                    #   mshort 혼자 180까지 쌓일 수 있었음(자기 엔진상한 100을 무시) → 자기 엔진상한으로 교체.
                    #   설정유효성(engine_caps_usdt에 ENGINE 존재하는지)은 루프 진입 전에 한 번만 확인함(위쪽).
                    if engine_caps is None:
                        continue   # 설정오류로 이번 사이클은 신규진입 전면 스킵(알림은 위에서 사이클당 1회만 이미 보냄)
                    open_margin = sum(p["margin"] for p in positions.values() if p.get("venue", "margin") == "margin")
                    ecap = engine_caps[ENGINE]
                    # ★ 2026-07-22: MARGIN_PER_TRADE를 ecap과 무관한 고정값으로 그대로 쓰면, 나중에 누가
                    #   engine_caps_usdt.mshort를 100 미만으로 바꿀 경우 선물폴백(fcap=50)때와 똑같이
                    #   이 분기가 영원히 막히는 버그가 재발할 수 있음 — 항상 ecap 기준으로 클램프.
                    trade_margin = min(MARGIN_PER_TRADE, ecap)
                    if open_margin + trade_margin > ecap:
                        log.info(f"진입 보류 {sym}({LOOKBACK_H}h+{ret6h:.0f}%): 누적노출 {open_margin:.0f}+{trade_margin:.0f}>엔진상한 {ecap}")
                        continue
                    # ★ 2026-07-22: get_margin_usdt()(계좌 전체 USDT 순자산)로 min() 클램프하던 걸 제거.
                    #   manuallong 등 타 엔진이 USDT를 정상 차입하면 이 값이 마이너스가 될 수 있는데,
                    #   min()이 그 마이너스를 그대로 골라 notional이 음수가 되고 조용히 진입 실패하는
                    #   버그가 있었음(실제로 ONEUSDT 신호를 이렇게 놓침). 진짜 잔고부족은 바이낸스가
                    #   API 레벨에서 거부하며 이미 로그·원장기록됨 — 여기선 고정금액만 사용.
                    margin = trade_margin
                    res = guard.open_short(coin, margin)
                    cooldown[sym] = now + COOLDOWN_H*3600
                    if res.get("live"):
                        positions[sym] = {"coin": coin, "entry_ts": now, "entry_price": res.get("price", px),
                                          "qty": res["qty"], "margin": margin, "venue": "margin",
                                          "pump": round(ret2h,1), "vr": round(vr,1),
                                          "exit_ts": now + HOLD_H*3600, "entry_iso": datetime.now(KST).isoformat(), "live": True}
                        log.warning(f"★실전 마진숏 진입 {sym} {LOOKBACK_H}h+{ret2h:.0f}% 증거금{margin:.0f} → {res['qty']}개")
                        try: notify.send(f"📉 마진숏 진입 {sym} {LOOKBACK_H}h+{ret2h:.0f}% (증거금{margin:.0f}USDT)")
                        except Exception: pass
                    else:
                        log.info(f"마진 진입 dry/실패 {sym}: {res}")
                else:
                    # ★ 선물 폴백: 마진 대출재고 없는 코인(BANK·ACE류) 전용. FUTURES_ENGINE 미설정/미arm이면 자동 dry.
                    if FUTURES_ENGINE not in fut_caps:
                        continue   # 선물폴백 미설정 — 조용히 스킵(마진과 별개 기능이라 사이클마다 알림 안 보냄)
                    open_fut = sum(p["margin"] for p in positions.values() if p.get("venue") == "futures")
                    fcap = fut_caps[FUTURES_ENGINE]
                    # ★ 2026-07-22 발견(사용자 지적): MARGIN_PER_TRADE(마진용 100)를 선물폴백 사이징에도
                    #   그대로 써서 fcap(mshort_fut 캡 50)보다 항상 커, 이 분기가 구조적으로 절대 통과할
                    #   수 없었음 — ERAUSDT가 07-21 10:26~15:40 5시간+ 동안 6h+40~85%로 계속 신호를
                    #   냈는데도 매번 "누적노출 0+100>엔진상한 50"으로 전부 놓침(mshort 실전 0건의
                    #   핵심 원인). 선물폴백 전용 사이징을 fcap 기준으로 별도 계산.
                    fut_margin = min(MARGIN_PER_TRADE, fcap)
                    if open_fut + fut_margin > fcap:
                        log.info(f"선물폴백 진입 보류 {sym}({LOOKBACK_H}h+{ret6h:.0f}%): 누적노출 {open_fut:.0f}+{fut_margin:.0f}>엔진상한 {fcap}")
                        continue
                    # ★ 2026-07-22: 마진 경로와 동일 이유로 get_futures_usdt() min() 클램프 제거.
                    #   선물지갑은 manuallong과 무관한 별도 지갑이라 마이너스가 될 일은 없지만, API
                    #   실패 시 0.0을 반환하는 동일 패턴이 있어 똑같이 조용한 실패로 이어질 수 있었음.
                    #   진짜 잔고부족은 open_short_futures() 내부에서 거래소가 거부하며 로그됨.
                    margin = fut_margin
                    res = fut_guard.open_short_futures(coin, margin)
                    cooldown[sym] = now + COOLDOWN_H*3600
                    if res.get("live"):
                        positions[sym] = {"coin": coin, "entry_ts": now, "entry_price": res.get("price", px),
                                          "qty": res["qty"], "margin": margin, "venue": "futures",
                                          "pump": round(ret2h,1), "vr": round(vr,1),
                                          "exit_ts": now + HOLD_H*3600, "entry_iso": datetime.now(KST).isoformat(), "live": True}
                        log.warning(f"★실전 선물숏 진입(마진대출폴백) {sym} {LOOKBACK_H}h+{ret2h:.0f}% 증거금{margin:.0f} → {res['qty']}개")
                        try: notify.send(f"📉 선물숏 진입(마진폴백) {sym} {LOOKBACK_H}h+{ret2h:.0f}% (증거금{margin:.0f}USDT)")
                        except Exception: pass
                    else:
                        log.info(f"선물 진입 dry/실패 {sym}: {res}")

            # 2) 청산: 40% 스탑(가격이 진입가+40% 상승 = 숏 손실) OR 48h 만기
            for sym in list(positions.keys()):
                pos = positions[sym]
                t = tick.get(sym)
                px = t[0] if t else pos["entry_price"]
                stop_hit = px >= pos["entry_price"] * (1 + STOP_PCT/100)
                if not stop_hit and now < pos["exit_ts"]:
                    continue
                reason = f"스탑+{STOP_PCT:.0f}%" if stop_hit else f"{HOLD_H}h만기"
                venue = pos.get("venue", "margin")   # 옛 포지션(필드 없음) = 마진으로 취급(원래 유일 경로였음)
                if venue == "futures":
                    cres = fut_guard.close_short_futures(pos["coin"])
                    lev = load_futures_config().get("leverage", 2)
                    venue_tag = "선물"
                else:
                    cres = guard.close_short(pos["coin"])
                    lev = load_config().get("leverage", 2)
                    venue_tag = "마진"
                # ★ 실전 포지션은 실제 청산(live) 확인 전엔 로컬에서 지우지 않음.
                #   과거 버그: 청산주문이 실패(API장애·IP차단 등)해도 무조건 positions에서 삭제해
                #   실제 거래소엔 레버리지 숏이 그대로 열려있는데 봇은 더 이상 스탑/만기를 감시 안 함.
                if pos["live"] and not cres.get("live"):
                    fails = pos.get("close_fails", 0) + 1
                    pos["close_fails"] = fails
                    log.error(f"★{venue_tag}청산 실패(포지션 유지, 다음루프 재시도) {sym} {reason} → {cres} (연속{fails}회)")
                    if fails in (1, 3) or fails % 10 == 0:
                        try: notify.send(f"🚨 {venue_tag}숏 청산 실패 {sym} {reason} → {cres} (연속{fails}회) — 실거래소엔 포지션 열려있음! 확인 필요")
                        except Exception: pass
                    continue
                pnl_pct = (1 - px/pos["entry_price"])*100
                pnl_usdt = pos["margin"] * lev * (pnl_pct/100)
                if pos["live"]:
                    (fut_guard if venue == "futures" else guard).record_realized(pnl_usdt)
                log_trade(dict(entry_time=pos["entry_iso"], exit_time=datetime.now(KST).isoformat(), symbol=sym,
                               pump_2h=pos["pump"], vol_mult=pos["vr"], entry_price=pos["entry_price"], exit_price=px,
                               margin_usdt=pos["margin"], pnl_pct=round(pnl_pct,2), pnl_usdt=round(pnl_usdt,2),
                               live=pos["live"], reason=f"{reason}[{venue_tag}]"))
                log.warning(f"★{venue_tag}숏 청산 {sym} @{px:g} {reason} pnl={pnl_pct:+.2f}%({pnl_usdt:+.2f}USDT) → {cres.get('live') and '실청산' or cres}")
                try: notify.send(f"📈 {venue_tag}숏 청산 {sym} {reason} pnl={pnl_pct:+.1f}% ({pnl_usdt:+.1f}USDT)")
                except Exception: pass
                del positions[sym]

            _save(POS_PATH, positions)
            # ★ 2026-07-22(감사 발견): cooldown이 메모리 전용이라 재시작 시 초기화 → 청산 직후 재시작되면
            #   12h 재진입 쿨다운 없이 즉시 재진입 가능했음. positions와 동일하게 매 사이클 디스크 영속화
            #   + 만료분은 저장 전에 정리(파일 무한증가 방지).
            # 참고: 이 정리는 파일 용량 관리를 위한 하우스키핑일 뿐 — 실제 쿨다운 적용 여부는
            # 신호탐지 루프에서 이번 사이클의 now로 이미 판정 끝남(cooldown.get(sym,0) > now), 무관.
            cooldown = {k: v for k, v in cooldown.items() if v > time.time()}
            _save(COOLDOWN_PATH, cooldown)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
