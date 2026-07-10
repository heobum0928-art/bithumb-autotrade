"""
블로우오프 숏 페이퍼 트레이더 (blowoff_short_paper) — 순수 모의, 실주문 0.

배경(2026-07-10): 바이낸스 선물 하이브리드 발굴에서 유일하게 "방향은 맞다"고 나온 것 =
급등 되돌림 숏. 검증 판정 REAL_MARGINAL — 통계적으로 실재하는 약한 경향이나
(모든 분할서 순수익 +1.6~4%/건, 부호 안 뒤집힘) ①오버랩 제거 시 t<1(유의성 미달)
②소수 코인 편중 ③홀딩 중 최대 +140% 역행(레버리지 불가) 때문에 실전 배분은 보류.
→ forward 모의로 실제 어떻게 되는지 데이터를 직접 쌓아 판정한다.

규칙 (검증 에이전트 확정본):
  - 대상: 바이낸스 USDT 무기한선물 (klines 수집된 111코인)
  - 진입: 5분봉 종가 기준 최근 2시간(24봉) 상승률 >= +20% → 그 순간 SHORT
  - 청산: 8시간(96봉) 후 종가 — 무손절(naked). 레버리지 1배 가정(청산 회피).
  - 코인당 쿨다운 2시간(중복 진입 방지)
  - 비용: 왕복 0.20%(테이커+슬립) 차감. 펀딩 근사 무시(평균 ~0).

★ 순수 모의: 어떤 주문 API도 호출 안 함. 가격버퍼 지속저장(재시작 대비).
가격버퍼 data/blowoff_price_buf.json | 포지션 data/blowoff_pos.json
기록 data/blowoff_short_trades.csv | 로그 logs/blowoff_short_paper.log
Run: python scripts/blowoff_short_paper.py
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
    try: _sock.bind(("127.0.0.1", 47250))
    except OSError: print("[ERROR] blowoff_short_paper 이미 실행 중 (포트 47250)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [BLOWOFF] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/blowoff_short_paper.log", encoding="utf-8")])
log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
POLL_SEC = 300                # 5분봉 주기에 맞춰 5분 폴링
PUMP_PCT = 20.0              # 2시간(24봉) 상승률 문턱
LOOKBACK_SAMPLES = 24        # 2시간 = 5분 x 24
VOL_MULT = 10.0             # 2026-07-11: 거래량 필터 추가 — 신호봉 거래량 >= 직전20봉평균 10배.
                            # 백테스트에서 "극단급등(거래량10배+2h+20%)+8h무손절"만 2배청산 0%로
                            # 유일하게 청산위험 없이 살아남음(TE+8.5%,t2.3,n29). 순도 높은 forward 표본 목적.
HOLD_SEC = 8 * 3600          # 8시간 홀딩
COOLDOWN_SEC = 2 * 3600      # 코인당 쿨다운 2시간
COST_PCT = 0.20             # 왕복 비용
BUF_MAXLEN = 30              # 가격버퍼 코인당 최대 샘플(2.5h치 여유)

BUF_PATH = ROOT / "data" / "blowoff_price_buf.json"
POS_PATH = ROOT / "data" / "blowoff_pos.json"
TRADES_PATH = ROOT / "data" / "blowoff_short_trades.csv"

# 대상 유니버스: binance_klines에 있는 코인
UNIVERSE = sorted(os.path.basename(f).replace("_5m.json", "")
                  for f in (ROOT / "data" / "binance_klines").glob("*_5m.json"))


def _load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(p, obj):
    tmp = Path(p).with_suffix(".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    tmp.replace(p)


def all_prices() -> dict:
    """바이낸스 전 선물 심볼 현재가 한 번에."""
    r = requests.get(f"{FAPI}/fapi/v1/ticker/price", timeout=10)
    r.raise_for_status()
    return {x["symbol"]: float(x["price"]) for x in r.json()}


def volume_spike_ok(sym: str) -> tuple[bool, float]:
    """가격 급등 후보에 대해서만 호출 — 최근 5분봉 거래량이 직전20봉평균의 VOL_MULT배 이상인지.
    (조건충족?, 실제배수). 조회 실패 시 (False, 0)."""
    try:
        r = requests.get(f"{FAPI}/fapi/v1/klines",
                         params={"symbol": sym, "interval": "5m", "limit": 25}, timeout=8)
        if r.status_code != 200:
            return False, 0.0
        kl = r.json()
        vols = [float(x[7]) for x in kl]  # quoteAssetVolume
        if len(vols) < 21:
            return False, 0.0
        avg20 = sum(vols[-21:-1]) / 20
        if avg20 <= 0:
            return False, 0.0
        vr = vols[-1] / avg20
        return vr >= VOL_MULT, vr
    except Exception:
        return False, 0.0


def log_trade(row: dict):
    new = not TRADES_PATH.exists()
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry_time", "exit_time", "symbol", "entry_price",
                                           "exit_price", "pump_2h_pct", "pnl_pct", "min_price_hold",
                                           "max_price_hold", "mfe_pct", "mae_pct"])
        if new: w.writeheader()
        w.writerow(row)


def main():
    buf = _load_json(BUF_PATH, {})        # sym -> list[[ts, price]]
    positions = _load_json(POS_PATH, {})  # sym -> {entry_ts, entry_price, pump, exit_ts, min_p, max_p}
    cooldown = {}                         # sym -> until_ts
    log.info(f"블로우오프 숏 페이퍼 시작 — {len(UNIVERSE)}코인, +{PUMP_PCT}%/2h→8h숏(무손절) | 순수모의(주문0)")
    try:
        from bithumb import notify
        notify.send(f"📡 블로우오프 숏 페이퍼 시작 — 바이낸스 급등되돌림 숏 검증(모의, 주문0)")
    except Exception: pass

    while True:
        try:
            now = time.time()
            prices = all_prices()

            # 1) 가격버퍼 갱신 + 진입 신호 탐지
            for coin in UNIVERSE:
                sym = f"{coin}USDT"
                px = prices.get(sym)
                if not px or px <= 0:
                    continue
                b = buf.setdefault(sym, [])
                b.append([now, px])
                # 오래된 것 정리
                if len(b) > BUF_MAXLEN:
                    del b[:len(b) - BUF_MAXLEN]

                if sym in positions:
                    continue
                if cooldown.get(sym, 0) > now:
                    continue
                if len(b) < LOOKBACK_SAMPLES:
                    continue
                past = b[-LOOKBACK_SAMPLES][1]
                if past <= 0:
                    continue
                ret2h = (px / past - 1) * 100
                if ret2h >= PUMP_PCT:
                    # 가격 급등 후보 → 거래량 필터 확인(코인당 1회 klines 조회)
                    vok, vr = volume_spike_ok(sym)
                    if not vok:
                        cooldown[sym] = now + COOLDOWN_SEC  # 이번 급등은 거래량 미달, 쿨다운 걸어 재확인 억제
                        log.info(f"급등 감지했으나 거래량 미달 스킵 {sym} (2h+{ret2h:.0f}%, 거래량 {vr:.1f}배<{VOL_MULT:.0f})")
                        continue
                    positions[sym] = {"entry_ts": now, "entry_price": px, "pump": round(ret2h, 2),
                                      "vol_mult": round(vr, 1),
                                      "exit_ts": now + HOLD_SEC, "min_p": px, "max_p": px,
                                      "entry_iso": datetime.now(KST).isoformat()}
                    cooldown[sym] = now + COOLDOWN_SEC
                    log.warning(f"숏 진입(모의) {sym} @{px:g} (2h +{ret2h:.1f}%, 거래량 {vr:.1f}배) → 8h후 청산")
                    try:
                        from bithumb import notify
                        notify.send(f"📉 블로우오프 숏 진입(모의) {sym} 2h+{ret2h:.0f}% 거래량{vr:.0f}배 @{px:g}")
                    except Exception: pass

            # 2) 열린 포지션 추적 + 만기 청산
            for sym in list(positions.keys()):
                pos = positions[sym]
                px = prices.get(sym)
                if px and px > 0:
                    pos["min_p"] = min(pos["min_p"], px)   # 숏 유리(최저가)
                    pos["max_p"] = max(pos["max_p"], px)   # 숏 불리(최고가=역행)
                if now >= pos["exit_ts"]:
                    exit_px = px if (px and px > 0) else pos["entry_price"]
                    entry = pos["entry_price"]
                    pnl = (1 - exit_px / entry) * 100 - COST_PCT   # 숏: 하락분이 이익
                    mfe = (1 - pos["min_p"] / entry) * 100          # 최대 유리(하락)
                    mae = (1 - pos["max_p"] / entry) * 100          # 최대 불리(음수=역행상승)
                    log_trade(dict(
                        entry_time=pos["entry_iso"], exit_time=datetime.now(KST).isoformat(),
                        symbol=sym, entry_price=entry, exit_price=exit_px, pump_2h_pct=pos["pump"],
                        pnl_pct=round(pnl, 2), min_price_hold=pos["min_p"], max_price_hold=pos["max_p"],
                        mfe_pct=round(mfe, 2), mae_pct=round(mae, 2),
                    ))
                    log.warning(f"숏 청산(모의) {sym} @{exit_px:g} pnl={pnl:+.2f}% (최대유리+{mfe:.1f}% 최대역행{mae:+.1f}%)")
                    try:
                        from bithumb import notify
                        notify.send(f"📈 블로우오프 숏 청산(모의) {sym} pnl={pnl:+.1f}%")
                    except Exception: pass
                    del positions[sym]

            _save_json(BUF_PATH, buf)
            _save_json(POS_PATH, positions)
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
