"""
스윙 트레이딩 백테스트 — 빗썸 일봉 데이터 기반

실행:
  python scripts/swing_backtest.py               # 전체 코인 전략 비교
  python scripts/swing_backtest.py --coin XLM    # 특정 코인
  python scripts/swing_backtest.py --coin BTC --strategy ma
"""
import sys
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 설정 ─────────────────────────────────────────────────────────────────────

FEE       = 0.0025   # 왕복 편도 0.25%
SLIPPAGE  = 0.001    # 슬리피지 0.1%
COST_RT   = (FEE + SLIPPAGE) * 2  # 왕복 총 비용 ~0.7%

COINS = ["BTC", "XLM", "WLD", "H", "HOME", "ONDO", "HBAR", "SUI"]

# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def fetch_daily(market: str, max_pages: int = 20) -> list[dict]:
    """빗썸 일봉 전체 다운로드 (페이지네이션)."""
    all_data: list[dict] = []
    to_dt = None
    for _ in range(max_pages):
        params: dict = {"market": market, "count": 200}
        if to_dt:
            params["to"] = to_dt
        try:
            resp = requests.get(
                "https://api.bithumb.com/v1/candles/days",
                params=params, timeout=10,
            )
            data = resp.json()
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        all_data.extend(data)
        if len(data) < 200:
            break
        to_dt = data[-1]["candle_date_time_utc"]
        time.sleep(0.08)
    # 오래된 → 최신 순 정렬
    all_data.sort(key=lambda x: x["candle_date_time_utc"])
    return all_data


def to_series(candles: list[dict]) -> tuple[list[float], list[float], list[float], list[str]]:
    closes = [float(c["trade_price"]) for c in candles]
    highs  = [float(c["high_price"]) for c in candles]
    lows   = [float(c["low_price"]) for c in candles]
    dates  = [c["candle_date_time_kst"][:10] for c in candles]
    return closes, highs, lows, dates

# ── 지표 계산 ─────────────────────────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    rsi: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            d = closes[i] - closes[i - 1]
            avg_g = (avg_g * (period - 1) + max(d, 0)) / period
            avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        rsi[i] = 100 - 100 / (1 + avg_g / avg_l) if avg_l else 100.0
    return rsi


def calc_ma(closes: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1: i + 1]) / period
    return result


def calc_bb(closes: list[float], period: int = 20, k: float = 2.0) -> tuple[list, list, list]:
    mid  = calc_ma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        std = (sum((x - mid[i]) ** 2 for x in window) / period) ** 0.5  # type: ignore[arg-type]
        upper[i] = mid[i] + k * std  # type: ignore[operator]
        lower[i] = mid[i] - k * std  # type: ignore[operator]
    return upper, mid, lower

# ── 전략 정의 ─────────────────────────────────────────────────────────────────

def strategy_rsi(closes, dates, rsi_buy=30, rsi_sell=65, rsi_period=14,
                 btc_closes=None, btc_rsi_min=40):
    """RSI 과매도 매수 / 과열 매도. BTC 필터 선택적."""
    rsi = calc_rsi(closes, rsi_period)
    btc_rsi = calc_rsi(btc_closes, rsi_period) if btc_closes else None

    trades, pos = [], None
    for i in range(rsi_period + 1, len(closes)):
        r = rsi[i]
        if r is None:
            continue
        br = btc_rsi[i] if btc_rsi else 999

        if pos is None:
            btc_ok = (btc_rsi is None) or (br is not None and br >= btc_rsi_min)
            if r < rsi_buy and btc_ok:
                pos = {"entry": closes[i], "date": dates[i], "idx": i}
        else:
            if r > rsi_sell:
                pnl = (closes[i] / pos["entry"] - 1) - COST_RT
                trades.append({"entry_date": pos["date"], "exit_date": dates[i],
                                "pnl": pnl, "hold": i - pos["idx"]})
                pos = None
    return trades


def strategy_ma_cross(closes, dates, fast=20, slow=60):
    """골든크로스(fast>slow) 매수 / 데드크로스 매도."""
    ma_f = calc_ma(closes, fast)
    ma_s = calc_ma(closes, slow)

    trades, pos = [], None
    for i in range(slow + 1, len(closes)):
        if ma_f[i] is None or ma_s[i] is None:
            continue
        prev_f, prev_s = ma_f[i - 1], ma_s[i - 1]
        if prev_f is None or prev_s is None:
            continue

        if pos is None:
            if prev_f <= prev_s and ma_f[i] > ma_s[i]:  # 골든크로스
                pos = {"entry": closes[i], "date": dates[i], "idx": i}
        else:
            if prev_f >= prev_s and ma_f[i] < ma_s[i]:  # 데드크로스
                pnl = (closes[i] / pos["entry"] - 1) - COST_RT
                trades.append({"entry_date": pos["date"], "exit_date": dates[i],
                                "pnl": pnl, "hold": i - pos["idx"]})
                pos = None
    return trades


def strategy_bb(closes, dates, period=20, k=2.0):
    """볼린저 밴드 하단 이탈 매수 / 중심선 복귀 매도."""
    upper, mid, lower = calc_bb(closes, period, k)

    trades, pos = [], None
    for i in range(period + 1, len(closes)):
        if lower[i] is None or mid[i] is None:
            continue
        if pos is None:
            if closes[i - 1] > lower[i - 1] and closes[i] <= lower[i]:  # type: ignore
                pos = {"entry": closes[i], "date": dates[i], "idx": i}
        else:
            if closes[i] >= mid[i]:
                pnl = (closes[i] / pos["entry"] - 1) - COST_RT
                trades.append({"entry_date": pos["date"], "exit_date": dates[i],
                                "pnl": pnl, "hold": i - pos["idx"]})
                pos = None
    return trades

