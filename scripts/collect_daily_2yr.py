"""[데이터 수집] 스윙 검증용 2년 일봉 — 매일 자동 갱신(loop).
빗썸 거래대금 상위 + 대형주 포함 유니버스의 일봉 ~750일치를 받아 롤링 캐시 저장.
스윙(시간축↑) 백테스트는 2년 일봉이 필요(90일 5분봉으론 검증 불가 — 대장 #11).
- 저장: data/candles_daily/{coin}_1d.json (날짜무인, 매 실행시 최신화).
- 실거래 무관, 0원. 윈도우 작업 스케줄러 일일 등록."""
import sys, time, json, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timedelta
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "candles_daily"
API = "https://api.bithumb.com/v1"
DAYS = 750
STABLE = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD"}
MAJORS = {"BTC", "ETH", "XRP", "SOL", "ADA", "DOGE"}   # 스윙은 대형주도 포함
MIN_VOL = 2_000_000_000
TOPN = 60


def universe():
    r = urllib.request.urlopen("https://api.bithumb.com/public/ticker/ALL_KRW", timeout=10)
    data = json.load(r)["data"]
    rows = []
    for coin, d in data.items():
        if coin == "date" or coin in STABLE:
            continue
        try:
            vol = float(d.get("acc_trade_value_24H", 0))
        except Exception:
            continue
        if vol >= MIN_VOL:
            rows.append((coin, vol))
    rows.sort(key=lambda x: -x[1])
    top = [c for c, _ in rows[:TOPN]]
    # 대형주 누락 시 추가
    for m in MAJORS:
        if m not in top:
            top.append(m)
    return top


def fetch_daily(coin, days=DAYS):
    need_from = datetime.now() - timedelta(days=days + 2)
    out, to = [], None
    while True:
        params = f"market=KRW-{coin}&count=200"
        if to:
            params += f"&to={urllib.parse.quote(to)}"
        try:
            r = urllib.request.urlopen(f"{API}/candles/days?{params}", timeout=10)
            chunk = json.load(r)
        except Exception:
            break
        if not isinstance(chunk, list) or not chunk:
            break
        out.extend(chunk)
        oldest = datetime.fromisoformat(chunk[-1]["candle_date_time_kst"])
        if oldest <= need_from or len(chunk) < 200:
            break
        to = chunk[-1]["candle_date_time_kst"]
        time.sleep(0.08)
    out.sort(key=lambda c: c["candle_date_time_kst"])
    return out


if __name__ == "__main__":
    import urllib.parse
    OUT.mkdir(parents=True, exist_ok=True)
    coins = universe()
    print(f"=== 2년 일봉 수집 {datetime.now():%Y-%m-%d %H:%M} | 유니버스 {len(coins)}개 ===", flush=True)
    ok = 0
    for i, c in enumerate(coins, 1):
        d = fetch_daily(c)
        if len(d) >= 200:
            (OUT / f"{c}_1d.json").write_text(json.dumps(d), encoding="utf-8")
            ok += 1
            tag = "OK"
        else:
            tag = f"짧음({len(d)})"
        print(f"[{i:2}/{len(coins)}] {c:8} {len(d):4}일 {tag}", flush=True)
    print(f"\n완료: {ok}/{len(coins)}개 (≥200일) → {OUT}", flush=True)
