"""
Oversold Bounce 전략 백테스트

펌핑 후 고점 대비 -3~7% 눌림 발생 시 진입, 반등 기대.
모멘텀 추종(cs_backtest.py)과 승률/EV 비교용.

실행: python scripts/bounce_backtest.py [--days N] [--pullback X] [--coins XRP ...]
"""

import sys
import time
import argparse
import statistics
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ── 전략 파라미터 ────────────────────────────────────────────────────────────
PUMP_CONFIRM_PCT  = 5.0    # 진입 전 최소 펌프 크기 (고점이 구간 시작가 대비 +5% 이상)
PULLBACK_PCT      = 0.04   # 고점 대비 눌림 기준 (-4%, 조정 가능)
PUMP_WINDOW       = 30     # 펌프 감지 구간 (캔들 수 = 분)

BE_TRIGGER        = 0.01   # BE 발동 (+1%)
FIXED_TP          = 0.03   # 익절 (+3%)
HARD_SL           = -0.02  # 손절 (-2%)
ROUND_TRIP_FEE    = 0.005  # 왕복 수수료 0.5%
TIMEOUT_MIN       = 60     # 최대 보유 시간 (분)

DEFAULT_COINS = [
    "H", "WNCG", "ID", "WLD", "XLM",
    "HBAR", "DOGE", "TRX", "ONDO", "SUI",
    "DRIFT", "IO", "VIRTUAL", "ZTX", "ALGO",
]

BASE_URL = "https://api.bithumb.com"


def fetch_candles(market: str, days: int = 7) -> list[dict]:
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
            print(f"  API 오류: {e}")
            break

        if not data or not isinstance(data, list):
            break

        all_candles.extend(data)
        oldest_dt = datetime.fromisoformat(data[-1]["candle_date_time_utc"])

        if oldest_dt <= cutoff:
            break

        to_dt = oldest_dt - timedelta(seconds=1)
        time.sleep(0.12)

    all_candles = [
        c for c in all_candles
        if datetime.fromisoformat(c["candle_date_time_utc"]) >= cutoff
    ]
    all_candles.sort(key=lambda c: c["candle_date_time_utc"])
    return all_candles


def simulate_trade(entry_price: float, future: list[dict]) -> dict:
    be_active = False
    highest = entry_price

    for i, c in enumerate(future[:TIMEOUT_MIN]):
        high  = c["high_price"]
        low   = c["low_price"]

        highest = max(highest, high)
        if highest >= entry_price * (1 + BE_TRIGGER):
            be_active = True

        if high >= entry_price * (1 + FIXED_TP):
            return {"reason": "TP", "pnl": FIXED_TP - ROUND_TRIP_FEE, "hold": i + 1}

        if be_active and low <= entry_price:
            return {"reason": "BE", "pnl": -ROUND_TRIP_FEE, "hold": i + 1}

        if low <= entry_price * (1 + HARD_SL):
            return {"reason": "SL", "pnl": HARD_SL - ROUND_TRIP_FEE, "hold": i + 1}

    idx = min(TIMEOUT_MIN - 1, len(future) - 1)
    if idx < 0:
        return {"reason": "END", "pnl": -ROUND_TRIP_FEE, "hold": 0}
    last = future[idx]["trade_price"]
    pnl = (last - entry_price) / entry_price - ROUND_TRIP_FEE
    return {"reason": "TIMEOUT", "pnl": pnl, "hold": idx + 1}


