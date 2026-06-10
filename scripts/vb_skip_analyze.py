"""
VB 진입 차단(skip) 반사실 분석 — "안 들어간 게 잘한 건가?"를 자동 채점.

vb_skip_log의 차단 기록마다 차단 시점 이후 5분봉을 받아
VB 청산 룰(SL -2%, 트레일 활성 +5% 고점기준, 트레일 -3%)을 그대로 시뮬레이션한다.

Run: python -X utf8 scripts/vb_skip_analyze.py [--days 7]
"""
import sys
import argparse
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.db import _conn  # noqa: E402

# vb_trader.py 전략 상수와 동일하게 유지할 것
VB_SL             = -0.02
VB_TRAIL_ACTIVATE = 0.05
VB_TRAIL_PCT      = 0.03
ENTRY_KRW         = 400_000

CANDLE_URL = "https://api.bithumb.com/v1/candles/minutes/5"


def fetch_candles(coin: str, since: datetime) -> list[dict]:
    """차단 시각 이후 5분봉 (오래된 순). 최대 200개 = 약 16.6시간."""
    r = requests.get(CANDLE_URL, params={"market": f"KRW-{coin}", "count": 200}, timeout=10)
    r.raise_for_status()
    candles = [c for c in r.json()
               if datetime.fromisoformat(c["candle_date_time_kst"]) >= since]
    candles.sort(key=lambda c: c["candle_date_time_kst"])
    return candles


def simulate(entry: float, candles: list[dict]) -> tuple[str, float, float]:
    """VB 룰 시뮬. return (결과, 청산/현재 수익률%, 고점%)"""
    highest = entry
    for c in candles:
        hi, lo = c["high_price"], c["low_price"]
        if hi > highest:
            highest = hi
        trail_stop = highest * (1 - VB_TRAIL_PCT)
        if (highest - entry) / entry >= VB_TRAIL_ACTIVATE and lo <= trail_stop:
            return "트레일링", (trail_stop - entry) / entry * 100, (highest - entry) / entry * 100
        if (lo - entry) / entry <= VB_SL:
            return "SL", VB_SL * 100, (highest - entry) / entry * 100
    cur = candles[-1]["trade_price"] if candles else entry
    return "보유중", (cur - entry) / entry * 100, (highest - entry) / entry * 100


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="최근 N일 차단 기록 분석")
    args = ap.parse_args()

    with _conn() as con:
        rows = con.execute(
            "SELECT date, skipped_at, coin, skip_reason, price, vb_target, btc_chg24h "
            "FROM vb_skip_log "
            "WHERE date >= date('now', ?) ORDER BY skipped_at",
            (f"-{args.days} days",),
        ).fetchall()

    if not rows:
        print("차단 기록 없음")
        return

    print(f"=== VB 차단 반사실 분석 ({len(rows)}건, 최근 {args.days}일) ===\n")
    print(f"{'날짜':10s} {'시각':5s} {'코인':6s} {'사유':6s} {'차단가':>10s} "
          f"{'결과':6s} {'손익%':>7s} {'고점%':>7s} {'손익원':>9s}")

    totals: dict[str, list[float]] = {}
    for r in rows:
        skipped = datetime.fromisoformat(r["skipped_at"])
        try:
            candles = fetch_candles(r["coin"], skipped)
            if not candles:
                print(f"{r['date']:10s} {skipped:%H:%M} {r['coin']:6s} {r['skip_reason']:6s} "
                      f"{r['price']:>10,.4g} 캔들없음(기록이 너무 오래됨)")
                continue
            outcome, pnl_pct, max_pct = simulate(r["price"], candles)
        except Exception as e:
            print(f"{r['date']:10s} {skipped:%H:%M} {r['coin']:6s} 조회실패: {e}")
            continue
        pnl_krw = pnl_pct / 100 * ENTRY_KRW
        totals.setdefault(r["skip_reason"], []).append(pnl_krw)
        print(f"{r['date']:10s} {skipped:%H:%M} {r['coin']:6s} {r['skip_reason']:6s} "
              f"{r['price']:>10,.4g} {outcome:6s} {pnl_pct:>+6.1f}% {max_pct:>+6.1f}% {pnl_krw:>+9,.0f}")

    print("\n=== 사유별 합산 (개별 진입 가정, 1포지션 제약 미반영) ===")
    for reason, pnls in totals.items():
        wins = sum(1 for p in pnls if p > 0)
        print(f"{reason:8s} {len(pnls):3d}건 | 승 {wins} 패 {len(pnls)-wins} | "
              f"합계 {sum(pnls):+,.0f}원")


if __name__ == "__main__":
    main()
