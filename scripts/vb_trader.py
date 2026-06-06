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

# ── 거래소 타임스탬프 파싱 ─────────────────────────────────────────────────────
def _parse_exchange_ts(date_s: str, time_s: str) -> float | None:
    """빗썸 WS content date(YYYYMMDD)+time(HHMMSS, KST) → epoch sec. 실패 시 None."""
    try:
        dt = datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=KST).timestamp()
    except (ValueError, TypeError):
        return None

# ── 실시간 가격 추적기 ─────────────────────────────────────────────────────────
class PriceTracker:
    MAXLEN = 60

    def __init__(self):
        self._hist: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._ws_running = False

    def start_ws(self, symbols: list[str]) -> None:
        import websocket as _wslib
        self._ws_running = True

        def on_open(ws):
            sub = {"type": "ticker", "symbols": symbols, "tickTypes": ["24H"]}
            ws.send(json.dumps(sub))
            log.info(f"[WS] 구독 완료: {len(symbols)}개 코인")

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("type") != "ticker":
                    return
                c      = data.get("content", {})
                symbol = c.get("symbol", "")
                if not symbol.endswith("_KRW"):
                    return
                coin    = symbol[:-4]
                price   = float(c.get("closePrice", 0) or 0)
                acc_val = float(c.get("value",      0) or 0)
                if price <= 0:
                    return
                now = time.time()
                with self._lock:
                    if coin not in self._hist:
                        self._hist[coin] = deque(maxlen=self.MAXLEN)
                    hist = self._hist[coin]
                    if not hist or now - hist[-1][0] >= WS_MIN_INTERVAL:
                        hist.append((now, price, acc_val))
            except Exception as e:
                log.debug(f"[WS] 파싱 오류: {e}")

        def on_error(ws, error):
            log.warning(f"[WS] 에러: {error}")

        def on_close(ws, code, msg):
            log.warning(f"[WS] 연결 종료: {code}")

        def run():
            while self._ws_running:
                try:
                    ws = _wslib.WebSocketApp(
                        WS_URL,
                        on_open=on_open, on_message=on_message,
                        on_error=on_error, on_close=on_close,
                    )
                    self._ws = ws
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    log.error(f"[WS] 실행 오류: {e}")
                if self._ws_running:
                    log.info("[WS] 5초 후 재연결...")
                    time.sleep(5)

        threading.Thread(target=run, daemon=True).start()

    def stop_ws(self) -> None:
        self._ws_running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def get_latest_price(self, coin: str) -> float:
        """coin의 최신 체결가 반환. 미수신이면 0.0."""
        with self._lock:
            hist = self._hist.get(coin)
            if not hist:
                return 0.0
            return hist[-1][1]  # (recv_ts, price, acc_val) 튜플의 인덱스 1

# ── VB 목표가 계산 ─────────────────────────────────────────────────────────────
def calc_vb_target(client: BithumbClient, coin: str) -> tuple[float, float] | None:
    """(vb_target, today_open) 반환. 실패 시 None.
    vb_target = today_open + (prev_high - prev_low) * K
    """
    try:
        candles = client.get_daily_candles(f"KRW-{coin}", count=2)
        if len(candles) < 2:
            log.warning(f"[{coin}] 일봉 캔들 부족: {len(candles)}개")
            return None
        today_open = float(candles[0]["opening_price"])
        prev_high  = float(candles[1]["high_price"])
        prev_low   = float(candles[1]["low_price"])
        prev_range = prev_high - prev_low
        if prev_range <= 0 or today_open <= 0:
            return None
        vb_target = today_open + prev_range * K
        log.debug(f"[{coin}] 시가={today_open:,.0f} 전일범위={prev_range:,.0f} VB목표={vb_target:,.0f}")
        return vb_target, today_open
    except Exception as e:
        log.warning(f"[{coin}] VB 목표가 계산 실패: {e}")
        return None

# ── DB 기록 + 모의 청산 ────────────────────────────────────────────────────────
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
        )
    except Exception as e:
        log.error(f"[DB] 기록 실패: {e}")


def _do_sell_dry(pos: dict, current_price: float, reason: str) -> None:
    """모의 청산: 실제 주문 없이 현재가로 PnL 계산 후 DB 기록."""
    vol     = pos["volume"]
    recv    = current_price * vol
    pnl_krw = recv - pos["cost_krw"]
    pnl_pct = pnl_krw / pos["cost_krw"] * 100
    log.warning(
        f"[{pos['coin']}] [{_LOG_TAG}] 청산 @{current_price:,.0f}원 "
        f"PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}"
    )
    _record(pos, current_price, recv, reason)
    notify.send(
        f"[{_LOG_TAG}] {pos['coin']} 청산 @{current_price:,.0f}원 "
        f"PnL={pnl_pct:+.2f}% | {reason}"
    )


def _do_sell_live(pos: dict, client: BithumbClient, reason: str) -> None:
    """실거래 청산 — 추후 구현."""
    log.error("[LIVE] 실거래 청산 미구현 — --dry-run 모드만 사용 가능")

