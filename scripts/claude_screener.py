"""
Claude 진입 스크리너 + 코드 청산

역할 분리:
  진입 판단: Claude (5분마다 시장 분석 → buy/wait 결정)
  청산 판단: 코드 규칙 (Claude 호출 없음)
    - 브레이크이븐: 최고가 +1% 도달 시 손절선 → 진입가(0%)
    - 고정 TP:      +3% 즉시 청산
    - 손절:         -2% (브레이크이븐 발동 전) 또는 0% (발동 후)

포지션: data/claude_pos.json
실행:   python scripts/claude_screener.py
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
POS_PATH          = Path("data/claude_pos.json")
WATCHLIST_PATH    = Path("data/claude_watchlist.json")
MIN_DAILY_VOL_KRW = 20_000_000_000
MAX_CANDIDATES    = 25
CLAUDE_TIMEOUT    = 60
SKIP_COINS        = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}

ENTRY_INTERVAL    = 300   # 진입 분석 주기 (5분)
POS_CHECK_INTERVAL = 10   # 포지션 체크 주기 (10초, Claude 없음)
ENTRY_KRW         = 15_000
ORDER_WAIT_SEC    = 15

# 청산 규칙 (코드 고정)
BE_TRIGGER        = 0.01   # 브레이크이븐 발동 +1%
FIXED_TP          = 0.03   # 고정 익절 +3%
HARD_SL           = -0.02  # 손절 -2% (브레이크이븐 미발동 구간)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CS][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(
            open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        ),
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
        tg  = cfg.get("telegram", {})
        token   = tg.get("bot_token", "")
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

def _candle_summary(client: BithumbClient, coin: str) -> str:
    """최근 12개 1분봉을 Claude용 compact 텍스트로 반환."""
    try:
        raw = client.get_candles(f"KRW-{coin}", unit=1, count=12)
        if not raw:
            return "  (캔들 없음)"
        candles = list(reversed(raw))  # 오래된→최신 순
        closes = [c["trade_price"] for c in candles]
        vols   = [c["candle_acc_trade_volume"] for c in candles]
        times  = [c["candle_date_time_kst"][11:16] for c in candles]

        # 거래량 추세: 최근 3개 vs 이전 3개
        v_new = sum(vols[-3:]) / 3
        v_old = sum(vols[-6:-3]) / 3 if len(vols) >= 6 else v_new
        if   v_new > v_old * 1.4: vol_trend = "거래량↑↑급증"
        elif v_new > v_old * 1.1: vol_trend = "거래량↑증가"
        elif v_new < v_old * 0.6: vol_trend = "거래량↓↓급감"
        elif v_new < v_old * 0.9: vol_trend = "거래량↓감소"
        else:                      vol_trend = "거래량→유지"

        # 가격 방향: 최근 3캔들
        if   closes[-1] > closes[-4] * 1.005: price_dir = "가격↑상승중"
        elif closes[-1] < closes[-4] * 0.995: price_dir = "가격↓하락중"
        else:                                  price_dir = "가격→횡보"

        # 마지막 5개 캔들 표시
        rows = [f"  {t} {c:,.0f}원 {v/1e6:.2f}M"
                for t, c, v in zip(times[-5:], closes[-5:], vols[-5:])]
        high12 = max(closes)
        low12  = min(closes)
        chg5m  = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0

        return (f"  [{price_dir} / {vol_trend}] 5분변화:{chg5m:+.2f}% "
                f"12분고점:{high12:,.0f} 저점:{low12:,.0f}\n" +
                "\n".join(rows))
    except Exception:
        return "  (캔들 조회 실패)"


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
    top = coins[:MAX_CANDIDATES]

    # 각 후보 코인 1분봉 캔들 추가
    for c in top:
        c["candles"] = _candle_summary(client, c["coin"])
    return top


def get_current_price(client: BithumbClient, coin: str) -> float:
    try:
        return float(client.get_ticker(coin).get("closing_price", 0))
    except Exception:
        return 0.0


def get_recent_trades(n: int = 10) -> list[dict]:
    try:
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute("""
            SELECT coin, ROUND(pnl_krw,0), ROUND(pnl_pct,2), exit_reason, entered_at
            FROM trades ORDER BY id DESC LIMIT ?
        """, (n,))
        rows = cur.fetchall()
        con.close()
        return [{"coin": r[0], "pnl_krw": r[1], "pnl_pct": r[2],
                 "exit": str(r[3])[:40], "at": str(r[4])[11:16]}
                for r in rows]
    except Exception:
        return []


def get_today_traded_coins() -> set[str]:
    """오늘 이미 거래된 코인 목록 (재진입 금지용)."""
    try:
        today = datetime.now(KST).date().isoformat()
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute("SELECT DISTINCT coin FROM trades WHERE entered_at >= ?", (today,))
        coins = {r[0] for r in cur.fetchall()}
        con.close()
        return coins
    except Exception:
        return set()


def get_pump_summary() -> str:
    try:
        today = datetime.now(KST).date().isoformat()
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute("""
            SELECT coin, COUNT(*) cnt, ROUND(AVG(price_chg_pct),1) avg_chg
            FROM pump_log WHERE detected_at >= ?
            GROUP BY coin ORDER BY cnt DESC LIMIT 8
        """, (today,))
        rows = cur.fetchall()
        con.close()
        return "\n".join(f"  {r[0]}: {r[1]}회 평균+{r[2]}%" for r in rows) or "  없음"
    except Exception:
        return "  조회 불가"


# ── Claude 진입 분석 ──────────────────────────────────────────────────────────

def _fmt_coin(c: dict) -> str:
    return (f"{c['coin']} | 24h:{c['chg_24h']:+.1f}% | 거래대금:{c['vol_bil']}억 | 현재:{c['price']:,.0f}원\n"
            f"{c.get('candles', '  (없음)')}")


def ask_claude_entry(snapshot: list[dict], recent: list[dict],
                     pump_summary: str, skip_coins: set[str] | None = None) -> dict:
    now_kst = datetime.now(KST).strftime("%H:%M")
    skip_coins = skip_coins or set()
    visible = [c for c in snapshot if c["coin"] not in skip_coins]
    coins_text = "\n\n".join(_fmt_coin(c) for c in visible) or "  (후보 없음)"

    recent_text = "\n".join(
        f"  {r['coin']} {r['pnl_pct']:+.2f}% ({r['pnl_krw']:+,.0f}원) [{r['exit']}] {r['at']}"
        for r in recent
    ) or "  없음"

    skip_text = ", ".join(sorted(skip_coins)) if skip_coins else "없음"

    prompt = f"""빗썸 알트코인 단타 AI 트레이더. 지금 포지션 없음, 진입 기회 탐색 중.

