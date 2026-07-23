"""
RSI 극단 과매수 숏 트레이더 (rsi_extreme_short) — 소액 실전 (2026-07-13 전환).

검증(2026-07-12, 기술지표 오토리서치 5각도 중 유일 생존):
  규칙: 5분봉 RSI(14) > 92 AND 해당봉 거래대금 > 직전20봉 평균의 3배
        AND 24h 상승률 < 40% (마진숏봇과 겹치지 않게)
        → 다음봉 시가에 SHORT, 4시간 홀딩
  적대검증(독립 재구현): TRAIN EV +1.28% → TEST +3.70%(t_day 4.12), OOS가 더 강함.
  2배 청산 0/202건(최대역행 43%), 무작위숏 대조군은 양쪽 마이너스(=베타 아님, 진짜 알파),
  상위3코인 제거 +2.36%, 최고주 제거 +2.87%, 분할점 5개 전부 유지.
  ★마진숏봇(6h+40% 숏)과 신호 겹침 9.9%뿐, 손익상관 ~0 → 완전히 독립적인 자리.
  판정: MARGINAL (통계는 강하나 표본 202건, forward모의 XAUT·STORJ 2건 진행중이던 상태) →
  사용자 판단으로 소액(증거금상한 30USDT) 실전 전환. 계속 MARGINAL임을 인지하고 운용할 것.

주의: 다른 정통 지표(RSI30/70, MACD, 볼린저, 스토캐스틱)는 106조합 전멸.
      거래량 미시구조·1분봉 추론도 전부 사망. 이것만 살아남음.

★ margin_guard(engine=rsishort) OFF면 자동 dry. 실전은 data/margin_live_config.json arm 필요.
   포트 47252. 포지션 data/rsi_short_pos.json | 기록 data/rsi_short_trades.csv | 로그 logs/rsi_extreme_short.log
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
from bithumb.margin_guard import MarginGuard, live_status, get_margin_usdt, load_config
from bithumb.binance_guard import BinanceGuard, load_config as load_futures_config

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [RSISHORT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/rsi_extreme_short.log", encoding="utf-8")])
log = logging.getLogger(__name__)

BASE = "https://api.binance.com"
ENGINE = "rsishort"
POLL_SEC = 300
RSI_MIN = 92.0            # 5분봉 RSI(14) 문턱
VOL_MULT = 3.0            # 신호봉 거래대금 / 직전20봉 평균
MAX_24H_CHG = 40.0        # 24h 상승률 이 미만만 (마진숏봇 영역과 분리)
HOLD_H = 8   # 2026-07-18: 4h→8h (TRAIN/TEST 재검증, 4h t2.90/0.57 → 8h t3.42/1.42 둘다개선. 24h/48h는 TEST서 붕괴)
COOLDOWN_H = 8
COST_PCT = 0.20 + 0.12/24*HOLD_H   # 왕복비용 + 대출이자(HOLD_H 반영)
MIN_QUOTE_VOL_5M = 10_000          # 유동성: 5분봉 거래대금 1만 USDT+ (검증서 이 필터가 오히려 개선)
MARGIN_PER_TRADE = 30.0            # ★ MARGINAL 판정이라 소액 시작 — 증거금상한과 동일(동시 1건)

# ★ 2026-07-23 선물 폴백 — margin_short_trader의 것과 동일 패턴이지만 트리거 조건은 다름.
#   mshort는 "코인이 대출가능 목록에 아예 없음"(BANK·ACE류, 영구적)을 유니버스 사전분기로 처리하지만,
#   RSI숏은 대출가능 목록(uni)에 있는 코인만 스캔하는데도 실행 시점 대출재고가 그새 바닥나 -3045로
#   실패하는 경우(SHELLUSDT 사례, 일시적)가 있음 — 그래서 사전분기가 아니라 open_short() 실패 시
#   -3045(대출재고 없음)일 때만 즉시 선물로 재시도하는 방식. FUTURES_ENGINE 미설정/미arm이면 dry.
#   ★ 미검증 캐비엇: COST_PCT는 마진 대출이자 기준 추정치라 선물 펀딩비(다를 수 있음)를 반영 안 함 —
#   선물폴백 경로로 체결된 소수 거래는 기록되는 pnl_pct가 실제와 소폭 다를 수 있음. RSI숏이 MARGINAL
#   판정(n=202)이라 이 서브샘플만 떼어 별도 백테스트할 표본이 안 됨 — mshort의 구조적으로 유사한
#   폴백 백테스트(TRAIN+2.17%/TEST+4.04%, 마진 대비 낮지만 순양수)에 기대어 판단. 그래서 최초엔
#   engine_caps_usdt만 등록하고 armed_engines엔 넣지 않음(dry 관찰 후 사용자 승인 시에만 실전 arm —
#   mshort_fut 롤아웃과 동일 절차).
FUTURES_ENGINE = "rsishort_fut"

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
    ls = live_status()
    mode = "🔴실전" if (ls["enabled"] and ENGINE in ls["armed"]) else "🔵모의(dry)"
    log.info(f"RSI극단 숏 시작 [{mode}] — 대출가능 {len(uni)}코인, RSI>{RSI_MIN:.0f} & 거래량{VOL_MULT:.0f}배 & 24h<{MAX_24H_CHG:.0f}% → {HOLD_H}h숏 | 증거금상한 {MARGIN_PER_TRADE:.0f}USDT")
    try: notify.send(f"📉 RSI극단 숏 시작 [{mode}] — RSI>{RSI_MIN:.0f}+거래량{VOL_MULT:.0f}배 숏 (MARGINAL 판정, 소액)")
    except Exception: pass

    while True:
        try:
            now = time.time()
            uni = universe() or uni
            guard = MarginGuard(ENGINE)
            fut_guard = BinanceGuard(FUTURES_ENGINE)
            fut_caps = load_futures_config().get("engine_caps_usdt", {})

            # ★ 2026-07-16 순서변경: 예전엔 신규진입 스캔(코인 200+개, API콜 다수)이 먼저라서
            #   PC재부팅 직후처럼 스캔이 느려지면 이미 만기된 포지션 청산이 몇 분~십여분 지연됐음
            #   (실제로 RSR 포지션이 만기 3.7시간 지나도록 안 닫혀서 수동개입한 사건).
            #   포지션 점검(만기청산·손절)을 항상 먼저 처리하도록 순서를 바꿔 이 지연을 원천 차단.
            # 1) 추적 + 만기 청산 (신규진입 스캔보다 먼저)
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
                    is_live = bool(p.get("live"))   # 구형(실전전환 전) 포지션은 live 키 없음 → 모의로 취급(안전)
                    venue = p.get("venue", "margin")   # ★ 2026-07-23: 선물폴백 포지션은 종목별 가드/설정이 다름
                    if is_live:
                        cres = fut_guard.close_short_futures(p.get("coin", sym[:-4])) if venue == "futures" \
                               else guard.close_short(p.get("coin", sym[:-4]))
                        # ★margin_short_trader와 동일 수정: 청산 실패 시 로컬에서 지우지 않고 재시도+알림
                        if not cres.get("live"):
                            fails = p.get("close_fails", 0) + 1
                            p["close_fails"] = fails
                            log.error(f"★청산 실패(포지션 유지, 재시도예정) {sym}({venue}) → {cres} (연속{fails}회)")
                            if fails in (1, 3) or fails % 10 == 0:
                                try: notify.send(f"🚨 RSI극단숏 청산 실패 {sym}({venue}) → {cres} (연속{fails}회) — 실거래소 포지션 열려있음! 확인 필요")
                                except Exception: pass
                            continue
                    pnl = (1 - exit_px/entry)*100 - COST_PCT
                    lev = (load_futures_config() if venue == "futures" else load_config()).get("leverage", 2)
                    pnl_usdt = p["margin"] * lev * (pnl/100) if is_live else 0.0
                    if is_live:
                        (fut_guard if venue == "futures" else guard).record_realized(pnl_usdt)
                    mfe = (1 - p["min_p"]/entry)*100
                    mae = (1 - p["max_p"]/entry)*100
                    log_trade(dict(entry_time=p["entry_iso"], exit_time=datetime.now(KST).isoformat(), symbol=sym,
                                   rsi=p["rsi"], vol_mult=p["vr"], chg24=p["chg24"],
                                   entry_price=entry, exit_price=exit_px, pnl_pct=round(pnl,2),
                                   mfe_pct=round(mfe,2), mae_pct=round(mae,2), reason=f"{HOLD_H}h만기"))
                    tag = "★실전" if is_live else "(모의)"
                    pnl_note = f" ({pnl_usdt:+.2f}USDT)" if is_live else ""
                    log.warning(f"숏 청산{tag}({venue}) {sym} @{exit_px:g} pnl={pnl:+.2f}%{pnl_note} (최대유리+{mfe:.1f}% 최대역행{mae:+.1f}%)")
                    if is_live:   # ★ 실전 체결만 알림 (모의는 로그로만 확인)
                        try: notify.send(f"📈 RSI극단 숏 청산 {sym} pnl={pnl:+.1f}%{pnl_note}")
                        except Exception: pass
                    del positions[sym]

            _save(POS_PATH, positions)

            # 24h 변동률 (실전봇 영역 제외용)
            chg24 = {}
            try:
                for x in requests.get(f"{BASE}/api/v3/ticker/24hr", timeout=15).json():
                    chg24[x["symbol"]] = float(x["priceChangePercent"])
            except Exception:
                pass

            # 2) 신호 탐지 (포지션 점검 이후)
            for coin in uni:
                sym = f"{coin}USDT"
                if sym in positions or cooldown.get(sym, 0) > now:
                    continue
                c24 = chg24.get(sym, 0)
                if c24 >= MAX_24H_CHG:   # 마진숏봇이 잡는 영역 → 스킵(중복 방지)
                    continue
                hit, rsi, vr, px, _ = check_signal(sym)
                if not hit or px <= 0:
                    continue
                # 동시노출 상한: 실전 포지션 이미 있으면 신규 실전진입 보류(동시 1건 — 증거금상한이 곧 1건
                # 규모, venue 무관하게 엔진 전체 기준. 마진/선물 두 벌 동시 열리지 않게 방지)
                open_total = sum(p["margin"] for p in positions.values() if p.get("live"))
                if open_total > 0:
                    log.info(f"진입 보류 {sym}(RSI{rsi:.0f}): 이미 실전포지션 {open_total:.0f}USDT 열려있음(상한 {MARGIN_PER_TRADE:.0f})")
                    continue
                # ★ 2026-07-22: margin_short_trader.py와 동일 버그 수정 — get_margin_usdt()(계좌 전체
                #   USDT 순자산)로 min() 클램프하던 걸 제거. manuallong 등 타 엔진이 USDT를 정상
                #   차입하면 이 값이 마이너스가 될 수 있는데, min()이 그대로 골라 조용히 진입 실패했음
                #   (실제로 FILUSDT 신호를 이렇게 놓침). 진짜 잔고부족은 바이낸스가 API에서 거부.
                margin = MARGIN_PER_TRADE
                res = guard.open_short(coin, margin)
                venue = "margin"
                # ★ 2026-07-23: 마진 대출재고 없음(-3045)일 때만 선물로 즉시 재시도. 다른 실패(가격조회
                #   실패·최소주문 미달·예외 등)는 선물에서도 똑같이 막힐 가능성이 높아 재시도하지 않음 —
                #   재고 고갈이라는 구체적 원인이 확인된 경우에만 폴백(막연한 전체 실패 우회 아님).
                if not res.get("live") and not res.get("dry"):
                    err = res.get("error")
                    code = err.get("code") if isinstance(err, dict) else None
                    if code == -3045 and FUTURES_ENGINE in fut_caps:
                        fut_margin = min(MARGIN_PER_TRADE, fut_caps[FUTURES_ENGINE])
                        log.info(f"마진대출 재고부족 {sym} → 선물폴백 시도(증거금{fut_margin:.0f})")
                        res = fut_guard.open_short_futures(coin, fut_margin)
                        margin = fut_margin
                        venue = "futures"
                cooldown[sym] = now + COOLDOWN_H*3600
                live = bool(res.get("live"))
                entry_px = res.get("price", px) if live else px
                positions[sym] = {"coin": coin, "entry_ts": now, "entry_price": entry_px, "rsi": round(rsi,1), "vr": round(vr,1),
                                  "chg24": round(c24,1), "exit_ts": now + HOLD_H*3600, "margin": margin, "live": live,
                                  "venue": venue, "min_p": entry_px, "max_p": entry_px, "entry_iso": datetime.now(KST).isoformat()}
                tag = "★실전" if live else "(모의)"
                log.warning(f"숏 진입{tag}({venue}) {sym} @{entry_px:g} RSI{rsi:.0f} 거래량{vr:.1f}배 24h{c24:+.0f}% → {HOLD_H}h후 청산")
                if live:   # ★ 실전 체결만 알림 (모의는 로그로만 확인)
                    try: notify.send(f"📉 RSI극단 숏 진입({venue}) {sym} RSI{rsi:.0f} 거래량{vr:.0f}배 @{entry_px:g}")
                    except Exception: pass

            _save(POS_PATH, positions)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
