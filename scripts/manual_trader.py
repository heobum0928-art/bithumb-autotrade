"""
재량매매 리스크관리 도구 (manual_trader) — 2026-07-09.

설계 철학: "뭘 살지"는 사람이 고르고 "언제 팔지/얼마나 살지"는 봇이 기계적으로 정한다.
차트를 보고 판단하는 재량(엣지가 있을 수도 있는 유일한 부분)은 그대로 쓰되,
과거 실패 원인(감정적 매도 타이밍, 변동성 대비 너무 좁은 고정 손절폭)을 봇이 막는다.

핵심 규칙 (docs/STRATEGY.md 재량매매 실험 2026-07-04 교훈 반영):
  "손절은 임의 고정폭이 아니라 진입 시점 실측 변동성(최근 캔들 레인지) 대비로 설정할 것"
  → 진입 직전 최근 24개 5분봉의 평균 (고가-저가)/종가 %를 측정해 SL/TRAIL을 그 배수로 설정.
  손실은 짧게, 수익은 길게(트레일링만, 고정 익절 없음) — 캐스케이드/whale_print와 동일 철학.

사용법 (tg_bot.py에서 명령어로 호출):
  manual_trader.enter("MPLX")       → 진입 (live_guard "manual" 엔진, 기본 dry)
  manual_trader.check_positions()   → 열린 포지션 손절/트레일 체크, 청산 시 메시지 리스트 반환
  manual_trader.status_text()       → 현재 포지션 상태 텍스트

★ live_guard 4중 관문 그대로 적용 — data/live_config.json에서 "manual"이 armed_engines에
없으면 항상 dry(모의). 실전 전환은 사용자가 직접 live_config.json을 켜야 함(코드가 안 켬).

검증 게이트 (실거래 전환 전 통과 필수): 모의 표본 n≥10~20건, 비용(0.5%) 차감 후 평균 기대값 > 0.
포지션 data/manual_pos.json | 거래기록 data/manual_trades.csv | 로그 logs/manual_trader.log
"""
import sys, json, csv, logging, statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bithumb.client import BithumbClient
from bithumb.live_guard import LiveGuard, round_to_tick

KST = timezone(timedelta(hours=9))
ENGINE = "manual"
POS_PATH = ROOT / "data" / "manual_pos.json"
TRADES_PATH = ROOT / "data" / "manual_trades.csv"
DEFAULT_KRW = 100_000  # live_config.json engine_caps_krw["manual"]과 일치시킬 것

# 변동성 기반 SL/TRAIL 파라미터 (배수 + 상하한 클램프)
VOL_LOOKBACK_BARS = 24     # 5분봉 24개 = 2시간
SL_MULT, SL_MIN, SL_MAX = 2.5, 3.0, 8.0
TRAIL_MULT, TRAIL_MIN, TRAIL_MAX = 2.0, 3.0, 8.0
ARM_MULT, ARM_MIN, ARM_MAX = 2.5, 4.0, 10.0

Path(ROOT / "logs").mkdir(exist_ok=True)
# ★ 2026-07-13 버그수정(margin_manual_trader.py와 동일건): logging.basicConfig()는 프로세스당
#   최초 1회만 유효 — 이 모듈이 먼저 import되면 문제 없어 보이지만, import 순서가 바뀌거나 다른
#   모듈이 먼저 basicConfig를 호출하면 이 모듈 로그가 엉뚱한 파일로 샐 수 있음. 로거 전용 핸들러
#   직접 부착 방식으로 교체해 import 순서와 완전히 무관하게 만듦.
log = logging.getLogger(__name__)
if not log.handlers:
    h = logging.FileHandler(ROOT / "logs" / "manual_trader.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [MANUAL] %(message)s"))
    log.addHandler(h); log.setLevel(logging.INFO); log.propagate = False

_client = None
def _get_client():
    global _client
    if _client is None:
        _client = BithumbClient()
    return _client


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
                                           "measured_vol_pct", "krw", "pnl_pct", "pnl_krw",
                                           "reason", "live"])
        if new: w.writeheader()
        w.writerow(row)


def _measure_volatility_pct(client, market: str) -> float:
    """진입 직전 실측 변동성 — 최근 N개 5분봉의 평균 (고가-저가)/종가 %."""
    try:
        candles = client.get_candles(market, unit=5, count=VOL_LOOKBACK_BARS)
    except Exception as e:
        log.warning(f"캔들 조회 실패({e}) → 기본 변동성 2.0% 사용")
        return 2.0
    if not candles:
        return 2.0
    ranges = []
    for c in candles:
        hi = c.get("high_price", 0); lo = c.get("low_price", 0); cl = c.get("trade_price", 0)
        if cl > 0:
            ranges.append((hi - lo) / cl * 100)
    return statistics.mean(ranges) if ranges else 2.0


