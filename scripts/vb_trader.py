"""
변동성 돌파(Volatility Breakout) 단타 봇

전략:
  목표가 = 당일 시가 + 전일 고저폭 × K
  진입: 실시간 가격이 목표가 돌파 시 시장가 매수
  익절: 진입가 대비 +VB_TP (3%)
  손절: 진입가 대비 +VB_SL (-2%)
  강제 청산: KST 00:00 미청산 포지션 시장가 청산

실행:
  python scripts/vb_trader.py --dry-run   <- 페이퍼 트레이딩 (실주문 없음)
  python scripts/vb_trader.py --live      <- 실거래 (별도 확인 필요)

포지션 파일: data/vb_pos.json
로그 파일:   logs/vb_trader.log
"""
import sys
import os
import atexit
import time
import json
import logging
import threading
import argparse
import socket
import yaml
from collections import deque
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

# ── KST 타임존 ─────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# ── 중복 실행 방지 (TCP 소켓 바인딩 — OS 수준 원자적 보장) ─────────────────────
_singleton_sock = None  # GC 방지용

def _ensure_single_instance() -> None:
    global _singleton_sock
    _singleton_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _singleton_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        _singleton_sock.bind(("127.0.0.1", 47220))  # vb_trader 전용 포트 (alt_monitor=47219)
    except OSError:
        print("[ERROR] vb_trader 이미 실행 중 (포트 47220 사용 중). 종료합니다.")
        sys.exit(1)
    atexit.register(_singleton_sock.close)

_ensure_single_instance()

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient
from bithumb.db import log_trade
from bithumb import notify

# ── --dry-run / --live 파싱 (early, before logging — _LOG_TAG needed for handler) ──
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--live",    action="store_true")
    args, _ = p.parse_known_args()
    return args

_args    = _parse_args()
_DRY_RUN: bool = _args.dry_run
_LOG_TAG: str  = "VB-DRY" if _DRY_RUN else "VB"

# ── 로깅 설정 ─────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{_LOG_TAG}][%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/vb_trader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 전략 상수 ─────────────────────────────────────────────────────────────────
K                    = 0.5              # 변동성 돌파 계수 (래리 윌리엄스)
VB_TP                = 0.03            # 익절 목표 +3%
VB_SL                = -0.02           # 손절 한도 -2%
VB_ENTRY_KRW         = 100_000         # 1회 진입금액 (10만원)
MIN_DAILY_VOLUME_KRW = 20_000_000_000  # 볼륨 화이트리스트 기준 (20억 KRW)
SCAN_SEC             = 2               # 가격 스캔 주기 (초)
WS_URL               = "wss://pubwss.bithumb.com/pub/ws"
WS_MIN_INTERVAL      = 1.0             # WS 재연결 최소 대기 (초)
POS_PATH             = Path("data/vb_pos.json")

# ── 볼륨 화이트리스트 ─────────────────────────────────────────────────────────
def _build_volume_whitelist(client: BithumbClient) -> set[str]:
    """24h 거래대금 20억+ 코인 심볼 반환."""
    try:
        tickers = client.get_ticker("ALL")
        wl: set[str] = set()
        for coin, data in tickers.items():
            if coin == "date":
                continue
            vol = float(data.get("acc_trade_value_24H", 0))
            if vol >= MIN_DAILY_VOLUME_KRW:
                wl.add(coin)
        log.info(f"[볼륨필터] {len(wl)}개 코인 (20억+)")
        return wl
    except Exception as e:
        log.warning(f"[볼륨필터] 갱신 실패: {e}")
        return set()

# ── 포지션 파일 I/O ───────────────────────────────────────────────────────────
def load_pos() -> dict | None:
    if not POS_PATH.exists():
        return None
    try:
        return json.loads(POS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

def save_pos(pos: dict | None) -> None:
    POS_PATH.parent.mkdir(exist_ok=True)
    if pos is None:
        POS_PATH.unlink(missing_ok=True)
    else:
        POS_PATH.write_text(json.dumps(pos, default=str), encoding="utf-8")

# ── 메인 루프 (stub — 04-02에서 구현) ─────────────────────────────────────────
def run(client: BithumbClient) -> None:
    """메인 이벤트 루프. 04-02-PLAN.md에서 완성된다."""
    log.info("[VB] run() stub — 04-02에서 구현")
    while True:
        time.sleep(60)

# ── 진입점 ────────────────────────────────────────────────────────────────────
def main() -> None:
    mode = "DRY-RUN" if _DRY_RUN else "LIVE"
    log.info(f"[{_LOG_TAG}] vb_trader 시작 — 모드: {mode}")
    client = BithumbClient()
    _build_volume_whitelist(client)
    run(client)

if __name__ == "__main__":
    from bithumb.db import init_db
    init_db()
    main()
