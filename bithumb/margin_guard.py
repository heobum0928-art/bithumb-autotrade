"""
바이낸스 크로스마진 숏 실행가드 (margin_guard) — 마진 숏 전략 실전배관. 기본값 OFF.

마진 숏 흐름: 코인을 빌려서(borrow) 팔고(sell) → 나중에 되사서(buy) 갚기(repay).
바이낸스 sideEffectType가 자동 처리:
  진입(숏): side=SELL, sideEffectType=MARGIN_BUY (자동 borrow 후 매도)
  청산: side=BUY, sideEffectType=AUTO_REPAY (매수 후 자동 상환)

live_guard/binance_guard와 동일한 4중 관문 + FAIL-SAFE OFF.
설정 data/margin_live_config.json (git 미추적):
  {"enabled": false, "armed_engines": [], "engine_caps_usdt": {"mshort": 100},
   "global_cap_usdt": 100, "daily_loss_limit_usdt": 30, "leverage": 2, "test_mode": false}
원장 data/margin_orders.csv | 상태 data/margin_live_state.json
"""
import json, csv, time, hmac, hashlib, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
import requests, yaml

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "margin_live_config.json"
STATE = ROOT / "data" / "margin_live_state.json"
LEDGER = ROOT / "data" / "margin_orders.csv"
BASE = "https://api.binance.com"

log = logging.getLogger("margin_guard")
if not log.handlers:
    (ROOT / "logs").mkdir(exist_ok=True)
    h = logging.FileHandler(ROOT / "logs" / "margin_guard.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [MGUARD] %(message)s"))
    log.addHandler(h); log.setLevel(logging.INFO)


def _keys():
    c = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    b = c.get("binance", {})
    return b.get("api_key", ""), b.get("api_secret", "")


def load_config() -> dict:
    default = {"enabled": False, "armed_engines": [], "engine_caps_usdt": {},
               "global_cap_usdt": 100, "daily_loss_limit_usdt": 30, "leverage": 2, "test_mode": False}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        out = dict(default); out.update(cfg)
        out["enabled"] = (out.get("enabled") is True)
        return out
    except Exception:
        return default


def _load_state():
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


_time_offset = {"ms": None, "checked_at": 0.0}

def _synced_timestamp() -> int:
    """바이낸스 서버시각과 동기화한 타임스탬프.
    ★ 2026-07-13 버그: 로컬PC 시계가 서버보다 ~1.3초 앞서있어 간헐적으로 -1021(타임스탬프오류)
    발생 → get_margin_usdt() 등이 조용히 실패해 0.0 반환(잔고 0으로 잘못 표시됨). 서버시각과의
    오프셋을 5분마다 갱신해 보정."""
    now = time.time()
    if _time_offset["ms"] is None or now - _time_offset["checked_at"] > 300:
        try:
            r = requests.get(f"{BASE}/api/v3/time", timeout=5)
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
    params["timestamp"] = _synced_timestamp(); params["recvWindow"] = 5000
    qs = urlencode(params)
    sig = hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{qs}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}
    if method == "GET": return requests.get(url, headers=headers, timeout=10)
    if method == "POST": return requests.post(url, headers=headers, timeout=10)
    if method == "DELETE": return requests.delete(url, headers=headers, timeout=10)
    raise ValueError(method)


def _price(sym):
    r = requests.get(f"{BASE}/api/v3/ticker/price", params={"symbol": sym}, timeout=8)
    return float(r.json()["price"]) if r.status_code == 200 else 0.0


def _symbol_filters(sym):
    """LOT_SIZE stepSize, MIN_NOTIONAL 반환 — 주문수량 반올림/최소금액 확인용."""
    try:
        r = requests.get(f"{BASE}/api/v3/exchangeInfo", params={"symbol": sym}, timeout=8)
        f = r.json()["symbols"][0]["filters"]
        step = next((float(x["stepSize"]) for x in f if x["filterType"] == "LOT_SIZE"), 0.0)
        minn = next((float(x.get("minNotional", x.get("notional", 0))) for x in f if x["filterType"] in ("MIN_NOTIONAL", "NOTIONAL")), 5.0)
        return step, minn
    except Exception:
        return 0.0, 5.0


