"""
CUDIS 전용 자동매매 스크립트 (메인 봇과 독립 실행)

데이터 분석 결과 기반 최적 진입 조건 (pump_log 487건 분석):
  펌핑 5~8% → 낙폭 -2~-4% → 오후(16~18시, 펌핑 100% 16시 발생) → 고점 2분+ → 거래량 10~20x
  → 조건 4개 이상 충족 시 자동 매수 → 트레일/손절 자동 매도

Run: python scripts/cudis_watch.py
"""
import sys
import time
import logging
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bithumb.client import BithumbClient
from bithumb.indicators import calc_rsi, calc_macd_bull, is_ema_bouncing
from bithumb.db import init_db, log_trade
from bithumb import notify

KST = timezone(timedelta(hours=9))

# ── 감시 파라미터 (분석 결과 기반) ──────────────────────────────────────────────
COIN            = "CUDIS"
GOOD_HOURS      = {16, 17, 18}   # 25건 전부 16시 펌핑 → 반등도 16~18시
PUMP_MIN        = 5.0
PUMP_MAX        = 8.0
DROP_MIN        = -12.0  # 실제 CUDIS 낙폭 -7~-10% (기존 -4.0은 0건 통과 불가 버그)
DROP_MAX        = -7.0   # 실제 낙폭 88%가 -7~-10% 구간
VOL_MIN         = 5.0    # 실제 최솟값 5.5x
VOL_MAX         = 60.0   # 실제 최댓값 57x
PEAK_MIN_MIN    = 2
ALERT_MIN_CONDS = 4
CHECK_INTERVAL  = 30   # 감시 주기 (초)

# ── 매매 파라미터 ────────────────────────────────────────────────────────────────
ENTRY_KRW          = 50_000      # 진입 금액
SL_PCT             = -0.02       # 손절 -2%
TRAIL_ACTIVATE_PCT =  0.02       # 트레일 발동 +2%
TRAIL_PCT          =  0.02       # 트레일 폭 2%
TP_HALF_PCT        =  0.05       # 1차 익절 +5%
MAX_HOLD_SEC       =  900        # 최대 보유 15분
MONITOR_INTERVAL   =  5          # 포지션 모니터링 주기 (초)

# ── 상태 ─────────────────────────────────────────────────────────────────────────
STATE_FILE   = ROOT / "data" / "cudis_pos.json"
_last_alert  = 0.0   # 중복 알림 방지 (10분 쿨다운)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CUDIS] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "cudis_watch.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── 주문 헬퍼 ────────────────────────────────────────────────────────────────────

def wait_order(client: BithumbClient, uuid: str, timeout: int = 20) -> dict:
    for _ in range(timeout):
        time.sleep(1)
        try:
            o = client.get_order(uuid)
            if o.get("state") == "done":
                return o
        except Exception:
            pass
    return {}


def get_balance(client: BithumbClient, coin: str) -> float:
    try:
        for a in client.get_accounts():
            if a["currency"] == coin.upper():
                return float(a["balance"])
    except Exception:
        pass
    return 0.0


def do_buy(client: BithumbClient) -> dict | None:
    """지정가 매수 — 최우선 매도호가로 진입."""
    market = f"KRW-{COIN}"
    try:
        ob   = client.get_orderbook(COIN)
        asks = ob.get("asks", [])
        if not asks:
            log.warning("매도호가 없음 — 진입 포기")
            return None
        best_ask = min(float(a["price"]) for a in asks)
        max_price = best_ask * 1.005   # 슬리피지 0.5% 허용
        volume   = ENTRY_KRW / best_ask
        log.info(f"지정가 매수 {best_ask:,.3f}원 × {volume:.4f}")
        r    = client.limit_buy(market, best_ask, volume)
        uuid = r.get("uuid")
        if not uuid:
            log.error("UUID 없음")
            return None
        order = wait_order(client, uuid)
        if order.get("state") != "done":
            log.warning("미체결 — 취소")
            try: client.cancel_order(uuid)
            except Exception: pass
            return None
        vol   = float(order.get("executed_volume", 0))
        funds = float(order.get("executed_funds",  0))
        fee   = float(order.get("paid_fee",        0))
        if vol <= 0:
            return None
        entry = funds / vol
        cost  = funds + fee
        log.info(f"매수 체결 | 단가={entry:,.3f}원 수량={vol:.4f} 비용={cost:,.0f}원")
        notify.send(f"🟢 [CUDIS 매수]\n단가={entry:,.3f}원 | 수량={vol:.4f} | {cost:,.0f}원")
        pos = {"coin": COIN, "market": market, "volume": vol,
               "entry_price": entry, "cost": cost,
               "entered_at": datetime.now().isoformat(), "sold_half": False}
        STATE_FILE.write_text(json.dumps(pos), encoding="utf-8")
        return pos
    except Exception as e:
        log.error(f"매수 실패: {e}")
        return None


