"""
바이낸스 선물 실행가드 (binance_guard) — 모의→실전 승격의 안전 배관. 기본값 전면 OFF.

live_guard.py(빗썸)의 바이낸스 USDT 무기한선물 버전. 설계 원칙 동일:
  1. FAIL-SAFE OFF: 설정파일 없거나 읽기 실패 → 무조건 실거래 금지(모의).
  2. 4중 관문: ①글로벌 LIVE 스위치 ON ②엔진이 armed 목록에 ③자본가드(엔진별+전체 상한 USDT)
     ④일일 손실한도 미초과 — 넷 다 통과해야만 실주문. 하나라도 막히면 dry(로그만).
  3. 사용자 승인이 유일한 arm 수단: data/binance_live_config.json을 사람이 직접 켜야 함.
  4. ★ 레버리지 안전: 선물은 청산위험이 있으므로 set_leverage로 배율을 명시 고정하고,
     목표 명목노출을 초과하지 않게 델타만 주문. 시장가만 사용(단순·확실).

인증: config.yaml의 binance.api_key/api_secret (gitignore). 선물거래 권한만, 출금권한 없어야 함.
설정: data/binance_live_config.json (git 미추적)
  {"enabled": false, "armed_engines": [], "engine_caps_usdt": {"core_lev": 20},
   "global_cap_usdt": 20, "daily_loss_limit_usdt": 5, "leverage": 2}
상태: data/binance_live_state.json (당일 실현손익 추적)
원장: data/binance_orders.csv

사용법:
    from bithumb.binance_guard import BinanceGuard
    g = BinanceGuard("core_lev")
    g.rebalance_long(target_notional_usdt=X)  # BTCUSDT 롱을 목표명목까지 (통과 시 실주문, 아니면 dry)
"""
import json, csv, time, hmac, hashlib, logging, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
import requests
import yaml

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "binance_live_config.json"
STATE = ROOT / "data" / "binance_live_state.json"
LEDGER = ROOT / "data" / "binance_orders.csv"
FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

log = logging.getLogger("binance_guard")
if not log.handlers:
    (ROOT / "logs").mkdir(exist_ok=True)
    h = logging.FileHandler(ROOT / "logs" / "binance_guard.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [BNGUARD] %(message)s"))
    log.addHandler(h); log.setLevel(logging.INFO)


def _keys():
    c = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    b = c.get("binance", {})
    return b.get("api_key", ""), b.get("api_secret", "")


def load_config() -> dict:
    """FAIL-SAFE: 파일 없거나 깨지면 OFF."""
    default = {"enabled": False, "armed_engines": [], "engine_caps_usdt": {},
               "global_cap_usdt": 20, "daily_loss_limit_usdt": 5, "leverage": 2}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
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
            s = {"date": today, "realized_pnl_today": 0.0}
    except Exception:
        s = {"date": today, "realized_pnl_today": 0.0}
    return s


def _save_state(s):
    try:
        tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=2), encoding="utf-8"); tmp.replace(STATE)
    except Exception as e:
        log.warning(f"state 저장 실패: {e}")


def _file_lock(path, timeout=5.0):
    """margin_guard.py와 동일 수정(2026-07-13) — 다중엔진 동시 record_realized() 경쟁상태 방지."""
    lock_path = str(path) + ".lock"
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_path
        except FileExistsError:
            if time.time() > deadline:
                try:
                    if time.time() - os.path.getmtime(lock_path) > 10:
                        os.remove(lock_path)
                except Exception:
                    pass
                deadline = time.time() + timeout
            time.sleep(0.05)


def _release_lock(lock_path):
    try: os.remove(lock_path)
    except Exception: pass


# ── 서명 요청 헬퍼 ──
_time_offset = {"ms": None, "checked_at": 0.0}