def _step_decimals(step) -> int:
    """step(예: 0.1, 0.001)의 소수자릿수. 부동소수점 잔여 제거용 round() 자릿수 계산에 사용."""
    if step <= 0: return 8
    s = f"{step:.10f}".rstrip('0')
    return len(s.split('.')[1]) if '.' in s else 0


def _round_step(qty, step):
    """step 배수로 내림 + step의 소수자릿수까지 반올림(부동소수점 잔여 제거).
    ★ 2026-07-13 버그: floor(qty/step)*step만 하면 174.60000000000002 같은 잔여가 남아
    바이낸스가 -51077(정밀도 초과)로 거부함. step 자체의 소수자릿수로 round()해서 제거."""
    if step <= 0: return qty
    import math
    steps = math.floor(qty / step + 1e-9)   # +eps: qty/step이 부동소수점오차로 정수 바로 아래 떨어지는 것 방지
    return round(steps * step, _step_decimals(step))


def _round_step_up(qty, step):
    """step 배수로 올림(청산 시 이자까지 넉넉히 갚기용) + 부동소수점 잔여 제거."""
    if step <= 0: return qty
    import math
    steps = math.ceil(qty / step - 1e-9)
    return round(steps * step, _step_decimals(step))


def get_margin_usdt() -> float:
    try:
        r = _signed("GET", "/sapi/v1/margin/account")
        if r.status_code == 200:
            for a in r.json().get("userAssets", []):
                if a["asset"] == "USDT":
                    return float(a["netAsset"])
    except Exception as e:
        log.warning(f"마진잔고 조회실패: {e}")
    return 0.0


def get_borrowed(coin) -> float:
    """해당 코인의 현재 대출(빌린) 수량 — 숏 포지션 크기."""
    try:
        r = _signed("GET", "/sapi/v1/margin/account")
        if r.status_code == 200:
            for a in r.json().get("userAssets", []):
                if a["asset"] == coin:
                    return float(a["borrowed"]) + float(a["interest"])
    except Exception as e:
        log.warning(f"대출조회 실패: {e}")
    return 0.0


def live_status():
    cfg = load_config(); s = _load_state()
    return {"enabled": cfg["enabled"], "armed": cfg.get("armed_engines", []),
            "global_cap_usdt": cfg.get("global_cap_usdt", 0), "leverage": cfg.get("leverage", 2),
            "daily_loss_limit_usdt": cfg.get("daily_loss_limit_usdt", 0),
            "realized_pnl_today": s.get("realized_pnl_today", 0.0), "test_mode": cfg.get("test_mode", False)}