현재 시각: {now_kst} KST
오늘 재진입 금지 코인: {skip_text}

=== 후보 코인 실시간 1분봉 ({len(visible)}개) ===
각 코인: 24h변화율 / 거래대금 / 현재가 + 최근 12분봉 요약 (시간 종가 거래량)

{coins_text}

=== 오늘 펌핑 감지 이력 ===
{pump_summary}

=== 최근 봇 실거래 ===
{recent_text}

=== 진입 기준 (엄격하게 적용) ===
- 24h +5~40% 코인 중 지금 이 순간 상승 모멘텀이 살아있는 코인만 선택
- A+ 셋업만 진입: 아래 조건 2개 이상 충족해야 함
    1) 최근 2캔들 연속 종가 상승 + 거래량 증가
    2) 5분 변화율 +0.5% 이상 (지금 막 움직이는 중)
    3) 현재가가 12분 고점 근처 (고점의 99% 이상) = 돌파 직전
- 조건 불충분하면 반드시 wait — 확신 없으면 대기가 정답
- 오늘 재진입 금지 코인은 절대 선택 금지
- 24h +50% 초과 과열, 하락 추세 코인 제외

JSON만 응답:
{{"action": "buy", "coin": "심볼", "reason": "충족된 조건 명시한 한 줄"}}
또는
{{"action": "wait", "reason": "한 줄"}}"""

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, encoding="utf-8",
        timeout=CLAUDE_TIMEOUT,
    )
    text = result.stdout.strip()
    if not text:
        raise ValueError(f"빈 응답 (stderr: {result.stderr[:100]})")
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                text = part
                break
    s, e = text.find("{"), text.rfind("}") + 1
    if s >= 0 and e > s:
        text = text[s:e]
    return json.loads(text)


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
        POS_PATH.write_text(
            json.dumps(pos, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ── 주문 실행 ─────────────────────────────────────────────────────────────────

def _wait_order(client: BithumbClient, uuid: str) -> dict:
    for _ in range(ORDER_WAIT_SEC):
        try:
            o = client.get_order(uuid)
            if o.get("state") in ("done", "cancel"):
                return o
        except Exception:
            pass
        time.sleep(1)
    return {}


def _coin_balance(client: BithumbClient, coin: str) -> float:
    try:
        for a in client.get_balance(coin):
            if a.get("currency") == coin.upper():
                return float(a.get("balance", 0))
    except Exception:
        pass
    return 0.0


def do_buy(client: BithumbClient, coin: str, record_only: bool) -> dict | None:
    market = f"KRW-{coin}"
    price  = get_current_price(client, coin)
    if price <= 0:
        log.warning(f"[{coin}] 가격 조회 실패")
        return None

    if record_only:
        vol = ENTRY_KRW / price
        log.info(f"[{coin}] 모의매수 @{price:,.1f}원 {vol:.4f} ({ENTRY_KRW:,}원)")
        return {"coin": coin, "market": market, "entry_price": price,
                "volume": vol, "cost_krw": ENTRY_KRW,
                "highest": price,
                "entry_ts": time.time(),
                "entered_at": datetime.now(KST).isoformat(), "mock": True}

    try:
        r    = client.market_buy(market, ENTRY_KRW)
        uuid = r.get("uuid")
        if not uuid:
            log.error(f"[{coin}] UUID 없음: {r}")
            return None
        order = _wait_order(client, uuid)
        if order.get("state") != "done":
            client.cancel_order(uuid)
            return None
        vol   = float(order.get("executed_volume", 0))
        funds = float(order.get("executed_funds",  0))
        fee   = float(order.get("paid_fee",        0))
        if vol <= 0:
            return None
        entry = funds / vol
        cost  = funds + fee
        log.info(f"[{coin}] 매수체결 @{entry:,.1f}원 {vol:.4f} ({cost:,.0f}원)")
        return {"coin": coin, "market": market, "entry_price": entry,
                "volume": vol, "cost_krw": cost,
                "highest": entry,
                "entry_ts": time.time(),
                "entered_at": datetime.now(KST).isoformat(), "mock": False}
    except Exception as e:
        log.error(f"[{coin}] 매수 실패: {e}")
        return None


def do_sell(client: BithumbClient, pos: dict, current: float,
            reason: str, record_only: bool) -> None:
    coin     = pos["coin"]
    entry    = pos["entry_price"]
    vol      = pos["volume"]
    cost_krw = pos["cost_krw"]
    pnl_pct  = (current - entry) / entry * 100

    if record_only or pos.get("mock"):
        recv     = current * vol
        pnl_krw  = recv - cost_krw
        log.info(f"[{coin}] 모의매도 @{current:,.1f}원 PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}")
        _send_tg(f"🤖 [Claude] 모의매도: <b>{coin}</b> PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)\n{reason}")
        _record(pos, current, recv, reason)
        save_pos(None)
        return

    actual = _coin_balance(client, coin)
    if actual <= 0:
        log.info(f"[{coin}] 잔고 없음 → 외부 청산")
        save_pos(None)
        return
    sell_vol = min(vol, actual)

    try:
        r    = client.market_sell(pos["market"], sell_vol)
        uuid = r.get("uuid")
        if not uuid:
            log.error(f"[{coin}] 매도 UUID 없음")
            return
        order = _wait_order(client, uuid)
        if order.get("state") != "done":
            log.warning(f"[{coin}] 매도 미체결 — 유지")
            return
        executed_vol = float(order.get("executed_volume", sell_vol) or sell_vol)
        recv     = float(order.get("executed_funds", 0)) - float(order.get("paid_fee", 0))
        fill     = float(order.get("executed_funds", 0)) / executed_vol if executed_vol else current
        pnl_pct  = (fill - entry) / entry * 100
        pnl_krw  = recv - cost_krw
        log.info(f"[{coin}] 매도체결 @{fill:,.1f}원 PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}")
        _send_tg(f"🤖 [Claude] 매도: <b>{coin}</b> PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원)\n{reason}")
        _record(pos, fill, recv, reason)
    except Exception as e:
        log.error(f"[{coin}] 매도 실패: {e}")
        return
    save_pos(None)


def _record(pos: dict, exit_price: float, recv_krw: float, reason: str) -> None:
    try:
        log_trade(
            coin=pos["coin"], market=pos["market"],
            entry_price=pos["entry_price"], exit_price=exit_price,
            volume=pos["volume"], cost_krw=pos["cost_krw"],
            received_krw=recv_krw,
            exit_reason=f"[CS] {reason}",
            entered_at=datetime.fromisoformat(pos["entered_at"]).replace(tzinfo=None),
            exited_at=datetime.now(),
            max_price=pos.get("highest", exit_price),
        )
    except Exception as e:
        log.error(f"[DB] 기록 실패: {e}")


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run() -> None:
    record_only  = _load_record_only()
    client       = BithumbClient()
    log.info(f"=== Claude 진입 스크리너 시작 | RECORD_ONLY={record_only} ===")
    log.info(f"    진입분석={ENTRY_INTERVAL}s | TP=+{FIXED_TP*100:.0f}% | "
             f"BE=+{BE_TRIGGER*100:.0f}% | SL={HARD_SL*100:.0f}%")

    last_entry_check = 0.0

    while True:
        loop_start = time.time()
        pos = load_pos()

        # ── 포지션 없음: 5분마다 Claude 분석 ────────────────────────────────
        if pos is None:
            if loop_start - last_entry_check < ENTRY_INTERVAL:
                remaining = ENTRY_INTERVAL - (loop_start - last_entry_check)
                log.info(f"[대기] 다음 진입 분석까지 {remaining:.0f}초")
                time.sleep(min(30, remaining))
                continue

            last_entry_check = loop_start
            try:
                snapshot    = get_market_snapshot(client)
                recent      = get_recent_trades()
                pump_s      = get_pump_summary()
                skip_coins  = get_today_traded_coins()
                if skip_coins:
                    log.info(f"[재진입금지] 오늘 거래 코인: {', '.join(sorted(skip_coins))}")
                log.info(f"[분석] 후보 {len(snapshot)}개 (1분봉 포함) | Claude 호출 중...")

                result = ask_claude_entry(snapshot, recent, pump_s, skip_coins)
                action = result.get("action", "wait")
                coin   = result.get("coin", "").upper()
                reason = result.get("reason", "")
                log.info(f"[Claude] action={action} coin={coin or '-'} | {reason}")

                # 워치리스트 부산물 기록 (alt_monitor 참고용)
                if snapshot:
                    WATCHLIST_PATH.write_text(
                        json.dumps({
                            "coins":      [coin] if action == "buy" and coin else [],
                            "avoid":      [],
                            "reason":     reason,
                            "updated_at": datetime.now(KST).isoformat(),
                        }, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                if action == "buy" and coin:
                    if coin in skip_coins:
                        log.warning(f"[{coin}] 오늘 이미 거래 — 재진입 차단")
                        action = "wait"

                if action == "buy" and coin:
                    new_pos = do_buy(client, coin, record_only)
                    if new_pos:
                        new_pos["reason"] = reason
                        save_pos(new_pos)
                        tag = "모의 " if record_only else ""
                        _send_tg(
                            f"🤖 [Claude 진입] {tag}<b>{coin}</b>"
                            f" @{new_pos['entry_price']:,.1f}원\n{reason}"
                        )

            except subprocess.TimeoutExpired:
                log.warning("Claude 타임아웃 — 대기")
            except json.JSONDecodeError as e:
                log.warning(f"JSON 파싱 실패: {e}")
            except Exception as e:
                log.error(f"진입 분석 오류: {e}")

        # ── 포지션 있음: 코드 규칙으로 청산 (Claude 없음) ────────────────────
        else:
            coin    = pos["coin"]
            entry   = pos["entry_price"]
            highest = pos.get("highest", entry)

            current = get_current_price(client, coin)
            if current <= 0:
                current = entry

            # 최고가 갱신
            if current > highest:
                highest = current
                pos["highest"] = highest
                save_pos(pos)

            pnl_pct = (current - entry) / entry

            # 브레이크이븐 발동 여부
            be_active = highest >= entry * (1 + BE_TRIGGER)
            sl        = entry if be_active else entry * (1 + HARD_SL)

            log.info(
                f"[{coin}] {current:,.1f}원 PnL={pnl_pct*100:+.2f}% "
                f"고점={highest:,.1f}원 SL={sl:,.1f}원"
                f"{' [BE]' if be_active else ''}"
            )

            # TP +3%
            if pnl_pct >= FIXED_TP:
                reason = f"TP +{pnl_pct*100:.1f}%"
                log.info(f"[{coin}] {reason} → 청산")
                do_sell(client, pos, current, reason, record_only)

            # SL (-2% 또는 브레이크이븐 0%)
            elif current <= sl:
                tag    = "BE" if be_active else "SL"
                reason = f"{tag} {pnl_pct*100:+.1f}%"
                log.info(f"[{coin}] {reason} → 청산")
                do_sell(client, pos, current, reason, record_only)

            time.sleep(POS_CHECK_INTERVAL)
            continue

        elapsed = time.time() - loop_start
        time.sleep(max(1, POS_CHECK_INTERVAL - elapsed))


if __name__ == "__main__":
    run()
