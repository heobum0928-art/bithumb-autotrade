"""
Claude Intelligence Mode — Liquid Co-Invest 방식

Claude가 바이낸스+빗썸 데이터를 종합 분석해서 진입/청산 결정.
코드는 데이터 수집과 실행만 담당.

실행:
  python scripts/claude_intelligence.py          # 모의투자
  python scripts/claude_intelligence.py --live   # 실거래 (실제 주문)
"""
import sys
import json
import time
import sqlite3
import logging
import argparse
import subprocess
import requests
import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.client import BithumbClient
from bithumb.db import DB_PATH, log_trade
from bithumb.indicators import get_binance_funding_rate, get_binance_spot_chg1m

# ── 설정 ─────────────────────────────────────────────────────────────────────

KST             = timezone(timedelta(hours=9))
CI_STATE_PATH   = Path("data/claude_ci_state.json")
CI_LOG_FILE     = "logs/claude_ci.log"
CI_TAG          = "CS-CI"

SCAN_INTERVAL   = 300    # 5분마다 Claude 분석
CHECK_INTERVAL  = 10     # 10초마다 포지션 체크
ENTRY_KRW       = 200_000
INITIAL_BALANCE = 1_000_000
CLAUDE_TIMEOUT  = 90
BAD_HOURS_KST   = {22, 23, 0, 1}

# 코인 목록 (Claude가 분석할 대상)
WATCH_COINS = [
    "BTC", "ETH", "XRP", "XLM", "HBAR", "SUI",
    "ONDO", "WLD", "H", "ALLO", "HOME", "DRIFT",
]

# ── 로깅 ─────────────────────────────────────────────────────────────────────

Path("logs").mkdir(exist_ok=True)

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--live", action="store_true")
    args, _ = p.parse_known_args()
    return args

