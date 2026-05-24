"""특정 코인의 현재가 + 기술지표 실시간 확인."""
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from datetime import datetime
from bithumb.client import BithumbClient
from bithumb.indicators import calc_rsi, calc_bb_pct, calc_macd_bull

RSI_MIN, RSI_MAX = 45, 90
BB_LIMIT          = 1.38
VOL_LIMIT_MULT    = 15.0

coin = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
market = f"KRW-{coin}"

c = BithumbClient()

def ok(flag): return "✓" if flag else "✗"

try:
    t = c.get_ticker(coin)
    price  = float(t["closing_price"])
    high   = float(t["max_price"])
    low    = float(t["min_price"])
    rate   = float(t["fluctate_rate_24H"])
    vol    = float(t.get("acc_trade_value_24H", 0))

    print(f"\n{'='*42}")
    print(f"  [{coin}]  {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*42}")
    print(f"  현재가: {price:>15,.3f} 원")
    print(f"  24H:    고 {high:,.3f}  저 {low:,.3f}  변화 {rate:+.2f}%")
    print(f"  거래대금: {vol/1e8:>8.1f} 억원")
    print(f"{'─'*42}")

    candles = c.get_candles(market, unit=1, count=35)
    rsi  = calc_rsi(candles)
    bb   = calc_bb_pct(candles)
    macd = calc_macd_bull(candles)

    rsi_ok  = rsi is not None and RSI_MIN <= rsi <= RSI_MAX
    bb_ok   = bb is None or bb <= BB_LIMIT
    macd_ok = macd is None or macd is True

    print(f"  RSI(14):  {rsi:>6.1f}   {ok(rsi_ok)}  (범위 {RSI_MIN}~{RSI_MAX})" if rsi else "  RSI(14):  없음")
    print(f"  BB%B:     {bb:>6.3f}   {ok(bb_ok)}  (≤ {BB_LIMIT})" if bb is not None else "  BB%B:     없음")
    if macd is not None:
        macd_txt = "상승" if macd else "하락"
        print(f"  MACD:     {macd_txt:>6s}   {ok(macd_ok)}")
    else:
        print(f"  MACD:     없음   {ok(macd_ok)}")

    print(f"{'─'*42}")
    all_pass = rsi_ok and bb_ok and macd_ok
    result = "▶ 기술지표 PASS — 다른 필터 통과 시 진입 가능" if all_pass else "▶ 기술지표 BLOCK"
    print(f"  {result}")
    if not rsi_ok and rsi:
        print(f"    → RSI {rsi:.1f} 범위 외 ({RSI_MIN}~{RSI_MAX})")
    if not bb_ok and bb:
        print(f"    → BB%B {bb:.3f} > {BB_LIMIT} 과열")
    if not macd_ok:
        print(f"    → MACD 하락 추세")
    print(f"{'='*42}\n")

except Exception as e:
    print(f"조회 실패: {e}")
