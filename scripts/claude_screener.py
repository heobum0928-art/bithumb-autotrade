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
        python scripts/claude_screener.py --dry-run   <- 페이퍼 트레이딩 (2분 간격, 실주문 없음)
"""
import sys
import json
import time
import sqlite3
import logging
import argparse
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
MIN_DAILY_VOL_KRW     = 20_000_000_000  # 실거래: 200억
DRY_MIN_DAILY_VOL_KRW = 3_000_000_000   # dry-run: 30억 (더 많은 후보)
MAX_CANDIDATES        = 25
CLAUDE_TIMEOUT    = 60
SKIP_COINS        = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}
DRY_BLACKLIST     = {"VTHO", "AL", "POKT"}  # 122건 분석: 승률 0~19% 불량 코인
DRY_MIN_PUMP_PCT  = 5.0   # 24h 변화율 5% 미만 코인 제외 (245건 분석: pump_pct 5%+ WIN율 62%)
DRY_BAD_HOURS_KST = {1, 5, 15, 16}  # 승률 낮은 시간대 차단

ENTRY_INTERVAL     = 300   # 진입 분석 주기 (5분, 실거래)
DRY_ENTRY_INTERVAL = 15    # 진입 분석 주기 (15초, 페이퍼 트레이딩 — 데이터 최대 수집)
POS_CHECK_INTERVAL = 10    # 포지션 체크 주기 (10초)
ENTRY_KRW          = 15_000
ORDER_WAIT_SEC     = 15

DRY_MAX_CANDIDATES  = 35        # 페이퍼 트레이딩용 확대 후보 수
DRY_MAX_POSITIONS   = 8         # 최대 동시 보유 포지션
DRY_ENTRY_KRW       = 125_000   # 1회 진입금액 (12.5만원 × 8 = 100만원)
DRY_INITIAL_BALANCE = 1_000_000 # 가상 시작 자본 (100만원)
DRY_STATE_PATH      = Path("data/claude_dry_state.json")

# 청산 규칙 (코드 고정)
BE_TRIGGER        = 0.01   # 브레이크이븐 발동 +1%
FIXED_TP          = 0.03   # 고정 익절 +3%
HARD_SL           = -0.02  # 손절 -2% (브레이크이븐 미발동 구간)

Path("logs").mkdir(exist_ok=True)

# dry-run 여부는 argparse로 결정, 로그 파일도 분리
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--dry-run",    action="store_true")
    p.add_argument("--watch-mode", action="store_true")
    args, _ = p.parse_known_args()
    return args

_args = _parse_args()
_DRY_RUN    = _args.dry_run
_WATCH_MODE = _args.watch_mode
_LOG_TAG  = "CS-WATCH" if _WATCH_MODE else ("CS-DRY" if _DRY_RUN else "CS")
_LOG_FILE = ("logs/claude_screener_watch.log" if _WATCH_MODE
             else ("logs/claude_screener_dry.log" if _DRY_RUN
                   else "logs/claude_screener.log"))
_POS_PATH = Path("data/claude_pos_dry.json") if (_DRY_RUN or _WATCH_MODE) else Path("data/claude_pos.json")

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{_LOG_TAG}][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(
            open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        ),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
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

def _candle_summary(client: BithumbClient, coin: str) -> tuple[str, float]:
    """최근 12개 1분봉을 Claude용 compact 텍스트 + 5분변화율로 반환."""
    try:
        raw = client.get_candles(f"KRW-{coin}", unit=1, count=12)
        if not raw:
            return "  (캔들 없음)", 0.0
        candles = list(reversed(raw))  # 오래된→최신 순
        closes = [c["trade_price"] for c in candles]
        vols   = [c["candle_acc_trade_volume"] for c in candles]
        times  = [c["candle_date_time_kst"][11:16] for c in candles]

        # 거래량 추세: 최근 3개 vs 이전 3개 (v_old=0 방어)
        v_new = sum(vols[-3:]) / 3
        v_old = sum(vols[-6:-3]) / 3 if len(vols) >= 6 else v_new
        if v_old == 0:
            vol_trend = "거래량→(이전없음)"
        elif v_new > v_old * 1.4: vol_trend = "거래량↑↑급증"
        elif v_new > v_old * 1.1: vol_trend = "거래량↑증가"
        elif v_new < v_old * 0.6: vol_trend = "거래량↓↓급감"
        elif v_new < v_old * 0.9: vol_trend = "거래량↓감소"
        else:                      vol_trend = "거래량→유지"

        # 가격 방향: 캔들 4개 이상일 때만 비교
        if len(closes) >= 4:
            if   closes[-1] > closes[-4] * 1.005: price_dir = "가격↑상승중"
            elif closes[-1] < closes[-4] * 0.995: price_dir = "가격↓하락중"
            else:                                  price_dir = "가격→횡보"
        else:
            price_dir = "가격→(데이터부족)"

        # 마지막 5개 캔들 표시
        rows = [f"  {t} {c:,.0f}원 {v/1e6:.2f}M"
                for t, c, v in zip(times[-5:], closes[-5:], vols[-5:])]
        high12 = max(closes)
        low12  = min(closes)
        chg5m  = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0

        text = (f"  [{price_dir} / {vol_trend}] 5분변화:{chg5m:+.2f}% "
                f"12분고점:{high12:,.0f} 저점:{low12:,.0f}\n" +
                "\n".join(rows))
        return text, chg5m
    except Exception:
        return "  (캔들 조회 실패)", 0.0


def get_market_snapshot(client: BithumbClient) -> list[dict]:
    tickers = client.get_ticker("ALL")
    coins = []
    for coin, data in tickers.items():
        if coin == "date" or coin in SKIP_COINS:
            continue
        if _DRY_RUN and coin in DRY_BLACKLIST:
            continue
        chg_24h = round(float(data.get("fluctate_rate_24H") or 0), 1)
        if _DRY_RUN and chg_24h < DRY_MIN_PUMP_PCT:
            continue
        vol = float(data.get("acc_trade_value_24H", 0))
        vol_threshold = DRY_MIN_DAILY_VOL_KRW if _DRY_RUN else MIN_DAILY_VOL_KRW
        if vol < vol_threshold:
            continue
        coins.append({
            "coin":    coin,
            "chg_24h": chg_24h,
            "vol_bil": round(vol / 1e8, 1),
            "price":   float(data.get("closing_price") or 0),
        })
    coins.sort(key=lambda x: -x["chg_24h"])
    limit = DRY_MAX_CANDIDATES if _DRY_RUN else MAX_CANDIDATES
    top = coins[:limit]

    # 각 후보 코인 1분봉 캔들 추가
    for c in top:
        c["candles"], c["chg5m"] = _candle_summary(client, c["coin"])
    return top


def get_current_price(client: BithumbClient, coin: str) -> float:
    try:
        return float(client.get_ticker(coin).get("closing_price", 0))
    except Exception:
        return 0.0


def get_btc_trend(client: BithumbClient) -> tuple[float, str]:
    """BTC 5분 변화율과 방향 반환. 실패 시 (0.0, '알수없음')."""
    try:
        resp = requests.get(
            "https://api.bithumb.com/v1/candles/minutes/1",
            params={"market": "KRW-BTC", "count": 6},
            timeout=5,
        )
        candles = resp.json()
        if not isinstance(candles, list) or len(candles) < 2:
            return 0.0, "알수없음"
        latest = candles[0]["trade_price"]
        prev5  = candles[-1]["opening_price"]
        chg = (latest - prev5) / prev5 * 100
        direction = "상승" if chg > 0.3 else ("하락" if chg < -0.3 else "횡보")
        return round(chg, 2), direction
    except Exception:
        return 0.0, "알수없음"


def get_recent_trades(n: int = 10) -> list[dict]:
    tag = _LOG_TAG  # CS-DRY, CS-WATCH, CS 중 현재 모드만
    try:
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute("""
            SELECT coin, ROUND(pnl_krw,0), ROUND(pnl_pct,2), exit_reason, entered_at
            FROM trades WHERE exit_reason LIKE ? ORDER BY id DESC LIMIT ?
        """, (f"%{tag}%", n))
        rows = cur.fetchall()
        con.close()
        return [{"coin": r[0], "pnl_krw": r[1], "pnl_pct": r[2],
                 "exit": str(r[3])[:40], "at": str(r[4])[11:16]}
                for r in rows]
    except Exception:
        return []


def get_today_traded_coins() -> set[str]:
    """오늘 이미 거래된 코인 목록 (재진입 금지용, 현재 모드만)."""
    try:
        today = datetime.now(KST).date().isoformat()
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        cur.execute(
            "SELECT DISTINCT coin FROM trades WHERE entered_at >= ? AND exit_reason LIKE ?",
            (today, f"%{_LOG_TAG}%"),
        )
        coins = {r[0] for r in cur.fetchall()}
        con.close()
        return coins
    except Exception as e:
        log.warning(f"[재진입금지] DB 조회 실패, 차단 목록 없음: {e}")
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
                     pump_summary: str, skip_coins: set[str] | None = None,
                     dry_run: bool = False, btc_chg: float = 0.0, btc_dir: str = "알수없음") -> dict:
    now_kst = datetime.now(KST).strftime("%H:%M")
    skip_coins = skip_coins or set()
    visible = [c for c in snapshot if c["coin"] not in skip_coins]
    coins_text = "\n\n".join(_fmt_coin(c) for c in visible) or "  (후보 없음)"

    recent_text = "\n".join(
        f"  {r['coin']} {r['pnl_pct']:+.2f}% ({r['pnl_krw']:+,.0f}원) [{r['exit']}] {r['at']}"
        for r in recent
    ) or "  없음"

    skip_text = ", ".join(sorted(skip_coins)) if skip_coins else "없음"

    if dry_run:
        btc_warn = f"\n- BTC {btc_dir}({btc_chg:+.2f}%) — BTC 하락 중이므로 진입 더 신중하게" if btc_chg < -0.5 else ""
        criteria = f"""=== 진입 기준 (실조건 적용) ===
