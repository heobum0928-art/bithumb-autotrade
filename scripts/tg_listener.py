"""
Telegram Command Listener — Crypto (빗썸) Bot
인라인 버튼 메뉴 방식

Run: python scripts/tg_listener.py  (별도 터미널)
"""
import sys
import time
import logging
import requests
import yaml
import sqlite3
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient
from bithumb.db import DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TG][%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def load_cfg() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


# ── 텔레그램 API ───────────────────────────────────────────────────────────────

def send(token: str, chat_id: str, text: str, buttons: list = None) -> None:
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": buttons}
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=body, timeout=5,
        )
    except Exception as e:
        log.warning(f"전송 실패: {e}")


def answer_callback(token: str, callback_id: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_id}, timeout=5,
        )
    except Exception:
        pass


def get_updates(token: str, offset: int) -> list:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={
                "offset": offset, "timeout": 20,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=25,
        )
        return resp.json().get("result", [])
    except Exception:
        return []


# ── 메뉴 ──────────────────────────────────────────────────────────────────────

MAIN_MENU = [
    [{"text": "📊 상태",     "callback_data": "status"},
     {"text": "💰 최근 손익", "callback_data": "pnl"}],
    [{"text": "📅 오늘",     "callback_data": "pnl_today"},
     {"text": "📋 어제",     "callback_data": "pnl_yesterday"}],
    [{"text": "📈 주간 요약", "callback_data": "pnl_week"},
     {"text": "❓ 도움말",   "callback_data": "help"}],
]


# ── 응답 빌더 ─────────────────────────────────────────────────────────────────

def build_status(client: BithumbClient) -> str:
    lines = ["<b>📊 현재 포트폴리오</b>"]
    total_krw = 0.0
    coin_vals = []
    try:
        for a in client.get_accounts():
            bal = float(a.get("balance", 0))
            if bal <= 0:
                continue
            cur = a["currency"]
            if cur == "KRW":
                total_krw = bal
                lines.append(f"💵 KRW: <b>{bal:,.0f}원</b>")
            elif cur not in ("P",):
                try:
                    ticker = client.get_ticker(cur)
                    price  = float(ticker["closing_price"])
                    val    = bal * price
                    rate   = float(ticker.get("fluctate_rate_24H", 0))
                    if val >= 1000:
                        coin_vals.append((cur, bal, price, val, rate))
                except Exception:
                    pass
        for cur, bal, price, val, rate in sorted(coin_vals, key=lambda x: -x[3]):
            emoji = "🟢" if rate >= 0 else "🔴"
            lines.append(
                f"{emoji} {cur}: {bal:.4f}개 @ {price:,.3f}원 "
                f"= <b>{val:,.0f}원</b> ({rate:+.1f}%)"
            )
        total = total_krw + sum(v[3] for v in coin_vals)
        lines.append(f"\n💼 <b>총 자산: {total:,.0f}원</b>")
    except Exception as e:
        lines.append(f"조회 실패: {e}")
    return "\n".join(lines)


def build_pnl_recent() -> str:
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades ORDER BY exited_at DESC LIMIT 10"
        ).fetchall()
        con.close()
        if not rows:
            return "최근 거래 기록 없음"
        lines = ["<b>💰 최근 거래 (최대 10건)</b>"]
        for r in rows:
            pnl   = r["pnl_krw"]
            sign  = "+" if pnl >= 0 else ""
            emoji = "✅" if pnl >= 0 else "❌"
            dt    = r["exited_at"][:16] if r["exited_at"] else "-"
            lines.append(
                f"{emoji} {dt}  <b>{r['coin']}</b>  "
                f"{sign}{pnl:,.0f}원 ({r['pnl_pct']:+.1f}%)"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"조회 실패: {e}"


def build_pnl_date(target: date) -> str:
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades WHERE date = ? ORDER BY exited_at",
            (target.isoformat(),)
        ).fetchall()
        con.close()
        ds = target.strftime("%Y-%m-%d")
        if not rows:
            return f"<b>📅 {ds}</b>\n거래 없음"
        total = sum(r["pnl_krw"] for r in rows)
        wins  = sum(1 for r in rows if r["pnl_krw"] > 0)
        n     = len(rows)
        lines = [
            f"<b>📅 {ds}</b>",
            f"거래: {n}건  승률: {wins/n*100:.0f}%  ({wins}승 {n-wins}패)",
            "",
        ]
        for r in rows:
            pnl   = r["pnl_krw"]
            sign  = "+" if pnl >= 0 else ""
            emoji = "✅" if pnl >= 0 else "❌"
            tm    = r["exited_at"][11:16] if r["exited_at"] else "-"
            lines.append(
                f"{emoji} {tm}  <b>{r['coin']}</b>  "
                f"{sign}{pnl:,.0f}원 ({r['pnl_pct']:+.1f}%)"
            )
        sign_t = "+" if total >= 0 else ""
        lines.append(f"\n💼 <b>총 PnL: {sign_t}{total:,.0f}원</b>")
        return "\n".join(lines)
    except Exception as e:
        return f"조회 실패: {e}"