def run_coin(coin: str, candles: list[dict], pullback_pct: float) -> list[dict]:
    trades: list[dict] = []
    n = len(candles)
    skip_until = 0

    for i in range(PUMP_WINDOW, n - 1):
        if i < skip_until:
            continue

        # 구간 시작가 대비 고점 계산 (펌프 확인)
        window = candles[i - PUMP_WINDOW:i]
        prices = [c["trade_price"] for c in window]
        start_price = prices[0]
        peak_price  = max(prices)
        current     = prices[-1]

        # 펌프 조건: 고점이 구간 시작가 대비 +PUMP_CONFIRM_PCT 이상
        pump_pct = (peak_price - start_price) / start_price * 100
        if pump_pct < PUMP_CONFIRM_PCT:
            continue

        # 눌림 조건: 현재가가 고점 대비 -pullback_pct 이하
        pullback = (current - peak_price) / peak_price
        if pullback > -pullback_pct:
            continue

        # 다음 캔들 시가로 진입
        entry_price = candles[i + 1]["opening_price"]
        if entry_price <= 0:
            continue

        future = candles[i + 2:]
        if not future:
            break

        result = simulate_trade(entry_price, future)
        result["coin"]     = coin
        result["pump_pct"] = round(pump_pct, 1)
        result["pullback"] = round(pullback * 100, 1)
        result["ts"]       = candles[i]["candle_date_time_kst"]
        trades.append(result)

        skip_until = i + result["hold"] + 2

    return trades


def print_report(all_trades: list[dict], days: int, coins: list[str],
                 pullback_pct: float) -> None:
    print()
    print("=" * 65)
    print("Oversold Bounce 백테스트")
    print(f"기간: {days}일  코인: {len(coins)}개  "
          f"눌림기준: -{pullback_pct*100:.0f}%  펌프조건: +{PUMP_CONFIRM_PCT:.0f}%")
    print(f"TP+{FIXED_TP*100:.0f}%  SL{HARD_SL*100:.0f}%  BE+{BE_TRIGGER*100:.0f}%  수수료{ROUND_TRIP_FEE*100:.1f}%")
    print("=" * 65)

    if not all_trades:
        print("시그널 없음")
        return

    n     = len(all_trades)
    wins  = [t for t in all_trades if t["pnl"] > 0]
    pnls  = [t["pnl"] for t in all_trades]
    wr    = len(wins) / n * 100
    ev    = statistics.mean(pnls)

    print(f"총 거래: {n}건   승수: {len(wins)}건   승률: {wr:.1f}%")
    print(f"EV: {ev*100:+.2f}%  평균보유: {statistics.mean(t['hold'] for t in all_trades):.0f}분")

    print("\n청산 이유:")
    for reason in ["TP", "BE", "SL", "TIMEOUT", "END"]:
        group = [t for t in all_trades if t["reason"] == reason]
        if not group: continue
        avg = statistics.mean(t["pnl"] for t in group)
        print(f"  {reason:8s}: {len(group):3d}건 ({len(group)/n*100:.0f}%)  평균PnL {avg*100:+.2f}%")

    print("\n코인별 상위 5:")
    from collections import defaultdict
    coin_g: dict = defaultdict(list)
    for t in all_trades:
        coin_g[t["coin"]].append(t)
    for coin, group in sorted(coin_g.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
        wr_c = len([t for t in group if t["pnl"] > 0]) / len(group) * 100
        avg  = statistics.mean(t["pnl"] for t in group)
        print(f"  {coin:8s}: {len(group):3d}건  승률{wr_c:.0f}%  EV{avg*100:+.2f}%")

    print("=" * 65)
    if n < 30:
        print(f"표본 부족 ({n}건) — 수치 신뢰도 낮음")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",     type=int,   default=7)
    parser.add_argument("--pullback", type=float, default=PULLBACK_PCT,
                        help="눌림 기준 (0.03=3%, 0.05=5%)")
    parser.add_argument("--coins",    nargs="+",  default=DEFAULT_COINS)
    args = parser.parse_args()

    print(f"Bounce 백테스트 시작 - {args.days}일  눌림:{args.pullback*100:.0f}%  "
          f"{len(args.coins)}개 코인")
    print("-" * 65)

    all_trades: list[dict] = []

    for coin in args.coins:
        market = f"KRW-{coin}"
        print(f"  [{coin}] 수집 중...", end=" ", flush=True)
        candles = fetch_candles(market, days=args.days)
        print(f"{len(candles):,}캔들", end="  ->  ", flush=True)

        if len(candles) < PUMP_WINDOW + 2:
            print("데이터 부족, 스킵")
            continue

        trades = run_coin(coin, candles, args.pullback)
        all_trades.extend(trades)
        print(f"시그널 {len(trades)}건")

    print_report(all_trades, args.days, args.coins, args.pullback)


if __name__ == "__main__":
    main()
