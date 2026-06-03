"""
Daily session log auto-writer.
DB에서 당일 거래/신호 데이터를 읽어 docs/sessions/YYYY-MM-DD.md 자동 생성.
watchdog.py가 날짜 변경 시 또는 시작 시 자동 호출.
"""
import sys
import sqlite3
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))

sys.stdout.reconfigure(encoding="utf-8")

ROOT     = Path(__file__).parent.parent
DB_PATH  = ROOT / "data" / "trades.db"
SESS_DIR = ROOT / "docs" / "sessions"
SESS_DIR.mkdir(parents=True, exist_ok=True)


def run(target_date: str | None = None) -> None:
    now_kst = datetime.now(KST)
    today = target_date or now_kst.date().isoformat()

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

    # ── 모의투자 데이터 ───────────────────────────────────────────────
    dry_trades = conn.execute(
        "SELECT coin, entered_at, pnl_krw, pnl_pct, exit_reason, max_pnl_pct "
        "FROM trades WHERE date=? AND exit_reason LIKE '%CS-DRY%' ORDER BY entered_at",
        (today,),
    ).fetchall()

    # ── CI Mode 데이터 ────────────────────────────────────────────────
    ci_trades = conn.execute(
        "SELECT coin, entered_at, pnl_krw, pnl_pct, exit_reason, max_pnl_pct, claude_reason "
        "FROM trades WHERE date=? AND exit_reason LIKE '%CS-CI%' ORDER BY entered_at",
        (today,),
    ).fetchall()

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
    lines.append(f"*자동 생성: {now_kst.strftime('%Y-%m-%d %H:%M')} KST*\n")

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

    # 모의투자 요약
    if dry_trades:
        dry_tp   = sum(1 for t in dry_trades if 'TP' in (t[4] or ''))
        dry_sl   = sum(1 for t in dry_trades if 'SL' in (t[4] or ''))
        dry_be   = sum(1 for t in dry_trades if 'BE' in (t[4] or ''))
        dry_pnl  = sum(t[2] or 0 for t in dry_trades)
        dry_wr   = dry_tp / len(dry_trades) * 100
        lines.append("## 모의투자 (CS-DRY)")
        lines.append(f"- {len(dry_trades)}건 | TP{dry_tp}/SL{dry_sl}/BE{dry_be} | 승률 {dry_wr:.0f}% | PnL **{dry_pnl:+,.0f}원**\n")
        lines.append("| 코인 | 진입시각 | PnL | 최고점 | 청산 |")
        lines.append("|---|---|---|---|---|")
        for coin, eat, pnl, pnl_pct, reason, max_p in dry_trades:
            t = eat[11:16] if eat else "-"
            reason_short = (reason or '-').replace('[CS-DRY] ', '')
            lines.append(f"| {coin} | {t} | {(pnl or 0):+,.0f}원 | +{(max_p or 0):.1f}% | {reason_short} |")
        lines.append("")

    # CI Mode 요약
    if ci_trades:
        ci_tp  = sum(1 for t in ci_trades if 'TP' in (t[4] or ''))
        ci_sl  = sum(1 for t in ci_trades if 'SL' in (t[4] or ''))
        ci_be  = sum(1 for t in ci_trades if 'BE' in (t[4] or ''))
        ci_pnl = sum(t[2] or 0 for t in ci_trades)
        ci_wr  = ci_tp / len(ci_trades) * 100
        lines.append("## Claude Intelligence Mode (CS-CI)")
        lines.append(f"- {len(ci_trades)}건 | TP{ci_tp}/SL{ci_sl}/BE{ci_be} | 승률 {ci_wr:.0f}% | PnL **{ci_pnl:+,.0f}원**\n")
        lines.append("| 코인 | 진입시각 | PnL | Claude 판단 이유 | 청산 |")
        lines.append("|---|---|---|---|---|")
        for coin, eat, pnl, pnl_pct, reason, max_p, claude_r in ci_trades:
            t = eat[11:16] if eat else "-"
            reason_short = (reason or '-').replace('[CS-CI] ', '')
            claude_short = (claude_r or '-')[:50]
            lines.append(f"| {coin} | {t} | {(pnl or 0):+,.0f}원 | {claude_short} | {reason_short} |")
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
