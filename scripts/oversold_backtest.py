"""
OVERSOLD Bounce 전략 백테스트

RSI 과매도 후 반등 진입 시뮬레이션.
alt_monitor OVERSOLD 전략 검증용.

실행: python scripts/oversold_backtest.py [--days N] [--rsi-watch X] [--rsi-entry Y]
"""

import sys
import time
import argparse
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import requests

# ── 전략 파라미터 ────────────────────────────────────────────────────────────
RSI_WATCH_DEFAULT  = 25    # 이 이하로 내려가면 watching 시작 (현행: 25)
RSI_ENTRY_DEFAULT  = 30    # 이 이상으로 2캔들 연속 회복 시 진입 (현행: 30)
RSI_PERIOD         = 14    # RSI 계산 기간

PUMP_MIN_24H       = 10.0  # 진입 대상 코인 최소 24h 상승률

BE_TRIGGER         = 0.01   # BE 발동 (+1%)
FIXED_TP           = 0.03   # 1차 익절 (+3%)
HARD_SL            = -0.02  # 손절 (-2%)
ROUND_TRIP_FEE     = 0.005  # 왕복 수수료
TIMEOUT_MIN        = 60

DEFAULT_COINS = [
    "H", "WNCG", "ID", "WLD", "DRIFT",
    "HBAR", "XLM", "TRX", "ONDO", "SUI",
    "IO", "VIRTUAL", "ZTX", "ALGO", "STRAX",
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


def calc_rsi(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)

    rsis = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period, len(closes)):
        if i > period:
            d = closes[i] - closes[i - 1]
            avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        rsis.append(round(rsi, 2))

    return [r if r is not None else 50.0 for r in rsis]


def simulate_trade(entry_price: float, future: list[dict]) -> dict:
    be_active = False
    highest = entry_price

    for i, c in enumerate(future[:TIMEOUT_MIN]):
        high = c["high_price"]
        low  = c["low_price"]
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


def run_coin(coin: str, candles: list[dict],
             rsi_watch: float, rsi_entry: float) -> list[dict]:
    closes = [c["trade_price"] for c in candles]
    rsis   = calc_rsi(closes, RSI_PERIOD)

    trades: list[dict] = []
    n = len(candles)
    skip_until = 0
    watching = False

    for i in range(RSI_PERIOD + 2, n - 1):
        if i < skip_until:
            watching = False
            continue

        rsi_cur  = rsis[i]
        rsi_prev = rsis[i - 1]

        # watching 진입 조건: RSI가 watch 임계값 이하
        if rsi_cur <= rsi_watch:
            watching = True

        if not watching:
            continue

        # 진입 조건: RSI가 2캔들 연속 entry 임계값 이상 회복
        if rsi_cur >= rsi_entry and rsi_prev >= rsi_entry:
            entry_price = candles[i + 1]["opening_price"]
            if entry_price <= 0:
                watching = False
                continue

            future = candles[i + 2:]
            if not future:
                break

            result = simulate_trade(entry_price, future)
            result["coin"]     = coin
            result["rsi_min"]  = round(min(rsis[max(0, i-20):i+1]), 1)
            result["rsi_entry"] = round(rsi_cur, 1)
            result["ts"]       = candles[i]["candle_date_time_kst"]
            trades.append(result)

            skip_until = i + result["hold"] + 2
            watching   = False

    return trades


def print_report(all_trades: list[dict], days: int, coins: list[str],
                 rsi_watch: float, rsi_entry: float) -> None:
    print()
    print("=" * 65)
    print("OVERSOLD Bounce 백테스트")
    print(f"기간: {days}일  코인: {len(coins)}개  "
          f"RSI watch:<{rsi_watch}  entry:>{rsi_entry}(2캔들)")
    print(f"TP+{FIXED_TP*100:.0f}%  SL{HARD_SL*100:.0f}%  "
          f"BE+{BE_TRIGGER*100:.0f}%  수수료{ROUND_TRIP_FEE*100:.1f}%")
    print("=" * 65)

    if not all_trades:
        print("시그널 없음")
        return

    n    = len(all_trades)
    wins = [t for t in all_trades if t["pnl"] > 0]
    pnls = [t["pnl"] for t in all_trades]
    wr   = len(wins) / n * 100
    ev   = statistics.mean(pnls)

    print(f"총 거래: {n}건   승수: {len(wins)}건   승률: {wr:.1f}%")
    print(f"EV: {ev*100:+.2f}%  평균보유: {statistics.mean(t['hold'] for t in all_trades):.0f}분")

    print("\n청산 이유:")
    for reason in ["TP", "BE", "SL", "TIMEOUT", "END"]:
        group = [t for t in all_trades if t["reason"] == reason]
        if not group: continue
        avg = statistics.mean(t["pnl"] for t in group)
        print(f"  {reason:8s}: {len(group):3d}건 ({len(group)/n*100:.0f}%)  "
              f"평균PnL {avg*100:+.2f}%")

    # RSI 최저점별 승률
    print("\nRSI 최저점별 승률:")
    buckets = defaultdict(lambda: {"tp": 0, "n": 0})
    for t in all_trades:
        m = t["rsi_min"]
        if m < 15:   b = "<15"
        elif m < 20: b = "15~20"
        elif m < 25: b = "20~25"
        elif m < 30: b = "25~30"
        else:        b = "30+"
        buckets[b]["n"] += 1
        if t["pnl"] > 0: buckets[b]["tp"] += 1
    for b in ["<15", "15~20", "20~25", "25~30", "30+"]:
        d = buckets[b]
        if d["n"] < 2: continue
        print(f"  RSI {b:6s}: {d['n']:3d}건  승률{d['tp']/d['n']*100:.0f}%")

    print("\n코인별:")
    coin_g: dict = defaultdict(list)
    for t in all_trades:
        coin_g[t["coin"]].append(t)
    for coin, group in sorted(coin_g.items(),
                               key=lambda x: len(x[1]), reverse=True)[:8]:
        wr_c = len([t for t in group if t["pnl"] > 0]) / len(group) * 100
        avg  = statistics.mean(t["pnl"] for t in group)
        print(f"  {coin:8s}: {len(group):3d}건  승률{wr_c:.0f}%  EV{avg*100:+.2f}%")

    print("=" * 65)
    if n < 30:
        print(f"표본 부족 ({n}건)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",      type=int,   default=7)
    parser.add_argument("--rsi-watch", type=float, default=RSI_WATCH_DEFAULT)
    parser.add_argument("--rsi-entry", type=float, default=RSI_ENTRY_DEFAULT)
    parser.add_argument("--coins",     nargs="+",  default=DEFAULT_COINS)
    args = parser.parse_args()

    rsi_watch = args.rsi_watch
    rsi_entry = args.rsi_entry

    print(f"OVERSOLD 백테스트 - {args.days}일  RSI watch:<{rsi_watch}  entry:>{rsi_entry}")
    print("-" * 65)

    all_trades: list[dict] = []

    for coin in args.coins:
        market = f"KRW-{coin}"
        print(f"  [{coin}] 수집 중...", end=" ", flush=True)
        candles = fetch_candles(market, days=args.days)
        print(f"{len(candles):,}캔들", end="  ->  ", flush=True)

        if len(candles) < RSI_PERIOD + 10:
            print("데이터 부족")
            continue

        trades = run_coin(coin, candles, rsi_watch, rsi_entry)
        all_trades.extend(trades)
        print(f"시그널 {len(trades)}건")

    print_report(all_trades, args.days, args.coins, rsi_watch, rsi_entry)


if __name__ == "__main__":
    main()