def _synced_timestamp() -> int:
    """바이낸스 서버시각과 동기화한 타임스탬프 (margin_guard.py와 동일 버그·수정 — 2026-07-13,
    로컬PC 시계가 서버보다 ~1.3초 앞서 간헐적 -1021 오류로 조회가 조용히 실패하던 문제)."""
    now = time.time()
    if _time_offset["ms"] is None or now - _time_offset["checked_at"] > 300:
        try:
            r = requests.get(f"{FAPI}/fapi/v1/time", timeout=5)
            server_ms = r.json()["serverTime"]
            _time_offset["ms"] = server_ms - int(now * 1000)
            _time_offset["checked_at"] = now
        except Exception:
            if _time_offset["ms"] is None:
                _time_offset["ms"] = 0
    return int(time.time() * 1000) + _time_offset["ms"]


def _signed(method, path, params=None):
    key, sec = _keys()
    params = params or {}
    params["timestamp"] = _synced_timestamp()
    params["recvWindow"] = 5000
    qs = urlencode(params)
    sig = hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{FAPI}{path}?{qs}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}
    if method == "GET":
        return requests.get(url, headers=headers, timeout=10)
    if method == "POST":
        return requests.post(url, headers=headers, timeout=10)
    raise ValueError(method)


def get_futures_usdt() -> float:
    """선물 USDT 지갑잔고(가용). 실패 시 0."""
    try:
        r = _signed("GET", "/fapi/v2/balance")
        if r.status_code == 200:
            for a in r.json():
                if a["asset"] == "USDT":
                    return float(a["availableBalance"])
    except Exception as e:
        log.warning(f"잔고조회 실패: {e}")
    return 0.0


def get_position() -> dict:
    """BTCUSDT 현재 포지션. {amt(+롱/-숏), entry, notional, unrealized}. 실패 시 amt=0."""
    try:
        r = _signed("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL})
        if r.status_code == 200:
            d = r.json()
            if d:
                p = d[0]
                amt = float(p.get("positionAmt", 0) or 0)
                entry = float(p.get("entryPrice", 0) or 0)
                mark = float(p.get("markPrice", 0) or 0)
                return {"amt": amt, "entry": entry, "mark": mark,
                        "notional": abs(amt) * mark, "unrealized": float(p.get("unRealizedProfit", 0) or 0)}
    except Exception as e:
        log.warning(f"포지션조회 실패: {e}")
    return {"amt": 0.0, "entry": 0.0, "mark": 0.0, "notional": 0.0, "unrealized": 0.0}


def _mark_price(sym: str = SYMBOL) -> float:
    try:
        r = requests.get(f"{FAPI}/fapi/v1/ticker/price", params={"symbol": sym}, timeout=8)
        if r.status_code == 200:
            return float(r.json()["price"])
    except Exception:
        pass
    return 0.0


def _step_decimals(step) -> int:
    """margin_guard.py와 동일 로직(2026-07-13 부동소수점 잔여 버그 수정) — 독립모듈 유지 위해 복제."""
    if step <= 0: return 8
    s = f"{step:.10f}".rstrip('0')
    return len(s.split('.')[1]) if '.' in s else 0


def _round_step(qty, step):
    """step 배수로 내림 + 소수자릿수 반올림(부동소수점 잔여 제거) — margin_guard.py와 동일."""
    if step <= 0: return qty
    import math
    steps = math.floor(qty / step + 1e-9)
    return round(steps * step, _step_decimals(step))


def _symbol_filters_futures(sym: str):
    """선물 LOT_SIZE stepSize, MIN_NOTIONAL 반환 — /fapi 전용(margin_guard._symbol_filters의 선물판)."""
    try:
        r = requests.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=10)
        for s in r.json()["symbols"]:
            if s["symbol"] == sym:
                f = s["filters"]
                step = next((float(x["stepSize"]) for x in f if x["filterType"] == "LOT_SIZE"), 0.0)
                minn = next((float(x.get("notional", 5.0)) for x in f if x["filterType"] == "MIN_NOTIONAL"), 5.0)
                return step, minn
    except Exception:
        pass
    return 0.0, 5.0


