"""
CS봇 전략 백테스트 - 빗썸 1분봉 과거 데이터 시뮬레이션

Claude API 없이 코드 기반 진입 조건만 검증.
결과는 실제 CS봇 성과의 상한선 참고치로 해석할 것.

실행: python scripts/cs_backtest.py [--days N] [--coins XRP EOS ...]
"""

import sys
import time
import argparse
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── 전략 파라미터 (CS봇과 동일) ──────────────────────────────────────────
ENTRY_MIN_CHG5M = 0.3    # 진입 최소 5분 변화율 (%)
APLUS_CHG5M     = 0.5    # A+ 셋업 최소 5분 변화율 (%)
VOL_SPIKE_MULT  = 1.4    # 거래량 급증 배수 기준
PRICE_RISE_PCT  = 0.005  # 4캔들 대비 가격 상승 기준 (+0.5%)

BE_TRIGGER      = 0.01   # BE 발동 기준 (+1%)
FIXED_TP        = 0.03   # 익절 기준 (+3%)
HARD_SL         = -0.02  # 손절 기준 (-2%)
ROUND_TRIP_FEE  = 0.005  # 왕복 수수료 0.5%
TIMEOUT_MIN     = 60     # 최대 보유 시간 (분)

WINDOW_SIZE     = 12     # 진입 판단 캔들 수 (CS봇 동일)

# ── 기본 테스트 대상 코인 ─────────────────────────────────────────────────
DEFAULT_COINS = [
    "XRP", "EOS", "TRX", "ADA", "XLM",
    "HBAR", "DOGE", "LINK", "ATOM", "VET",
]

BASE_URL = "https://api.bithumb.com"


