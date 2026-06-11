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
CI_TAG          = "CS-CID"

SCAN_INTERVAL   = 300    # 5분마다 Claude 분석
CHECK_INTERVAL  = 10     # 10초마다 포지션 체크
ENTRY_KRW       = 500_000
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

def collect_web_sentiment() -> str:
    """Fear & Greed 지수 + CoinGecko 트렌딩/글로벌 수집."""
    lines = ["=== 시장 센티멘트 ==="]

    # Fear & Greed Index
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        lines.append(f"Fear & Greed: {d['value']}/100 ({d['value_classification']})")
    except Exception:
        lines.append("Fear & Greed: 조회불가")

    # CoinGecko 글로벌 시장 데이터
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=8)
        g = r.json()["data"]
        btc_dom = g["market_cap_percentage"].get("btc", 0)
        chg_24h = g["market_cap_change_percentage_24h_usd"]
        lines.append(f"글로벌 24h: {chg_24h:+.2f}% | BTC 도미넌스: {btc_dom:.1f}%")
    except Exception:
        lines.append("글로벌 시장: 조회불가")

    # CoinGecko 트렌딩 코인 (상위 5개)
    try:
        r = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=8)
        coins = r.json()["coins"][:5]
        symbols = ", ".join(c["item"]["symbol"].upper() for c in coins)
        lines.append(f"CoinGecko 트렌딩: {symbols}")
    except Exception:
        lines.append("트렌딩: 조회불가")

    return "\n".join(lines)


def get_current_price(client: BithumbClient, coin: str) -> float:
    try:
        t = client.get_ticker(coin) or {}
        return float(t.get("closing_price") or 0)
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

    # 웹 센티멘트 (Fear & Greed + Reddit)
    lines.append("")
    lines.append(collect_web_sentiment())

    return "\n".join(lines)


_ROOT = Path(__file__).parent.parent
_CLAUDE_CWD = str(_ROOT.parent)  # c:\code\ — CLAUDE.md 없는 상위 디렉토리


def _run_claude(prompt: str) -> str:
    """claude CLI 호출 (Haiku — 토큰 절약). cwd를 프로젝트 밖으로 설정해 CLAUDE.md 미로드."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-haiku-4-5-20251001"],
        capture_output=True, text=True, encoding="utf-8",
        timeout=CLAUDE_TIMEOUT, cwd=_CLAUDE_CWD,
    )
    return result.stdout.strip()


def _run_claude_sonnet(prompt: str) -> str:
    """Judge용 — Haiku로 통일."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-haiku-4-5-20251001"],
        capture_output=True, text=True, encoding="utf-8",
        timeout=CLAUDE_TIMEOUT, cwd=_CLAUDE_CWD,
    )
    return result.stdout.strip()


def _extract_json(text: str) -> dict | None:
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1:
        return None
    try:
        return json.loads(text[start:end])
    except Exception:
        return None


