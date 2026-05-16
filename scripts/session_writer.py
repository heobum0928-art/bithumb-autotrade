"""
Daily session log auto-writer.
DB에서 당일 거래/신호 데이터를 읽어 docs/sessions/YYYY-MM-DD.md 자동 생성.
watchdog.py가 날짜 변경 시 또는 시작 시 자동 호출.
"""
import sys
import sqlite3
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT     = Path(__file__).parent.parent
DB_PATH  = ROOT / "data" / "trades.db"
SESS_DIR = ROOT / "docs" / "sessions"
SESS_DIR.mkdir(parents=True, exist_ok=True)


def run(target_date: str | None = None) -> None:
    today = target_date or date.today().isoformat()

    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(DB_PATH)

    # ── 거래 데이터 ──────────────────────────────────────────────────
    trades = conn.execute(
        "SELECT coin, entered_at, pnl_krw, pnl_pct, exit_reason, hold_seconds "
        "FROM trades WHERE date=? ORDER BY entered_at",
        (today,),
    ).fetchall()

    # ── 신호 데이터 ──────────────────────────────────────────────────
    signals = conn.execute(
        "SELECT entry_type, skip_reason FROM signal_log WHERE date(entered_at)=?",
        (today,),
    ).fetchall()

    # ── pump_log ─────────────────────────────────────────────────────
    pumps = conn.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN pullback_2pct=1 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN bounce_after=1 THEN 1 ELSE 0 END) "
        "FROM pump_log WHERE date(detected_at)=?",
        (today,),
    ).fetchone()

    conn.close()

    # ── 집계 ─────────────────────────────────────────────────────────
    total_pnl   = sum(t[2] or 0 for t in trades)
    wins        = [t for t in trades if (t[2] or 0) > 0]
    losses      = [t for t in trades if (t[2] or 0) <= 0]
    win_rate    = len(wins) / len(trades) * 100 if trades else 0

    entered_sigs = [s for s in signals if s[0] in ("regular", "newlisting", "preemptive")]
    blocked_sigs = [s for s in signals if s[1]]

    skip_counts: dict[str, int] = {}
    for _, reason in blocked_sigs:
        k = (reason or "unknown").split("(")[0]  # RSI범위외(93) → RSI범위외
        skip_counts[k] = skip_counts.get(k, 0) + 1

    pump_total    = pumps[0] or 0
    pump_pullback = pumps[1] or 0
    pump_bounce   = pumps[2] or 0

    # ── 마크다운 생성 ─────────────────────────────────────────────────
    lines = [f"# {today} 세션 기록\n"]
    lines.append(f"*자동 생성: {datetime.now().strftime('%H:%M')} KST*\n")

    # 거래 요약
    lines.append("## 거래 요약")
    if trades:
        lines.append(f"- 총 {len(trades)}건 | 승 {len(wins)}건 패 {len(losses)}건 | 승률 {win_rate:.0f}%")
        lines.append(f"- 당일 PnL: **{total_pnl:+,.0f}원**\n")
        lines.append("| 코인 | 진입시각 | PnL | 청산 이유 |")
        lines.append("|---|---|---|---|")
        for coin, eat, pnl, pnl_pct, reason, hold in trades:
            t = eat[11:16] if eat else "-"
            lines.append(f"| {coin} | {t} | {(pnl or 0):+,.0f}원 ({(pnl_pct or 0):+.1f}%) | {reason or '-'} |")
    else:
        lines.append("- 진입 0건\n")

    lines.append("")

    # 신호 요약
    lines.append("## 신호 현황")
    lines.append(f"- 총 신호: {len(signals)}건 | 진입: {len(entered_sigs)}건 | 차단: {len(blocked_sigs)}건")
    if skip_counts:
        top = sorted(skip_counts.items(), key=lambda x: -x[1])[:5]
        lines.append("- 주요 차단 사유: " + ", ".join(f"{k}({v}건)" for k, v in top))
    lines.append("")

    # pump_log 요약
    if pump_total > 0:
        lines.append("## 펌핑 이벤트 (pump_log)")
        lines.append(f"- 감지: {pump_total}건 | -2% 눌림 발생: {pump_pullback}건 | 반등 성공: {pump_bounce}건")
        if pump_total > 0:
            lines.append(f"- 눌림 발생률: {pump_pullback/pump_total*100:.0f}% | 반등률: {pump_bounce/max(pump_pullback,1)*100:.0f}%")
        lines.append("")

    # 봇 상태
    lines.append("## 봇 상태")
    lines.append("- 정상 운영 중 (watchdog 감시)")
    lines.append("")

    out = SESS_DIR / f"{today}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[session_writer] {out.name} 작성 완료")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
