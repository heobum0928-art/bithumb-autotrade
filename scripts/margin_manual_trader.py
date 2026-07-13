"""
재량 마진숏 리스크관리 도구 (margin_manual_trader) — 2026-07-13.

manual_trader.py(빗썸 재량 롱)와 동일 철학, 방향만 반대(바이낸스 크로스마진 숏):
"뭘 숏칠지"는 사람이 고르고(뉴스·차트 판단), "손절폭·사이징"은 봇이 기계적으로 정한다.
과거 실패 원인(감정적 매도 타이밍, 변동성 대비 너무 좁은 고정 손절폭)을 봇이 막는다.

핵심 규칙 (manual_trader.py와 동일 — 진입 시점 실측 변동성 기반 SL/TRAIL):
  진입 직전 최근 24개 5분봉의 평균 (고가-저가)/종가 %를 측정해 SL/TRAIL을 그 배수로 설정.
  손실은 짧게, 수익은 길게(트레일링만, 고정 익절 없음).
  ★ 숏이라 방향이 반대: 손절=가격상승, 트레일=저점 대비 반등 시 청산(peak_price 대신 trough_price).

사용법 (tg_bot.py에서 명령어로 호출):
  margin_manual_trader.enter("PYR")       → 진입 (margin_guard "manualshort" 엔진, 기본 dry)
  margin_manual_trader.check_positions()  → 열린 포지션 손절/트레일 체크, 청산 시 메시지 리스트 반환
  margin_manual_trader.status_text()      → 현재 포지션 상태 텍스트

★ margin_guard 4중 관문 그대로 적용 — data/margin_live_config.json에서 "manualshort"가
armed_engines에 없으면 항상 dry(모의). 실전 전환은 사용자가 직접 켜야 함(코드가 안 켬).

검증 게이트 없음(원래 manual_trader도 재량 도구라 예외) — 단 기본 소액(20 USDT), MARGINAL 취급.
포지션 data/margin_manual_pos.json | 거래기록 data/margin_manual_trades.csv | 로그 logs/margin_manual_trader.log
"""
import sys, json, csv, logging, statistics, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests
from bithumb.margin_guard import MarginGuard, load_config

KST = timezone(timedelta(hours=9))
ENGINE = "manualshort"
BASE = "https://api.binance.com"
POS_PATH = ROOT / "data" / "margin_manual_pos.json"
TRADES_PATH = ROOT / "data" / "margin_manual_trades.csv"
DEFAULT_MARGIN_USDT = 50.0   # live_config.json engine_caps_usdt["manualshort"]와 일치시킬 것 (2026-07-13 20→50 상향, 사용자요청)

# 변동성 기반 SL/TRAIL 파라미터 — manual_trader.py와 동일 배수/클램프
VOL_LOOKBACK_BARS = 24     # 5분봉 24개 = 2시간
SL_MULT, SL_MIN, SL_MAX = 2.5, 3.0, 8.0
TRAIL_MULT, TRAIL_MIN, TRAIL_MAX = 2.0, 3.0, 8.0
ARM_MULT, ARM_MIN, ARM_MAX = 2.5, 4.0, 10.0

Path(ROOT / "logs").mkdir(exist_ok=True)
# ★ 2026-07-13 버그수정: logging.basicConfig()는 프로세스당 최초 1회만 유효(이후 호출은 무시됨).
#   tg_bot.py가 manual_trader를 먼저 import해서 그쪽 basicConfig가 루트로거를 선점 → 이 모듈의
#   basicConfig는 조용히 무시되고, 여기서 찍는 로그(청산·손절 포함)가 전부 manual_trader.log로
#   새어나가고 있었음(UNI 손절 이벤트를 모니터가 놓친 원인). margin_guard.py와 동일하게 이 로거
#   전용 핸들러를 직접 붙이는 방식으로 교체 — 다른 모듈의 basicConfig 호출 순서와 무관하게 항상 자기 파일에 씀.
log = logging.getLogger(__name__)
if not log.handlers:
    h = logging.FileHandler(ROOT / "logs" / "margin_manual_trader.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [MMANUAL] %(message)s"))
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
    """진입 직전 실측 변동성 — 최근 N개 5분봉의 평균 (고가-저가)/종가 %."""
    try:
        r = requests.get(f"{BASE}/api/v3/klines", params={"symbol": sym, "interval": "5m", "limit": VOL_LOOKBACK_BARS}, timeout=8)
        k = r.json()
        if r.status_code != 200 or not k:
            return 2.0
    except Exception as e:
        log.warning(f"캔들 조회 실패({e}) → 기본 변동성 2.0% 사용")
        return 2.0
    ranges = []
    for c in k:
        hi, lo, cl = float(c[2]), float(c[3]), float(c[4])
        if cl > 0:
            ranges.append((hi - lo) / cl * 100)
    return statistics.mean(ranges) if ranges else 2.0


