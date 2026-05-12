"""
Telegram command bot — query trading bot status from phone.

Commands:
  /status  — bot running, current position, cooldowns
  /trades  — today's trade history
  /pnl     — daily PnL summary

Run: python scripts/tg_bot.py
"""
import sys
import os
import json
import time
import sqlite3
import logging
import requests
import yaml
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TG][%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

CFG       = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
TG_CFG    = CFG.get("telegram", {})
TOKEN     = TG_CFG.get("bot_token", "")
CHAT_ID   = str(TG_CFG.get("chat_id", ""))
API       = f"https://api.telegram.org/bot{TOKEN}"

DB_PATH        = Path("data/trades.db")
ACTIVE_POS     = Path("data/active_pos.json")
LOSS_COINS     = Path("data/loss_coins.json")
LOCK_FILE      = Path("data/bot.lock")


# ── Telegram helpers ──────────────────────────────────────────────────────────

def send(text: str, chat_id: str = CHAT_ID) -> None:
    try:
        requests.post(f"{API}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                      timeout=5)
    except Exception as e:
        log.warning(f"send 실패: {e}")


def get_updates(offset: int) -> list:
    try:
        r = requests.get(f"{API}/getUpdates",
                         params={"offset": offset, "timeout": 20, "allowed_updates": ["message"]},
                         timeout=25)
        return r.json().get("result", [])
    except Exception:
        return []


# ── Command handlers ──────────────────────────────────────────────────────────

def cmd_status() -> str:
    lines = ["<b>[봇 상태]</b>"]

    # 봇 프로세스 확인 (Windows 호환)
    if LOCK_FILE.exists():
        try:
            import subprocess
            pid = int(LOCK_FILE.read_text().strip())
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True
            )
            if str(pid) in result.stdout:
                lines.append(f"● 실행 중 (PID {pid})")
            else:
                lines.append("✗ 봇 종료됨 (lock 파일 있지만 프로세스 없음)")
        except Exception:
            lines.append("✗ 봇 종료됨")
    else:
        lines.append("✗ 봇 종료됨")

    # 현재 포지션
    if ACTIVE_POS.exists():
        try:
            pos = json.loads(ACTIVE_POS.read_text())
            coin = pos.get("coin", "?")
            entry = pos.get("entry_price", 0)
            entered = pos.get("entered_at", "")[:16]
            lines.append(f"\n<b>포지션:</b> {coin} @ {entry:,.0f}원 ({entered})")
        except Exception:
            lines.append("\n포지션: 없음")
    else:
        lines.append("\n포지션: 없음")

    # 쿨다운
    if LOSS_COINS.exists():
        try:
            loss = json.loads(LOSS_COINS.read_text())
            now = time.time()
            active = {c: v for c, v in loss.items()
                      if isinstance(v, dict) and v.get("until", 0) > now}
            if active:
                lines.append("\n<b>쿨다운:</b>")
                for c, v in active.items():
                    h = (v["until"] - now) / 3600
                    lines.append(f"  {c}: {h:.1f}h 남음")
            else:
                lines.append("\n쿨다운: 없음")
        except Exception:
            pass

    return "\n".join(lines)


def cmd_trades() -> str:
    if not DB_PATH.exists():
        return "DB 없음"
    conn = sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
    rows = conn.execute("""
        SELECT coin, pnl_krw, pnl_pct, exit_reason, entered_at
        FROM trades WHERE date(entered_at) >= ? ORDER BY entered_at
    """, (today,)).fetchall()
    conn.close()

    if not rows:
        return f"<b>[오늘 거래]</b>\n없음"

    lines = [f"<b>[오늘 거래]</b> ({today})"]
    total = 0
    wins = 0
    for coin, pnl, pct, reason, entered in rows:
        pnl = pnl or 0
        total += pnl
        if pnl > 0:
            wins += 1
        sign = "✓" if pnl > 0 else "✗"
        t = entered[11:16]
        lines.append(f"{sign} {t} {coin}: {pnl:+,.0f}원 ({pct*100:+.1f}%)")

    n = len(rows)
    lines.append(f"\n합계: <b>{total:+,.0f}원</b> | {wins}승{n-wins}패")
    return "\n".join(lines)


def cmd_pnl() -> str:
    if not DB_PATH.exists():
        return "DB 없음"
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT date(entered_at) as d,
               SUM(pnl_krw) as total,
               COUNT(*) as cnt,
               SUM(CASE WHEN pnl_krw > 0 THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE entry_type IS NULL OR entry_type != 'preemptive'
        GROUP BY d ORDER BY d DESC LIMIT 7
    """).fetchall()
    conn.close()

    if not rows:
        return "<b>[PnL]</b>\n데이터 없음"

    lines = ["<b>[일별 PnL]</b>"]
    cum = 0
    for d, total, cnt, wins in reversed(rows):
        total = total or 0
        cum += total
        wr = wins / cnt * 100 if cnt else 0
        sign = "+" if total >= 0 else ""
        lines.append(f"{d}: {sign}{total:,.0f}원 ({wins}승{cnt-wins}패, 승률{wr:.0f}%)")

    lines.append(f"\n7일 합계: <b>{cum:+,.0f}원</b>")
    return "\n".join(lines)


COMMANDS = {
    "/status": cmd_status,
    "/trades": cmd_trades,
    "/pnl":    cmd_pnl,
}

HELP_TEXT = (
    "<b>[명령어]</b>\n"
    "/status — 봇 상태 · 포지션 · 쿨다운\n"
    "/trades — 오늘 거래 내역\n"
    "/pnl    — 최근 7일 손익"
)


# ── Main polling loop ─────────────────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        log.error("config.yaml에 telegram.bot_token 없음")
        sys.exit(1)

    log.info(f"텔레그램 챗봇 시작 (chat_id={CHAT_ID})")
    send("✅ 트레이딩 봇 챗봇 시작\n" + HELP_TEXT)

    offset = 0
    while True:
        updates = get_updates(offset)
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip().lower()

            # 등록된 chat_id 만 응답
            if chat_id != CHAT_ID:
                log.warning(f"알 수 없는 chat_id: {chat_id}")
                continue

            log.info(f"명령: {text}")
            cmd = text.split()[0] if text else ""
            if cmd in COMMANDS:
                reply = COMMANDS[cmd]()
            elif cmd in ("/help", "/start"):
                reply = HELP_TEXT
            else:
                reply = f"모르는 명령어: {text}\n" + HELP_TEXT

            send(reply, chat_id)

        time.sleep(1)


if __name__ == "__main__":
    main()
