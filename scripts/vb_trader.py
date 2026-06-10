"""
변동성 돌파(Volatility Breakout) 단타 봇

전략:
  목표가 = 당일 시가 + 전일 고저폭 × K
  진입: 실시간 가격이 목표가 돌파 시 시장가 매수
  청산 (하이브리드):
    +5% 미만 구간: SL -2%만 적용 (손실 차단)
    +5% 돌파 시:  트레일링 스탑 활성화 — 고점 대비 -3% 이탈 시 청산
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
from bithumb.db import log_trade, log_vb_skip
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
VB_SL                = -0.03           # 손절 한도 -3% (2026-06-11 백테스트 90일 검증: SL-2% 대비 +35% 수익)
VB_TRAIL_ACTIVATE    = 0.05            # 트레일링 스탑 활성화 기준 +5%
VB_TRAIL_PCT         = 0.03            # 트레일링 스탑 폭 — 고점 대비 -3%
VB_ENTRY_KRW         = 400_000         # 1회 진입금액 (40만원)
MIN_DAILY_VOLUME_KRW = 2_000_000_000   # 볼륨 화이트리스트 기준 (20억 KRW) — 2026-06-10 200억→20억 (원래 설계 의도 복원)
BTC_WEAK_FILTER      = -0.015          # BTC 24h 이하면 진입 차단 (잠정, 10신호 후 재평가)
SCAN_SEC             = 2               # 가격 스캔 주기 (초)
BAD_HOURS_KST        = {0, 1}          # 자정 직후 진입 차단 (일봉 캔들 불안정 구간)
STABLECOIN_EXCLUDE   = {"USDT", "USDC", "DAI", "TUSD", "BUSD", "FDUSD"}  # VB 전략 무효 코인
BREAKOUT_CONFIRM_SEC = 10              # 목표가 돌파 후 진입 전 유지 확인 시간 (초)
WS_URL               = "wss://pubwss.bithumb.com/pub/ws"
WS_MIN_INTERVAL      = 1.0             # WS 재연결 최소 대기 (초)
POS_PATH             = Path("data/vb_pos.json")
ENTERED_PATH         = Path("data/vb_entered.json")

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
            if vol >= MIN_DAILY_VOLUME_KRW and coin not in STABLECOIN_EXCLUDE:
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


def load_entered() -> set[str]:
    """당일 진입 완료 코인 목록 로드. 날짜가 오늘이 아니면 빈 셋 반환."""
    try:
        if not ENTERED_PATH.exists():
            return set()
        data = json.loads(ENTERED_PATH.read_text(encoding="utf-8"))
        if data.get("date") != date.today().isoformat():
            return set()
        return set(data.get("coins", []))
    except Exception:
        return set()


def save_entered(entered: set[str]) -> None:
    """당일 진입 완료 코인 목록 저장."""
    ENTERED_PATH.parent.mkdir(exist_ok=True)
    ENTERED_PATH.write_text(
        json.dumps({"date": date.today().isoformat(), "coins": list(entered)}),
        encoding="utf-8",
    )

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
            entry_btc_chg=pos.get("btc_chg24h"),
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


def _do_sell_live(pos: dict, client: BithumbClient, reason: str, current_price: float = 0.0) -> bool:
    """실거래 청산 — KRW delta로 실수령액 계산."""
    coin = pos["coin"]
    vol  = pos["volume"]
    try:
        accounts_before = client.get_accounts()
        krw_before = next((float(a["balance"]) for a in accounts_before if a["currency"] == "KRW"), 0.0)
        client.market_sell(pos["market"], vol)
        time.sleep(2)
        accounts_after = client.get_accounts()
        krw_after  = next((float(a["balance"]) for a in accounts_after if a["currency"] == "KRW"), 0.0)
        recv_krw   = krw_after - krw_before
        if recv_krw <= 0:
            recv_krw = current_price * vol  # 조회 실패 fallback
        exit_price = recv_krw / vol if vol > 0 else current_price
        pnl_krw    = recv_krw - pos["cost_krw"]
        pnl_pct    = pnl_krw / pos["cost_krw"] * 100
        log.warning(
            f"[{coin}] [VB] 실거래 청산 @{exit_price:,.1f}원 "
            f"PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}"
        )
        _record(pos, exit_price, recv_krw, reason)
        notify.send(
            f"[VB] {coin} 청산 @{exit_price:,.1f}원 "
            f"PnL={pnl_pct:+.2f}% ({pnl_krw:+,.0f}원) | {reason}"
        )
        return True
    except Exception as e:
        log.error(f"[{coin}] 청산 실패: {e}")
        return False

# ── 메인 루프 ─────────────────────────────────────────────────────────────────
def run() -> None:
    client  = BithumbClient()
    tracker = PriceTracker()

    whitelist: set[str]           = set()
    vb_targets: dict[str, float]  = {}   # coin -> vb_target
    entered_coins: set[str]       = load_entered()  # 재시작해도 당일 진입 이력 유지
    breakout_first_seen: dict[str, float] = {}      # coin -> 첫 돌파 감지 timestamp

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

    # 기존 포지션 코인은 entered_coins에 등록 (재시작 후 중복 진입 방지)
    if pos is not None and pos["coin"] not in entered_coins:
        entered_coins.add(pos["coin"])
        save_entered(entered_coins)

    log.info(
        f"[VB] 시작 완료 | 모드={'DRY-RUN' if _DRY_RUN else 'LIVE'} "
        f"| 코인={len(whitelist)}개 | 포지션={'있음: '+pos['coin'] if pos else '없음'}"
        f"| 오늘 진입완료: {entered_coins}"
    )

    while True:
        try:
            now_kst = datetime.now(KST)

            # 날짜 교체: VB 목표가 + 화이트리스트 재계산
            if date.today() != today:
                today = date.today()
                midnight_cleared = False
                entered_coins.clear()
                save_entered(entered_coins)
                breakout_first_seen.clear()
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

            # 자정 강제 청산 (00:00~00:05 KST, 1회만 — API 지연 고려해 5분 윈도우)
            if now_kst.hour == 0 and now_kst.minute < 5 and not midnight_cleared:
                if pos is not None:
                    current = tracker.get_latest_price(pos["coin"])
                    if current <= 0:
                        current = pos["entry_price"]
                        log.warning(f"[{pos['coin']}] 자정강제청산: 가격 미수신, 진입가로 대체")
                    if _DRY_RUN:
                        _do_sell_dry(pos, current, "자정강제청산")
                        pos = None
                        save_pos(None)
                    elif _do_sell_live(pos, client, "자정강제청산", current):
                        pos = None
                        save_pos(None)
                    else:
                        log.error(f"[{pos['coin']}] 자정강제청산 실패 — 포지션 유지, 재시도 대기")
                        # midnight_cleared=True 하지 않아 5분 내 재시도
                        time.sleep(SCAN_SEC)
                        continue
                midnight_cleared = True
                log.info("[VB] 자정 처리 완료 — midnight_cleared=True")

            # 포지션 없음: VB 목표가 돌파 감지
            if pos is None and datetime.now(KST).hour in BAD_HOURS_KST:
                time.sleep(SCAN_SEC)
                continue

            if pos is None:
                for coin, target in list(vb_targets.items()):  # 스냅샷으로 반복
                    if pos is not None:  # 루프 중 진입 완료 시 즉시 탈출
                        break
                    if coin in entered_coins:
                        # 당일 이미 진입한 코인 스킵 — 목표 돌파 상태면 반사실 기록
                        cur = tracker.get_latest_price(coin)
                        if 0 < target <= cur:
                            log_vb_skip(coin, "재진입차단", cur, target)
                        continue
                    current = tracker.get_latest_price(coin)
                    if current <= 0:
                        continue
                    if current < target:
                        breakout_first_seen.pop(coin, None)  # 목표가 아래로 내려가면 초기화
                        continue
                    # 늦은 진입 방지: 목표가 대비 +3% 초과 시 스킵
                    if (current - target) / target > 0.03:
                        log.info(f"[{coin}] 늦은진입 스킵 — 목표 {target:,.0f}원 대비 현재 {(current-target)/target*100:+.1f}%")
                        log_vb_skip(coin, "늦은진입", current, target)
                        breakout_first_seen.pop(coin, None)
                        continue
                    # BTC 약세 필터: BTC 24h -1.5% 이하면 진입 스킵 (조회 실패 시 fail-open)
                    try:
                        btc_info   = client.get_ticker("BTC")
                        btc_chg24h = float(btc_info["fluctate_rate_24H"]) / 100.0
                    except Exception as e:
                        btc_chg24h = None
                        log.warning(f"[BTC필터] 조회 실패 — 필터 생략하고 진입 허용: {e}")
                    if btc_chg24h is not None and btc_chg24h <= BTC_WEAK_FILTER:
                        log.warning(
                            f"[{coin}] BTC약세 스킵 — BTC 24h {btc_chg24h*100:+.1f}% "
                            f"(기준 {BTC_WEAK_FILTER*100:.1f}%) | 목표 {target:,.0f}원 돌파였음"
                        )
                        log_vb_skip(coin, "BTC약세", current, target, btc_chg24h)
                        breakout_first_seen.pop(coin, None)
                        continue
                    # 돌파 유지 확인 (BREAKOUT_CONFIRM_SEC초 이상 목표가 위에 머물러야 진입)
                    now_ts = time.time()
                    if coin not in breakout_first_seen:
                        breakout_first_seen[coin] = now_ts
                        log.debug(f"[{coin}] 돌파 감지 — {BREAKOUT_CONFIRM_SEC}초 유지 확인 중 (목표={target:,.0f}원)")
                        continue
                    if now_ts - breakout_first_seen[coin] < BREAKOUT_CONFIRM_SEC:
                        continue
                    breakout_first_seen.pop(coin, None)
                    # 목표가 상향 돌파 확인 완료 → 진입
                    if _DRY_RUN:
                        volume = VB_ENTRY_KRW / current
                    else:
                        # 실거래 매수 — delta 방식으로 실수량 계산
                        try:
                            accs_before = client.get_accounts()
                            vol_before  = next((float(a["balance"]) for a in accs_before if a["currency"] == coin), 0.0)
                            client.market_buy(f"KRW-{coin}", VB_ENTRY_KRW)
                            time.sleep(2)
                            accs_after  = client.get_accounts()
                            vol_after   = next((float(a["balance"]) for a in accs_after if a["currency"] == coin), 0.0)
                            volume      = vol_after - vol_before
                            if volume <= 0:
                                log.error(f"[{coin}] 매수 실패 — 수량 0, 스킵")
                                entered_coins.add(coin)
                                save_entered(entered_coins)
                                continue
                        except Exception as e:
                            log.error(f"[{coin}] 매수 오류: {e}")
                            entered_coins.add(coin)
                            save_entered(entered_coins)
                            continue
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
                        "btc_chg24h":  btc_chg24h,
                    }
                    save_pos(pos)
                    entered_coins.add(coin)
                    save_entered(entered_coins)
                    log.warning(
                        f"[{coin}] [{_LOG_TAG}] 목표가 돌파! "
                        f"목표={target:,.0f} 현재={current:,.0f}원 → {'모의' if _DRY_RUN else '실거래'} 진입"
                    )
                    notify.send(
                        f"[{_LOG_TAG}] {coin} {'모의' if _DRY_RUN else '실거래'} 진입 "
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

                pnl_pct    = (current - entry) / entry
                highest    = pos.get("highest", entry)
                trail_stop = highest * (1 - VB_TRAIL_PCT)
                # 트레일링 활성화는 고점 기준 (2026-06-10 버그수정):
                # 현재 수익률 기준이면 고점 +5~8.25% 구간에서 스탑 가격이 +5% 미만이 되어
                # 트레일링이 영원히 발동 불가 → SL까지 밀리는 사각지대 발생
                trail_active = (highest - entry) / entry >= VB_TRAIL_ACTIVATE

                if trail_active and current <= trail_stop:
                    # 트레일링 스탑 발동 (고점 -3%) — 수익 청산: 당일 재진입 허용
                    reason = f"트레일링-{VB_TRAIL_PCT*100:.0f}% (고점 {highest:,.0f}원 {pnl_pct*100:+.1f}%)"
                    if _DRY_RUN:
                        _do_sell_dry(pos, current, reason)
                        entered_coins.discard(coin)
                        save_entered(entered_coins)
                        pos = None
                        save_pos(None)
                    elif _do_sell_live(pos, client, reason, current):
                        entered_coins.discard(coin)
                        save_entered(entered_coins)
                        pos = None
                        save_pos(None)

                elif pnl_pct <= VB_SL:
                    # SL 손절 — 당일 재진입 차단 유지
                    reason = f"SL-2% ({pnl_pct * 100:+.1f}%)"
                    if _DRY_RUN:
                        _do_sell_dry(pos, current, reason)
                        pos = None
                        save_pos(None)
                    elif _do_sell_live(pos, client, reason, current):
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
        f"| K={K} | Trail활성={VB_TRAIL_ACTIVATE*100:.0f}% | Trail폭={VB_TRAIL_PCT*100:.0f}% | SL={abs(VB_SL)*100:.0f}% "
        f"| 진입={VB_ENTRY_KRW:,.0f}원"
    )
    run()

if __name__ == "__main__":
    from bithumb.db import init_db
    init_db()
    main()
