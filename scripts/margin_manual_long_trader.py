"""
재량 마진롱 리스크관리 도구 (margin_manual_long_trader) — 2026-07-14, 중장기 세팅 2026-07-17.

margin_manual_trader.py(재량 마진숏)와 완전히 동일한 철학, 방향만 반대(바이낸스 크로스마진 롱):
"뭘 롱칠지"는 사람이 고르고(뉴스·차트 판단), "손절폭·사이징"은 봇이 기계적으로 정한다.

★ 참고(2026-07-14): 이 프로젝트에서 "투매 후 반등 롱"을 자동신호로 검증했을 때는 실패했음
(docs/STRATEGY.md: "전 변형 마이너스·청산 높음"). 이 도구는 자동신호가 아니라 순수 재량 도구라
그 검증실패와는 무관 — 사람 판단이 신호, 봇은 리스크관리만.

★ 2026-07-17 중장기 전환: 재량숏(margin_manual_trader)이 6건 중 5건 손절폭에 정확히 걸리며
-14.6USDT(승률17%) 확정, manualshort 정지. 사용자 지시("승률보다 크게 먹는 방법, 단기말고 중장기")로
이 도구를 2시간 단타형에서 일봉 기반 중장기 스윙형으로 전환. 손절/트레일 폭을 넓혀 노이즈에
안 털리고 큰 상승을 끝까지 따라가는 쪽으로 재설계.

핵심 규칙 (변동성 기반 SL/TRAIL, 단 2026-07-17부터 측정 기준을 일봉으로 변경):
  진입 직전 최근 20일 일봉의 평균 (고가-저가)/종가 %를 측정해 SL/TRAIL을 그 배수로 설정
  (2시간 5분봉 노이즈가 아니라 일간 변동성 — 며칠~몇 주 보유를 전제로 함).
  손실은 짧게, 수익은 길게(트레일링만, 고정 익절 없음, 강제 시간청산 없음).
  ★ 롱이라 방향은 manual_trader.py(빗썸 재량롱)와 동일 — peak_price 추적, 저항 대신 고점 기준.

사용법 (tg_bot.py에서 명령어로 호출):
  margin_manual_long_trader.enter("WLD")      → 진입 (margin_guard "manuallong" 엔진, 기본 dry)
  margin_manual_long_trader.check_positions() → 열린 포지션 손절/트레일 체크, 청산 시 메시지 리스트 반환
  margin_manual_long_trader.status_text()     → 현재 포지션 상태 텍스트

★ margin_guard 4중 관문 그대로 적용 — data/margin_live_config.json에서 "manuallong"이
armed_engines에 없으면 항상 dry(모의). 실전 전환은 사용자가 직접 켜야 함(코드가 안 켬).

검증 게이트 없음(재량 도구라 manual_trader.py/margin_manual_trader.py와 동일 예외) — 기본 소액, MARGINAL 취급.
포지션 data/margin_manual_long_pos.json | 거래기록 data/margin_manual_long_trades.csv | 로그 logs/margin_manual_long_trader.log
"""
import sys, json, csv, logging, statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests
from bithumb.margin_guard import MarginGuard, load_config

KST = timezone(timedelta(hours=9))
ENGINE = "manuallong"
BASE = "https://api.binance.com"
POS_PATH = ROOT / "data" / "margin_manual_long_pos.json"
TRADES_PATH = ROOT / "data" / "margin_manual_long_trades.csv"
DEFAULT_MARGIN_USDT = 20.0   # 2026-07-17 중장기 소액테스트 — live_config.json engine_caps_usdt["manuallong"]와 별개(상한은 그대로 유지)

# ★ 2026-07-17 중장기 스윙 세팅 — 일봉 기준 변동성으로 SL/TRAIL을 넓게 잡아
#   노이즈에 안 털리고 큰 상승을 트레일로 끝까지 따라가는 쪽으로 재설계.
VOL_LOOKBACK_DAYS = 20      # 일봉 20개 = 약 3주
SL_MULT, SL_MIN, SL_MAX = 1.5, 8.0, 20.0
TRAIL_MULT, TRAIL_MIN, TRAIL_MAX = 2.0, 10.0, 25.0
ARM_MULT, ARM_MIN, ARM_MAX = 1.2, 6.0, 15.0