class MarginGuard:
    def __init__(self, engine="mshort"):
        self.engine = engine

    def _gate(self, margin_usdt):
        cfg = load_config()
        if not cfg["enabled"]:
            return False, "글로벌 LIVE OFF"
        if self.engine not in cfg.get("armed_engines", []):
            return False, f"{self.engine} 미arm"
        cap = cfg.get("engine_caps_usdt", {}).get(self.engine)
        if cap is None:
            return False, f"{self.engine} 자본가드 미설정"
        if margin_usdt > cap:
            return False, f"엔진 증거금상한 초과({margin_usdt:.1f}>{cap})"
        if margin_usdt > cfg.get("global_cap_usdt", 0):
            return False, f"전체상한 초과"
        s = _load_state()
        if s["realized_pnl_today"] <= -abs(cfg.get("daily_loss_limit_usdt", 0)):
            return False, f"일일손실한도 도달({s['realized_pnl_today']:.2f})"
        return True, "OK"

    def _ledger(self, action, sym, qty, result):
        new = not LEDGER.exists()
        try:
            with open(LEDGER, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new: w.writerow(["time", "engine", "action", "symbol", "qty", "result"])
                w.writerow([datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), self.engine, action, sym,
                            f"{qty:.8f}", str(result)[:250]])
        except Exception as e:
            log.warning(f"원장기록 실패: {e}")

    def open_short(self, coin, margin_usdt):
        """마진 숏 진입: 증거금×레버리지 명목만큼 코인을 빌려서 시장가 매도.
        가드 통과 시에만 실주문. 반환: {live/dry, qty, ...}."""
        cfg = load_config()
        ok, reason = self._gate(margin_usdt)
        if not ok:
            log.info(f"[{self.engine}] 숏진입 차단(dry) {coin} 증거금{margin_usdt} — {reason}")
            self._ledger("open_short", coin, 0, f"DRY:{reason}")
            return {"dry": True, "reason": reason}
        sym = f"{coin}USDT"
        price = _price(sym)
        if price <= 0:
            return {"error": "price 실패"}
        lev = cfg.get("leverage", 2)
        notional = margin_usdt * lev
        step, minn = _symbol_filters(sym)
        if notional < minn:
            return {"error": f"명목 {notional:.1f} < 최소주문 {minn}"}
        qty = _round_step(notional / price, step)
        if qty <= 0:
            return {"error": "수량 0"}
        # 시장가 매도 + 자동 borrow
        try:
            r = _signed("POST", "/sapi/v1/margin/order",
                        {"symbol": sym, "side": "SELL", "type": "MARKET",
                         "quantity": qty, "sideEffectType": "MARGIN_BUY", "isIsolated": "FALSE"})
            res = r.json()
            if r.status_code != 200:
                log.error(f"[{self.engine}] ★숏진입 실패 {sym} {qty} → {res}")
                self._ledger("open_short", coin, qty, f"ERR:{res}")
                return {"error": res}
        except Exception as e:
            self._ledger("open_short", coin, qty, f"ERR:{e}")
            return {"error": str(e)}
        fill_qty = float(res.get("executedQty", qty))
        fill_usdt = float(res.get("cummulativeQuoteQty", qty * price))
        log.warning(f"[{self.engine}] ★마진숏진입 {sym} {fill_qty} (수취 {fill_usdt:.2f} USDT) @~{price:.6g}")
        self._ledger("open_short", coin, fill_qty, res)
        return {"live": True, "qty": fill_qty, "entry_usdt": fill_usdt, "price": price, "result": res}

    def close_short(self, coin):
        """마진 숏 청산: 빌린 수량을 시장가 매수 + 자동상환."""
        cfg = load_config()
        if not cfg["enabled"] or self.engine not in cfg.get("armed_engines", []):
            self._ledger("close_short", coin, 0, "DRY:미arm")
            return {"dry": True}
        sym = f"{coin}USDT"
        borrowed = get_borrowed(coin)
        if borrowed <= 0:
            return {"error": "대출수량 0(청산할 숏 없음)"}
        step, _ = _symbol_filters(sym)
        # 이자까지 갚으려면 살짝 넉넉히 — 스텝 올림(부동소수점 잔여 제거 포함)
        qty = _round_step_up(borrowed, step)
        try:
            r = _signed("POST", "/sapi/v1/margin/order",
                        {"symbol": sym, "side": "BUY", "type": "MARKET",
                         "quantity": qty, "sideEffectType": "AUTO_REPAY", "isIsolated": "FALSE"})
            res = r.json()
            if r.status_code != 200:
                log.error(f"[{self.engine}] ★숏청산 실패 {sym} {qty} → {res}")
                self._ledger("close_short", coin, qty, f"ERR:{res}")
                return {"error": res}
        except Exception as e:
            self._ledger("close_short", coin, qty, f"ERR:{e}")
            return {"error": str(e)}
        fill_usdt = float(res.get("cummulativeQuoteQty", 0))
        log.warning(f"[{self.engine}] ★마진숏청산 {sym} {res.get('executedQty')} (지불 {fill_usdt:.2f} USDT)")
        self._ledger("close_short", coin, qty, res)
        return {"live": True, "close_usdt": fill_usdt, "result": res}

    def record_realized(self, pnl_usdt):
        s = _load_state(); s["realized_pnl_today"] += pnl_usdt; _save_state(s)
        log.info(f"[{self.engine}] 실현손익 {pnl_usdt:+.2f} USDT → 당일 {s['realized_pnl_today']:+.2f}")


if __name__ == "__main__":
    print("=== margin_guard 자가검증 (기본 OFF) ===")
    print("live_status:", json.dumps(live_status(), ensure_ascii=False))
    print("마진 USDT 잔고:", get_margin_usdt())
    g = MarginGuard("mshort")
    print("gate(50):", g._gate(50))
    print("open_short(BTC,10) [OFF라 dry]:", g.open_short("BTC", 10))