def ask_claude_debate(market_data: str) -> dict:
    """Bull vs Bear 토론 후 Judge 최종 판단."""
    now_kst = datetime.now(KST).strftime("%H:%M")

    # ── 1단계: Bull (강세 분석가) ───────────────────────────────────────
    bull_prompt = f"""너는 강세 암호화폐 트레이더다. 지금 시각: {now_kst} KST

{market_data}

아래 코인 중 지금 당장 매수할 만한 코인 1개를 골라 적극적으로 추천해라.
- 바이낸스 선행 + 빗썸 모멘텀 + 펀딩율 조합으로 판단
- TP/SL도 제안해 (TP 5~15%, SL 3~8%)

한 문단으로 추천 이유 설명 후, 마지막 줄에 JSON:
{{"coin": "심볼", "tp_pct": 8, "sl_pct": 4, "reason": "한 줄 요약"}}
진입할 코인이 없으면: {{"coin": null, "reason": "이유"}}"""

    try:
        bull_text = _run_claude(bull_prompt)
        log.info(f"[CI][Bull] {bull_text[:120]}")
    except Exception as e:
        log.warning(f"Bull 호출 실패: {e}")
        return {"action": "wait", "reason": f"Bull 오류: {e}"}

    bull_json = _extract_json(bull_text)
    if not bull_json or not bull_json.get("coin"):
        log.info(f"[CI][Bull] 추천 없음 — wait")
        return {"action": "wait", "reason": bull_json.get("reason", "Bull 추천 없음") if bull_json else "Bull 응답 파싱 실패"}

    # ── 2단계: Bear (약세 분석가) ───────────────────────────────────────
    bear_prompt = f"""너는 신중한 약세 분석가다. 지금 시각: {now_kst} KST

{market_data}

강세 분석가가 {bull_json['coin']} 매수를 추천했다:
"{bull_json.get('reason', '')}"

이 판단의 위험성과 반론을 구체적으로 제시해라.
- 펀딩율, 과열 신호, 최근 손절 패턴, 시장 방향 등을 근거로
- 한 문단으로 반론 후, 마지막 줄에 JSON:
{{"risk_level": "높음/중간/낮음", "main_risk": "핵심 리스크 한 줄"}}"""

    try:
        bear_text = _run_claude(bear_prompt)
        log.info(f"[CI][Bear] {bear_text[:120]}")
    except Exception as e:
        log.warning(f"Bear 호출 실패: {e}")
        bear_text = ""

    bear_json = _extract_json(bear_text) or {"risk_level": "중간", "main_risk": "Bear 분석 실패"}

    # ── 3단계: Judge (최종 판단) ────────────────────────────────────────
    judge_prompt = f"""너는 최종 결정권자다. 지금 시각: {now_kst} KST

강세 의견:
{bull_text[:300]}

약세 의견:
{bear_text[:300]}

두 의견을 종합해서 최종 결정을 내려라.
리스크가 "{bear_json.get('risk_level', '중간')}"이고 핵심 리스크는 "{bear_json.get('main_risk', '')}"다.

confidence는 매수 확신도 (0=완전 불확실, 10=완전 확신). 6 이상이면 매수 가능.

JSON만 응답:
{{"action": "buy", "coin": "심볼", "tp_pct": 8, "sl_pct": 4, "confidence": 7, "reason": "최종 판단 한 줄"}}
또는
{{"action": "wait", "confidence": 3, "reason": "한 줄 이유"}}"""

    try:
        judge_text = _run_claude_sonnet(judge_prompt)
        log.info(f"[CI][Judge] {judge_text[:120]}")
        final = _extract_json(judge_text)
        if final:
            return final
    except Exception as e:
        log.warning(f"Judge 호출 실패: {e}")

    return {"action": "wait", "reason": "Judge 판단 실패"}

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
        if _LIVE:
            try:
                # 매도 전 KRW 잔고 기록 (delta 계산용)
                pre_accounts = client.get_accounts()
                krw_before = next((float(a["balance"]) for a in pre_accounts if a["currency"] == "KRW"), 0.0)
                order = client.market_sell(f"KRW-{coin}", pos["volume"])
                log.info(f"[CI {coin}] 실거래 매도 주문: {order.get('uuid')}")
                time.sleep(1)
                accounts = client.get_accounts()
                krw_after = next((float(a["balance"]) for a in accounts if a["currency"] == "KRW"), 0.0)
                recv = krw_after - krw_before  # 실제 수령 금액만
            except Exception as e:
                log.error(f"[CI {coin}] 매도 실패 — 포지션 유지: {e}")
                save_state(state)
                return False  # 포지션 유지, 다음 체크에서 재시도
        else:
            recv = exit_price * pos["volume"]

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

        # 3. 데이터 수집 + Claude 토론 판단
        log.info(f"[CI] 데이터 수집 중... (잔고 {balance:,.0f}원)")
        try:
            market_data = collect_market_data(client)
            decision    = ask_claude_debate(market_data)
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

        coin       = decision.get("coin", "").upper()
        tp_pct     = float(decision.get("tp_pct", 8))
        sl_pct     = float(decision.get("sl_pct", 4))
        confidence = float(decision.get("confidence", 5))

        if not coin:
            log.warning("coin 없음 — 스킵")
            continue

        # confidence 기반 진입금액 결정 (7 미만 → 10만원, 7 이상 → 20만원)
        entry_krw = ENTRY_KRW if confidence >= 7 else ENTRY_KRW // 2
        log.info(f"[CI] confidence={confidence:.0f} → 진입금액 {entry_krw:,}원")

        # 4. 진입
        price = get_current_price(client, coin)
        if price <= 0:
            log.warning(f"{coin} 가격 0 — 스킵")
            continue

        if _LIVE:
            try:
                # 매수 전 보유 수량 기록 (delta 계산용)
                pre_accounts = client.get_accounts()
                vol_before = next((float(a["balance"]) for a in pre_accounts if a["currency"] == coin), 0.0)
                order = client.market_buy(f"KRW-{coin}", entry_krw)
                log.info(f"[CI {coin}] 실거래 매수 주문: {order.get('uuid')}")
                time.sleep(1)
                accounts = client.get_accounts()
                vol_after = next((float(a["balance"]) for a in accounts if a["currency"] == coin), 0.0)
                volume = vol_after - vol_before  # 실제 체결 수량만
                if volume <= 0:
                    log.error(f"[CI {coin}] 체결 수량 0 — 스킵")
                    continue
            except Exception as e:
                log.error(f"[CI {coin}] 매수 실패: {e}")
                continue
        else:
            volume = entry_krw / price

        new_pos = {
            "coin":        coin,
            "market":      f"KRW-{coin}",
            "entry_price": price,
            "volume":      volume,
            "cost_krw":    entry_krw,
            "entered_at":  datetime.now(KST).isoformat(),
            "highest":     price,
            "be_active":   False,
            "tp_pct":      tp_pct,
            "sl_pct":      sl_pct,
            "reason":      reason,
        }
        state["balance"]  -= entry_krw  # 실제 사용 금액으로 차감
        state["position"] = new_pos
        save_state(state)

        log.info(f"[CI {coin}] 진입 @{price:,.1f}원 "
                 f"TP+{tp_pct:.0f}% SL-{sl_pct:.0f}% conf={confidence:.0f} {entry_krw:,}원 | {reason}")
        _send_tg(f"🤖 <b>[CI {_MODE}] {coin} 진입</b>\n"
                 f"@{price:,.1f}원 | TP+{tp_pct:.0f}% SL-{sl_pct:.0f}%\n"
                 f"확신도: {confidence:.0f}/10 ({entry_krw:,}원)\n"
                 f"이유: {reason}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
