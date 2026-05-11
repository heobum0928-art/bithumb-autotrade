"""Signal condition vs outcome pattern analysis.

Joins signal_log with trades to show which entry conditions lead to higher win rate.
Usage:
    python scripts/signal_stats.py
    python scripts/signal_stats.py --days 30
"""
import sys
import argparse
import sqlite3
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bithumb.db import DB_PATH


def run(days: int | None) -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    date_filter = ""
    if days:
        date_filter = f"AND t.date >= date('now', '-{days} days')"

    rows = con.execute(f"""
        SELECT s.entry_type, s.price_chg_pct, s.vol_mult, s.hour_kst, s.strict_mode,
               s.rsi, s.bb_pct, s.macd_bull,
               t.pnl_pct, t.exit_reason, t.hold_seconds
        FROM signal_log s
        JOIN trades t ON s.coin = t.coin
                     AND substr(s.entered_at, 1, 16) = substr(t.entered_at, 1, 16)
        WHERE 1=1 {date_filter}
        ORDER BY s.entered_at
    """).fetchall()

    if not rows:
        print("데이터 없음 (signal_log 비어있음 - 다음 진입부터 기록됩니다)")
        return

    total = len(rows)
    wins  = [r for r in rows if r["pnl_pct"] > 0]
    print(f"\n{'='*55}")
    print(f" 신호 패턴 분석  (총 {total}건, 승률 {len(wins)/total*100:.0f}%)")
    print(f"{'='*55}")

    def table(label: str, groups: dict) -> None:
        print(f"\n[{label}]")
        print(f"  {'구분':<18} {'건수':>4} {'승률':>6} {'평균PnL':>9}")
        print(f"  {'-'*40}")
        for key in sorted(groups):
            g = groups[key]
            w = sum(1 for r in g if r["pnl_pct"] > 0)
            avg_pnl = sum(r["pnl_pct"] for r in g) / len(g)
            wr = w / len(g) * 100
            bar = "▓" * int(wr / 10)
            print(f"  {str(key):<18} {len(g):>4} {wr:>5.0f}% {avg_pnl:>+8.2f}%  {bar}")

    # 1. 진입 유형별
    by_type: dict = {}
    for r in rows:
        by_type.setdefault(r["entry_type"], []).append(r)
    table("진입 유형", by_type)

    # 2. 거래량 배수 구간별
    by_vol: dict = {}
    for r in rows:
        vm = r["vol_mult"]
        if vm is None:
            key = "N/A"
        elif vm < 7:
            key = "5~7배"
        elif vm < 10:
            key = "7~10배"
        elif vm < 15:
            key = "10~15배"
        else:
            key = "15배+"
        by_vol.setdefault(key, []).append(r)
    table("거래량 배수", by_vol)

    # 3. 시간대별 (KST 시간)
    by_hour: dict = {}
    for r in rows:
        h = r["hour_kst"]
        if h is None:
            continue
        key = f"{h:02d}시"
        by_hour.setdefault(key, []).append(r)
    table("시간대 (KST)", by_hour)

    # 4. 가격변화율 구간별
    by_price: dict = {}
    for r in rows:
        pc = r["price_chg_pct"]
        if pc is None:
            key = "N/A"
        elif pc < 4:
            key = "3~4%"
        elif pc < 6:
            key = "4~6%"
        elif pc < 10:
            key = "6~10%"
        else:
            key = "10%+"
        by_price.setdefault(key, []).append(r)
    table("진입 시 가격변화율", by_price)

    # 5. RSI 구간별
    by_rsi: dict = {}
    for r in rows:
        rsi = r["rsi"]
        if rsi is None:
            key = "N/A"
        elif rsi < 40:
            key = "RSI<40 (과매도)"
        elif rsi < 60:
            key = "RSI 40~60"
        elif rsi < 70:
            key = "RSI 60~70"
        else:
            key = "RSI 70+ (과매수)"
        by_rsi.setdefault(key, []).append(r)
    table("RSI 구간", by_rsi)

    # 6. 볼린저밴드 %B 구간별
    by_bb: dict = {}
    for r in rows:
        bb = r["bb_pct"]
        if bb is None:
            key = "N/A"
        elif bb < 0.5:
            key = "%B<0.5 (하단)"
        elif bb < 0.8:
            key = "%B 0.5~0.8"
        elif bb < 1.0:
            key = "%B 0.8~1.0"
        else:
            key = "%B 1.0+ (돌파)"
        by_bb.setdefault(key, []).append(r)
    table("볼린저밴드 %B", by_bb)

    # 7. MACD 방향
    by_macd: dict = {}
    for r in rows:
        if r["macd_bull"] is None:
            key = "N/A"
        elif r["macd_bull"]:
            key = "MACD 상승"
        else:
            key = "MACD 하락"
        by_macd.setdefault(key, []).append(r)
    table("MACD 방향", by_macd)

    # 8. 엄격모드 여부
    by_strict: dict = {}
    for r in rows:
        key = "엄격모드" if r["strict_mode"] else "일반"
        by_strict.setdefault(key, []).append(r)
    table("엄격모드", by_strict)

    # 6. 스킵 로그 분석 (진입 안 한 케이스)
    skip_filter = f"AND date(s.entered_at) >= date('now', '-{days} days')" if days else ""
    skips = con.execute(f"""
        SELECT s.coin, s.skip_reason, s.price_chg_pct, s.vol_mult, s.hour_kst, s.entered_at
        FROM signal_log s
        WHERE s.entry_type = 'skipped' {skip_filter}
        ORDER BY s.entered_at DESC
    """).fetchall()

    if skips:
        print(f"\n[스킵 로그 — 신호 났지만 진입 안 한 케이스 ({len(skips)}건)]")
        print(f"  {'사유':<14} {'건수':>4}")
        print(f"  {'-'*22}")
        by_skip: dict = {}
        for s in skips:
            by_skip.setdefault(s["skip_reason"] or "기타", []).append(s)
        for reason, items in sorted(by_skip.items(), key=lambda x: -len(x[1])):
            print(f"  {reason:<14} {len(items):>4}건")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="최근 N일만 분석")
    args = parser.parse_args()
    run(args.days)