def build_pnl_week() -> str:
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        week_ago = (date.today() - timedelta(days=6)).isoformat()
        rows = con.execute(
            "SELECT date, SUM(pnl_krw) as day_pnl, COUNT(*) as cnt, "
            "SUM(CASE WHEN pnl_krw > 0 THEN 1 ELSE 0 END) as wins "
            "FROM trades WHERE date >= ? GROUP BY date ORDER BY date DESC",
            (week_ago,)
        ).fetchall()
        con.close()
        if not rows:
            return "이번 주 거래 없음"
        lines = ["<b>📈 주간 요약 (최근 7일)</b>", ""]
        total = 0.0
        for r in rows:
            pnl   = r["day_pnl"]
            total += pnl
            sign  = "+" if pnl >= 0 else ""
            emoji = "✅" if pnl >= 0 else "❌"
            wr    = r["wins"] / r["cnt"] * 100
            lines.append(
                f"{emoji} {r['date']}  {sign}{pnl:,.0f}원  "
                f"({r['cnt']}건 {wr:.0f}%)"
            )
        sign_t = "+" if total >= 0 else ""
        lines.append(f"\n💼 <b>주간 합계: {sign_t}{total:,.0f}원</b>")
        return "\n".join(lines)
    except Exception as e:
        return f"조회 실패: {e}"


def build_help() -> str:
    return (
        "<b>❓ 명령어 안내</b>\n\n"
        "버튼을 누르거나 명령어를 입력하세요:\n\n"
        "📊 /status — 현재 잔고 + 보유 코인\n"
        "💰 /pnl — 최근 10건 거래 손익\n"
        "📅 /today — 오늘 손익\n"
        "📋 /yesterday — 어제 손익\n"
        "📈 /week — 주간 요약\n"
        "❓ /help — 도움말\n"
        "/start — 메인 메뉴"
    )


# ── 핸들러 ────────────────────────────────────────────────────────────────────

def handle(token: str, chat_id: str, client: BithumbClient, data: str) -> None:
    if data in ("status", "/status"):
        send(token, chat_id, build_status(client), MAIN_MENU)
    elif data in ("pnl", "/pnl"):
        send(token, chat_id, build_pnl_recent(), MAIN_MENU)
    elif data in ("pnl_today", "/today"):
        send(token, chat_id, build_pnl_date(date.today()), MAIN_MENU)
    elif data in ("pnl_yesterday", "/yesterday"):
        send(token, chat_id, build_pnl_date(date.today() - timedelta(days=1)), MAIN_MENU)
    elif data in ("pnl_week", "/week"):
        send(token, chat_id, build_pnl_week(), MAIN_MENU)
    elif data in ("help", "/help"):
        send(token, chat_id, build_help(), MAIN_MENU)
    elif data in ("/start", "start"):
        send(token, chat_id,
             "🤖 <b>Crypto 자동매매 봇</b>\n\n아래 버튼을 눌러 정보를 확인하세요.",
             MAIN_MENU)


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run():
    cfg     = load_cfg()
    token   = cfg["telegram"]["bot_token"]
    chat_id = cfg["telegram"]["chat_id"]
    client  = BithumbClient()

    log.info("=== Crypto 텔레그램 봇 시작 ===")

    offset = 0
    while True:
        try:
            updates = get_updates(token, offset)
            for upd in updates:
                offset = upd["update_id"] + 1

                # 인라인 버튼 클릭
                if "callback_query" in upd:
                    cb      = upd["callback_query"]
                    from_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                    if from_id != chat_id:
                        continue
                    answer_callback(token, cb["id"])
                    data = cb.get("data", "")
                    log.info(f"버튼: {data}")
                    handle(token, chat_id, client, data)

                # 텍스트 명령어
                elif "message" in upd:
                    msg     = upd["message"]
                    from_id = str(msg.get("chat", {}).get("id", ""))
                    if from_id != chat_id:
                        continue
                    text = msg.get("text", "").strip()
                    if not text:
                        continue
                    log.info(f"명령어: {text}")
                    handle(token, chat_id, client, text)

        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"오류: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()