def fetch_candles(market: str, days: int = 7) -> list[dict]:
    """빗썸 API에서 1분봉 과거 데이터 수집 (오래된 순 정렬)."""
    all_candles: list[dict] = []
    to_dt = datetime.utcnow()
    cutoff = datetime.utcnow() - timedelta(days=days)

    while True:
        params = {
            "market": market,
            "count": 200,
            "to": to_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/candles/minutes/1", params=params, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"API 오류: {e}")
            break

        if not data or not isinstance(data, list):
            break

        all_candles.extend(data)

        oldest_str = data[-1]["candle_date_time_utc"]
        oldest_dt = datetime.fromisoformat(oldest_str)  # naive UTC

        if oldest_dt <= cutoff:
            break

        to_dt = oldest_dt - timedelta(seconds=1)
        time.sleep(0.12)

    # cutoff 이후만 + 오래된 순 정렬
    all_candles = [
        c for c in all_candles
        if datetime.fromisoformat(c["candle_date_time_utc"]) >= cutoff
    ]
    all_candles.sort(key=lambda c: c["candle_date_time_utc"])
    return all_candles


def check_entry(window: list[dict]) -> tuple[bool, str, float]:
    """12개 캔들 윈도우에서 CS봇 진입 조건 확인.

    Returns: (진입여부, 셋업타입, chg5m)
    """
    if len(window) < WINDOW_SIZE:
        return False, "", 0.0

    closes = [c["trade_price"] for c in window]
    vols   = [c["candle_acc_trade_volume"] for c in window]

    # 5분 변화율
    chg5m = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0.0

    # 기본 모멘텀 필터
    if chg5m < ENTRY_MIN_CHG5M:
        return False, "", chg5m

    # 거래량 추세
    v_new = sum(vols[-3:]) / 3
    v_old = sum(vols[-6:-3]) / 3
    if v_old == 0:
        return False, "", chg5m
    vol_spike = v_new > v_old * VOL_SPIKE_MULT

    # 가격 방향
    price_rising = closes[-1] > closes[-4] * (1 + PRICE_RISE_PCT)

    # A+ 셋업: 2연속 상승 + 거래량급증 + chg5m≥0.5% + 12분 고점 근처
    two_consec = closes[-1] > closes[-2] > closes[-3]
    near_high  = closes[-1] >= max(closes) * 0.995
    is_aplus   = two_consec and vol_spike and chg5m >= APLUS_CHG5M and near_high

    # 일반 진입: 가격상승 + 거래량급증
    is_regular = price_rising and vol_spike

    if is_aplus:
        return True, "A+", chg5m
    elif is_regular:
        return True, "regular", chg5m

    return False, "", chg5m


def simulate_trade(entry_price: float, future: list[dict]) -> dict:
    """진입가 기준 미래 캔들로 BE/TP/SL/타임아웃 청산 시뮬레이션."""
    be_active = False
    highest   = entry_price

    for i, c in enumerate(future[:TIMEOUT_MIN]):
        high  = c["high_price"]
        low   = c["low_price"]
        close = c["trade_price"]

        highest = max(highest, high)

        if highest >= entry_price * (1 + BE_TRIGGER):
            be_active = True

        # TP
        if high >= entry_price * (1 + FIXED_TP):
            return {"reason": "TP",  "pnl": FIXED_TP + 0 - ROUND_TRIP_FEE, "hold": i + 1}

        # BE 청산 (BE 발동 후 진입가 이하로 떨어지면)
        if be_active and low <= entry_price:
            return {"reason": "BE",  "pnl": 0 - ROUND_TRIP_FEE, "hold": i + 1}

        # SL
        if low <= entry_price * (1 + HARD_SL):
            return {"reason": "SL",  "pnl": HARD_SL - ROUND_TRIP_FEE, "hold": i + 1}

    # 타임아웃
    idx = min(TIMEOUT_MIN - 1, len(future) - 1)
    if idx < 0:
        return {"reason": "END", "pnl": -ROUND_TRIP_FEE, "hold": 0}
    last = future[idx]["trade_price"]
    pnl  = (last - entry_price) / entry_price - ROUND_TRIP_FEE
    return {"reason": "TIMEOUT", "pnl": pnl, "hold": idx + 1}


def run_coin(coin: str, candles: list[dict]) -> list[dict]:
    """한 코인의 전체 캔들에서 시그널 감지 + 트레이드 시뮬레이션."""
    trades: list[dict] = []
    n = len(candles)
    skip_until = 0

    for i in range(WINDOW_SIZE, n - 1):
        if i < skip_until:
            continue

        window = candles[i - WINDOW_SIZE:i]
        should_enter, setup, chg5m = check_entry(window)
        if not should_enter:
            continue

        # 다음 캔들 시가로 진입 (lookahead 방지)
        entry_price = candles[i + 1]["opening_price"]
        if entry_price <= 0:
            continue

        future = candles[i + 2:]
        if not future:
            break

        result = simulate_trade(entry_price, future)
        result["coin"]  = coin
        result["setup"] = setup
        result["chg5m"] = round(chg5m, 2)
        result["ts"]    = candles[i]["candle_date_time_kst"]
        trades.append(result)

        skip_until = i + result["hold"] + 2

    return trades


def print_report(all_trades: list[dict], days: int, coins: list[str]) -> None:
    """전체 트레이드 결과 리포트 출력."""
    print()
    print("=" * 62)
    print("CS봇 전략 백테스트  (Claude 없이 코드 조건만 적용)"  )
    print(f"기간: {days}일   코인: {len(coins)}개   "
          f"파라미터: TP+{FIXED_TP*100:.0f}%  SL{HARD_SL*100:.0f}%  BE+{BE_TRIGGER*100:.0f}%")
    print("=" * 62)

    if not all_trades:
        print("트레이드 없음 - 진입 조건 충족 시그널 없음")
        return

    total    = len(all_trades)
    wins     = [t for t in all_trades if t["pnl"] > 0]
    pnls     = [t["pnl"] for t in all_trades]
    win_rate = len(wins) / total * 100
    ev       = statistics.mean(pnls)

    print(f"총 거래수: {total}건   승수: {len(wins)}건   승률: {win_rate:.1f}%")
    print(f"EV(기대값): {ev * 100:+.2f}%  /  "
          f"평균 보유: {statistics.mean(t['hold'] for t in all_trades):.0f}분")
    if total >= 2:
        print(f"표준편차: {statistics.stdev(pnls) * 100:.2f}%")

    # 청산 이유별 분석
    print("\n청산 이유:")
    for reason in ["TP", "BE", "SL", "TIMEOUT", "END"]:
        group = [t for t in all_trades if t["reason"] == reason]
        if not group:
            continue
        cnt = len(group)
        avg = statistics.mean(t["pnl"] for t in group)
        pct = cnt / total * 100
        print(f"  {reason:8s}: {cnt:3d}건 ({pct:4.0f}%)   평균PnL {avg * 100:+.2f}%")

    # 셋업 타입별
    print("\n셋업 타입:")
    for setup in ["A+", "regular"]:
        group = [t for t in all_trades if t["setup"] == setup]
        if not group:
            continue
        wr  = len([t for t in group if t["pnl"] > 0]) / len(group) * 100
        avg = statistics.mean(t["pnl"] for t in group)
        print(f"  {setup:8s}: {len(group):3d}건   승률 {wr:.0f}%   평균PnL {avg * 100:+.2f}%")

    # 코인별 (상위 5)
    print("\n코인별 상위 5:")
    coin_groups: dict[str, list] = {}
    for t in all_trades:
        coin_groups.setdefault(t["coin"], []).append(t)
    for coin, group in sorted(coin_groups.items(),
                               key=lambda x: len(x[1]), reverse=True)[:5]:
        wr  = len([t for t in group if t["pnl"] > 0]) / len(group) * 100
        avg = statistics.mean(t["pnl"] for t in group)
        print(f"  {coin:6s}: {len(group):3d}건   승률 {wr:.0f}%   평균PnL {avg * 100:+.2f}%")

    print("=" * 62)
    if total < 30:
        print(f"⚠ 표본 부족 ({total}건 < 30건) - 수치 신뢰도 낮음")
    print("※ Claude 판단 없음 - 실제 CS봇은 이보다 진입 수 적고 승률 다를 수 있음")


def main() -> None:
    parser = argparse.ArgumentParser(description="CS봇 전략 백테스트 (코드 조건만)")
    parser.add_argument("--days",  type=int, default=7,
                        help="분석 기간 (일, 기본 7)")
    parser.add_argument("--coins", nargs="+", default=DEFAULT_COINS,
                        help="테스트 코인 리스트 (기본 10개)")
    args = parser.parse_args()

    print(f"CS봇 백테스트 시작 - {args.days}일   {len(args.coins)}개 코인")
    print("-" * 62)

    all_trades: list[dict] = []

    for coin in args.coins:
        market = f"KRW-{coin}"
        print(f"  [{coin}] 수집 중...", end=" ", flush=True)
        candles = fetch_candles(market, days=args.days)
        print(f"{len(candles):,}캔들", end="  →  ", flush=True)

        if len(candles) < WINDOW_SIZE + 2:
            print("데이터 부족, 스킵")
            continue

        trades = run_coin(coin, candles)
        all_trades.extend(trades)
        print(f"시그널 {len(trades)}건")

    print_report(all_trades, args.days, args.coins)


if __name__ == "__main__":
    main()