- 24h +5% 이상 코인 중 지금 이 순간 상승 모멘텀이 살아있는 코인만 선택
- 아래 조건 2개 이상 충족해야 진입:
    1) 5분 변화율 +0.5% 이상 (지금 막 움직이는 중)
    2) 최근 2캔들 연속 종가 상승 + 거래량 증가 (거래량 3x↑↑ 우선)
    3) 현재가가 12분 고점 근처 (고점의 99% 이상)
- 조건 불충분하면 반드시 wait — 확신 없으면 대기가 정답
- 24h +50% 초과 과열 코인 제외{btc_warn}"""
    else:
        criteria = """=== 진입 기준 (엄격하게 적용) ===
- 24h +5~40% 코인 중 지금 이 순간 상승 모멘텀이 살아있는 코인만 선택
- A+ 셋업만 진입: 아래 조건 2개 이상 충족해야 함
    1) 최근 2캔들 연속 종가 상승 + 거래량 증가
    2) 5분 변화율 +0.5% 이상 (지금 막 움직이는 중)
    3) 현재가가 12분 고점 근처 (고점의 99% 이상) = 돌파 직전
- 조건 불충분하면 반드시 wait — 확신 없으면 대기가 정답
- 24h +50% 초과 과열, 하락 추세 코인 제외"""

    btc_line = f"BTC 5분 변화율: {btc_chg:+.2f}% ({btc_dir})"

    prompt = f"""빗썸 알트코인 단타 AI 트레이더. 지금 포지션 없음, 진입 기회 탐색 중.

