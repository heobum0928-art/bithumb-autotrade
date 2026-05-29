"""
Claude AI 액티브 트레이더 — 시장 분석 후 직접 진입/청산 결정

Max 요금제 사용: claude CLI subprocess (API 크레딧 불필요)

모드:
  NO-POSITION (30s 루프): 시장 데이터 → Claude "진입할 코인?" → 매수
  IN-POSITION (10s 루프): 현재 PnL → Claude "지금 팔아?" → 매도
  하드스탑 -3% / 하드TP +10%: Claude 무관 즉시 실행

포지션: data/claude_pos.json
워치리스트(alt_monitor 필터용): data/claude_watchlist.json

실행: python scripts/claude_screener.py
"""
import sys
import json
import time
import sqlite3
import logging
import subprocess
import yaml
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.client import BithumbClient
from bithumb.db import DB_PATH, log_trade

# ── 설정 ──────────────────────────────────────────────────────────────────────
KST               = timezone(timedelta(hours=9))
WATCHLIST_PATH    = Path("data/claude_watchlist.json")
POS_PATH          = Path("data/claude_pos.json")
MIN_DAILY_VOL_KRW = 20_000_000_000   # 24h 거래대금 20억+ 코인만
MAX_CANDIDATES    = 25
CLAUDE_TIMEOUT    = 60               # claude CLI 최대 대기 (초)
SKIP_COINS        = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}

NO_POS_INTERVAL   = 30    # 포지션 없을 때 루프 (초)
IN_POS_INTERVAL   = 10    # 포지션 있을 때 루프 (초)
MARKET_REFRESH    = 300   # 시장 데이터 갱신 주기 (초) — 5분
HARD_STOP_PCT     = -0.03  # 하드 스탑 -3% (Claude 판단 불필요)
HARD_TP_PCT       = 0.10   # 하드 TP +10% (Claude 판단 불필요)
ENTRY_KRW         = 15_000  # 진입 금액 (원)
ORDER_WAIT_SEC    = 15      # 주문 체결 최대 대기 (초)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCREENER][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)),
        logging.FileHandler("logs/claude_screener.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── 설정 로드 ─────────────────────────────────────────────────────────────────

def _load_record_only() -> bool:
    try:
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        return bool(cfg.get("trading", {}).get("record_only", True))
    except Exception:
        return True


def _send_tg(text: str) -> None:
    try:
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        tg = cfg.get("telegram", {})
        token = tg.get("bot_token", "")
        chat_id = str(tg.get("chat_id", ""))
        if token and chat_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
    except Exception:
        pass


# ── 시장 데이터 ───────────────────────────────────────────────────────────────

def get_market_snapshot(client: BithumbClient) -> list[dict]:
    tickers = client.get_ticker("ALL")
    coins = []
    for coin, data in tickers.items():
        if coin == "date" or coin in SKIP_COINS:
            continue
        vol = float(data.get("acc_trade_value_24H", 0))
        if vol < MIN_DAILY_VOL_KRW:
            continue
        coins.append({
            "coin":    coin,
            "chg_24h": round(float(data.get("fluctate_rate_24H", 0)), 1),
            "vol_bil": round(vol / 1e8, 1),
            "price":   float(data.get("closing_price", 0)),
        })
    coins.sort(key=lambda x: -x["chg_24h"])
    return coins[:MAX_CANDIDATES]


def get_current_price(client: BithumbClient, coin: str) -> float:
    try:
        data = client.get_ticker(coin)
        return float(data.get("closing_price", 0))
    except Exception:
        return 0.0


def get_recent_trades(n: int = 15) -> list[dict]:
    try:
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute("""
            SELECT coin, ROUND(pnl_pct*100,1), exit_reason, entered_at
            FROM trades ORDER BY id DESC LIMIT ?
        """, (n,))
        rows = cur.fetchall()
        con.close()
        return [{"coin": r[0], "pnl": r[1], "exit": str(r[2])[:40], "at": str(r[3])[:16]}
                for r in rows]
    except Exception:
        return []


def get_pump_log_summary() -> str:
    try:
        today = datetime.now(KST).date().isoformat()
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute("""
            SELECT coin, COUNT(*) cnt,
                   ROUND(AVG(price_chg_pct),1) avg_chg,
                   ROUND(MAX(price_chg_pct),1) max_chg
            FROM pump_log WHERE detected_at >= ?
            GROUP BY coin ORDER BY cnt DESC LIMIT 10
        """, (today,))
        rows = cur.fetchall()
        con.close()
        return "\n".join(f"  {r[0]}: {r[1]}회 평균+{r[2]}% 최대+{r[3]}%" for r in rows) or "  없음"
    except Exception:
        return "  조회 불가"


# ── 주문 체결 대기 ─────────────────────────────────────────────────────────────

def wait_for_order(client: BithumbClient, uuid: str, max_sec: int = ORDER_WAIT_SEC) -> dict:
    deadline = time.time() + max_sec
    while time.time() < deadline:
        try:
            order = client.get_order(uuid)
            if order.get("state") in ("done", "cancel"):
                return order
        except Exception:
            pass
        time.sleep(1)
    return {}


def get_coin_balance(client: BithumbClient, coin: str) -> float:
    try:
        accounts = client.get_balance(coin)
        for acct in accounts:
            if acct.get("currency") == coin.upper():
                return float(acct.get("balance", 0))
    except Exception:
        pass
    return 0.0


# ── Claude CLI 호출 ───────────────────────────────────────────────────────────

def ask_claude(prompt: str) -> dict:
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=CLAUDE_TIMEOUT,
    )
    text = result.stdout.strip()
    if not text:
        raise ValueError(f"claude CLI 빈 응답 (stderr: {result.stderr[:200]})")
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                text = part
                break
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)