def enter(coin: str, margin_usdt: float = None) -> str:
    """재량 숏 진입. 성공/차단 여부와 계산된 SL/TRAIL을 텍스트로 반환(텔레그램 응답용)."""
    coin = coin.upper().strip()
    sym = f"{coin}USDT"
    margin_usdt = margin_usdt or DEFAULT_MARGIN_USDT

    positions = _load_positions()
    if coin in positions:
        return f"⚠️ {coin} 이미 재량숏 포지션 보유 중 — 중복 진입 안 함"

    # ★ 2026-07-13 버그수정: 같은 코인 중복만 막고 있어서, 다른 코인 여러 개 넣으면 엔진상한(50)을
    #   넘겨서 누적될 수 있었음(에이전트 감사로 발견) — 코인 무관 누적 증거금으로 상한 체크 추가.
    #   live만 합산: margin_guard.open_short()는 게이트 실패 시 실주문 코드(POST /sapi/v1/margin/order)에
    #   도달하기 전 조기 return하므로(margin_guard.py:219-222), dry 포지션은 코드 구조상 거래소에 전혀
    #   닿지 않음 — 실제 증거금 소모 0 확정. 전부 합산하면 dry잔재가 실전상한을 잘못 막는 역효과만 생김.
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
    res = guard.open_short(coin, margin_usdt)
    is_live = bool(res.get("live"))

    positions[coin] = {
        "coin": coin, "entry_price": entry_price, "margin_usdt": margin_usdt,
        "sl_pct": round(sl_pct, 2), "trail_pct": round(trail_pct, 2), "arm_pct": round(arm_pct, 2),
        "measured_vol_pct": round(vol_pct, 2),
        "stop_price": entry_price * (1 + sl_pct / 100),   # ★숏: 가격 상승이 손실 → 상방 손절
        "trough_price": entry_price,                       # ★숏: peak 대신 저점 추적
        "armed": False,
        "entered_at": datetime.now(KST).isoformat(),
        "live": is_live,
        "qty": res.get("qty") if is_live else None,
    }
    _save_positions(positions)
    log.warning(f"재량숏 진입 {coin} @{entry_price:,.6g} SL+{sl_pct:.1f}% TRAIL{trail_pct:.1f}%(arm-{arm_pct:.1f}%) "
                f"실측변동성{vol_pct:.2f}% live={is_live} ({res})")

    mode = "🔴 실전" if is_live else "🔵 모의(dry)"
    return (f"{mode} 재량숏 진입 {coin} @{entry_price:,.6g} ({margin_usdt:.0f}USDT 증거금)\n"
            f"실측변동성(2h): {vol_pct:.2f}%\n"
            f"손절: +{sl_pct:.1f}% (@{positions[coin]['stop_price']:,.6g}, 가격 오르면 손절)\n"
            f"트레일: -{arm_pct:.1f}% 도달 시 무장 → 저점대비 +{trail_pct:.1f}% 반등하면 청산\n"
            f"익절 상한 없음 — 떨어지는 만큼 트레일로 따라감")


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

        pos["trough_price"] = min(pos["trough_price"], price)   # ★숏: 최저가 갱신
        favorable_pct = (1 - pos["trough_price"] / pos["entry_price"]) * 100   # 저점 기준 유리했던 폭(하락%)
        if not pos["armed"] and favorable_pct >= pos["arm_pct"]:
            pos["armed"] = True
            log.info(f"{coin} 트레일 무장 (저점 {pos['trough_price']:,.6g}, -{favorable_pct:.1f}%)")

        exit_reason = None
        if price >= pos["stop_price"]:
            exit_reason = f"손절+{pos['sl_pct']:.1f}%"
        elif pos["armed"]:
            trail_level = pos["trough_price"] * (1 + pos["trail_pct"] / 100)
            if price >= trail_level:
                exit_reason = f"트레일(저점-{favorable_pct:.1f}%→{(1-price/pos['entry_price'])*100:+.1f}%)"

        if exit_reason is None:
            changed = True
            continue

        # 청산
        is_live = bool(pos.get("live"))
        if is_live:
            cres = guard.close_short(pos["coin"])
            # ★margin_short_trader와 동일 안전패턴: 청산 실패 시 포지션 유지+재시도+알림
            if not cres.get("live"):
                fails = pos.get("close_fails", 0) + 1
                pos["close_fails"] = fails
                log.error(f"★재량숏 청산 실패(포지션 유지, 재시도예정) {coin} → {cres} (연속{fails}회)")
                if fails in (1, 3) or fails % 10 == 0:
                    msgs.append(f"🚨 재량숏 청산 실패 {coin} → {cres} (연속{fails}회) — 실거래소 포지션 열려있음! 확인 필요")
                changed = True
                continue

        pnl_pct = (1 - price / pos["entry_price"]) * 100
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
        log.warning(f"재량숏 청산 {coin} @{price:,.6g} {exit_reason} pnl={pnl_pct:+.2f}%({pnl_usdt:+.2f}USDT)")
        mode = "🔴" if is_live else "🔵"
        pnl_note = f" ({pnl_usdt:+.2f}USDT)" if is_live else ""
        msgs.append(f"{mode} 재량숏 청산 {coin} @{price:,.6g}\n{exit_reason}\n손익: {pnl_pct:+.2f}%{pnl_note}")
        del positions[coin]
        changed = True

    if changed:
        _save_positions(positions)
    return msgs


def status_text() -> str:
    positions = _load_positions()
    if not positions:
        return "재량숏 포지션 없음"
    lines = ["<b>[재량숏 포지션]</b>"]
    for coin, pos in positions.items():
        price = _price(f"{coin}USDT")
        chg = (1 - price / pos["entry_price"]) * 100 if price > 0 else 0
        mode = "🔴실전" if pos.get("live") else "🔵모의"
        armed = "무장" if pos["armed"] else "대기"
        lines.append(f"{mode} {coin}: {pos['entry_price']:,.6g}→{price:,.6g} (숏손익{chg:+.2f}%) "
                     f"| 손절+{pos['sl_pct']:.1f}% 트레일{armed}")
    return "\n".join(lines)