현재 시각: {now_kst} KST
{btc_line}
오늘 재진입 금지 코인: {skip_text}

=== 후보 코인 실시간 1분봉 ({len(visible)}개) ===
각 코인: 24h변화율 / 거래대금 / 현재가 + 최근 12분봉 요약 (시간 종가 거래량)

{coins_text}

=== 오늘 펌핑 감지 이력 ===
{pump_summary}

=== 최근 봇 실거래 ===
{recent_text}

{criteria}
- 오늘 재진입 금지 코인은 절대 선택 금지

JSON만 응답:
{{"action": "buy", "coin": "심볼", "reason": "충족된 조건 명시한 한 줄"}}
또는
{{"action": "wait", "reason": "한 줄"}}"""

    model_args = ["--model", "claude-haiku-4-5-20251001"]
    result = subprocess.run(
        ["claude"] + model_args + ["-p", prompt],
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
        if _POS_PATH.exists():
            return json.loads(_POS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def save_pos(pos: dict | None) -> None:
    if pos is None:
        _POS_PATH.unlink(missing_ok=True)
    else:
        _POS_PATH.write_text(
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
            exit_reason=f"[{_LOG_TAG}] {reason}",
            entered_at=datetime.fromisoformat(pos["entered_at"]).replace(tzinfo=None),
            exited_at=datetime.now(),
            max_price=pos.get("highest", exit_price),
            entry_chg24h=pos.get("entry_chg24h"),
            claude_reason=pos.get("reason"),
            entry_chg5m=pos.get("entry_chg5m"),
            entry_btc_chg=pos.get("entry_btc_chg"),
            entry_n_pos=pos.get("entry_n_pos"),
        )
    except Exception as e:
        log.error(f"[DB] 기록 실패: {e}")


# ── 페이퍼 트레이딩 상태 관리 ─────────────────────────────────────────────────

def load_dry_state() -> dict:
    try:
        if DRY_STATE_PATH.exists():
            return json.loads(DRY_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"balance": DRY_INITIAL_BALANCE, "positions": []}


def save_dry_state(state: dict) -> None:
    DRY_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 페이퍼 트레이딩 루프 ──────────────────────────────────────────────────────

def run_dry() -> None:
    client = BithumbClient()
    log.info(f"=== CS DRY-RUN 시작 | 가상자본 {DRY_INITIAL_BALANCE:,}원 | "
             f"최대 {DRY_MAX_POSITIONS}포지션 | 1회 {DRY_ENTRY_KRW:,}원 ===")
    log.info(f"    진입분석={DRY_ENTRY_INTERVAL}s | TP=+{FIXED_TP*100:.0f}% | "
             f"BE=+{BE_TRIGGER*100:.0f}% | SL={HARD_SL*100:.0f}%")
    # dry-run TG 알림 비활성화 (데이터 수집용, Watch Mode만 알림)

    last_entry_check = 0.0
    last_status_tg   = 0.0   # 주기적 텔레그램 상태 알림용

    while True:
        loop_start = time.time()
        state     = load_dry_state()
        balance   = state["balance"]
        positions = list(state["positions"])  # 복사본 — dirty 감지 가능

        # ── 1. 모든 포지션 모니터링 및 청산 ─────────────────────────────────
        closed_any = False
        dirty      = False
        for pos in list(positions):
            coin    = pos["coin"]
            entry   = pos["entry_price"]
            highest = pos.get("highest", entry)

            current = get_current_price(client, coin)
            if current <= 0:
                current = entry

            if current > highest:
                highest = current
                pos["highest"] = highest
                dirty = True

            pnl_pct   = (current - entry) / entry
            be_active = pos.get("be_active", False) or highest >= entry * (1 + BE_TRIGGER)
            if be_active and not pos.get("be_active"):
                pos["be_active"] = True  # 한번 발동하면 재시작 후에도 유지
                dirty = True
            sl        = entry if be_active else entry * (1 + HARD_SL)

            exit_reason = None
            exit_price  = current
            if pnl_pct >= FIXED_TP:
                exit_price  = entry * (1 + FIXED_TP)
                exit_reason = f"TP {pnl_pct*100:+.1f}%"
            elif current <= sl:
                exit_price  = sl
                tag         = "BE" if be_active else "SL"
                exit_reason = f"{tag} {pnl_pct*100:+.1f}%"

            if exit_reason:
                recv    = exit_price * pos["volume"]
                pnl_krw = recv - pos["cost_krw"]
                balance += recv
                log.info(f"[DRY {coin}] {exit_reason} PnL={pnl_pct*100:+.2f}% "
                         f"({pnl_krw:+,.0f}원) | 잔고={balance:,.0f}원")
                _record(pos, exit_price, recv, exit_reason)
                positions.remove(pos)
                closed_any = True
            else:
                log.info(f"[DRY {coin}] {current:,.1f}원 PnL={pnl_pct*100:+.2f}%"
                         f"{' [BE]' if be_active else ''}")

        if closed_any or dirty:
            state["balance"]   = balance
            state["positions"] = positions
            save_dry_state(state)

        # ── 2. 새 진입 탐색 (예산 + 포지션 수 여유 있을 때) ──────────────────
        now_kst_hour = datetime.now(KST).hour
        bad_hour = _DRY_RUN and now_kst_hour in DRY_BAD_HOURS_KST
        can_enter = (len(positions) < DRY_MAX_POSITIONS
                     and balance >= DRY_ENTRY_KRW
                     and loop_start - last_entry_check >= DRY_ENTRY_INTERVAL
                     and not bad_hour)

        if can_enter:
            last_entry_check = loop_start
            try:
                snapshot   = get_market_snapshot(client)
                recent     = get_recent_trades()
                pump_s     = get_pump_summary()
                held_coins = {p["coin"] for p in positions}
                skip_coins = held_coins
                btc_chg, btc_dir = get_btc_trend(client)
                log.info(f"[DRY] 포지션 {len(positions)}/{DRY_MAX_POSITIONS} | "
                         f"잔고 {balance:,.0f}원 | BTC {btc_dir}({btc_chg:+.2f}%) | Claude 호출...")
                result = ask_claude_entry(snapshot, recent, pump_s, skip_coins,
                                          dry_run=True, btc_chg=btc_chg, btc_dir=btc_dir)
                action = result.get("action", "wait")
                coin   = result.get("coin", "").upper()
                reason = result.get("reason", "")
                log.info(f"[DRY Claude] {action} {coin or '-'} | {reason}")

                if action == "buy" and coin and coin not in skip_coins:
                    price = get_current_price(client, coin)
                    if price > 0 and balance >= DRY_ENTRY_KRW:
                        vol = DRY_ENTRY_KRW / price
                        coin_data = next((c for c in snapshot if c["coin"] == coin), {})
                        new_pos = {
                            "coin": coin, "market": f"KRW-{coin}",
                            "entry_price": price, "volume": vol,
                            "cost_krw": DRY_ENTRY_KRW, "highest": price,
                            "entry_ts": time.time(),
                            "entered_at": datetime.now(KST).isoformat(),
                            "mock": True, "reason": reason,
                            "entry_chg24h": coin_data.get("chg_24h"),
                            "entry_chg5m":  coin_data.get("chg5m"),
                            "entry_btc_chg": btc_chg,
                            "entry_n_pos":  len(positions),
                        }
                        balance   -= DRY_ENTRY_KRW
                        positions.append(new_pos)
                        state["balance"]   = balance
                        state["positions"] = positions
                        save_dry_state(state)
                        log.info(f"[DRY {coin}] 모의매수 @{price:,.1f}원 | 잔고={balance:,.0f}원")

            except subprocess.TimeoutExpired:
                log.warning("[DRY] Claude 타임아웃")
            except json.JSONDecodeError as e:
                log.warning(f"[DRY] JSON 파싱 실패: {e}")
            except Exception as e:
                log.error(f"[DRY] 진입 분석 오류: {e}")
        elif not can_enter and not positions:
            remaining = DRY_ENTRY_INTERVAL - (loop_start - last_entry_check)
            log.info(f"[DRY 대기] {remaining:.0f}초 후 분석 | 잔고={balance:,.0f}원")

        # 30분마다 텔레그램 상태 요약
        now_ts = time.time()
        if now_ts - last_status_tg >= 1800:
            last_status_tg = now_ts
            pos_lines = "\n".join(
                f"  {p['coin']} @{p['entry_price']:,.0f}원 "
                f"PnL={((get_current_price(client, p['coin']) or p['entry_price']) - p['entry_price']) / p['entry_price'] * 100:+.1f}%"
                for p in positions
            ) or "  없음"
            log.info(f"[DRY 현황] 잔고:{balance:,.0f}원 포지션{len(positions)}/{DRY_MAX_POSITIONS}")

        elapsed = time.time() - loop_start
        time.sleep(max(1, POS_CHECK_INTERVAL - elapsed))


# ── Watch Mode — Claude 워치리스트 + 코드 즉시 진입 ──────────────────────────

WATCH_INTERVAL    = 180   # Claude 워치리스트 갱신 주기 (3분)
WATCH_MAX_POS     = 5
WATCH_ENTRY_KRW   = 200_000
WATCH_STATE_PATH  = Path("data/claude_watch_state.json")

# 코드 기반 즉시 진입 조건
WATCH_CHG1M_MIN   = 0.3   # 1분 변화율 최소 (%)
WATCH_VOL_MULT    = 1.5   # 거래량 배수 최소
WATCH_TICK_RATIO  = 0.55  # 체결강도 최소 (매수 55% 이상)
WATCH_ACTIVE_HOURS = {18, 19, 20}  # 진입 허용 시간대 KST (74건 분석: 18시 67%, 20시 50%)
WATCH_TRAIL_PCT   = 0.015 # 트레일링 스탑 (-1.5% from high)


def _get_recent_watch_results(hours: int = 2) -> str:
    """최근 N시간 CS-WATCH 거래 결과 요약 (Claude 피드백용)."""
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        con = sqlite3.connect(str(DB_PATH))
        rows = con.execute("""
            SELECT coin, pnl_pct, exit_reason
            FROM trades
            WHERE exit_reason LIKE '%CS-WATCH%' AND exited_at >= ?
            ORDER BY exited_at DESC LIMIT 15
        """, (cutoff,)).fetchall()
        con.close()
        if not rows:
            return "  (최근 거래 없음)"
        lines = []
        for coin, pnl, reason in rows:
            tag = "TP" if "TP" in (reason or "") else ("SL" if "SL" in (reason or "") else "BE")
            mark = "+" if tag == "TP" else ("-" if tag == "SL" else "~")
            lines.append(f"  {mark} {coin}: {tag} ({(pnl or 0):+.1f}%)")
        return "\n".join(lines)
    except Exception:
        return "  (조회 실패)"


def ask_claude_watchlist(snapshot: list[dict], btc_dir: str) -> list[str]:
    """전체 시장 분석 후 다음 3분 내 주목할 코인 3개 반환."""
    top10 = snapshot[:10]
    coins_text = "\n".join(
        f"{c['coin']} | 24h:{c['chg_24h']:+.1f}% | 거래대금:{c['vol_bil']}억 | {c.get('candles','')[:80]}"
        for c in top10
    )
    recent_results = _get_recent_watch_results(hours=2)

    prompt = f"""빗썸 알트코인 시장 분석. BTC 방향: {btc_dir}