def build_no_pos_prompt(snapshot: list[dict], recent: list[dict], pump_summary: str) -> str:
    now_kst = datetime.now(KST).strftime("%H:%M")
    return f"""빗썸 알트코인 단타 AI 트레이더입니다. 지금 포지션이 없고 진입 기회를 찾고 있습니다.

현재 시각: {now_kst} KST

=== 빗썸 현재 시장 (24h 거래대금 20억+, 상위 {len(snapshot)}개) ===
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

=== 오늘 펌핑 이력 (pump_log) ===
{pump_summary}

=== 최근 봇 실거래 결과 ===
{json.dumps(recent, ensure_ascii=False, indent=2)}

=== 진입 기준 ===
- 24h 변화율 +5~40% 구간 (과열 +50% 초과 제외, 죽은 코인 0% 이하 제외)
- 거래대금 클수록 진입/청산 용이
- 오늘 펌핑 이력 여러 번 = 지속 수급 신호
- 최근 봇이 손실 본 코인 제외

다음 5~30분 내 +3% 이상 급등 가능성 높은 코인이 있으면 진입, 없으면 대기.

JSON만 응답 (앞뒤 설명 없이):
{{"action": "buy", "coin": "COIN심볼", "reason": "한 줄 이유", "watchlist": ["COIN1","COIN2","COIN3","COIN4"], "avoid": ["BAD1"]}}
또는
{{"action": "wait", "reason": "한 줄 이유", "watchlist": ["COIN1","COIN2","COIN3","COIN4"], "avoid": ["BAD1"]}}"""


def build_in_pos_prompt(pos: dict, current_price: float, snapshot: list[dict]) -> str:
    entry     = pos["entry_price"]
    pnl_pct   = (current_price - entry) / entry * 100
    hold_sec  = time.time() - pos["entry_ts"]
    now_kst   = datetime.now(KST).strftime("%H:%M")
    coin      = pos["coin"]
    top5      = snapshot[:5] if snapshot else []
    return f"""빗썸 알트코인 단타 AI 트레이더입니다. 현재 포지션 보유 중입니다.

현재 시각: {now_kst} KST
보유 코인: {coin}
진입가: {entry:,.1f}원
현재가: {current_price:,.1f}원
PnL: {pnl_pct:+.2f}%
보유 시간: {hold_sec/60:.1f}분

=== 시장 상위 5개 현황 ===
{json.dumps(top5, ensure_ascii=False)}

단기 차익 실현 관점에서 지금 매도해야 하는가?
판단 기준:
- 충분히 올랐고 더 상승 여력이 적음 → 매도
- 추세 꺾임 징후 → 매도
- 아직 상승 여력 있음 → 보유

JSON만 응답:
{{"action": "sell", "reason": "한 줄 이유"}}
또는
{{"action": "hold", "reason": "한 줄 이유"}}"""


# ── 포지션 파일 ───────────────────────────────────────────────────────────────

