"""
추세추종(Donchian Channel Breakout) 백테스트 — 빗썸 일봉 기반.

전략 (단순 추세추종, 메이저 코인 한정):
  진입: 종가가 직전 N일 최고가 돌파 시 다음날 시가 매수
  청산: 종가가 직전 M일 최저가 이탈 시 다음날 시가 매도
  (Donchian 20/10이 기본 — Turtle Trading 계열)

지난 VB 백테스트 실패에서 배운 보정:
  1. 비관적 체결: 진입/청산 모두 "다음날 시가"(돌파 본 후 실제 주문 가능 시점)
     + 슬리피지 기본 0.3% (메이저 코인 기준 실측 보수치)
  2. 생존편향 제거: 코인을 BTC/ETH/XRP/SOL/ADA/DOGE 등 "2년 전에도 메이저"였던
     고정 리스트로 한정 — 오늘 거래량으로 선정하지 않음
  3. walk-forward: 전반 50% (train)에서 N/M 선택, 후반 50% (test)에서만 성과 판정
     test 결과만 보고한다.

Run:
  python -X utf8 scripts/trend_backtest.py                # 기본 (train에서 그리드, test 보고)
  python -X utf8 scripts/trend_backtest.py --slip 0.005   # 슬리피지 0.5%로 스트레스
"""
import sys
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 고정 파라미터 ─────────────────────────────────────────────────────────────
# 2024년 이전부터 메이저였던 코인만 — 생존편향 방지를 위해 하드코딩
COINS = ["BTC", "ETH", "XRP", "SOL", "ADA", "DOGE", "TRX", "LINK", "AVAX", "DOT"]

FEE_RT       = 0.005    # 왕복 수수료 0.5% (빗썸 0.25% × 2)
SLIPPAGE     = 0.003    # 기본 슬리피지 왕복 0.3% (메이저 코인 시장가 보수치)
DAYS         = 720      # 2년
ENTRY_KRW    = 400_000

# 그리드 서치 후보 (train 구간에서만 선택)
ENTRY_WINDOWS = [10, 20, 30, 55]
EXIT_WINDOWS  = [5, 10, 20]

CACHE_DIR = Path("data/candles_cache")
API = "https://api.bithumb.com/v1/candles/days"