def do_sell(client: BithumbClient, pos: dict, volume: float, reason: str) -> float | None:
    """시장가 매도."""
    bal = get_balance(client, COIN)
    if bal <= 0:
        log.info("잔고 없음 — 외부 청산")
        return None
    vol = min(volume, bal)
    for attempt in range(1, 11):
        try:
            r    = client.market_sell(pos["market"], vol)
            uuid = r.get("uuid")
            order = wait_order(client, uuid)
            if order.get("state") == "done":
                received = float(order.get("executed_funds", 0)) - float(order.get("paid_fee", 0))
                log.info(f"매도 체결 | 사유={reason} 수령={received:,.0f}원")
                return received
        except Exception as e:
            log.warning(f"매도 {attempt}회 실패: {e}")
        bal = get_balance(client, COIN)
        if bal <= 0:
            return None
        vol = min(volume, bal)
        time.sleep(5)
    return None


# ── 포지션 모니터 ─────────────────────────────────────────────────────────────────

def monitor_position(client: BithumbClient, pos: dict) -> None:
    """매수 후 청산까지 모니터링 (트레일/손절/TP/타임아웃)."""
    entry   = pos["entry_price"]
    volume  = pos["volume"]
    cost    = pos["cost"]
    entered = datetime.fromisoformat(pos["entered_at"])
    highest = entry
    sold_half = False
    remaining = volume

    log.info(f"포지션 모니터 시작 | 진입={entry:,.3f}원 수량={volume:.4f}")

    while True:
        time.sleep(MONITOR_INTERVAL)
        try:
            ticker  = client.get_ticker(COIN)
            current = float(ticker.get("closing_price", 0) or ticker.get("trade_price", 0))
        except Exception:
            continue
        if current <= 0:
            continue

        if current > highest:
            highest = current

        hold_sec = (datetime.now() - entered).total_seconds()
        pnl_pct  = (current - entry) / entry * 100

        # 트레일 스탑 계산
        if highest >= entry * (1 + TRAIL_ACTIVATE_PCT):
            trail_stop = highest * (1 - TRAIL_PCT)
        else:
            trail_stop = entry * (1 + SL_PCT)

        log.info(
            f"현재={current:,.3f}원 | PnL={pnl_pct:+.2f}% | "
            f"고점={highest:,.3f}원 | 스탑={trail_stop:,.3f}원 | {hold_sec:.0f}초"
        )

        # 1차 익절 +5% → 절반 매도
        if not sold_half and current >= entry * (1 + TP_HALF_PCT):
            half = remaining / 2
            recv = do_sell(client, pos, half, f"1차익절+{TP_HALF_PCT*100:.0f}%")
            if recv:
                sold_half = True
                remaining -= half
                notify.send(
                    f"💰 [CUDIS 1차익절] +{TP_HALF_PCT*100:.0f}%\n"
                    f"현재={current:,.3f}원 | 수령={recv:,.0f}원"
                )
            continue

        # 손절 / 트레일 청산
        exit_reason = None
        if current <= trail_stop:
            if highest >= entry * (1 + TRAIL_ACTIVATE_PCT):
                exit_reason = f"트레일스탑 {pnl_pct:+.2f}%"
            else:
                exit_reason = f"손절 {pnl_pct:+.2f}%"

        # 타임아웃
        if hold_sec >= MAX_HOLD_SEC:
            exit_reason = f"타임아웃 {hold_sec:.0f}초 {pnl_pct:+.2f}%"

        if exit_reason:
            recv = do_sell(client, pos, remaining, exit_reason)
            recv_total = (recv or 0)
            if sold_half:
                # 이미 절반 팔았으므로 cost의 절반 기준
                recv_total += cost / 2
            pnl_krw = recv_total - cost
            log.info(f"청산 완료 | {exit_reason} | PnL={pnl_krw:+,.0f}원")
            notify.send(
                f"{'🔴' if pnl_krw < 0 else '🟢'} [CUDIS 청산] {exit_reason}\n"
                f"PnL={pnl_krw:+,.0f}원 ({(pnl_krw/cost*100):+.2f}%)"
            )
            try:
                log_trade(
                    coin=COIN, market=pos["market"],
                    entry_price=entry, exit_price=current,
                    volume=volume, cost_krw=cost,
                    received_krw=recv_total,
                    exit_reason=exit_reason,
                    entered_at=entered, exited_at=datetime.now(),
                    max_price=highest,
                )
            except Exception as e:
                log.error(f"DB 기록 실패: {e}")
            STATE_FILE.unlink(missing_ok=True)
            return


# ── 신호 감지 ─────────────────────────────────────────────────────────────────────