def load_pos() -> dict | None:
    try:
        if POS_PATH.exists():
            return json.loads(POS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def save_pos(pos: dict | None) -> None:
    if pos is None:
        POS_PATH.unlink(missing_ok=True)
    else:
        POS_PATH.write_text(json.dumps(pos, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 매수/매도 실행 ────────────────────────────────────────────────────────────

def do_buy(client: BithumbClient, coin: str, record_only: bool) -> dict | None:
    market = f"KRW-{coin}"
    if record_only:
        price = get_current_price(client, coin)
        if price <= 0:
            log.warning(f"[{coin}] 가격 조회 실패 — 모의 매수 스킵")
            return None
        vol = ENTRY_KRW / price
        log.info(f"[{coin}] 모의 매수 @{price:,.1f}원 {vol:.6f} ({ENTRY_KRW:,}원)")
        return {
            "coin":        coin,
            "market":      market,
            "entry_price": price,
            "volume":      vol,
            "cost_krw":    ENTRY_KRW,
            "entry_ts":    time.time(),
            "entered_at":  datetime.now(KST).isoformat(),
            "mock":        True,
        }

    try:
        r    = client.market_buy(market, ENTRY_KRW)
        uuid = r.get("uuid")
        if not uuid:
            log.error(f"[{coin}] 매수 UUID 없음: {r}")
            return None
        order = wait_for_order(client, uuid)
        if order.get("state") != "done":
            log.warning(f"[{coin}] 매수 미체결 — 취소")
            try:
                client.cancel_order(uuid)
            except Exception:
                pass
            return None
        vol   = float(order.get("executed_volume", 0))
        funds = float(order.get("executed_funds",  0))
        fee   = float(order.get("paid_fee",        0))
        if vol <= 0:
            return None
        entry = funds / vol
        cost  = funds + fee
        log.info(f"[{coin}] 매수 체결 @{entry:,.1f}원 {vol:.6f} ({cost:,.0f}원)")
        return {
            "coin":        coin,
            "market":      market,
            "entry_price": entry,
            "volume":      vol,
            "cost_krw":    cost,
            "entry_ts":    time.time(),
            "entered_at":  datetime.now(KST).isoformat(),
            "mock":        False,
        }
    except Exception as e:
        log.error(f"[{coin}] 매수 실패: {e}")
        return None


def do_sell(client: BithumbClient, pos: dict, current_price: float,
            reason: str, record_only: bool) -> None:
    coin     = pos["coin"]
    entry    = pos["entry_price"]
    vol      = pos["volume"]
    cost_krw = pos["cost_krw"]
    pnl_pct  = (current_price - entry) / entry * 100

    if record_only or pos.get("mock"):
        recv_krw = current_price * vol
        pnl_krw  = recv_krw - cost_krw
        log.info(f"[{coin}] 모의 매도 @{current_price:,.1f}원 PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}")
        _send_tg(f"🤖 [Claude] 모의 매도: <b>{coin}</b>\nPnL: {pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)\n이유: {reason}")
        _record_trade(pos, current_price, recv_krw, reason)
        save_pos(None)
        return

    actual_vol = get_coin_balance(client, coin)
    if actual_vol <= 0:
        log.info(f"[{coin}] 잔고 없음 — 외부 청산으로 처리")
        save_pos(None)
        return
    sell_vol = min(vol, actual_vol)

    try:
        r    = client.market_sell(pos["market"], sell_vol)
        uuid = r.get("uuid")
        if not uuid:
            log.error(f"[{coin}] 매도 UUID 없음: {r}")
            return
        order = wait_for_order(client, uuid)
        if order.get("state") != "done":
            log.warning(f"[{coin}] 매도 미체결 — 재시도 없이 포지션 유지")
            return
        recv_krw  = (float(order.get("executed_funds", 0))
                     - float(order.get("paid_fee", 0)))
        fill_price = (float(order.get("executed_funds", 0))
                      / float(order.get("executed_volume", sell_vol) or sell_vol))
        pnl_pct  = (fill_price - entry) / entry * 100
        pnl_krw  = recv_krw - cost_krw
        log.info(f"[{coin}] 매도 체결 @{fill_price:,.1f}원 PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}")
        _send_tg(f"🤖 [Claude] 매도: <b>{coin}</b>\nPnL: {pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)\n이유: {reason}")
        _record_trade(pos, fill_price, recv_krw, reason)
    except Exception as e:
        log.error(f"[{coin}] 매도 실패: {e}")
        return

    save_pos(None)


def _record_trade(pos: dict, exit_price: float, recv_krw: float, reason: str) -> None:
    try:
        log_trade(
            coin=pos["coin"],
            market=pos["market"],
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            volume=pos["volume"],
            cost_krw=pos["cost_krw"],
            received_krw=recv_krw,
            exit_reason=f"[Claude] {reason}",
            entered_at=datetime.fromisoformat(pos["entered_at"]),
            exited_at=datetime.now(),
            max_price=exit_price,
        )
    except Exception as e:
        log.error(f"[DB] 기록 실패: {e}")


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run() -> None:
    record_only = _load_record_only()
    client      = BithumbClient()
    log.info(f"=== Claude 액티브 트레이더 시작 | RECORD_ONLY={record_only} ===")
    if record_only:
        log.warning("⚠ RECORD_ONLY=True — 모의 거래 모드 (실거래 미실행)")

    snapshot:      list[dict] = []
    recent:        list[dict] = []
    pump_summary:  str        = "  없음"
    last_refresh:  float      = 0.0

    while True:
        loop_start = time.time()

        # 시장 데이터 5분마다 갱신
        if loop_start - last_refresh >= MARKET_REFRESH:
            try:
                snapshot      = get_market_snapshot(client)
                recent        = get_recent_trades()
                pump_summary  = get_pump_log_summary()
                last_refresh  = loop_start
                log.info(f"시장 데이터 갱신 | 후보 {len(snapshot)}개")
            except Exception as e:
                log.error(f"시장 데이터 갱신 실패: {e}")

        pos = load_pos()

        # ── NO-POSITION 모드 ──────────────────────────────────────────────────
        if pos is None:
            try:
                prompt = build_no_pos_prompt(snapshot, recent, pump_summary)
                result = ask_claude(prompt)

                # 워치리스트 부산물 — alt_monitor 필터용
                watchlist = result.get("watchlist", [])
                avoid     = result.get("avoid", [])
                if watchlist:
                    now_kst = datetime.now(KST)
                    WATCHLIST_PATH.write_text(
                        json.dumps({
                            "coins":      watchlist,
                            "avoid":      avoid,
                            "reason":     result.get("reason", ""),
                            "updated_at": now_kst.isoformat(),
                        }, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                action = result.get("action", "wait")
                coin   = result.get("coin", "").upper()
                reason = result.get("reason", "")
                log.info(f"[NoPos] action={action} coin={coin or '-'} | {reason}")

                if action == "buy" and coin:
                    new_pos = do_buy(client, coin, record_only)
                    if new_pos:
                        new_pos["reason"] = reason
                        save_pos(new_pos)
                        tag = "모의 " if record_only else ""
                        _send_tg(
                            f"🤖 [Claude] {tag}매수: <b>{coin}</b>"
                            f" @{new_pos['entry_price']:,.1f}원\n이유: {reason}"
                        )

            except subprocess.TimeoutExpired:
                log.warning("Claude CLI 타임아웃 — 대기 유지")
            except json.JSONDecodeError as e:
                log.warning(f"JSON 파싱 실패: {e}")
            except Exception as e:
                log.error(f"NoPos 루프 오류: {e}")

            elapsed = time.time() - loop_start
            time.sleep(max(5, NO_POS_INTERVAL - elapsed))

        # ── IN-POSITION 모드 ─────────────────────────────────────────────────
        else:
            coin  = pos["coin"]
            entry = pos["entry_price"]

            current = get_current_price(client, coin)
            if current <= 0:
                current = entry  # 가격 조회 실패 시 유지

            pnl_pct = (current - entry) / entry

            # 하드 스탑 -3%
            if pnl_pct <= HARD_STOP_PCT:
                reason = f"하드스탑 {pnl_pct*100:+.1f}%"
                log.warning(f"[{coin}] {reason} → 강제 청산")
                do_sell(client, pos, current, reason, record_only)
                time.sleep(max(2, IN_POS_INTERVAL - (time.time() - loop_start)))
                continue

            # 하드 TP +10%
            if pnl_pct >= HARD_TP_PCT:
                reason = f"하드TP {pnl_pct*100:+.1f}%"
                log.info(f"[{coin}] {reason} → 강제 익절")
                do_sell(client, pos, current, reason, record_only)
                time.sleep(max(2, IN_POS_INTERVAL - (time.time() - loop_start)))
                continue

            # Claude 청산 판단
            try:
                prompt = build_in_pos_prompt(pos, current, snapshot)
                result = ask_claude(prompt)
                action = result.get("action", "hold")
                reason = result.get("reason", "")
                log.info(f"[{coin}] InPos action={action} PnL={pnl_pct*100:+.1f}% | {reason}")

                if action == "sell":
                    do_sell(client, pos, current, f"Claude: {reason[:50]}", record_only)

            except subprocess.TimeoutExpired:
                log.warning(f"[{coin}] Claude 타임아웃 — 보유 유지")
            except json.JSONDecodeError as e:
                log.warning(f"[{coin}] JSON 파싱 실패 — 보유 유지: {e}")
            except Exception as e:
                log.error(f"[{coin}] InPos 루프 오류: {e}")

            elapsed = time.time() - loop_start
            time.sleep(max(2, IN_POS_INTERVAL - elapsed))


if __name__ == "__main__":
    run()