# ── 결과 분석 ─────────────────────────────────────────────────────────────────

def analyze(trades: list[dict], capital: float = 1_000_000) -> dict:
    if not trades:
        return {"n": 0, "win_rate": 0, "ev": 0, "total_pnl": 0,
                "avg_hold": 0, "max_dd": 0, "annual_return": 0}
    n     = len(trades)
    wins  = sum(1 for t in trades if t["pnl"] > 0)
    ev    = sum(t["pnl"] for t in trades) / n
    total = sum(t["pnl"] for t in trades)

    # 최대 낙폭
    equity, peak, max_dd = capital, capital, 0.0
    for t in trades:
        equity *= (1 + t["pnl"])
        peak    = max(peak, equity)
        max_dd  = max(max_dd, (peak - equity) / peak)

    # 연수익률 추정 (전체 기간 / 실거래 일수 기준)
    avg_hold  = sum(t["hold"] for t in trades) / n
    total_days = sum(t["hold"] for t in trades)
    years     = total_days / 365 if total_days else 1
    final_eq  = capital * (1 + total)
    annual    = (final_eq / capital) ** (1 / years) - 1 if years > 0 else 0

    return {
        "n": n, "win_rate": wins / n, "ev": ev,
        "total_pnl": total, "avg_hold": avg_hold,
        "max_dd": max_dd, "annual_return": annual,
    }


def print_result(label: str, res: dict, capital: float = 1_000_000) -> None:
    if res["n"] == 0:
        print(f"  {label:30s} 거래 없음")
        return
    krw = res["total_pnl"] * capital
    print(
        f"  {label:35s} "
        f"{res['n']:4d}건  "
        f"승률{res['win_rate']*100:4.0f}%  "
        f"EV{res['ev']*100:+5.2f}%  "
        f"총수익{krw:+,.0f}원  "
        f"연수익{res['annual_return']*100:+5.1f}%  "
        f"MDD{res['max_dd']*100:.0f}%  "
        f"평균{res['avg_hold']:.0f}일"
    )

# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default=None, help="특정 코인만 (예: XLM)")
    ap.add_argument("--strategy", default="all", help="rsi|ma|bb|all")
    ap.add_argument("--capital", type=float, default=1_000_000)
    args = ap.parse_args()

    coins = [args.coin.upper()] if args.coin else COINS

    print("BTC 데이터 로딩...")
    btc_raw = fetch_daily("KRW-BTC")
    btc_closes, _, _, btc_dates = to_series(btc_raw)
    btc_date_map = {d: c for d, c in zip(btc_dates, btc_closes)}
    print(f"  BTC: {len(btc_raw)}일 ({btc_dates[0]} ~ {btc_dates[-1]})\n")

    sep = "=" * 110
    print(sep)
    print(f"{'전략':35s} {'건수':>5} {'승률':>6} {'EV':>7} {'총수익':>12} {'연수익':>8} {'MDD':>6} {'평균보유':>8}")
    print(sep)

    for coin in coins:
        print(f"\n[ {coin} ]")
        raw = fetch_daily(f"KRW-{coin}")
        if len(raw) < 60:
            print(f"  데이터 부족 ({len(raw)}일)")
            continue
        closes, highs, lows, dates = to_series(raw)
        print(f"  데이터: {len(raw)}일 ({dates[0]} ~ {dates[-1]})")

        # BTC closes를 coin 날짜에 맞춰 정렬
        btc_aligned = [btc_date_map.get(d) for d in dates]
        btc_valid   = all(b is not None for b in btc_aligned)
        btc_for_coin = btc_aligned if btc_valid else None  # type: ignore

        if args.strategy in ("rsi", "all"):
            for buy, sell in [(25, 60), (30, 60), (30, 65), (35, 65)]:
                label = f"RSI buy<{buy} sell>{sell}"
                t = strategy_rsi(closes, dates, rsi_buy=buy, rsi_sell=sell)
                print_result(label, analyze(t, args.capital), args.capital)

            if btc_for_coin:
                for buy, sell in [(30, 65)]:
                    label = f"RSI buy<{buy} sell>{sell} + BTC≥40"
                    t = strategy_rsi(closes, dates, rsi_buy=buy, rsi_sell=sell,
                                     btc_closes=btc_for_coin, btc_rsi_min=40)
                    print_result(label, analyze(t, args.capital), args.capital)

        if args.strategy in ("ma", "all"):
            for fast, slow in [(20, 60), (20, 50), (10, 30)]:
                label = f"MA크로스 {fast}/{slow}"
                t = strategy_ma_cross(closes, dates, fast=fast, slow=slow)
                print_result(label, analyze(t, args.capital), args.capital)

        if args.strategy in ("bb", "all"):
            for period, k in [(20, 2.0), (20, 1.5)]:
                label = f"볼린저밴드 {period}일 {k}σ"
                t = strategy_bb(closes, dates, period=period, k=k)
                print_result(label, analyze(t, args.capital), args.capital)

    print(f"\n{sep}")
    print(f"수수료: 편도 {FEE*100:.2f}% + 슬리피지 {SLIPPAGE*100:.1f}% = 왕복 {COST_RT*100:.2f}%")


if __name__ == "__main__":
    main()