_args   = _parse_args()
_LIVE   = _args.live
_MODE   = "실거래" if _LIVE else "모의투자"

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [CI][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(
            open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        ),
        logging.FileHandler(CI_LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 상태 관리 ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        if CI_STATE_PATH.exists():
            return json.loads(CI_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"balance": INITIAL_BALANCE, "position": None}


def save_state(state: dict) -> None:
    CI_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ── 알림 ─────────────────────────────────────────────────────────────────────

def _send_tg(text: str) -> None:
    try:
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        tg  = cfg.get("telegram", {})
        requests.post(
            f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
            json={"chat_id": tg["chat_id"], "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass

# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def get_current_price(client: BithumbClient, coin: str) -> float:
    try:
        return float(client.get_price(f"KRW-{coin}") or 0)
    except Exception:
        return 0.0


def collect_market_data(client: BithumbClient) -> str:
    """바이낸스+빗썸 데이터를 Claude용 텍스트로 수집."""
    lines = []

    # BTC 방향
    btc_bnb = get_binance_spot_chg1m("BTC")
    btc_fund = get_binance_funding_rate("BTC")
    btc_bithumb = get_binance_spot_chg1m("BTC")
    lines.append(f"=== 시장 현황 ===")
    lines.append(f"BTC 바이낸스 1분: {btc_bnb:+.3f}%" if btc_bnb is not None else "BTC 바이낸스: 조회불가")
    lines.append(f"BTC 펀딩율: {btc_fund*100:+.4f}%" if btc_fund is not None else "BTC 펀딩율: 조회불가")
    lines.append("")

    # 코인별 데이터
    lines.append("=== 코인별 데이터 ===")
    coin_data = []
    for coin in WATCH_COINS:
        try:
            # 빗썸 현재가 + 24h 변화
            t = client.get_ticker(coin) or {}
            bithumb_price  = float(t.get("closing_price") or 0)
            chg_24h        = float(t.get("fluctate_rate_24H") or 0)
            vol_24h        = float(t.get("acc_trade_value_24H") or 0)

            # 바이낸스 데이터
            bnb_chg  = get_binance_spot_chg1m(coin)
            bnb_fund = get_binance_funding_rate(coin)

            if bithumb_price <= 0:
                continue

            bnb_str  = f"바이낸스1m:{bnb_chg:+.2f}%" if bnb_chg is not None else "바이낸스:미상장"
            fund_str = f"펀딩:{bnb_fund*100:+.4f}%" if bnb_fund is not None else ""
            vol_str  = f"거래대금:{vol_24h/1e8:.0f}억"

            line = (f"{coin:8s} 빗썸:{bithumb_price:,.1f}원 "
                    f"24h:{chg_24h:+.1f}% {vol_str} | {bnb_str} {fund_str}")
            lines.append(line)
            coin_data.append({"coin": coin, "chg_24h": chg_24h, "bnb_chg": bnb_chg, "fund": bnb_fund})
        except Exception:
            continue

    # 최근 거래 결과
    lines.append("")
    lines.append("=== 최근 거래 결과 (학습용) ===")
    try:
        con = sqlite3.connect(str(DB_PATH))
        rows = con.execute(
            "SELECT coin, pnl_pct, exit_reason, entered_at FROM trades "
            f"WHERE exit_reason LIKE '%{CI_TAG}%' ORDER BY id DESC LIMIT 10"
        ).fetchall()
        con.close()
        if rows:
            for r in rows:
                tag = "+" if (r[1] or 0) > 0 else "-"
                lines.append(f"  {tag} {r[0]} {r[1]:+.2f}% [{r[2]}] {r[3][11:16]}")
        else:
            lines.append("  (아직 거래 없음)")
    except Exception:
        lines.append("  (조회 실패)")

    return "\n".join(lines)


def ask_claude(market_data: str, position: dict | None) -> dict:
    """Claude에게 시장 데이터 주고 매수/대기 결정 받기."""
    now_kst = datetime.now(KST).strftime("%H:%M")

    if position:
        pos_text = (f"현재 보유: {position['coin']} "
                    f"진입가:{position['entry_price']:,.1f}원 "
                    f"TP:{position.get('tp_pct', 8)*100:.0f}% "
                    f"SL:{position.get('sl_pct', 4)*100:.0f}%")
    else:
        pos_text = "현재 포지션 없음 — 진입 기회 탐색 중"

    prompt = f"""너는 빗썸 암호화폐 단타 트레이더다. 지금 시각: {now_kst} KST

{pos_text}

{market_data}

=== 판단 기준 ===
- 바이낸스에서 먼저 오르고 있는 코인 = 빗썸에서 곧 따라 오를 가능성
- 펀딩율 양수(+) = 롱 우세 = 상승 분위기
- 펀딩율 너무 높음(+0.1% 초과) = 과열 위험
- 24h 변화율 +5~30% = 모멘텀 있음 (너무 높으면 과열)
- 최근 같은 코인에서 SL 났으면 다시 진입 신중하게

=== 포지션 없을 때 ===
지금 당장 사야 할 코인이 있으면 아래 JSON.
없으면 wait.

TP와 SL은 시장 상황에 맞게 직접 결정해. (예: TP 5~15%, SL 3~8%)

JSON만 응답:
{{"action": "buy", "coin": "심볼", "tp_pct": 8, "sl_pct": 4, "reason": "한 줄 이유"}}
또는
{{"action": "wait", "reason": "한 줄 이유"}}"""

    try:
        result = subprocess.run(
            ["claude", "--model", "claude-haiku-4-5-20251001", "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            timeout=CLAUDE_TIMEOUT,
        )
        text = result.stdout.strip()
        if not text:
            return {"action": "wait", "reason": "빈 응답"}
        # JSON 추출
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1:
            return {"action": "wait", "reason": "JSON 없음"}
        return json.loads(text[start:end])
    except Exception as e:
        log.warning(f"Claude 호출 실패: {e}")
        return {"action": "wait", "reason": f"오류: {e}"}

# ── 포지션 모니터링 + 청산 ────────────────────────────────────────────────────

def monitor_position(client: BithumbClient, state: dict) -> bool:
    """포지션 체크. 청산 발생 시 True 반환."""
    pos = state.get("position")
    if not pos:
        return False

    coin    = pos["coin"]
    entry   = pos["entry_price"]
    tp_pct  = pos.get("tp_pct", 8) / 100
    sl_pct  = pos.get("sl_pct", 4) / 100
    highest = pos.get("highest", entry)

    current = get_current_price(client, coin)
    if current <= 0:
        return False

    if current > highest:
        pos["highest"] = current
        highest = current

    pnl_pct    = (current - entry) / entry
    be_active  = pos.get("be_active", False) or highest >= entry * 1.01
    if be_active and not pos.get("be_active"):
        pos["be_active"] = True

    # 청산 조건
    exit_reason = None
    exit_price  = current

    if pnl_pct >= tp_pct:
        exit_price  = entry * (1 + tp_pct)
        exit_reason = f"TP {pnl_pct*100:+.1f}%"
    elif be_active and current <= entry:
        exit_price  = entry
        exit_reason = f"BE {pnl_pct*100:+.1f}%"
    elif not be_active and pnl_pct <= -sl_pct:
        exit_price  = entry * (1 - sl_pct)
        exit_reason = f"SL {pnl_pct*100:+.1f}%"

    if exit_reason:
        recv    = exit_price * pos["volume"]
        pnl_krw = recv - pos["cost_krw"]
        state["balance"] = state.get("balance", INITIAL_BALANCE) + recv
        log.info(f"[CI {coin}] {exit_reason} PnL={pnl_pct*100:+.2f}% ({pnl_krw:+,.0f}원)")
        _send_tg(f"🤖 <b>[CI {_MODE}]</b> <b>{coin}</b> {exit_reason}\n"
                 f"PnL: {pnl_krw:+,.0f}원 | 잔고: {state['balance']:,.0f}원")
        try:
            log_trade(
                coin=coin, market=f"KRW-{coin}",
                entry_price=entry, exit_price=exit_price,
                volume=pos["volume"], cost_krw=pos["cost_krw"],
                received_krw=recv,
                exit_reason=f"[{CI_TAG}] {exit_reason}",
                entered_at=datetime.fromisoformat(pos["entered_at"]).replace(tzinfo=None),
                exited_at=datetime.now(),
                max_price=highest,
                claude_reason=pos.get("reason"),
            )
        except Exception as e:
            log.error(f"DB 저장 실패: {e}")
        state["position"] = None
        save_state(state)
        return True

    log.info(f"[CI {coin}] {current:,.1f}원 PnL={pnl_pct*100:+.2f}%"
             f"{' [BE]' if be_active else ''} TP={tp_pct*100:.0f}% SL={sl_pct*100:.0f}%")
    save_state(state)
    return False

# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run() -> None:
    client = BithumbClient()
    log.info(f"=== Claude Intelligence Mode 시작 [{_MODE}] ===")
    log.info(f"  분석주기={SCAN_INTERVAL}s | 포지션체크={CHECK_INTERVAL}s | 진입={ENTRY_KRW:,}원")
    _send_tg(f"🤖 <b>Claude Intelligence Mode 시작</b> [{_MODE}]\n"
             f"바이낸스+빗썸 데이터 종합 → Claude 판단 → 실행")

    last_scan = 0.0

    while True:
        state = load_state()

        # 1. 포지션 모니터링
        if state.get("position"):
            monitor_position(client, state)
            time.sleep(CHECK_INTERVAL)
            continue

        # 2. 진입 탐색 (5분마다, 나쁜 시간대 제외)
        now_h = datetime.now(KST).hour
        if now_h in BAD_HOURS_KST:
            log.debug(f"저유동성 시간대({now_h}시) — 대기")
            time.sleep(60)
            continue

        if time.time() - last_scan < SCAN_INTERVAL:
            time.sleep(CHECK_INTERVAL)
            continue

        last_scan = time.time()
        balance   = state.get("balance", INITIAL_BALANCE)

        if balance < ENTRY_KRW:
            log.warning(f"잔고 부족 ({balance:,.0f}원) — 대기")
            time.sleep(60)
            continue

        # 3. 데이터 수집 + Claude 판단
        log.info(f"[CI] 데이터 수집 중... (잔고 {balance:,.0f}원)")
        try:
            market_data = collect_market_data(client)
            decision    = ask_claude(market_data, state.get("position"))
        except Exception as e:
            log.error(f"데이터 수집/Claude 오류: {e}")
            time.sleep(60)
            continue

        action = decision.get("action", "wait")
        reason = decision.get("reason", "")
        log.info(f"[CI] Claude 판단: {action} — {reason}")

        if action != "buy":
            time.sleep(CHECK_INTERVAL)
            continue

        coin    = decision.get("coin", "").upper()
        tp_pct  = float(decision.get("tp_pct", 8))
        sl_pct  = float(decision.get("sl_pct", 4))

        if not coin:
            log.warning("coin 없음 — 스킵")
            continue

        # 4. 진입
        price = get_current_price(client, coin)
        if price <= 0:
            log.warning(f"{coin} 가격 0 — 스킵")
            continue

        volume  = ENTRY_KRW / price
        new_pos = {
            "coin":        coin,
            "market":      f"KRW-{coin}",
            "entry_price": price,
            "volume":      volume,
            "cost_krw":    ENTRY_KRW,
            "entered_at":  datetime.now(KST).isoformat(),
            "highest":     price,
            "be_active":   False,
            "tp_pct":      tp_pct,
            "sl_pct":      sl_pct,
            "reason":      reason,
        }
        state["balance"]  -= ENTRY_KRW
        state["position"] = new_pos
        save_state(state)

        log.info(f"[CI {coin}] 진입 @{price:,.1f}원 "
                 f"TP+{tp_pct:.0f}% SL-{sl_pct:.0f}% | {reason}")
        _send_tg(f"🤖 <b>[CI {_MODE}] {coin} 진입</b>\n"
                 f"@{price:,.1f}원 | TP+{tp_pct:.0f}% SL-{sl_pct:.0f}%\n"
                 f"이유: {reason}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