def enter(coin: str, krw: float = None) -> str:
    """재량 진입. 성공/차단 여부와 계산된 SL/TRAIL을 텍스트로 반환(텔레그램 응답용)."""
    coin = coin.upper().strip()
    market = f"KRW-{coin}"
    krw = krw or DEFAULT_KRW
    client = _get_client()

    positions = _load_positions()
    if coin in positions:
        return f"⚠️ {coin} 이미 포지션 보유 중 — 중복 진입 안 함"

    try:
        ticker = client.get_ticker(coin)
        entry_price = float(ticker.get("closing_price", 0))
    except Exception as e:
        return f"❌ {coin} 시세 조회 실패: {e}"
    if entry_price <= 0:
        return f"❌ {coin} 가격 조회 실패(0원)"

    vol_pct = _measure_volatility_pct(client, market)
    sl_pct = _clamp(vol_pct * SL_MULT, SL_MIN, SL_MAX)
    trail_pct = _clamp(vol_pct * TRAIL_MULT, TRAIL_MIN, TRAIL_MAX)
    arm_pct = _clamp(vol_pct * ARM_MULT, ARM_MIN, ARM_MAX)

    guard = LiveGuard(ENGINE)
    res = guard.execute_buy(client, market, krw)
    is_live = bool(res.get("live"))

    positions[coin] = {
        "market": market,
        "entry_price": entry_price,
        "krw": krw,
        "sl_pct": round(sl_pct, 2),
        "trail_pct": round(trail_pct, 2),
        "arm_pct": round(arm_pct, 2),
        "measured_vol_pct": round(vol_pct, 2),
        "stop_price": round_to_tick(entry_price * (1 - sl_pct / 100)),
        "peak_price": entry_price,
        "armed": False,
        "entered_at": datetime.now(KST).isoformat(),
        "live": is_live,
        "volume": res.get("result", {}).get("executed_volume") if is_live else None,
    }
    _save_positions(positions)
    log.warning(f"진입 {coin} @{entry_price:,.2f} SL-{sl_pct:.1f}% TRAIL{trail_pct:.1f}%(arm+{arm_pct:.1f}%) "
                f"실측변동성{vol_pct:.2f}% live={is_live}")

    mode = "🔴 실전" if is_live else "🔵 모의(dry)"
    return (f"{mode} 진입 {coin} @{entry_price:,.2f}원 ({krw:,.0f}원)\n"
            f"실측변동성(2h): {vol_pct:.2f}%\n"
            f"손절: -{sl_pct:.1f}% (@{positions[coin]['stop_price']:,.2f})\n"
            f"트레일: +{arm_pct:.1f}% 도달 시 무장 → 고점대비 -{trail_pct:.1f}%\n"
            f"익절 상한 없음 — 오르는 만큼 트레일로 따라감")


def check_positions() -> list[str]:
    """열린 포지션 전수 점검. 손절/트레일 조건 충족 시 청산하고 메시지 리스트 반환."""
    positions = _load_positions()
    if not positions:
        return []
    client = _get_client()
    guard = LiveGuard(ENGINE)
    msgs = []
    changed = False

    for coin, pos in list(positions.items()):
        try:
            ticker = client.get_ticker(coin)
            price = float(ticker.get("closing_price", 0))
        except Exception as e:
            log.warning(f"{coin} 시세조회 실패: {e}")
            continue
        if price <= 0:
            continue

        pos["peak_price"] = max(pos["peak_price"], price)
        peak_gain_pct = (pos["peak_price"] / pos["entry_price"] - 1) * 100
        if not pos["armed"] and peak_gain_pct >= pos["arm_pct"]:
            pos["armed"] = True
            log.info(f"{coin} 트레일 무장 (고점 {pos['peak_price']:,.2f}, +{peak_gain_pct:.1f}%)")

        exit_reason = None
        if price <= pos["stop_price"]:
            exit_reason = f"손절-{pos['sl_pct']:.1f}%"
        elif pos["armed"]:
            trail_level = pos["peak_price"] * (1 - pos["trail_pct"] / 100)
            if price <= trail_level:
                exit_reason = f"트레일(고점+{peak_gain_pct:.1f}%→{(price/pos['entry_price']-1)*100:+.1f}%)"

        if exit_reason is None:
            changed = True  # peak_price/armed 갱신됐을 수 있으므로 저장
            continue

        # 청산
        if pos["live"] and pos.get("volume"):
            res = guard.execute_sell_soft(client, pos["market"], float(pos["volume"]), krw_hint=pos["krw"])
        else:
            res = {"dry": True}
        pnl_pct = (price / pos["entry_price"] - 1) * 100
        pnl_krw = pos["krw"] * pnl_pct / 100
        if pos["live"]:
            guard.record_realized(pnl_krw)

        _log_trade(dict(
            entry_time=pos["entered_at"], exit_time=datetime.now(KST).isoformat(),
            coin=coin, entry_price=pos["entry_price"], exit_price=price,
            sl_pct=pos["sl_pct"], trail_pct=pos["trail_pct"], arm_pct=pos["arm_pct"],
            measured_vol_pct=pos["measured_vol_pct"], krw=pos["krw"],
            pnl_pct=round(pnl_pct, 2), pnl_krw=round(pnl_krw, 0),
            reason=exit_reason, live=pos["live"],
        ))
        log.warning(f"청산 {coin} @{price:,.2f} {exit_reason} pnl={pnl_pct:+.2f}%({pnl_krw:+,.0f}원)")
        mode = "🔴" if pos["live"] else "🔵"
        msgs.append(f"{mode} 청산 {coin} @{price:,.2f}원\n{exit_reason}\n손익: {pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)")
        del positions[coin]
        changed = True

    if changed:
        _save_positions(positions)
    return msgs


def status_text() -> str:
    positions = _load_positions()
    if not positions:
        return "재량매매 포지션 없음"
    lines = ["<b>[재량매매 포지션]</b>"]
    client = _get_client()
    for coin, pos in positions.items():
        try:
            price = float(client.get_ticker(coin).get("closing_price", 0))
            chg = (price / pos["entry_price"] - 1) * 100
        except Exception:
            price, chg = 0, 0
        mode = "🔴실전" if pos["live"] else "🔵모의"
        armed = "무장" if pos["armed"] else "대기"
        lines.append(f"{mode} {coin}: {pos['entry_price']:,.2f}→{price:,.2f} ({chg:+.2f}%) "
                     f"| 손절-{pos['sl_pct']:.1f}% 트레일{armed}")
    return "\n".join(lines)