def get_futures_position(sym: str) -> dict | None:
    """임의 심볼의 선물 포지션 조회 — get_position()의 다중심볼판(코어는 BTCUSDT 고정이라 몰랐음).
    ★ 조회 실패 시 None 반환(포지션 0과 구분) — close_short_futures()가 "API실패"를 "포지션 없음"으로
    오인해 실제로 열려있는 포지션을 추적 포기하는 걸 막기 위함(margin_guard.py 청산실패 안전패턴과 동일)."""
    try:
        r = _signed("GET", "/fapi/v2/positionRisk", {"symbol": sym})
        if r.status_code == 200:
            d = r.json()
            if d:
                p = d[0]
                amt = float(p.get("positionAmt", 0) or 0)
                entry = float(p.get("entryPrice", 0) or 0)
                mark = float(p.get("markPrice", 0) or 0)
                return {"amt": amt, "entry": entry, "mark": mark,
                        "notional": abs(amt) * mark, "unrealized": float(p.get("unRealizedProfit", 0) or 0)}
            return {"amt": 0.0, "entry": 0.0, "mark": 0.0, "notional": 0.0, "unrealized": 0.0}
        log.warning(f"포지션조회 실패({sym}): status={r.status_code} {r.text[:200]}")
    except Exception as e:
        log.warning(f"포지션조회 예외({sym}): {e}")
    return None


def live_status() -> dict:
    cfg = load_config()
    lock_path = _file_lock(STATE)
    try:
        s = _load_state()
    finally:
        _release_lock(lock_path)
    return {"enabled": cfg["enabled"], "armed": cfg.get("armed_engines", []),
            "global_cap_usdt": cfg.get("global_cap_usdt", 0), "leverage": cfg.get("leverage", 2),
            "daily_loss_limit_usdt": cfg.get("daily_loss_limit_usdt", 0),
            "realized_pnl_today": s.get("realized_pnl_today", 0.0)}


