"""
실전 가드 (live_guard) — 모의→실전 승격의 안전 배관. 기본값 전면 OFF.

설계 원칙 (절대 준수):
  1. FAIL-SAFE OFF: 설정파일 없거나 읽기 실패 → 무조건 실거래 금지(모의).
  2. 4중 관문: ①글로벌 LIVE 스위치 ON ②엔진이 armed 목록에 ③자본가드(엔진별+전체 상한)
     ④일일 손실한도 미초과 — 넷 다 통과해야만 실주문. 하나라도 막히면 dry(로그만).
  3. 사용자 승인이 유일한 arm 수단: data/live_config.json을 사람이 직접 켜야 함(코드가 안 켬).
  4. 빗썸 API 2.0은 서버측 스톱 없음 → 봇다운 보호 불가 → 첫 실전은 코어(일봉,스톱불필요)만.
     단타 실전은 소액+keepalive로만, 스톱은 봇이 관리(다운 시 구멍 인지).

설정: data/live_config.json (git 미추적 — 런타임 제어, 절대 커밋 금지)
  {"enabled": false, "armed_engines": [], "engine_caps_krw": {"core": 50000},
   "global_cap_krw": 50000, "daily_loss_limit_krw": 10000}
상태: data/live_state.json (당일 실현손익·노출 추적, 일일 리셋)
원장: data/live_orders.csv (모든 실주문 기록)

엔진 사용법:
    from bithumb.live_guard import LiveGuard
    g = LiveGuard("core")
    res = g.execute_buy(client, "KRW-BTC", 50000)   # 통과 시 실매수, 아니면 dry
    g.record_realized(pnl_krw)                        # 포지션 청산 시 실현손익 기록
"""
import json, csv, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "live_config.json"
STATE = ROOT / "data" / "live_state.json"
LEDGER = ROOT / "data" / "live_orders.csv"

log = logging.getLogger("live_guard")
if not log.handlers:
    (ROOT / "logs").mkdir(exist_ok=True)
    h = logging.FileHandler(ROOT / "logs" / "live_guard.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [LIVE] %(message)s"))
    log.addHandler(h); log.setLevel(logging.INFO)


def load_config() -> dict:
    """FAIL-SAFE: 파일 없거나 깨지면 OFF로 반환."""
    default = {"enabled": False, "armed_engines": [], "engine_caps_krw": {},
               "global_cap_krw": 50000, "daily_loss_limit_krw": 10000}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        # 누락 키 기본값 보강, enabled는 명시적 True만 인정
        out = dict(default); out.update(cfg)
        out["enabled"] = (out.get("enabled") is True)
        return out
    except Exception:
        return default


def _load_state() -> dict:
    today = datetime.now(KST).date().isoformat()
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
        if s.get("date") != today:
            s = {"date": today, "realized_pnl_today": 0.0, "open_exposure_krw": 0.0}
    except Exception:
        s = {"date": today, "realized_pnl_today": 0.0, "open_exposure_krw": 0.0}
    return s


def _save_state(s: dict):
    try:
        tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    except Exception as e:
        log.warning(f"state 저장 실패: {e}")


def _tick_size(price: float) -> float:
    """빗썸(업비트 API2.0 동일 규격) KRW마켓 호가단위. 구간별 유효 최소 가격증분."""
    brackets = [
        (2_000_000, 1000), (1_000_000, 500), (500_000, 100), (100_000, 50),
        (10_000, 10), (1_000, 1), (100, 0.1), (10, 0.01), (1, 0.001),
        (0.1, 0.0001), (0.01, 0.00001), (0.001, 0.000001), (0.0001, 0.0000001),
    ]
    for floor, tick in brackets:
        if price >= floor:
            return tick
    return 0.00000001


def round_to_tick(price: float) -> float:
    """가격을 호가단위 배수로 반올림 — 안 맞으면 지정가 주문이 400으로 거부됨(2026-07-04 NEX 실전 확인)."""
    tick = _tick_size(price)
    return round(round(price / tick) * tick, 10)


def live_status() -> dict:
    """현재 실전 가드 상태(브리핑/표시용)."""
    cfg = load_config(); s = _load_state()
    return {"enabled": cfg["enabled"], "armed": cfg.get("armed_engines", []),
            "global_cap": cfg.get("global_cap_krw", 0), "daily_loss_limit": cfg.get("daily_loss_limit_krw", 0),
            "realized_pnl_today": s.get("realized_pnl_today", 0.0), "open_exposure": s.get("open_exposure_krw", 0.0)}


