"""[데이터 확대 수집] 백테스트 표본 키우기 — 거래대금 ≥3억 전체 KRW마켓 2년 일봉.
기존 collect_daily_2yr(상위60+대형주=41코인)보다 넓게. data/candles_daily에 추가.
실거래 무관 0원. 공개API. 1회성 확대 수집."""
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
STABLE = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD", "PYUSD", "USDS"}
MIN_VOL = 300_000_000   # 3억 (기존 20억 → 대폭 완화)


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
    return [c for c, _ in rows]


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
    OUT.mkdir(parents=True, exist_ok=True)
    coins = universe()
    print(f"=== 확대 수집 {datetime.now():%Y-%m-%d %H:%M} | 유니버스 {len(coins)}개(거래대금≥3억) ===", flush=True)
    ok = new = 0
    for i, c in enumerate(coins, 1):
        existed = (OUT / f"{c}_1d.json").exists()
        d = fetch_daily(c)
        if len(d) >= 200:
            (OUT / f"{c}_1d.json").write_text(json.dumps(d), encoding="utf-8")
            ok += 1
            if not existed:
                new += 1
        if i % 20 == 0:
            print(f"  ...{i}/{len(coins)} 진행 (신규 {new})", flush=True)
    print(f"\n완료: {ok}/{len(coins)}개 저장 (신규 {new}개 추가) → {OUT}", flush=True)