class BinanceGuard:
    def __init__(self, engine: str):
        self.engine = engine

    def _gate(self, notional_usdt: float) -> tuple[bool, str]:
        cfg = load_config()
        if not cfg["enabled"]:
            return False, "글로벌 LIVE OFF"
        if self.engine not in cfg.get("armed_engines", []):
            return False, f"{self.engine} 미arm"
        cap = cfg.get("engine_caps_usdt", {}).get(self.engine)
        if cap is None:
            return False, f"{self.engine} 자본가드 미설정"
        # 명목노출/레버리지 = 증거금. 증거금이 엔진상한·전체상한 넘으면 차단
        lev = max(1, cfg.get("leverage", 2))
        margin = notional_usdt / lev
        if margin > cap:
            return False, f"엔진 증거금상한 초과({margin:.1f}>{cap})"
        if margin > cfg.get("global_cap_usdt", 0):
            return False, f"전체 상한 초과({margin:.1f}>{cfg.get('global_cap_usdt',0)})"
        lock_path = _file_lock(STATE)
        try:
            s = _load_state()
        finally:
            _release_lock(lock_path)
        dll = cfg.get("daily_loss_limit_usdt", 0)
        if s["realized_pnl_today"] <= -abs(dll):
            return False, f"일일 손실한도 도달({s['realized_pnl_today']:.2f})"
        return True, "OK"

    def _ledger(self, side, qty, notional, result):
        new = not LEDGER.exists()
        try:
            with open(LEDGER, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new: w.writerow(["time", "engine", "side", "symbol", "qty", "notional_usdt", "result"])
                w.writerow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), self.engine, side, SYMBOL,
                            f"{qty:.6f}", f"{notional:.2f}", str(result)[:200]])
        except Exception as e:
            log.warning(f"원장 기록 실패: {e}")

    def _set_leverage(self, lev):
        try:
            _signed("POST", "/fapi/v1/leverage", {"symbol": SYMBOL, "leverage": int(lev)})
        except Exception as e:
            log.warning(f"레버리지 설정 실패: {e}")

    def rebalance_long(self, target_notional_usdt: float) -> dict:
        """BTCUSDT 롱을 목표 명목노출까지 조정(시장가). target=0이면 전량 청산.
        4중 관문 통과 시에만 실주문, 아니면 dry."""
        cfg = load_config()
        price = _mark_price()
        if price <= 0:
            return {"error": "price 조회 실패"}

        pos = get_position()
        cur_notional = pos["amt"] * price   # 롱이면 +, 숏이면 -(코어는 롱만)
        delta_notional = target_notional_usdt - cur_notional
        # 최소 변화 필터 (명목 5 USDT 미만 변화는 스킵 — 바이낸스 최소주문/수수료 낭비 방지)
        if abs(delta_notional) < 5:
            return {"skip": True, "reason": f"변화 작음({delta_notional:.1f} USDT)"}

        # 증가(추가매수)일 때만 자본가드 — 목표 명목 기준 통과 확인. 감소(청산)는 항상 허용.
        if delta_notional > 0:
            ok, reason = self._gate(target_notional_usdt)
            if not ok:
                log.info(f"[{self.engine}] 롱조정 차단(dry) 목표{target_notional_usdt:.1f} — {reason}")
                self._ledger("buy", 0, delta_notional, f"DRY:{reason}")
                return {"dry": True, "reason": reason}
        else:
            # 청산 방향도 글로벌 OFF/미arm이면 실행 안 함(모의 일관성)
            if not cfg["enabled"] or self.engine not in cfg.get("armed_engines", []):
                self._ledger("sell", 0, delta_notional, "DRY:미arm")
                return {"dry": True, "reason": "미arm"}

        # 실주문
        self._set_leverage(cfg.get("leverage", 2))
        side = "BUY" if delta_notional > 0 else "SELL"
        qty = round(abs(delta_notional) / price, 3)  # BTC 수량 (0.001 단위)
        if qty <= 0:
            return {"skip": True, "reason": "수량 0"}
        try:
            r = _signed("POST", "/fapi/v1/order",
                        {"symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": qty})
            res = r.json()
            if r.status_code != 200:
                log.error(f"[{self.engine}] ★주문실패 {side} {qty} → {res}")
                self._ledger(side, qty, delta_notional, f"ERR:{res}")
                return {"error": res}
        except Exception as e:
            log.error(f"[{self.engine}] 주문 예외: {e}")
            self._ledger(side, qty, delta_notional, f"ERR:{e}")
            return {"error": str(e)}
        log.warning(f"[{self.engine}] ★실주문 {side} {qty}BTC(명목 {delta_notional:+.1f} USDT) @~{price:.0f} → {res.get('orderId')}")
        self._ledger(side, qty, delta_notional, res)
        return {"live": True, "result": res}

    def open_short_futures(self, coin: str, margin_usdt: float) -> dict:
        """선물 숏 진입 — margin_guard.open_short()의 선물판(대출 불필요, 재고 제약 없음).
        margin_short_trader의 마진대출 실패(-3045) 폴백 전용 — 백테스트 확인(2026-07-21):
        마진 대비 비용은 더 들지만(반전 구간 펀딩비 역풍) 여전히 순양수, 대출막힌 코인 잡는 용도.
        가드 통과 시에만 실주문."""
        cfg = load_config()
        ok, reason = self._gate(margin_usdt * cfg.get("leverage", 2))
        if not ok:
            log.info(f"[{self.engine}] 선물숏진입 차단(dry) {coin} 증거금{margin_usdt} — {reason}")
            self._ledger("open_short_fut", 0, margin_usdt, f"DRY:{reason}")
            return {"dry": True, "reason": reason}
        sym = f"{coin}USDT"
        price = _mark_price(sym)
        if price <= 0:
            return {"error": "price 실패"}
        lev = cfg.get("leverage", 2)
        notional = margin_usdt * lev
        step, minn = _symbol_filters_futures(sym)
        if notional < minn:
            return {"error": f"명목 {notional:.1f} < 최소주문 {minn}"}
        qty = _round_step(notional / price, step)
        if qty <= 0:
            return {"error": "수량 0"}
        try:
            _signed("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": int(lev)})
        except Exception as e:
            log.warning(f"[{self.engine}] 레버리지 설정 실패({sym}): {e}")
        try:
            r = _signed("POST", "/fapi/v1/order",
                        {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": qty})
            res = r.json()
            if r.status_code != 200:
                log.error(f"[{self.engine}] ★선물숏진입 실패 {sym} {qty} → {res}")
                self._ledger("open_short_fut", qty, notional, f"ERR:{res}")
                return {"error": res}
        except Exception as e:
            log.error(f"[{self.engine}] 선물숏진입 예외: {e}")
            self._ledger("open_short_fut", qty, notional, f"ERR:{e}")
            return {"error": str(e)}
        fill_qty = float(res.get("executedQty", qty))
        log.warning(f"[{self.engine}] ★선물숏진입(마진대출폴백) {sym} {fill_qty} (명목 {notional:.1f} USDT) @~{price:.6g}")
        self._ledger("open_short_fut", fill_qty, notional, res)
        return {"live": True, "qty": fill_qty, "entry_usdt": margin_usdt, "price": price, "result": res}

    def close_short_futures(self, coin: str) -> dict:
        """선물 숏 청산 — 현재 포지션 수량만큼 시장가 매수(BUY)로 반대매매."""
        cfg = load_config()
        if not cfg["enabled"] or self.engine not in cfg.get("armed_engines", []):
            self._ledger("close_short_fut", 0, 0, "DRY:미arm")
            return {"dry": True}
        sym = f"{coin}USDT"
        pos = get_futures_position(sym)
        if pos is None:
            log.error(f"[{self.engine}] ★선물숏청산: 포지션조회 실패 {sym} — 재시도 필요, 청산 시도 안 함")
            return {"error": "포지션조회 실패(재시도 필요)"}
        if pos["amt"] >= 0:
            return {"error": "숏 포지션 없음(amt>=0)"}
        qty = abs(pos["amt"])
        try:
            r = _signed("POST", "/fapi/v1/order",
                        {"symbol": sym, "side": "BUY", "type": "MARKET", "quantity": qty,
                         "reduceOnly": "true"})
            res = r.json()
            if r.status_code != 200:
                log.error(f"[{self.engine}] ★선물숏청산 실패 {sym} {qty} → {res}")
                self._ledger("close_short_fut", qty, 0, f"ERR:{res}")
                return {"error": res}
        except Exception as e:
            log.error(f"[{self.engine}] 선물숏청산 예외: {e}")
            self._ledger("close_short_fut", qty, 0, f"ERR:{e}")
            return {"error": str(e)}
        log.warning(f"[{self.engine}] ★선물숏청산 {sym} {res.get('executedQty')}")
        self._ledger("close_short_fut", qty, 0, res)
        return {"live": True, "result": res}

    def record_realized(self, pnl_usdt: float):
        lock_path = _file_lock(STATE)
        try:
            s = _load_state(); s["realized_pnl_today"] += pnl_usdt; _save_state(s)
            log.info(f"[{self.engine}] 실현손익 {pnl_usdt:+.2f} USDT → 당일누적 {s['realized_pnl_today']:+.2f}")
        finally:
            _release_lock(lock_path)


if __name__ == "__main__":
    # 자가검증: 기본 OFF 상태 확인 + 읽기전용 조회
    print("=== binance_guard 자가검증 ===")
    print("live_status:", json.dumps(live_status(), ensure_ascii=False))
    print("선물 USDT 잔고:", get_futures_usdt())
    print("BTCUSDT 포지션:", get_position())
    g = BinanceGuard("core_lev")
    print("gate(명목40USDT):", g._gate(40))
    print("rebalance_long(40) [OFF라 dry여야 함]:", g.rebalance_long(40))
