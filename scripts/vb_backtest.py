"""
VB(변동성 돌파) 백테스트 엔진 — 빗썸 과거 캔들 기반.

vb_trader.py의 룰을 그대로 재현:
  진입: 당일시가 + (전일 고저폭 × K) 상향 돌파, 목표 대비 +3% 초과 추격 금지
  청산: SL / 트레일링(고점 기준 활성화) / 자정(마지막 캔들) 강제청산
  1코인 1포지션 (같은 시각 복수 돌파 시 먼저 돌파한 코인)

5분봉은 data/candles_cache/ 에 캐시 — 재실행 시 API 호출 없음.

Run:
  python -X utf8 scripts/vb_backtest.py --days 30
  python -X utf8 scripts/vb_backtest.py --days 30 --k 0.4 --sl -0.025
"""
import sys
import json
import time
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 기본 파라미터 (vb_trader.py와 동일) ──────────────────────────────────────
K              = 0.5
SL             = -0.02
TRAIL_ACTIVATE = 0.05
TRAIL_PCT      = 0.03
LATE_LIMIT     = 0.03
ENTRY_KRW      = 400_000
FEE_RT         = 0.005          # 왕복 수수료 0.5%
MIN_VOL_KRW    = 2_000_000_000  # 화이트리스트 기준 20억

CACHE_DIR = Path("data/candles_cache")
API = "https://api.bithumb.com/v1"


# ── 데이터 수집 ───────────────────────────────────────────────────────────────
def get_whitelist() -> list[str]:
    r = requests.get("https://api.bithumb.com/public/ticker/ALL_KRW", timeout=10)
    data = r.json()["data"]
    wl = [c for c, d in data.items()
          if c != "date" and float(d.get("acc_trade_value_24H", 0)) >= MIN_VOL_KRW]
    return sorted(wl)