# ── 메인 루프 ─────────────────────────────────────────────────────────────────
def run() -> None:
    client  = BithumbClient()
    tracker = PriceTracker()

    whitelist: set[str]       = set()
    vb_targets: dict[str, float] = {}   # coin -> vb_target
    entered_coins: set[str]   = set()   # 당일 진입 완료 코인 (중복 방지, 자정 초기화)

    pos: dict | None = load_pos()
    today            = date.today()
    midnight_cleared = False

    # 초기화: 화이트리스트 + VB 목표가 + WebSocket
    whitelist = _build_volume_whitelist(client)
    for coin in whitelist:
        result = calc_vb_target(client, coin)
        if result:
            vb_targets[coin] = result[0]
    log.info(f"[VB] VB 목표가 계산 완료: {len(vb_targets)}개 코인")

    symbols = [f"{c}_KRW" for c in whitelist]
    if symbols:
        tracker.start_ws(symbols)
        time.sleep(5)   # WS 연결 안정화 대기
    else:
        log.warning("[VB] 화이트리스트가 비어 있음 — WebSocket 구독 없음")

    log.info(
        f"[VB] 시작 완료 | 모드={'DRY-RUN' if _DRY_RUN else 'LIVE'} "
        f"| 코인={len(whitelist)}개 | 포지션={'있음: '+pos['coin'] if pos else '없음'}"
    )

    while True:
        try:
            now_kst = datetime.now(KST)

            # 날짜 교체: VB 목표가 + 화이트리스트 재계산
            if date.today() != today:
                today = date.today()
                midnight_cleared = False
                entered_coins.clear()
                log.info("[VB] 날짜 교체 — 화이트리스트/VB목표가 재계산")
                whitelist = _build_volume_whitelist(client)
                vb_targets.clear()
                for coin in whitelist:
                    result = calc_vb_target(client, coin)
                    if result:
                        vb_targets[coin] = result[0]
                # WebSocket 재구독 (코인 목록 변경 반영)
                tracker.stop_ws()
                symbols = [f"{c}_KRW" for c in whitelist]
                if symbols:
                    tracker.start_ws(symbols)
                    time.sleep(5)

            # 자정 강제 청산 (00:00~00:01 KST, 1회만)
            if now_kst.hour == 0 and now_kst.minute == 0 and not midnight_cleared:
                if pos is not None:
                    current = tracker.get_latest_price(pos["coin"])
                    if current > 0:
                        if _DRY_RUN:
                            _do_sell_dry(pos, current, "자정강제청산")
                        else:
                            _do_sell_live(pos, client, "자정강제청산")
                    else:
                        log.warning(f"[{pos['coin']}] 자정강제청산: 가격 미수신, 진입가로 기록")
                        _do_sell_dry(pos, pos["entry_price"], "자정강제청산(가격미수신)")
                    pos = None
                    save_pos(None)
                midnight_cleared = True
                log.info("[VB] 자정 처리 완료 — midnight_cleared=True")

            # 포지션 없음: VB 목표가 돌파 감지
            if pos is None:
                for coin, target in vb_targets.items():
                    if coin in entered_coins:
                        continue  # 당일 이미 진입한 코인 스킵
                    current = tracker.get_latest_price(coin)
                    if current <= 0 or current < target:
                        continue
                    # 목표가 상향 돌파 → 모의 진입
                    volume = VB_ENTRY_KRW / current
                    pos = {
                        "coin":        coin,
                        "market":      f"KRW-{coin}",
                        "entry_price": current,
                        "volume":      volume,
                        "cost_krw":    VB_ENTRY_KRW,
                        "highest":     current,
                        "entered_at":  datetime.now().isoformat(),
                        "vb_target":   target,
                        "mock":        _DRY_RUN,
                    }
                    save_pos(pos)
                    entered_coins.add(coin)
                    log.warning(
                        f"[{coin}] [{_LOG_TAG}] 목표가 돌파! "
                        f"목표={target:,.0f} 현재={current:,.0f}원 → 모의 진입"
                    )
                    notify.send(
                        f"[{_LOG_TAG}] {coin} 모의 진입 "
                        f"@{current:,.0f}원 (목표={target:,.0f}원)"
                    )
                    break  # 1코인 1포지션

            # 포지션 있음: TP/SL 체크
            elif pos is not None:
                coin    = pos["coin"]
                entry   = pos["entry_price"]
                current = tracker.get_latest_price(coin)
                if current <= 0:
                    time.sleep(SCAN_SEC)
                    continue

                if current > pos.get("highest", entry):
                    pos["highest"] = current
                    save_pos(pos)

                pnl_pct = (current - entry) / entry

                if pnl_pct >= VB_TP:
                    reason = f"TP+3% ({pnl_pct * 100:+.1f}%)"
                    if _DRY_RUN:
                        _do_sell_dry(pos, current, reason)
                    else:
                        _do_sell_live(pos, client, reason)
                    pos = None
                    save_pos(None)

                elif pnl_pct <= VB_SL:
                    reason = f"SL-2% ({pnl_pct * 100:+.1f}%)"
                    if _DRY_RUN:
                        _do_sell_dry(pos, current, reason)
                    else:
                        _do_sell_live(pos, client, reason)
                    pos = None
                    save_pos(None)

        except KeyboardInterrupt:
            log.info("[VB] 종료 요청 (Ctrl+C)")
            tracker.stop_ws()
            break
        except Exception as e:
            log.error(f"[VB] 루프 오류: {e}", exc_info=True)

        time.sleep(SCAN_SEC)

# ── 진입점 ────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info(
        f"[VB] vb_trader 시작 — 모드={'DRY-RUN' if _DRY_RUN else 'LIVE'} "
        f"| K={K} | TP={VB_TP*100:.0f}% | SL={abs(VB_SL)*100:.0f}% "
        f"| 진입={VB_ENTRY_KRW:,.0f}원"
    )
    run()

if __name__ == "__main__":
    from bithumb.db import init_db
    init_db()
    main()
