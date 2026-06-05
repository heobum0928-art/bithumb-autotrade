"""
트레일링 스탑 파라미터 최적화 백테스트

Watch Mode 전략 (거래량 급증 + 가격 상승 진입) 에서
트레일링 폭별 EV 비교.

실행: python scripts/trail_backtest.py [--days N] [--coins XRP ...]
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
VOL_MULT_MIN   = 1.5   # 거래량 배수 진입 조건
CHG1M_MIN      = 0.3   # 1분 변화율 진입 조건 (%)
BE_TRIGGER     = 0.01  # BE 발동 (+1%)
HARD_SL        = -0.02 # 손절 (-2%)
ROUND_TRIP_FEE = 0.005 # 왕복 수수료
TIMEOUT_MIN    = 60    # 최대 보유 (분)

TRAIL_OPTIONS  = [0.010, 0.015, 0.020, 0.025, 0.030]  # 테스트할 트레일 폭

DEFAULT_COINS = [
    "H", "WNCG", "WLD", "XLM", "ONDO",
    "ALLO", "HOME", "GNO", "ID", "SUI",
    "DRIFT", "IO", "TRX", "HBAR", "ALGO",
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


def check_entry(candles: list[dict], i: int) -> bool:
    """Watch Mode 진입 조건: 거래량 급증 + 1분 변화율"""
    if i < 4:
        return False
    cur  = candles[i]
    prev = candles[i - 1]

    # 1분 변화율
    chg1m = (cur["trade_price"] - prev["trade_price"]) / prev["trade_price"] * 100
    if chg1m < CHG1M_MIN:
        return False

    # 거래량 배수 (직전 3분 평균 대비)
    avg_vol = sum(candles[i-j]["candle_acc_trade_volume"] for j in range(1, 4)) / 3
    if avg_vol == 0:
        return False
    vol_mult = cur["candle_acc_trade_volume"] / avg_vol
    if vol_mult < VOL_MULT_MIN:
        return False

    return True


def simulate_trade(entry_price: float, future: list[dict], trail_pct: float) -> dict:
    """트레일링 스탑으로 청산 시뮬레이션."""
    be_active = False
    highest   = entry_price

    for i, c in enumerate(future[:TIMEOUT_MIN]):
        high = c["high_price"]
        low  = c["low_price"]
        highest = max(highest, high)

        if highest >= entry_price * (1 + BE_TRIGGER):
            be_active = True

        # 트레일링 스탑 계산
        if be_active:
            sl = highest * (1 - trail_pct)
        else:
            sl = entry_price * (1 + HARD_SL)

        if low <= sl:
            exit_price = sl
            pnl = (exit_price - entry_price) / entry_price - ROUND_TRIP_FEE
            if not be_active:
                reason = "SL"
            elif highest > entry_price * 1.03:
                reason = "TRAIL"
            else:
                reason = "BE"
            return {"reason": reason, "pnl": pnl, "hold": i + 1,
                    "max_pnl": (highest - entry_price) / entry_price * 100}

    # 타임아웃
    idx = min(TIMEOUT_MIN - 1, len(future) - 1)
    if idx < 0:
        return {"reason": "END", "pnl": -ROUND_TRIP_FEE, "hold": 0, "max_pnl": 0}
    last = future[idx]["trade_price"]
    pnl = (last - entry_price) / entry_price - ROUND_TRIP_FEE
    return {"reason": "TIMEOUT", "pnl": pnl, "hold": idx + 1,
            "max_pnl": (highest - entry_price) / entry_price * 100}


def run_coin(coin: str, candles: list[dict]) -> list[dict]:
    """한 코인에서 진입 시그널 수집."""
    signals = []
    n = len(candles)
    skip_until = 0

    for i in range(5, n - 1):
        if i < skip_until:
            continue
        if not check_entry(candles, i):
            continue

        entry_price = candles[i + 1]["opening_price"]
        if entry_price <= 0:
            continue

        future = candles[i + 2:]
        if not future:
            break

        signals.append({
            "coin": coin,
            "entry_price": entry_price,
            "future": future,
            "ts": candles[i]["candle_date_time_kst"],
        })
        skip_until = i + TIMEOUT_MIN + 2

    return signals


def print_report(results: dict, days: int) -> None:
    print()
    print("=" * 65)
    print(f"트레일링 스탑 파라미터 최적화  ({days}일 데이터)")
    print(f"진입조건: 거래량{VOL_MULT_MIN}x+  1분변화율{CHG1M_MIN}%+  BE+{BE_TRIGGER*100:.0f}%  SL{HARD_SL*100:.0f}%")
    print("=" * 65)

    best_trail = None
    best_ev    = -999

    for trail, trades in sorted(results.items()):
        if not trades:
            continue
        n    = len(trades)
        wins = [t for t in trades if t["pnl"] > 0]
        pnls = [t["pnl"] for t in trades]
        ev   = statistics.mean(pnls)
        wr   = len(wins) / n * 100

        trail_cnt  = sum(1 for t in trades if t["reason"] == "TRAIL")
        be_cnt     = sum(1 for t in trades if t["reason"] == "BE")
        sl_cnt     = sum(1 for t in trades if t["reason"] == "SL")
        to_cnt     = sum(1 for t in trades if t["reason"] == "TIMEOUT")
        avg_max    = statistics.mean(t["max_pnl"] for t in trades)

        marker = " <-- 최적" if ev > best_ev else ""
        print(f"트레일 -{trail*100:.1f}%  |  {n}건  승률{wr:.0f}%  "
              f"EV{ev*100:+.2f}%  평균최고{avg_max:.1f}%")
        print(f"         TRAIL{trail_cnt} BE{be_cnt} SL{sl_cnt} TO{to_cnt}{marker}")

        if ev > best_ev:
            best_ev    = ev
            best_trail = trail

    print("=" * 65)
    print(f"최적 트레일 폭: -{best_trail*100:.1f}%  (EV {best_ev*100:+.2f}%)")
    if best_ev > 0:
        print("EV 플러스 — 수익 구조 확인!")
    else:
        print(f"EV 마이너스 — 수익 위해 승률 개선 필요")
        bep_wr = (ROUND_TRIP_FEE + abs(HARD_SL)) / (best_trail + abs(HARD_SL)) * 100 if best_trail else 0
        print(f"손익분기 승률: {bep_wr:.0f}% (현재 대비 개선 필요)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",  type=int, default=7)
    parser.add_argument("--coins", nargs="+", default=DEFAULT_COINS)
    args = parser.parse_args()

    print(f"트레일링 백테스트 시작 - {args.days}일  {len(args.coins)}개 코인")
    print(f"테스트 트레일 폭: {[f'-{t*100:.0f}%' for t in TRAIL_OPTIONS]}")
    print("-" * 65)

    all_signals: list[dict] = []

    for coin in args.coins:
        market = f"KRW-{coin}"
        print(f"  [{coin}] 수집 중...", end=" ", flush=True)
        candles = fetch_candles(market, days=args.days)
        print(f"{len(candles):,}캔들", end="  ->  ", flush=True)

        if len(candles) < 10:
            print("데이터 부족")
            continue

        signals = run_coin(coin, candles)
        all_signals.extend(signals)
        print(f"시그널 {len(signals)}건")

    print(f"\n총 시그널: {len(all_signals)}건 — 파라미터별 시뮬레이션 중...")

    # 각 트레일 파라미터로 시뮬레이션
    results = {trail: [] for trail in TRAIL_OPTIONS}
    for sig in all_signals:
        for trail in TRAIL_OPTIONS:
            result = simulate_trade(sig["entry_price"], sig["future"], trail)
            result["coin"] = sig["coin"]
            results[trail].append(result)

    print_report(results, args.days)


if __name__ == "__main__":
    main()