class LiveGuard:
    def __init__(self, engine: str):
        self.engine = engine

    def can_trade(self, krw: float) -> tuple[bool, str]:
        """4중 관문. (가능?, 사유)."""
        cfg = load_config()
        if not cfg["enabled"]:
            return False, "글로벌 LIVE OFF"
        if self.engine not in cfg.get("armed_engines", []):
            return False, f"{self.engine} 미arm"
        cap = cfg.get("engine_caps_krw", {}).get(self.engine)
        if cap is None:
            return False, f"{self.engine} 자본가드 미설정"
        if krw > cap:
            return False, f"엔진 상한 초과({krw:.0f}>{cap})"
        s = _load_state()
        if s["open_exposure_krw"] + krw > cfg.get("global_cap_krw", 0):
            return False, f"전체 노출 상한 초과({s['open_exposure_krw']+krw:.0f}>{cfg.get('global_cap_krw',0)})"
        dll = cfg.get("daily_loss_limit_krw", 0)
        if s["realized_pnl_today"] <= -abs(dll):
            return False, f"일일 손실한도 도달({s['realized_pnl_today']:.0f})"
        return True, "OK"

    def _ledger(self, side, market, krw, vol, result):
        new = not LEDGER.exists()
        try:
            with open(LEDGER, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new: w.writerow(["time", "engine", "side", "market", "krw", "volume", "result"])
                w.writerow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), self.engine, side, market,
                            f"{krw:.0f}" if krw else "", f"{vol:.8f}" if vol else "", str(result)[:200]])
        except Exception as e:
            log.warning(f"원장 기록 실패: {e}")

    def execute_buy(self, client, market: str, krw: float) -> dict:
        ok, reason = self.can_trade(krw)
        if not ok:
            log.info(f"[{self.engine}] 매수 차단(dry) {market} {krw:.0f}원 — {reason}")
            self._ledger("buy", market, krw, 0, f"DRY:{reason}")
            return {"dry": True, "reason": reason}
        try:
            res = client.market_buy(market, krw)   # ★ 실주문
        except Exception as e:
            log.error(f"[{self.engine}] 실매수 실패 {market}: {e}")
            self._ledger("buy", market, krw, 0, f"ERR:{e}")
            return {"error": str(e)}
        s = _load_state(); s["open_exposure_krw"] += krw; _save_state(s)
        log.warning(f"[{self.engine}] ★실매수 {market} {krw:.0f}원 → {res}")
        self._ledger("buy", market, krw, 0, res)
        return {"live": True, "result": res}

    def _actual_fill_funds(self, client, uuid: str, max_wait: float = 6.0):
        """시장가 주문의 실제 체결 KRW(executed_funds) 짧게 폴링 조회 (2026-07-05 추가).

        기존엔 주문 직후 응답(대개 state=wait, executed_funds=0)이나 호출부가 넘긴
        추정가(krw_hint, 주문 전 조회한 ticker가 기준)로 노출액(exposure)을 계산해서,
        손절처럼 가격이 진입가보다 낮게 체결될 때 항상 실제보다 적게 차감되는
        결정론적 오차가 있었음(잔여 노출액 버그의 원인). 실패 시 None 반환 → 호출부가
        krw_hint로 폴백(청산 확실성 우선, 정확도보다 안전 우선).
        """
        import time as _time
        if not uuid: return None
        deadline = _time.time() + max_wait
        while _time.time() < deadline:
            try:
                od = client.get_order(uuid)
                if od.get("state") == "done":
                    return float(od.get("executed_funds", 0) or 0)
            except Exception:
                pass
            _time.sleep(1.0)
        return None

    def execute_sell(self, client, market: str, volume: float, krw_hint: float = 0.0) -> dict:
        cfg = load_config()
        # 매도는 보유 청산이므로 자본가드 무관, 단 글로벌 OFF/미arm이면 실행 안 함(모의 일관성)
        if not cfg["enabled"] or self.engine not in cfg.get("armed_engines", []):
            reason = "글로벌 LIVE OFF" if not cfg["enabled"] else f"{self.engine} 미arm"
            log.info(f"[{self.engine}] 매도 차단(dry) {market} {volume:.8f} — {reason}")
            self._ledger("sell", market, 0, volume, f"DRY:{reason}")
            return {"dry": True, "reason": reason}
        try:
            res = client.market_sell(market, volume)   # ★ 실주문
        except Exception as e:
            log.error(f"[{self.engine}] 실매도 실패 {market}: {e}")
            self._ledger("sell", market, 0, volume, f"ERR:{e}")
            return {"error": str(e)}
        actual = self._actual_fill_funds(client, res.get("uuid"))
        exposure_delta = actual if actual is not None else krw_hint
        s = _load_state(); s["open_exposure_krw"] = max(0.0, s["open_exposure_krw"] - exposure_delta); _save_state(s)
        log.warning(f"[{self.engine}] ★실매도 {market} {volume:.8f} → {res}")
        self._ledger("sell", market, 0, volume, res)
        return {"live": True, "result": res, "actual_funds": actual}

    def execute_sell_soft(self, client, market: str, volume: float, krw_hint: float = 0.0,
                          limit_slip_pct: float = 0.3, wait_sec: float = 8.0) -> dict:
        """소프트 스탑 매도 — 슬리피지 상한 지정 (2026-07-03).

        시장가로 바로 던지지 않고: ①현재 최우선 매수호가 대비 limit_slip_pct% 아래에
        지정가 매도 → ②wait_sec초 내 체결 확인 → ③미체결분은 취소 후 시장가 폴백.
        급락 순간 시장가 슬리피지(-1.5% 설정 → -2.18% 실측)를 상한으로 제한하는 목적.
        어떤 단계든 실패하면 시장가 폴백(청산 확실성 우선).
        """
        import time as _time
        cfg = load_config()
        if not cfg["enabled"] or self.engine not in cfg.get("armed_engines", []):
            return self.execute_sell(client, market, volume, krw_hint)  # dry 경로 재사용
        coin = market.split("-")[1]
        # ① 호가 조회 → 지정가 산출 (실패 시 시장가 폴백)
        try:
            ob = client.get_orderbook(coin)
            best_bid = max(float(b["price"]) for b in ob.get("bids", []))
            limit_px = round_to_tick(best_bid * (1 - limit_slip_pct / 100))
        except Exception as e:
            log.warning(f"[{self.engine}] 소프트스탑 호가조회 실패({e}) → 시장가 폴백")
            return self.execute_sell(client, market, volume, krw_hint)
        # ② 지정가 매도
        try:
            res = client.limit_sell(market, limit_px, volume)
            uuid = res.get("uuid")
        except Exception as e:
            log.warning(f"[{self.engine}] 소프트스탑 지정가 실패({e}) → 시장가 폴백")
            return self.execute_sell(client, market, volume, krw_hint)
        self._ledger("sell", market, 0, volume, f"SOFT-LIMIT@{limit_px:.4f}:{res}")
        # ③ 체결 대기
        deadline = _time.time() + wait_sec
        remaining = volume
        while _time.time() < deadline:
            _time.sleep(1.5)
            try:
                od = client.get_order(uuid)
                remaining = float(od.get("remaining_volume", 0) or 0)
                if remaining <= 0:
                    log.warning(f"[{self.engine}] ★소프트스탑 전량체결 {market} @{limit_px:.4f}")
                    actual = float(od.get("executed_funds", 0) or 0) or None  # 이미 조회한 실제 체결액 재사용
                    exposure_delta = actual if actual else krw_hint
                    s = _load_state(); s["open_exposure_krw"] = max(0.0, s["open_exposure_krw"] - exposure_delta); _save_state(s)
                    return {"live": True, "result": od, "soft": True, "actual_funds": actual}
            except Exception:
                pass
        # ④ 미체결분 취소 후 시장가 폴백
        try: client.cancel_order(uuid)
        except Exception: pass
        try:
            bal = client.get_balance(coin)
            for a in bal:
                if a.get("currency") == coin:
                    b = float(a.get("balance", 0) or 0)
                    if b > 0: remaining = min(remaining if remaining > 0 else volume, b)
        except Exception: pass
        if remaining > 0:
            log.warning(f"[{self.engine}] 소프트스탑 {wait_sec}s 미체결 {remaining:.8f} → 시장가 폴백")
            return self.execute_sell(client, market, remaining, krw_hint * (remaining / volume if volume else 1))
        actual = self._actual_fill_funds(client, uuid, max_wait=3.0)
        exposure_delta = actual if actual is not None else krw_hint
        s = _load_state(); s["open_exposure_krw"] = max(0.0, s["open_exposure_krw"] - exposure_delta); _save_state(s)
        return {"live": True, "result": "soft-filled", "soft": True, "actual_funds": actual}

    def record_realized(self, pnl_krw: float):
        """실전 포지션 청산 시 실현손익 기록 → 일일 손실한도 추적."""
        s = _load_state(); s["realized_pnl_today"] += pnl_krw; _save_state(s)
        log.info(f"[{self.engine}] 실현손익 {pnl_krw:+.0f}원 → 당일누적 {s['realized_pnl_today']:+.0f}원")


if __name__ == "__main__":
    # 자가검증: 기본 OFF 상태에서 매수 시도 → 반드시 차단(dry)돼야 함
    print("=== live_guard 자가검증 (기본 OFF) ===")
    print("현재 상태:", json.dumps(live_status(), ensure_ascii=False))
    g = LiveGuard("core")
    print("can_trade(50000):", g.can_trade(50000))
    print("execute_buy(가짜클라이언트):", g.execute_buy(None, "KRW-BTC", 50000))