Path(ROOT / "logs").mkdir(exist_ok=True)
# ★ logging.basicConfig() 대신 로거전용 핸들러 직접부착 — margin_manual_trader.py의 2026-07-13 버그
#   (import순서에 따라 로그가 엉뚱한 파일로 새는 문제)를 처음부터 재발 방지.
log = logging.getLogger(__name__)
if not log.handlers:
    h = logging.FileHandler(ROOT / "logs" / "margin_manual_long_trader.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [MMLONG] %(message)s"))
    log.addHandler(h); log.setLevel(logging.INFO); log.propagate = False


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _load_positions() -> dict:
    try:
        return json.loads(POS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_positions(pos: dict):
    tmp = POS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(pos, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(POS_PATH)


def _log_trade(row: dict):
    new = not TRADES_PATH.exists()
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["entry_time", "exit_time", "coin", "entry_price",
                                           "exit_price", "sl_pct", "trail_pct", "arm_pct",
                                           "measured_vol_pct", "margin_usdt", "pnl_pct", "pnl_usdt",
                                           "reason", "live"])
        if new: w.writeheader()
        w.writerow(row)


def _price(sym):
    try:
        r = requests.get(f"{BASE}/api/v3/ticker/price", params={"symbol": sym}, timeout=8)
        return float(r.json()["price"]) if r.status_code == 200 else 0.0
    except Exception:
        return 0.0


def _measure_volatility_pct(sym: str) -> float:
    """진입 직전 실측 변동성 — 최근 N개 일봉의 평균 (고가-저가)/종가 % (중장기 스윙용, 2026-07-17)."""
    try:
        r = requests.get(f"{BASE}/api/v3/klines", params={"symbol": sym, "interval": "1d", "limit": VOL_LOOKBACK_DAYS}, timeout=8)
        k = r.json()
        if r.status_code != 200 or not k:
            return 8.0
    except Exception as e:
        log.warning(f"캔들 조회 실패({e}) → 기본 변동성 8.0% 사용")
        return 8.0
    ranges = []
    for c in k:
        hi, lo, cl = float(c[2]), float(c[3]), float(c[4])
        if cl > 0:
            ranges.append((hi - lo) / cl * 100)
    return statistics.mean(ranges) if ranges else 8.0


def enter(coin: str, margin_usdt: float = None) -> str:
    """재량 롱 진입. 성공/차단 여부와 계산된 SL/TRAIL을 텍스트로 반환(텔레그램 응답용)."""
    coin = coin.upper().strip()
    sym = f"{coin}USDT"
    margin_usdt = margin_usdt or DEFAULT_MARGIN_USDT

    positions = _load_positions()
    if coin in positions:
        return f"⚠️ {coin} 이미 재량롱 포지션 보유 중 — 중복 진입 안 함"

    # 누적노출 상한 확인(margin_manual_trader.py와 동일 — live만 합산, dry는 실제증거금 0이라 제외)
    open_margin = sum(p["margin_usdt"] for p in positions.values() if p.get("live"))
    engine_caps = load_config().get("engine_caps_usdt", {})
    ecap = engine_caps.get(ENGINE)
    if ecap is None:
        return f"🚨 설정오류: engine_caps_usdt에 {ENGINE} 없음 — 진입 차단(margin_live_config.json 확인 필요)"
    if open_margin + margin_usdt > ecap:
        return f"⚠️ {coin} 진입 보류 — 누적노출 {open_margin:.0f}+{margin_usdt:.0f} > 엔진상한 {ecap} (기존 포지션 정리 후 재시도)"

    entry_price = _price(sym)
    if entry_price <= 0:
        return f"❌ {coin} 시세 조회 실패(0)"

    vol_pct = _measure_volatility_pct(sym)
    sl_pct = _clamp(vol_pct * SL_MULT, SL_MIN, SL_MAX)
    trail_pct = _clamp(vol_pct * TRAIL_MULT, TRAIL_MIN, TRAIL_MAX)
    arm_pct = _clamp(vol_pct * ARM_MULT, ARM_MIN, ARM_MAX)

    guard = MarginGuard(ENGINE)
    res = guard.open_long(coin, margin_usdt)
    is_live = bool(res.get("live"))

    positions[coin] = {
        "coin": coin, "entry_price": entry_price, "margin_usdt": margin_usdt,
        "sl_pct": round(sl_pct, 2), "trail_pct": round(trail_pct, 2), "arm_pct": round(arm_pct, 2),
        "measured_vol_pct": round(vol_pct, 2),
        "stop_price": entry_price * (1 - sl_pct / 100),   # ★롱: 가격 하락이 손실 → 하방 손절
        "peak_price": entry_price,                         # ★롱: trough 대신 고점 추적
        "armed": False,
        "entered_at": datetime.now(KST).isoformat(),
        "live": is_live,
        "qty": res.get("qty") if is_live else None,
    }
    _save_positions(positions)
    log.warning(f"재량롱 진입 {coin} @{entry_price:,.6g} SL-{sl_pct:.1f}% TRAIL{trail_pct:.1f}%(arm+{arm_pct:.1f}%) "
                f"실측변동성{vol_pct:.2f}% live={is_live} ({res})")

    mode = "🔴 실전" if is_live else "🔵 모의(dry)"
    return (f"{mode} 재량롱 진입 {coin} @{entry_price:,.6g} ({margin_usdt:.0f}USDT 증거금)\n"
            f"실측변동성(2h): {vol_pct:.2f}%\n"
            f"손절: -{sl_pct:.1f}% (@{positions[coin]['stop_price']:,.6g}, 가격 떨어지면 손절)\n"
            f"트레일: +{arm_pct:.1f}% 도달 시 무장 → 고점대비 -{trail_pct:.1f}% 하락하면 청산\n"
            f"익절 상한 없음 — 오르는 만큼 트레일로 따라감")


def check_positions() -> list:
    """열린 포지션 전수 점검. 손절/트레일 조건 충족 시 청산하고 메시지 리스트 반환."""
    positions = _load_positions()
    if not positions:
        return []
    guard = MarginGuard(ENGINE)
    msgs = []
    changed = False

    for coin, pos in list(positions.items()):
        sym = f"{coin}USDT"
        price = _price(sym)
        if price <= 0:
            continue

        pos["peak_price"] = max(pos["peak_price"], price)   # ★롱: 최고가 갱신
        favorable_pct = (pos["peak_price"] / pos["entry_price"] - 1) * 100   # 고점 기준 유리했던 폭(상승%)
        if not pos["armed"] and favorable_pct >= pos["arm_pct"]:
            pos["armed"] = True
            log.info(f"{coin} 트레일 무장 (고점 {pos['peak_price']:,.6g}, +{favorable_pct:.1f}%)")

        exit_reason = None
        if price <= pos["stop_price"]:
            exit_reason = f"손절-{pos['sl_pct']:.1f}%"
        elif pos["armed"]:
            trail_level = pos["peak_price"] * (1 - pos["trail_pct"] / 100)
            if price <= trail_level:
                exit_reason = f"트레일(고점+{favorable_pct:.1f}%→{(price/pos['entry_price']-1)*100:+.1f}%)"

        if exit_reason is None:
            changed = True
            continue

        # 청산
        is_live = bool(pos.get("live"))
        if is_live:
            cres = guard.close_long(pos["coin"])
            # ★margin_manual_trader.py와 동일 안전패턴: 청산 실패 시 포지션 유지+재시도+알림
            if not cres.get("live"):
                fails = pos.get("close_fails", 0) + 1
                pos["close_fails"] = fails
                log.error(f"★재량롱 청산 실패(포지션 유지, 재시도예정) {coin} → {cres} (연속{fails}회)")
                if fails in (1, 3) or fails % 10 == 0:
                    msgs.append(f"🚨 재량롱 청산 실패 {coin} → {cres} (연속{fails}회) — 실거래소 포지션 열려있음! 확인 필요")
                changed = True
                continue

        pnl_pct = (price / pos["entry_price"] - 1) * 100
        pnl_usdt = pos["margin_usdt"] * load_config().get("leverage", 2) * (pnl_pct / 100) if is_live else 0.0
        if is_live:
            guard.record_realized(pnl_usdt)

        _log_trade(dict(
            entry_time=pos["entered_at"], exit_time=datetime.now(KST).isoformat(),
            coin=coin, entry_price=pos["entry_price"], exit_price=price,
            sl_pct=pos["sl_pct"], trail_pct=pos["trail_pct"], arm_pct=pos["arm_pct"],
            measured_vol_pct=pos["measured_vol_pct"], margin_usdt=pos["margin_usdt"],
            pnl_pct=round(pnl_pct, 2), pnl_usdt=round(pnl_usdt, 2),
            reason=exit_reason, live=is_live,
        ))
        log.warning(f"재량롱 청산 {coin} @{price:,.6g} {exit_reason} pnl={pnl_pct:+.2f}%({pnl_usdt:+.2f}USDT)")
        mode = "🔴" if is_live else "🔵"
        pnl_note = f" ({pnl_usdt:+.2f}USDT)" if is_live else ""
        msgs.append(f"{mode} 재량롱 청산 {coin} @{price:,.6g}\n{exit_reason}\n손익: {pnl_pct:+.2f}%{pnl_note}")
        del positions[coin]
        changed = True

    if changed:
        _save_positions(positions)
    return msgs


def status_text() -> str:
    positions = _load_positions()
    if not positions:
        return "재량롱 포지션 없음"
    lines = ["<b>[재량롱 포지션]</b>"]
    for coin, pos in positions.items():
        price = _price(f"{coin}USDT")
        chg = (price / pos["entry_price"] - 1) * 100 if price > 0 else 0
        mode = "🔴실전" if pos.get("live") else "🔵모의"
        armed = "무장" if pos["armed"] else "대기"
        lines.append(f"{mode} {coin}: {pos['entry_price']:,.6g}→{price:,.6g} (롱손익{chg:+.2f}%) "
                     f"| 손절-{pos['sl_pct']:.1f}% 트레일{armed}")
    return "\n".join(lines)
