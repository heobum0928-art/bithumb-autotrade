"""
Telegram Command Listener
  /status  — 현재 잔고 + 오늘 PnL 요약
  /pnl     — 최근 7일 거래 성과

Run: python scripts/tg_listener.py  (별도 터미널)
"""
import sys
import time
import logging
import requests
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TG][%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def load_cfg() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


def send(token: str, chat_id: str, text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        log.warning(f"전송 실패: {e}")


def get_updates(token: str, offset: int) -> list:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": 20, "allowed_updates": ["message"]},
            timeout=25,
        )
        return resp.json().get("result", [])
    except Exception:
        return []


def build_status(client: BithumbClient) -> str:
    lines = ["<b>[현재 포트폴리오]</b>"]
    total_krw = 0.0
    coin_vals = []

    accounts = client.get_accounts()
    for a in accounts:
        bal = float(a.get("balance", 0))
        if bal <= 0:
            continue
        cur = a["currency"]
        if cur == "KRW":
            total_krw = bal
            lines.append(f"KRW: {bal:,.0f}원")
        elif cur not in ("P",):
            try:
                ticker = client.get_ticker(cur)
                price = float(ticker["closing_price"])
                val = bal * price
                rate = float(ticker.get("fluctate_rate_24H", 0))
                if val >= 1000:
                    coin_vals.append((cur, bal, price, val, rate))
            except Exception:
                pass

    for cur, bal, price, val, rate in sorted(coin_vals, key=lambda x: -x[3]):
        lines.append(f"{cur}: {bal:.2f}개 @ {price:.3f}원 = {val:,.0f}원 ({rate:+.1f}%)")

    total = total_krw + sum(v[3] for v in coin_vals)
    lines.append(f"\n<b>총 자산: {total:,.0f}원</b>")
    return "\n".join(lines)


def build_pnl() -> str:
    try:
        import sqlite3
        con = sqlite3.connect("trades.db")
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades ORDER BY exited_at DESC LIMIT 10"
        ).fetchall()
        con.close()
        if not rows:
            return "최근 거래 기록 없음"
        lines = ["<b>[최근 거래]</b>"]
        for r in rows:
            sign = "+" if r["pnl_krw"] >= 0 else ""
            lines.append(
                f"{r['coin']} | {sign}{r['pnl_krw']:,.0f}원 ({r['pnl_pct']:+.1f}%) | {r['exit_reason'][:20] if r['exit_reason'] else '-'}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"기록 조회 실패: {e}"


def run():
    cfg     = load_cfg()
    token   = cfg["telegram"]["bot_token"]
    chat_id = cfg["telegram"]["chat_id"]
    client  = BithumbClient()

    log.info("=== 텔레그램 명령 리스너 시작 ===")
    log.info("/status, /pnl 명령 대기 중...")

    offset = 0
    while True:
        try:
            updates = get_updates(token, offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                text = msg.get("text", "").strip()
                from_id = str(msg.get("chat", {}).get("id", ""))

                if from_id != chat_id:
                    continue  # 등록된 chat_id만 허용

                log.info(f"명령 수신: {text}")

                if text == "/status":
                    reply = build_status(client)
                    send(token, chat_id, reply)
                elif text == "/pnl":
                    reply = build_pnl()
                    send(token, chat_id, reply)
                elif text == "/help":
                    send(token, chat_id,
                         "<b>[명령어]</b>\n"
                         "/status - 현재 잔고 + 코인 현황\n"
                         "/pnl    - 최근 10건 거래 손익")
        except KeyboardInterrupt:
            log.info("종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"오류: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()
