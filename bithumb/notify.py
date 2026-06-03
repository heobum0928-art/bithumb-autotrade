"""Telegram notification helper."""
import logging
import requests
import yaml
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_cfg = None


def _get_cfg() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    return _cfg


def _is_quiet_hours() -> bool:
    cfg = _get_cfg().get("telegram", {})
    qs = cfg.get("quiet_start", -1)
    qe = cfg.get("quiet_end", -1)
    if qs < 0 or qe < 0:
        return False
    h = datetime.now().hour
    if qs <= qe:
        return qs <= h < qe
    return h >= qs or h < qe  # 자정 걸치는 경우


def send(text: str, force: bool = False) -> bool:
    """Send Telegram message. Skipped during quiet hours unless force=True."""
    cfg = _get_cfg().get("telegram", {})
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return False
    if not force and _is_quiet_hours():
        log.debug("[Telegram] 무음 시간대 — 전송 생략")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=5)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"[Telegram] 전송 실패: {e}")
        return False


def notify_detected(coin: str, first_price: float) -> None:
    send(f"<b>[신규 상장 감지]</b> {coin}/KRW\n첫 체결가: <b>{first_price:,.0f}원</b>")


def notify_buy(coin: str, entry_price: float, volume: float, cost_krw: float) -> None:
    send(  # 매수 체결은 무음 시간도 항상 전송
        f"<b>[매수 체결]</b> {coin}/KRW\n"
        f"단가: {entry_price:,.0f}원\n"
        f"수량: {volume:.6f}\n"
        f"투자금: {cost_krw:,.0f}원",
        force=True,
    )


def notify_sell(coin: str, pnl_krw: float, pnl_pct: float, reason: str) -> None:
    sign = "+" if pnl_krw >= 0 else ""
    send(  # 매도 체결은 무음 시간도 항상 전송
        f"<b>[매도 체결]</b> {coin}/KRW\n"
        f"사유: {reason}\n"
        f"손익: <b>{sign}{pnl_krw:,.0f}원 ({pnl_pct:+.2f}%)</b>",
        force=True,
    )


def notify_daily(total_pnl: float, count: int, win_rate: float) -> None:
    send(
        f"<b>[일일 리포트]</b>\n"
        f"거래: {count}건 | 승률: {win_rate*100:.1f}%\n"
        f"총 PnL: <b>{total_pnl:+,.0f}원</b>"
    )


def notify_error(msg: str) -> None:
    send(f"<b>[오류]</b> {msg}", force=True)  # 오류는 무음 무시


def notify_ci_daily(
    today_cnt: int, today_pnl: float, today_tp: int, today_sl: int, today_be: int,
    total_cnt: int, total_wr: float, total_pnl: float, go_target: int = 30,
) -> None:
    remaining = max(0, go_target - total_cnt)
    sign = "+" if today_pnl >= 0 else ""
    send(
        f"<b>📊 [CI 일일 리포트]</b>\n"
        f"오늘: {today_cnt}건 | TP{today_tp}/SL{today_sl}/BE{today_be} | <b>{sign}{today_pnl:,.0f}원</b>\n"
        f"\n<b>── 누적 ──</b>\n"
        f"총 {total_cnt}건 (GO까지 {remaining}건 남음)\n"
        f"승률 {total_wr:.0f}% | PnL <b>{total_pnl:+,.0f}원</b>",
        force=True,
    )