# ── 데이터 수집 ───────────────────────────────────────────────────────────────
def fetch_daily(coin: str, days: int) -> list[dict]:
    """최근 N일 일봉 (오래된 순). 디스크 캐시."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{coin}_1d_{days}d_{datetime.now().date().isoformat()}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    need_from = datetime.now() - timedelta(days=days + 1)
    out: list[dict] = []
    to: str | None = None
    while True:
        params = {"market": f"KRW-{coin}", "count": 200}
        if to:
            params["to"] = to
        r = requests.get(API, params=params, timeout=10)
        if r.status_code != 200:
            break
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk:
            break
        out.extend(chunk)
        oldest = datetime.fromisoformat(chunk[-1]["candle_date_time_kst"])
        if oldest <= need_from or len(chunk) < 200:
            break
        to = oldest.strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(0.1)
    out.sort(key=lambda c: c["candle_date_time_kst"])
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


# ── 시뮬레이션 ────────────────────────────────────────────────────────────────
def simulate(candles: list[dict], n_entry: int, m_exit: int,
             fee: float, slip: float) -> list[dict]:
    """Donchian N/M 추세추종. 거래 리스트 반환.
    진입/청산 모두 신호 다음날 시가 체결 (lookahead 방지)."""
    trades: list[dict] = []
    pos_entry: float | None = None
    pos_date: str = ""
    for i in range(max(n_entry, m_exit), len(candles) - 1):
        close = candles[i]["trade_price"]
        next_open = candles[i + 1]["opening_price"]
        next_date = candles[i + 1]["candle_date_time_kst"][:10]

        if pos_entry is None:
            hh = max(c["high_price"] for c in candles[i - n_entry:i])
            if close > hh:
                pos_entry = next_open * (1 + slip / 2)  # 진입 슬리피지
                pos_date = next_date
        else:
            ll = min(c["low_price"] for c in candles[i - m_exit:i])
            if close < ll:
                exit_px = next_open * (1 - slip / 2)    # 청산 슬리피지
                pnl_pct = (exit_px - pos_entry) / pos_entry - fee
                trades.append({
                    "entry_date": pos_date, "exit_date": next_date,
                    "entry": pos_entry, "exit": exit_px, "pnl_pct": pnl_pct,
                })
                pos_entry = None
    # 미청산 포지션은 마지막 종가로 평가 청산
    if pos_entry is not None:
        exit_px = candles[-1]["trade_price"] * (1 - slip / 2)
        pnl_pct = (exit_px - pos_entry) / pos_entry - fee
        trades.append({
            "entry_date": pos_date, "exit_date": candles[-1]["candle_date_time_kst"][:10],
            "entry": pos_entry, "exit": exit_px, "pnl_pct": pnl_pct, "open": True,
        })
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": 0, "avg_pct": 0, "total_pct": 0, "mdd_pct": 0}
    wins = [t for t in trades if t["pnl_pct"] > 0]
    # 복리 수익률 + MDD (시간순)
    trades_sorted = sorted(trades, key=lambda t: t["exit_date"])
    equity, peak, mdd = 1.0, 1.0, 0.0
    for t in trades_sorted:
        equity *= 1 + t["pnl_pct"]
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "avg_pct": sum(t["pnl_pct"] for t in trades) / len(trades) * 100,
        "total_pct": (equity - 1) * 100,
        "mdd_pct": mdd * 100,
    }


def run_portfolio(coin_candles: dict[str, list[dict]], n: int, m: int,
                  fee: float, slip: float, lo: int, hi: int) -> list[dict]:
    """코인별 독립 시뮬 (자본 분할 가정), [lo:hi] 캔들 구간만."""
    all_trades = []
    for coin, candles in coin_candles.items():
        seg = candles[lo:hi] if hi > 0 else candles[lo:]
        if len(seg) < max(n, m) + 10:
            continue
        for t in simulate(seg, n, m, fee, slip):
            t["coin"] = coin
            all_trades.append(t)
    return all_trades


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slip", type=float, default=SLIPPAGE)
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args()

    print(f"데이터 수집: {len(COINS)}개 메이저 코인 × {args.days}일 일봉 (고정 리스트 — 생존편향 방지)")
    coin_candles: dict[str, list[dict]] = {}
    for coin in COINS:
        try:
            candles = fetch_daily(coin, args.days)
            if len(candles) >= 100:
                coin_candles[coin] = candles
                print(f"  {coin}: {len(candles)}일 ({candles[0]['candle_date_time_kst'][:10]} ~)")
        except Exception as e:
            print(f"  {coin} 실패: {e}")

    n_days = min(len(c) for c in coin_candles.values())
    split = n_days // 2
    print(f"\nwalk-forward: 전반 {split}일 = train (파라미터 선택), 후반 = test (성과 판정)")
    print(f"비용: 수수료 {FEE_RT*100:.1f}% + 슬리피지 {args.slip*100:.1f}% 왕복\n")

    # ── train: 그리드 서치 ──
    print("=== TRAIN (파라미터 선택용 — 이 수치로 성과 판정 금지) ===")
    best, best_score = None, -1e9
    for n in ENTRY_WINDOWS:
        for m in EXIT_WINDOWS:
            tr = run_portfolio(coin_candles, n, m, FEE_RT, args.slip, 0, split)
            s = summarize(tr)
            score = s["total_pct"]
            marker = ""
            if score > best_score:
                best, best_score = (n, m), score
                marker = " <-"
            print(f"  진입{n:>2}일/청산{m:>2}일: {s['n']:3}건 승률{s['win_rate']:5.1f}% "
                  f"평균{s['avg_pct']:+6.2f}% 누적{s['total_pct']:+8.1f}% MDD{s['mdd_pct']:5.1f}%{marker}")

    n, m = best
    print(f"\ntrain 선택: 진입 {n}일 돌파 / 청산 {m}일 이탈")

    # ── test: out-of-sample 판정 ──
    print(f"\n=== TEST (out-of-sample — 이것이 진짜 성적) ===")
    tr = run_portfolio(coin_candles, n, m, FEE_RT, args.slip, split, 0)
    s = summarize(tr)
    print(f"  {s['n']}건 | 승률 {s['win_rate']:.1f}% | 평균 {s['avg_pct']:+.2f}%/건 "
          f"| 누적 {s['total_pct']:+.1f}% | MDD {s['mdd_pct']:.1f}%")
    krw = ENTRY_KRW * s["total_pct"] / 100
    print(f"  진입금 {ENTRY_KRW:,}원 기준 누적 {krw:+,.0f}원 (코인별 분할 가정)")

    if args.detail:
        print()
        for t in sorted(tr, key=lambda x: x["entry_date"]):
            flag = " [미청산]" if t.get("open") else ""
            print(f"  {t['entry_date']} ~ {t['exit_date']} {t['coin']:5s} "
                  f"{t['pnl_pct']*100:+6.1f}%{flag}")

    # 코인별 분해 (편중 확인)
    by_coin: dict[str, list[float]] = {}
    for t in tr:
        by_coin.setdefault(t["coin"], []).append(t["pnl_pct"])
    print("\n  코인별 (test):")
    for coin, pnls in sorted(by_coin.items(), key=lambda x: -sum(x[1])):
        print(f"    {coin:5s} {len(pnls):2}건 누적 {sum(pnls)*100:+7.1f}%")

    verdict = "GO 후보 — 모의 검증 단계로" if s["total_pct"] > 0 and s["n"] >= 10 else "NO-GO — 전략 폐기"
    print(f"\n판정: {verdict}")


if __name__ == "__main__":
    main()
