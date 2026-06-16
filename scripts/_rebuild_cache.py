"""[유틸] 오늘자 백테스트 유니버스 캐시 재구축.
live RT 화이트리스트와 동일 기준(거래대금 상위 50, 20억+, 스테이블·대형주 제외)으로
각 코인의 5m/90d 캔들을 fetch_5m로 받아 오늘 날짜 캐시에 채운다. BTC도 포함(장세필터용).
실거래 무관 — 데이터 수집만."""
import sys, urllib.request, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.alt_entry_backtest import fetch_5m, BARS_PER_DAY

STABLE = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD"}
MAJORS = {"BTC", "ETH", "XRP", "SOL", "ADA", "DOGE"}
MIN_VOL = 2_000_000_000
TOPN = 50


def universe() -> list[str]:
    r = urllib.request.urlopen("https://api.bithumb.com/public/ticker/ALL_KRW", timeout=10)
    data = json.load(r)["data"]
    rows = []
    for coin, d in data.items():
        if coin == "date" or coin in STABLE or coin in MAJORS:
            continue
        try:
            vol = float(d.get("acc_trade_value_24H", 0))
        except Exception:
            continue
        if vol >= MIN_VOL:
            rows.append((coin, vol))
    rows.sort(key=lambda x: -x[1])
    return [c for c, _ in rows[:TOPN]]


if __name__ == "__main__":
    coins = universe()
    print(f"유니버스 {len(coins)}개 (상위 {TOPN}, 20억+, 스테이블·대형주 제외)", flush=True)
    coins = ["BTC"] + coins  # BTC는 장세필터용
    ok = 0
    for i, c in enumerate(coins, 1):
        try:
            d = fetch_5m(c)
            n = len(d)
            tag = "OK" if n >= BARS_PER_DAY * 5 else "짧음"
            if n >= BARS_PER_DAY * 5:
                ok += 1
            print(f"[{i:2}/{len(coins)}] {c:8} {n:6}봉 {tag}", flush=True)
        except Exception as e:
            print(f"[{i:2}/{len(coins)}] {c:8} 실패 {e}", flush=True)
    print(f"\n완료: {ok}/{len(coins)}개 유효 캐시", flush=True)
