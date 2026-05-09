"""
New listing monitor.
Polls Bithumb ticker/ALL every N seconds and logs any newly appeared coins.
Run: python scripts/monitor_listing.py
"""
import sys
import time
import logging
import yaml
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.client import BithumbClient

# ── logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def load_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))


def format_price(ticker_data: dict) -> str:
    price = ticker_data.get("closing_price", "?")
    change = ticker_data.get("fluctate_rate_24H", "?")
    volume = ticker_data.get("acc_trade_value_24H", "?")
    try:
        volume_m = float(volume) / 1_000_000
        volume_str = f"{volume_m:.1f}M KRW"
    except (ValueError, TypeError):
        volume_str = str(volume)
    return f"price={int(float(price)):,}원  change={change}%  volume={volume_str}"


def run_monitor(poll_sec: int = 2) -> None:
    client = BithumbClient()
    log.info("=== 빗썸 신규 상장 감지 모니터 시작 ===")

    # 초기 스냅샷
    all_ticker = client.get_ticker("ALL")
    known_coins: set[str] = {k for k in all_ticker if k != "date"}
    log.info(f"현재 상장 코인: {len(known_coins)}개 — 폴링 간격 {poll_sec}초")

    while True:
        try:
            time.sleep(poll_sec)
            all_ticker = client.get_ticker("ALL")
            current_coins: set[str] = {k for k in all_ticker if k != "date"}

            new_coins = current_coins - known_coins
            if new_coins:
                for coin in sorted(new_coins):
                    info = all_ticker.get(coin, {})
                    log.warning(
                        f"[신규 상장 감지!] {coin}/KRW — {format_price(info)}"
                    )

            # 상폐 감지 (참고용)
            delisted = known_coins - current_coins
            if delisted:
                for coin in sorted(delisted):
                    log.info(f"[상폐/거래정지 감지] {coin}/KRW — 목록에서 사라짐")

            known_coins = current_coins

        except KeyboardInterrupt:
            log.info("모니터 종료 (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"폴링 오류: {e} — {poll_sec}초 후 재시도")
            time.sleep(poll_sec)


if __name__ == "__main__":
    cfg = load_config()
    poll_sec = cfg.get("monitor", {}).get("poll_interval_sec", 2)
    run_monitor(poll_sec=poll_sec)