def fetch_5m(coin: str, days: int) -> list[dict]:
    """최근 N일 5분봉 (오래된 순). 디스크 캐시 사용."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{coin}_5m_{days}d_{date.today().isoformat()}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    need_from = datetime.now() - timedelta(days=days + 1)
    out: list[dict] = []
    to: str | None = None
    while True:
        params = {"market": f"KRW-{coin}", "count": 200}
        if to:
            params["to"] = to
        r = requests.get(f"{API}/candles/minutes/5", params=params, timeout=10)
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
        time.sleep(0.05)
    out.sort(key=lambda c: c["candle_date_time_kst"])
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


# ── 시뮬레이션 ────────────────────────────────────────────────────────────────
def build_days(candles: list[dict]) -> dict[str, list[dict]]:
    """KST 일자별 5분봉 묶음."""
    days: dict[str, list[dict]] = {}
    for c in candles:
        days.setdefault(c["candle_date_time_kst"][:10], []).append(c)
    return days


def simulate_coin_day(day_candles: list[dict], prev_range: float, k: float,
                      sl: float, trail_act: float, trail_pct: float,
                      be_stop: float | None = None) -> dict | None:
    """하루치 시뮬. 진입 없으면 None, 있으면 결과 dict."""
    if not day_candles or prev_range <= 0:
        return None
    day_open = day_candles[0]["opening_price"]
    target = day_open + prev_range * k
    if target <= 0:
        return None

    entry = None
    entry_idx = None
    for i, c in enumerate(day_candles):
        if c["high_price"] >= target:
            # 갭 처리: 캔들 시가가 이미 목표 위면 시가 진입, 아니면 목표가 진입
            px = max(target, c["opening_price"])
            if (px - target) / target > LATE_LIMIT:
                continue  # 늦은진입 — 다음 캔들에서 +3% 이내로 돌아오면 진입 가능
            entry, entry_idx = px, i
            break
    if entry is None:
        return None

    # 진입 캔들의 저가는 돌파 "이전" 가격이므로 손절/트레일 판정 제외 (고점만 반영)
    highest = max(entry, day_candles[entry_idx]["high_price"])
    exit_px, reason = None, None
    stop = entry * (1 + sl)   # 현재 스탑 가격 (본전 스탑 적용 시 상향됨)
    for c in day_candles[entry_idx + 1:]:
        hi, lo = c["high_price"], c["low_price"]
        # 본전 스탑: 고점이 +be_stop 도달하면 스탑을 진입가로 이동
        if be_stop is not None and (highest - entry) / entry >= be_stop:
            stop = max(stop, entry)
        # 보수적 순서: 같은 캔들에서 손절 먼저 체크 (불리하게 가정)
        if lo <= stop:
            exit_px = stop
            reason = "본전스탑" if stop >= entry else "SL"
            break
        if hi > highest:
            highest = hi
        trail_stop = highest * (1 - trail_pct)
        if (highest - entry) / entry >= trail_act and lo <= trail_stop:
            exit_px, reason = trail_stop, "트레일링"
            break
    if exit_px is None:
        exit_px, reason = day_candles[-1]["trade_price"], "자정청산"

    pnl_pct = (exit_px - entry) / entry - FEE_RT
    return {
        "entry": entry, "exit": exit_px, "reason": reason,
        "pnl_pct": pnl_pct, "pnl_krw": pnl_pct * ENTRY_KRW,
        "entry_time": day_candles[entry_idx]["candle_date_time_kst"][11:16],
        "max_pct": (highest - entry) / entry,
    }


def run_backtest(coins: list[str], days: int, k: float, sl: float,
                 trail_act: float, trail_pct: float, quiet: bool = False,
                 be_stop: float | None = None) -> dict:
    # 코인별 일자별 캔들 + 일별 고저폭 준비
    coin_days: dict[str, dict[str, list[dict]]] = {}
    for i, coin in enumerate(coins):
        try:
            candles = fetch_5m(coin, days)
        except Exception as e:
            if not quiet:
                print(f"  {coin} 수집 실패: {e}")
            continue
        if candles:
            coin_days[coin] = build_days(candles)
        if not quiet and (i + 1) % 10 == 0:
            print(f"  데이터 준비 {i+1}/{len(coins)}...")

    # 날짜 순회 — 1코인 1포지션: 그날 가장 먼저 돌파한 코인만 진입
    all_dates = sorted({d for cd in coin_days.values() for d in cd})[1:]  # 첫날은 전일 없음
    trades: list[dict] = []
    for d in all_dates:
        prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
        day_trades: list[tuple[str, dict]] = []
        for coin, cd in coin_days.items():
            if d not in cd or prev not in cd:
                continue
            prev_candles = cd[prev]
            prev_high = max(c["high_price"] for c in prev_candles)
            prev_low  = min(c["low_price"] for c in prev_candles)
            res = simulate_coin_day(cd[d], prev_high - prev_low, k, sl, trail_act, trail_pct, be_stop)
            if res:
                day_trades.append((coin, res))
        if not day_trades:
            continue
        # 가장 먼저 돌파한 코인 1개만 (재진입은 단순화를 위해 미반영)
        day_trades.sort(key=lambda x: x[1]["entry_time"])
        coin, res = day_trades[0]
        res["coin"], res["date"] = coin, d
        trades.append(res)

    tot = sum(t["pnl_krw"] for t in trades)
    wins = [t for t in trades if t["pnl_pct"] > 0]
    return {
        "k": k, "sl": sl, "trail_act": trail_act, "trail_pct": trail_pct,
        "n": len(trades), "wins": len(wins),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "total_krw": tot,
        "avg_pct": sum(t["pnl_pct"] for t in trades) / len(trades) * 100 if trades else 0,
        "trades": trades,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--k", type=float, default=K)
    ap.add_argument("--sl", type=float, default=SL)
    ap.add_argument("--trail-act", type=float, default=TRAIL_ACTIVATE)
    ap.add_argument("--trail-pct", type=float, default=TRAIL_PCT)
    ap.add_argument("--detail", action="store_true", help="개별 거래 출력")
    args = ap.parse_args()

    print(f"화이트리스트 조회 중 (20억+)...")
    coins = get_whitelist()
    print(f"{len(coins)}개 코인 | 최근 {args.days}일 | "
          f"K={args.k} SL={args.sl*100:.1f}% 트레일 {args.trail_act*100:.0f}%/{args.trail_pct*100:.0f}% "
          f"| 진입 {ENTRY_KRW:,}원 | 수수료 {FEE_RT*100:.1f}%\n")

    r = run_backtest(coins, args.days, args.k, args.sl, args.trail_act, args.trail_pct)

    if args.detail:
        for t in r["trades"]:
            print(f"{t['date']} {t['entry_time']} {t['coin']:7s} "
                  f"{t['pnl_pct']*100:+5.1f}% ({t['pnl_krw']:+8,.0f}원) "
                  f"고점 {t['max_pct']*100:+5.1f}% | {t['reason']}")
        print()

    by_reason: dict[str, list[float]] = {}
    for t in r["trades"]:
        by_reason.setdefault(t["reason"], []).append(t["pnl_krw"])
    print(f"=== 결과: {r['n']}건 | 승 {r['wins']} 패 {r['n']-r['wins']} "
          f"(승률 {r['win_rate']:.0f}%) | 평균 {r['avg_pct']:+.2f}%/건 ===")
    print(f"=== 합계 {r['total_krw']:+,.0f}원 (진입금 40만 기준, {args.days}일) ===")
    for reason, pnls in sorted(by_reason.items()):
        print(f"  {reason:6s} {len(pnls):3d}건 | 소계 {sum(pnls):+,.0f}원")


if __name__ == "__main__":
    main()
