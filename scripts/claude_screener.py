"""
Claude 시장 스크리너 — 5분마다 빗썸 시장 분석 후 유망 코인 선별

Max 요금제 사용: Anthropic API 크레딧 불필요
claude CLI를 subprocess로 호출 → data/claude_watchlist.json 저장
alt_monitor.py가 이 파일을 읽어서 진입 필터 적용

실행: python scripts/claude_screener.py
"""
import sys
import json
import time
import sqlite3
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.client import BithumbClient
from bithumb.db import DB_PATH

# ── 설정 ──────────────────────────────────────────────────────────────────────
KST               = timezone(timedelta(hours=9))
INTERVAL_SEC      = 300           # 5분마다 분석
WATCHLIST_PATH    = Path("data/claude_watchlist.json")
MIN_DAILY_VOL_KRW = 20_000_000_000
MAX_CANDIDATES    = 25
WATCHLIST_COINS   = 4
CLAUDE_TIMEOUT    = 60            # claude CLI 응답 최대 대기 (초)
SKIP_COINS        = {"BTC", "ETH", "XRP", "USDT", "USDC", "BNB", "SOL"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCREENER][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)),
        logging.FileHandler("logs/claude_screener.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

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


# ── Claude CLI 호출 ───────────────────────────────────────────────────────────

def ask_claude_cli(snapshot: list[dict], recent_trades: list[dict],
                   pump_summary: str) -> dict:
    now_kst = datetime.now(KST).strftime("%H:%M")

    prompt = f"""빗썸 알트코인 단기 트레이딩 봇의 AI 분석가로서 답해주세요.

현재 시각: {now_kst} KST

=== 빗썸 현재 시장 (24h 거래대금 20억+, 상위 {len(snapshot)}개) ===
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

=== 오늘 펌핑 이력 (pump_log) ===
{pump_summary}

=== 최근 봇 실거래 결과 ===
{json.dumps(recent_trades, ensure_ascii=False, indent=2)}

=== 지시 ===
다음 5~30분 내 단기 급등(+3% 이상) 가능성 높은 코인 {WATCHLIST_COINS}개 선별하세요.

기준:
- 24h 변화율 +5~40%: 이미 너무 올랐거나(+50% 초과) 안 오른 것 제외
- 거래대금 클수록 유동성 확보 → 먹고 나오기 쉬움
- 오늘 pump_log에 여러 번 감지된 코인 = 지속 수급
- 최근 봇이 손실 본 코인 제외

JSON만 응답 (앞뒤 설명 없이):
{{"watchlist": ["COIN1", "COIN2", "COIN3", "COIN4"], "avoid": ["BAD1"], "reason": "한 줄 이유"}}"""

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

    # 코드블록 감싸진 경우 처리
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                text = part
                break

    # JSON 부분만 추출 (앞뒤 설명이 붙은 경우)
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    return json.loads(text)


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

def run() -> None:
    client = BithumbClient()
    log.info(f"Claude 스크리너 시작 | 간격={INTERVAL_SEC}s | CLI 모드 (Max 요금제)")

    while True:
        start = time.time()
        try:
            now_kst = datetime.now(KST)
            log.info(f"=== 분석 {now_kst.strftime('%H:%M:%S')} ===")

            snapshot     = get_market_snapshot(client)
            recent       = get_recent_trades()
            pump_summary = get_pump_log_summary()
            log.info(f"후보 {len(snapshot)}개 수집")

            result    = ask_claude_cli(snapshot, recent, pump_summary)
            watchlist = result.get("watchlist", [])
            avoid     = result.get("avoid", [])
            reason    = result.get("reason", "")

            output = {
                "coins":      watchlist,
                "avoid":      avoid,
                "reason":     reason,
                "updated_at": now_kst.isoformat(),
            }
            WATCHLIST_PATH.write_text(
                json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log.info(f"워치리스트: {watchlist}")
            log.info(f"회피:       {avoid}")
            log.info(f"이유:       {reason}")

        except subprocess.TimeoutExpired:
            log.warning("claude CLI 타임아웃 — 워치리스트 미갱신")
        except json.JSONDecodeError as e:
            log.warning(f"JSON 파싱 실패: {e}")
        except Exception as e:
            log.error(f"스크리너 오류: {e}")

        elapsed   = time.time() - start
        sleep_sec = max(10, INTERVAL_SEC - elapsed)
        log.info(f"다음 분석까지 {sleep_sec:.0f}초")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    run()