def check_entry(client: BithumbClient) -> bool:
    """조건 분석 후 진입 여부 반환."""
    global _last_alert

    candles = client.get_candles(f"KRW-{COIN}", unit=3, count=60)
    if not candles or len(candles) < 15:
        return False

    current = float(candles[0]["trade_price"])

    # 최근 20캔들(1시간) 고점
    window   = candles[:20]
    peak_idx = min(range(len(window)), key=lambda i: -float(window[i]["high_price"]))
    peak_price = float(window[peak_idx]["high_price"])

    # 고점 직전 5캔들 최저가 = 기준가
    before = candles[peak_idx + 1 : peak_idx + 6]
    if len(before) < 2:
        return False
    base_price = min(float(c["low_price"]) for c in before)
    if base_price <= 0:
        return False

    pump_pct       = (peak_price - base_price) / base_price * 100
    drop_pct       = (current - peak_price) / peak_price * 100
    min_since_peak = peak_idx * 3

    peak_vol  = float(window[peak_idx].get("candle_acc_trade_volume", 0))
    prev_vols = [float(c.get("candle_acc_trade_volume", 0)) for c in before]
    avg_vol   = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    vol_mult  = peak_vol / avg_vol if avg_vol > 0 else 0

    rsi  = calc_rsi(candles)
    hour = datetime.now(KST).hour
    rsi_str = f"{rsi:.1f}" if rsi else "N/A"

    log.info(
        f"현재={current:,.3f}원 | 펌핑={pump_pct:+.1f}% | 낙폭={drop_pct:+.1f}% | "
        f"고점경과={min_since_peak}분 | 거래량={vol_mult:.1f}x | RSI={rsi_str} | {hour}시"
    )

    conds = {
        f"펌핑 5~8% ({pump_pct:+.1f}%)":       PUMP_MIN <= pump_pct <= PUMP_MAX,
        f"낙폭 -2~-4% ({drop_pct:+.1f}%)":     DROP_MAX <= drop_pct <= DROP_MIN,
        f"야간 시간대 ({hour}시)":               hour in GOOD_HOURS,
        f"고점 2분+ ({min_since_peak}분)":      min_since_peak >= PEAK_MIN_MIN,
        f"거래량 10~20x ({vol_mult:.1f}x)":    VOL_MIN <= vol_mult <= VOL_MAX,
        f"RSI 회복 ({rsi_str})":                rsi is not None and 28 <= rsi <= 42,
    }

    passed = sum(conds.values())

    if passed >= ALERT_MIN_CONDS:
        now_ts = time.time()
        msg = f"🎯 [CUDIS] {passed}/6 조건 충족 → 자동 매수 시도\n\n"
        for name, ok in conds.items():
            msg += f"{'✅' if ok else '❌'} {name}\n"
        msg += f"\n현재가: {current:,.3f}원"
        if now_ts - _last_alert >= 600:
            notify.send(msg)
            _last_alert = now_ts
        log.warning(f"진입 조건 충족: {passed}/6")
        return True

    return False


# ── 메인 ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    init_db()
    client = BithumbClient("config.yaml")

    # 크래시 후 복구: 포지션 파일 있으면 모니터링 재개
    if STATE_FILE.exists():
        try:
            pos = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            log.warning(f"미청산 포지션 발견 — 모니터링 재개: {pos}")
            notify.send(f"♻️ [CUDIS] 재시작 — 포지션 복구: {pos['entry_price']:,.3f}원")
            monitor_position(client, pos)
        except Exception as e:
            log.error(f"포지션 복구 실패: {e}")
            STATE_FILE.unlink(missing_ok=True)

    log.info("=== CUDIS 자동매매 감시 시작 ===")
    log.info(
        f"진입조건: 펌핑{PUMP_MIN}~{PUMP_MAX}% | 낙폭{DROP_MAX}~{DROP_MIN}% | "
        f"시간{sorted(GOOD_HOURS)}시 | 고점{PEAK_MIN_MIN}분+ | 거래량{VOL_MIN}~{VOL_MAX}x | {ALERT_MIN_CONDS}개 이상"
    )
    log.info(
        f"매매조건: 진입{ENTRY_KRW:,}원 | 손절{SL_PCT*100:.0f}% | "
        f"트레일{TRAIL_PCT*100:.0f}%(발동{TRAIL_ACTIVATE_PCT*100:.0f}%) | "
        f"1차익절{TP_HALF_PCT*100:.0f}% | 최대{MAX_HOLD_SEC//60}분"
    )
    notify.send(
        f"👁 CUDIS 자동매매 감시 시작\n"
        f"조건 {ALERT_MIN_CONDS}개+ → 자동 매수 {ENTRY_KRW:,}원\n"
        f"SL{SL_PCT*100:.0f}% | 트레일{TRAIL_PCT*100:.0f}% | 1차익절{TP_HALF_PCT*100:.0f}%"
    )

    while True:
        try:
            # 메인 봇이 CUDIS 포지션 보유 중이면 대기
            main_pos_file = ROOT / "data" / "active_pos.json"
            if main_pos_file.exists():
                try:
                    mp = json.loads(main_pos_file.read_text(encoding="utf-8"))
                    if mp.get("coin") == COIN:
                        log.info("메인 봇이 CUDIS 보유 중 — 진입 대기")
                        time.sleep(CHECK_INTERVAL)
                        continue
                except Exception:
                    pass

            if check_entry(client):
                pos = do_buy(client)
                if pos:
                    monitor_position(client, pos)
                    log.info("포지션 종료 — 감시 재개")
                else:
                    log.warning("매수 실패 — 계속 감시")
                    time.sleep(60)

        except Exception as e:
            log.error(f"오류: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
