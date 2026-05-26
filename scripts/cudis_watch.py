"""
CUDIS 전용 패턴 감시 스크립트 (메인 봇과 독립 실행)

데이터 분석 결과 기반 최적 진입 조건:
  펌핑 5~8% → 낙폭 -2~-4% → 야간(1,19~21시) → 고점 2분+ → 거래량 10~20x
  → 조건 4개 이상 충족 시 텔레그램 알림

Run: python scripts/cudis_watch.py
"""
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bithumb.client import BithumbClient
from bithumb.indicators import calc_rsi, calc_macd_bull, is_ema_bouncing
from bithumb import notify

KST          = timezone(timedelta(hours=9))
COIN         = "CUDIS"
GOOD_HOURS   = {1, 19, 20, 21}   # 반등률 높은 시간대 (팀1 분석)
PUMP_MIN     = 5.0
PUMP_MAX     = 8.0
DROP_MIN     = -4.0
DROP_MAX     = -2.0
VOL_MIN      = 10.0
VOL_MAX      = 20.0
PEAK_MIN_MIN = 2       # 고점 유지 최소 분
ALERT_MIN_CONDITIONS = 4   # 알림 발동 최소 조건 수

CHECK_INTERVAL = 30  # 초

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CUDIS] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "cudis_watch.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

_last_alert_ts: float = 0.0   # 중복 알림 방지 (10분 쿨다운)


def analyze(client) -> None:
    global _last_alert_ts

    candles = client.get_candles(f"KRW-{COIN}", unit=3, count=60)
    if not candles or len(candles) < 15:
        return

    current = float(candles[0]["trade_price"])

    # 최근 20캔들(1시간)에서 고점 탐색
    window = candles[:20]
    peak_idx = min(range(len(window)), key=lambda i: -float(window[i]["high_price"]))
    peak_price = float(window[peak_idx]["high_price"])

    # 고점 직전 5캔들 최저가 = 펌핑 기준가
    before = candles[peak_idx + 1 : peak_idx + 6]
    if len(before) < 2:
        return
    base_price = min(float(c["low_price"]) for c in before)
    if base_price <= 0:
        return

    pump_pct      = (peak_price - base_price) / base_price * 100
    drop_pct      = (current - peak_price) / peak_price * 100
    min_since_peak = peak_idx * 3   # 고점 경과 분

    # 거래량 배수 (고점 캔들 vs 직전 5캔들 평균)
    peak_vol  = float(window[peak_idx].get("candle_acc_trade_volume", 0))
    prev_vols = [float(c.get("candle_acc_trade_volume", 0)) for c in before]
    avg_vol   = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    vol_mult  = peak_vol / avg_vol if avg_vol > 0 else 0

    rsi     = calc_rsi(candles)
    macd_ok = calc_macd_bull(candles)
    ema_ok  = is_ema_bouncing(candles)
    hour    = datetime.now(KST).hour

    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
    log.info(
        f"현재가={current:,.2f}원 | 펌핑={pump_pct:+.1f}% | 낙폭={drop_pct:+.1f}% | "
        f"고점경과={min_since_peak}분 | 거래량={vol_mult:.1f}x | RSI={rsi_str} | {hour}시"
    )

    # 조건 평가
    conds = {
        f"펌핑 5~8% ({pump_pct:+.1f}%)":      PUMP_MIN <= pump_pct <= PUMP_MAX,
        f"낙폭 -2~-4% ({drop_pct:+.1f}%)":    DROP_MAX <= drop_pct <= DROP_MIN,
        f"야간 시간대 ({hour}시)":              hour in GOOD_HOURS,
        f"고점 2분+ ({min_since_peak}분)":     min_since_peak >= PEAK_MIN_MIN,
        f"거래량 10~20x ({vol_mult:.1f}x)":   VOL_MIN <= vol_mult <= VOL_MAX,
        f"RSI 회복 ({rsi_str})":               rsi is not None and 28 <= rsi <= 40,
    }

    passed = sum(conds.values())

    if passed >= ALERT_MIN_CONDITIONS:
        now_ts = time.time()
        if now_ts - _last_alert_ts < 600:  # 10분 이내 중복 알림 방지
            return
        _last_alert_ts = now_ts

        stars = "⭐" * (passed - 3)
        msg = (
            f"{stars} [CUDIS 진입 알림] {passed}/6 조건 충족\n\n"
        )
        for name, ok in conds.items():
            msg += f"{'✅' if ok else '❌'} {name}\n"
        msg += f"\n현재가: {current:,.2f}원"
        if passed == 6:
            msg += "\n\n🚀 전 조건 충족 — 즉시 진입 고려!"

        notify.send(msg)
        log.warning(f"알림 전송: {passed}/6 조건 충족 | 현재가={current:,.2f}원")


def main() -> None:
    client = BithumbClient("config.yaml")

    log.info("=== CUDIS 전용 감시 시작 ===")
    log.info(f"조건: 펌핑{PUMP_MIN}~{PUMP_MAX}% | 낙폭{DROP_MAX}~{DROP_MIN}% | "
             f"시간{sorted(GOOD_HOURS)}시 | 고점{PEAK_MIN_MIN}분+ | 거래량{VOL_MIN}~{VOL_MAX}x")
    notify.send(
        f"👁 CUDIS 감시 시작\n"
        f"조건: 펌핑{PUMP_MIN}~{PUMP_MAX}% + 낙폭{DROP_MAX}~{DROP_MIN}% + "
        f"야간{sorted(GOOD_HOURS)}시 + 고점{PEAK_MIN_MIN}분+"
    )

    while True:
        try:
            analyze(client)
        except Exception as e:
            log.error(f"분석 실패: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
