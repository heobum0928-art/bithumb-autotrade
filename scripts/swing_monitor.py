"""
스윙 트레이딩 모니터 — 매일 MA 크로스 체크 + 텔레그램 알림

전략:
  BTC  MA 20/60 골든/데드크로스
  XLM  MA 10/30 골든/데드크로스

실행:
  python scripts/swing_monitor.py          # 즉시 1회 체크
  python scripts/swing_monitor.py --loop   # 매일 22:00 KST 자동 반복
"""
import sys
import time
import argparse
import requests
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

KST = timezone(timedelta(hours=9))
CHECK_HOUR_KST = 22   # 매일 밤 10시 체크

WATCHLIST = [
    {"coin": "BTC", "fast": 20, "slow": 60},
    {"coin": "XLM", "fast": 10, "slow": 30},
]

# ── 데이터 ────────────────────────────────────────────────────────────────────

def fetch_daily(market: str, count: int = 70) -> list[dict]:
    resp = requests.get(
        "https://api.bithumb.com/v1/candles/days",
        params={"market": market, "count": count},
        timeout=10,
    )
    data = resp.json()
    if not isinstance(data, list):
        return []
    return list(reversed(data))   # 오래된 → 최신


def calc_ma(closes: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1: i + 1]) / period
    return result

# ── 알림 ─────────────────────────────────────────────────────────────────────

def _send_tg(text: str) -> None:
    try:
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        tg  = cfg.get("telegram", {})
        token = tg.get("bot_token", "")
        chat  = tg.get("chat_id", "")
        if not token or not chat:
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass

# ── 체크 ─────────────────────────────────────────────────────────────────────

def check_once() -> None:
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    print(f"\n[스윙모니터] {now_kst} KST 체크")

    signals  = []
    statuses = []

    for item in WATCHLIST:
        coin = item["coin"]
        fast = item["fast"]
        slow = item["slow"]

        candles = fetch_daily(f"KRW-{coin}", count=slow + 5)
        if len(candles) < slow + 1:
            print(f"  {coin}: 데이터 부족")
            continue

        closes = [float(c["trade_price"]) for c in candles]
        dates  = [c["candle_date_time_kst"][:10] for c in candles]
        ma_f   = calc_ma(closes, fast)
        ma_s   = calc_ma(closes, slow)

        # 오늘(마지막)과 어제(그 직전) 값
        today_f  = ma_f[-1]
        today_s  = ma_s[-1]
        prev_f   = ma_f[-2]
        prev_s   = ma_s[-2]
        price    = closes[-1]

        if None in (today_f, today_s, prev_f, prev_s):
            continue

        diff_pct = (today_f - today_s) / today_s * 100  # type: ignore
        state    = "골든" if today_f > today_s else "데드"

        # 크로스 감지
        golden = prev_f <= prev_s and today_f > today_s   # type: ignore
        dead   = prev_f >= prev_s and today_f < today_s   # type: ignore

        status_line = (
            f"  {coin} MA{fast}/{slow}  "
            f"현재가 {price:,.0f}원  "
            f"MA{fast}={today_f:,.0f}  MA{slow}={today_s:,.0f}  "
            f"차이 {diff_pct:+.2f}%  [{state}크로스 구간]"
        )
        print(status_line)
        statuses.append(status_line.strip())

        if golden:
            msg = (
                f"🟢 <b>[스윙] {coin} 골든크로스!</b>\n"
                f"MA{fast}({today_f:,.0f}) > MA{slow}({today_s:,.0f})\n"
                f"현재가: {price:,.0f}원\n"
                f"→ <b>매수 고려</b> (일봉 기준, 몇 시간 내 진입 OK)"
            )
            signals.append(msg)
            print(f"  ★★ {coin} 골든크로스 신호!")
        elif dead:
            msg = (
                f"🔴 <b>[스윙] {coin} 데드크로스!</b>\n"
                f"MA{fast}({today_f:,.0f}) < MA{slow}({today_s:,.0f})\n"
                f"현재가: {price:,.0f}원\n"
                f"→ <b>매도 고려</b> (보유 중이면 청산)"
            )
            signals.append(msg)
            print(f"  ★★ {coin} 데드크로스 신호!")

    # 신호 있으면 즉시 알림
    for sig in signals:
        _send_tg(sig)

    # 매일 상태 요약 발송
    summary = f"📊 <b>스윙 모니터 {now_kst}</b>\n\n" + "\n".join(statuses)
    if signals:
        summary += "\n\n⚡ <b>신호 발생!</b> 위 메시지 확인"
    else:
        summary += "\n\n신호 없음 — 대기 중"
    _send_tg(summary)
    print(f"  텔레그램 발송 완료 (신호 {len(signals)}개)")

# ── 루프 ─────────────────────────────────────────────────────────────────────

def run_loop() -> None:
    print(f"스윙 모니터 시작 — 매일 {CHECK_HOUR_KST}:00 KST 체크")
    last_check_date = None

    while True:
        now = datetime.now(KST)
        today = now.date()

        if now.hour >= CHECK_HOUR_KST and last_check_date != today:
            check_once()
            last_check_date = today

        time.sleep(60)   # 1분마다 시각 확인

# ── 진입점 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="매일 22시 자동 반복")
    args = ap.parse_args()

    if args.loop:
        run_loop()
    else:
        check_once()