=== 최근 2시간 내 추천 결과 (학습용) ===
{recent_results}
+ = TP 성공 / - = SL 손절 / ~ = BE 본전
손절 잦은 코인은 피하고, 성공 패턴 있는 코인 우선 고려.

=== 현재 후보 코인 ===
{coins_text}

다음 3분 내 모멘텀이 발생할 가능성이 높은 코인 3개를 골라라.
조건: 상승 준비 중인 코인 (아직 크게 안 올랐지만 곧 움직일 것 같은)
최근 손절 많은 코인은 제외할 것.

JSON만 응답:
{{"watchlist": ["심볼1", "심볼2", "심볼3"], "reason": "한 줄 이유"}}"""

    model_args = ["--model", "claude-haiku-4-5-20251001"]
    result = subprocess.run(
        ["claude"] + model_args + ["-p", prompt],
        capture_output=True, text=True, encoding="utf-8",
        timeout=CLAUDE_TIMEOUT,
    )
    try:
        text  = result.stdout.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        data  = json.loads(text[start:end])
        return [c.upper() for c in data.get("watchlist", [])][:3]
    except Exception:
        return []


def _get_vol_mult(client: BithumbClient, coin: str) -> float:
    """현재 1분봉 거래량 / 직전 3분 평균 배수."""
    try:
        resp = requests.get(
            "https://api.bithumb.com/v1/candles/minutes/1",
            params={"market": f"KRW-{coin}", "count": 5},
            timeout=5,
        )
        candles = resp.json()
        if not isinstance(candles, list) or len(candles) < 4:
            return 0.0
        cur_vol = candles[0]["candle_acc_trade_volume"]
        avg_vol = sum(c["candle_acc_trade_volume"] for c in candles[1:4]) / 3
        return cur_vol / avg_vol if avg_vol > 0 else 0.0
    except Exception:
        return 0.0


def _get_tick_ratio(client: BithumbClient, coin: str) -> float:
    """최근 20건 체결 중 매수 비율 반환. 실패 시 1.0 (통과)."""
    try:
        txs = client.get_transaction_history(coin, count=20)
        if not txs:
            return 1.0
        buys = sum(1 for t in txs if t.get("type") == "bid")
        return buys / len(txs)
    except Exception:
        return 1.0


def _get_chg1m(client: BithumbClient, coin: str) -> float:
    """최근 1분 변화율 (%)."""
    try:
        resp = requests.get(
            "https://api.bithumb.com/v1/candles/minutes/1",
            params={"market": f"KRW-{coin}", "count": 2},
            timeout=5,
        )
        candles = resp.json()
        if not isinstance(candles, list) or len(candles) < 2:
            return 0.0
        cur   = candles[0]["trade_price"]
        prev  = candles[1]["trade_price"]
        return (cur - prev) / prev * 100 if prev > 0 else 0.0
    except Exception:
        return 0.0


def load_watch_state() -> dict:
    try:
        if WATCH_STATE_PATH.exists():
            return json.loads(WATCH_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"balance": DRY_INITIAL_BALANCE, "positions": []}


def save_watch_state(state: dict) -> None:
    WATCH_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_watch() -> None:
    client = BithumbClient()
    log.info("=== CS WATCH MODE 시작 | Claude 워치리스트 + 코드 즉시 진입 ===")
    _send_tg("👁 <b>[Watch Mode 시작]</b> Claude 5분 분석 + 코드 즉시 진입")

    watchlist: list[str] = []
    last_watch  = 0.0
    last_status = 0.0
    snapshot: list[dict] = []
    btc_chg = 0.0

    while True:
        loop_start = time.time()
        state     = load_watch_state()
        balance   = state["balance"]
        positions = list(state["positions"])

        # ── 1. 포지션 청산 모니터링 ──────────────────────────────────────────
        closed_any = False
        dirty      = False
        for pos in list(positions):
            coin    = pos["coin"]
            entry   = pos["entry_price"]
            highest = pos.get("highest", entry)

            current = get_current_price(client, coin)
            if current <= 0:
                continue

            if current > highest:
                highest = current
                pos["highest"] = highest
                dirty = True

            pnl_pct   = (current - entry) / entry
            be_active = pos.get("be_active", False) or highest >= entry * (1 + BE_TRIGGER)
            if be_active and not pos.get("be_active"):
                pos["be_active"] = True
                dirty = True

            # 트레일링 스탑: BE 발동 후 고점 대비 -1.5% 추적 (고정 TP 없음)
            if be_active:
                sl = highest * (1 - WATCH_TRAIL_PCT)
            else:
                sl = entry * (1 + HARD_SL)

            exit_reason = None
            exit_price  = current
            if current <= sl:
                exit_price  = sl
                if not be_active:
                    exit_reason = f"SL {pnl_pct*100:+.1f}%"
                else:
                    tag = "TRAIL" if highest > entry * 1.03 else "BE"
                    exit_reason = f"{tag} {pnl_pct*100:+.1f}%"

            if exit_reason:
                recv    = exit_price * pos["volume"]
                pnl_krw = recv - pos["cost_krw"]
                balance += recv
                log.info(f"[WATCH {coin}] {exit_reason} {pnl_krw:+,.0f}원 | 잔고={balance:,.0f}원")
                _send_tg(f"👁 [WATCH] <b>{coin}</b> {exit_reason} {pnl_krw:+,.0f}원")
                _record(pos, exit_price, recv, exit_reason)
                positions.remove(pos)
                closed_any = True
            else:
                log.info(f"[WATCH {coin}] {current:,.1f}원 {pnl_pct*100:+.2f}%"
                         f"{' [BE]' if be_active else ''}")

        if closed_any or dirty:
            state["balance"]   = balance
            state["positions"] = positions
            save_watch_state(state)

        # ── 2. Claude 워치리스트 갱신 (5분마다) ──────────────────────────────
        if loop_start - last_watch >= WATCH_INTERVAL:
            last_watch = loop_start
            try:
                snapshot  = get_market_snapshot(client)
                btc_chg, btc_dir = get_btc_trend(client)
                new_list = ask_claude_watchlist(snapshot, btc_dir)
                if new_list:
                    watchlist = new_list
                    log.info(f"[WATCH] 워치리스트 갱신: {watchlist} | BTC {btc_dir}")
                    _send_tg(f"👁 워치리스트: {', '.join(watchlist)}")
            except Exception as e:
                log.warning(f"[WATCH] 워치리스트 갱신 실패: {e}")

        # ── 3. 워치리스트 코인 즉시 진입 체크 (코드만) ───────────────────────
        now_kst_h = datetime.now(KST).hour
        in_active = now_kst_h in WATCH_ACTIVE_HOURS
        if not in_active and watchlist:
            log.debug(f"[WATCH] 비활성 시간대({now_kst_h}시) — 진입 차단")
        if watchlist and in_active and len(positions) < WATCH_MAX_POS and balance >= WATCH_ENTRY_KRW:
            held = {p["coin"] for p in positions}
            for coin in watchlist:
                if coin in held:
                    continue
                chg1m      = _get_chg1m(client, coin)
                vol_mult   = _get_vol_mult(client, coin)
                tick_ratio = _get_tick_ratio(client, coin)
                if (chg1m >= WATCH_CHG1M_MIN
                        and vol_mult >= WATCH_VOL_MULT
                        and tick_ratio >= WATCH_TICK_RATIO):
                    price = get_current_price(client, coin)
                    if price > 0 and balance >= WATCH_ENTRY_KRW:
                        vol = WATCH_ENTRY_KRW / price
                        coin_data = next((c for c in snapshot if c["coin"] == coin), None)
                        new_pos = {
                            "coin": coin, "market": f"KRW-{coin}",
                            "entry_price": price, "volume": vol,
                            "cost_krw": WATCH_ENTRY_KRW, "highest": price,
                            "entered_at": datetime.now(KST).isoformat(),
                            "entry_chg1m": round(chg1m, 2),
                            "entry_vol_mult": round(vol_mult, 2),
                            "reason": f"chg1m={chg1m:+.2f}% vol={vol_mult:.1f}x tick={tick_ratio*100:.0f}%",
                            "entry_chg24h": coin_data.get("chg_24h") if coin_data else None,
                            "entry_chg5m":  coin_data.get("chg5m") if coin_data else None,
                            "entry_btc_chg": btc_chg,
                            "entry_n_pos":  len(positions),
                        }
                        balance -= WATCH_ENTRY_KRW
                        positions.append(new_pos)
                        state["balance"]   = balance
                        state["positions"] = positions
                        save_watch_state(state)
                        log.info(f"[WATCH {coin}] 즉시진입 @{price:,.1f}원 "
                                 f"chg1m={chg1m:+.2f}% vol={vol_mult:.1f}x tick={tick_ratio*100:.0f}% | 잔고={balance:,.0f}원")
                        _send_tg(f"👁 [진입] <b>{coin}</b> @{price:,.1f}원 "
                                 f"| 1분{chg1m:+.2f}% 거래량{vol_mult:.1f}x")
                        held.add(coin)

        # 30분 텔레그램 상태
        if time.time() - last_status >= 1800:
            last_status = time.time()
            pos_lines = "\n".join(
                f"  {p['coin']} {((get_current_price(client,p['coin']) or p['entry_price'])-p['entry_price'])/p['entry_price']*100:+.1f}%"
                for p in positions
            ) or "  없음"
            _send_tg(f"👁 <b>[Watch 현황]</b>\n잔고:{balance:,.0f}원 | 포지션{len(positions)}/{WATCH_MAX_POS}\n"
                     f"워치리스트:{', '.join(watchlist)}\n{pos_lines}")

        elapsed = time.time() - loop_start
        time.sleep(max(1, POS_CHECK_INTERVAL - elapsed))


# ── 실거래 루프 ────────────────────────────────────────────────────────────────

def run() -> None:
    if _WATCH_MODE:
        run_watch()
        return
    if _DRY_RUN:
        run_dry()
        return

    record_only = _load_record_only()
    client      = BithumbClient()
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
                snapshot   = get_market_snapshot(client)
                recent     = get_recent_trades()
                pump_s     = get_pump_summary()
                skip_coins = get_today_traded_coins()
                if skip_coins:
                    log.info(f"[재진입금지] 오늘 거래 코인: {', '.join(sorted(skip_coins))}")
                log.info(f"[분석] 후보 {len(snapshot)}개 | Claude 호출 중...")

                result = ask_claude_entry(snapshot, recent, pump_s, skip_coins)
                action = result.get("action", "wait")
                coin   = result.get("coin", "").upper()
                reason = result.get("reason", "")
                log.info(f"[Claude] action={action} coin={coin or '-'} | {reason}")

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
                    coin_data = next((c for c in snapshot if c["coin"] == coin), None)
                    chg5m = coin_data["chg5m"] if coin_data else 0.0
                    if chg5m < 0.3:
                        log.info(f"[{coin}] 5분변화율 {chg5m:+.2f}% < +0.3% — 모멘텀 없음, 차단")
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

            if current > highest:
                highest = current
                pos["highest"] = highest
                save_pos(pos)

            pnl_pct   = (current - entry) / entry
            be_active = highest >= entry * (1 + BE_TRIGGER)
            sl        = entry if be_active else entry * (1 + HARD_SL)

            log.info(
                f"[{coin}] {current:,.1f}원 PnL={pnl_pct*100:+.2f}% "
                f"고점={highest:,.1f}원 SL={sl:,.1f}원"
                f"{' [BE]' if be_active else ''}"
            )

            if pnl_pct >= FIXED_TP:
                reason = f"TP +{pnl_pct*100:.1f}%"
                log.info(f"[{coin}] {reason} → 청산")
                do_sell(client, pos, current, reason, record_only)
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
