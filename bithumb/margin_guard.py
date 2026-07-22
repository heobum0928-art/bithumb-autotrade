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
import json, csv, time, hmac, hashlib, logging, os
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


def _file_lock(path, timeout=5.0):
    """★ 2026-07-13 버그수정: mshort·rsishort·manualshort 3개 프로세스가 같은
    margin_live_state.json에 동시 read-modify-write하면 한쪽 손실기록이 유실되어
    일일손실한도 게이트가 무력화될 수 있었음(에이전트 감사로 발견). 배타적 파일생성 기반
    락으로 read-modify-write를 직렬화. Windows 호환(os.open O_CREAT|O_EXCL)."""
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
                        os.remove(lock_path)   # 비정상종료로 남은 오래된 락 제거 후 재시도
                except Exception:
                    pass
                deadline = time.time() + timeout
            time.sleep(0.05)


def _release_lock(lock_path):
    try: os.remove(lock_path)
    except Exception: pass


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
        log.error(f"마진잔고 조회실패: {e}")
    return 0.0


def get_margin_level() -> float:
    """계좌 전체 담보비율(marginLevel) — 롱/숏 엔진이 계좌를 공유하므로 신규진입 안전판으로 사용.
    ★ 조회 실패 시 의도적으로 FAIL-OPEN(999.0, 무제한 취급) — 이 값 하나가 -gate()의 유일한
    안전장치가 아니라 기존 4중 관문(cap·daily_loss_limit 등) 위에 얹은 보조 방어선이고,
    실제 청산 방지는 결국 바이낸스 자체 마진콜 로직이 최종 보루이기 때문. 대신 여기서
    조용히 넘어가지 않도록 log.error로 남겨 추적 가능하게 함(get_margin_usdt()의 같은
    실패 경로가 IP차단으로 오진됐던 사례 재발 방지 — 여긴 별도 태그로 구분)."""
    try:
        r = _signed("GET", "/sapi/v1/margin/account")
        if r.status_code == 200:
            return float(r.json().get("marginLevel", 999))
        log.error(f"마진레벨 조회 실패(status={r.status_code}) — FAIL-OPEN(999)으로 진행, 원인 확인 필요")
    except Exception as e:
        log.error(f"마진레벨 조회 예외({e}) — FAIL-OPEN(999)으로 진행, 원인 확인 필요")
    return 999.0


def get_borrowed(coin) -> float | None:
    """해당 코인의 현재 대출(빌린) 수량 — 숏 포지션 크기.
    ★ API실패/예외 시 None 반환(0.0과 구분) — get_futures_position()과 동일 안전패턴.
    호출부(close_short)에서 "진짜 대출 0(청산완료)"과 "조회실패(청산보류해야함)"를 구분해야 함."""
    try:
        r = _signed("GET", "/sapi/v1/margin/account")
        if r.status_code == 200:
            for a in r.json().get("userAssets", []):
                if a["asset"] == coin:
                    return float(a["borrowed"]) + float(a["interest"])
            return 0.0   # 목록에 없음 = 진짜 대출 0(정상)
        log.error(f"대출조회 실패(status={r.status_code})")
    except Exception as e:
        log.error(f"대출조회 실패: {e}")
    return None


def get_held(coin) -> float | None:
    """해당 코인의 현재 보유(매수한, 자유잔고) 수량 — 롱 포지션 크기. get_borrowed()의 롱 버전.
    ★ API실패/예외 시 None 반환(0.0과 구분) — 위와 동일 이유."""
    try:
        r = _signed("GET", "/sapi/v1/margin/account")
        if r.status_code == 200:
            for a in r.json().get("userAssets", []):
                if a["asset"] == coin:
                    return float(a["free"])
            return 0.0   # 목록에 없음 = 진짜 보유 0(정상)
        log.error(f"보유조회 실패(status={r.status_code})")
    except Exception as e:
        log.error(f"보유조회 실패: {e}")
    return None


def live_status():
    cfg = load_config()
    lock_path = _file_lock(STATE)
    try:
        s = _load_state()
    finally:
        _release_lock(lock_path)
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
        # ★ 2026-07-13: record_realized()와 동일 락으로 상태읽기 보호 — 완전한 원자성(진입 결정~주문 전체)은
        #   아니지만(네트워크 주문호출까지 락을 걸면 크래시 시 데드락 위험), 최소한 절반쓰기 상태를 읽는
        #   것과 record_realized()의 갱신이 서로 겹치는 걸 막아 경쟁창을 최대한 좁힘.
        lock_path = _file_lock(STATE)
        try:
            s = _load_state()
        finally:
            _release_lock(lock_path)
        if s["realized_pnl_today"] <= -abs(cfg.get("daily_loss_limit_usdt", 0)):
            return False, f"일일손실한도 도달({s['realized_pnl_today']:.2f})"
        # ★ 2026-07-22: 계좌를 롱/숏 엔진이 공유해서 한쪽이 크게 차입하면 담보비율이 나빠질 수 있음.
        #   신규진입 전 모든 엔진 공통으로 여기서 한 번 확인(단일 관문) — 청산위험(통상 1.1 부근) 대비
        #   여유를 두고 1.5 미만이면 신규진입 차단(기존 포지션엔 영향 없음, 청산 자체는 거래소가 별도 처리).
        margin_level = get_margin_level()
        if margin_level < 1.5:
            return False, f"계좌 담보비율 낮음(marginLevel={margin_level:.2f}<1.5) — 신규진입 보류"
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
            log.error(f"[{self.engine}] ★숏진입 실패 {sym} — 가격조회 실패(price<=0)")
            return {"error": "price 실패"}
        lev = cfg.get("leverage", 2)
        notional = margin_usdt * lev
        step, minn = _symbol_filters(sym)
        if notional < minn:
            log.error(f"[{self.engine}] ★숏진입 실패 {sym} — 명목 {notional:.1f} < 최소주문 {minn}(증거금{margin_usdt:.2f}×{lev}배)")
            return {"error": f"명목 {notional:.1f} < 최소주문 {minn}"}
        qty = _round_step(notional / price, step)
        if qty <= 0:
            log.error(f"[{self.engine}] ★숏진입 실패 {sym} — 반올림후 수량0(notional={notional:.2f} price={price})")
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
        if borrowed is None:
            return {"error": "대출조회 실패(API) — 청산 보류, 다음 재시도"}
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

    def open_long(self, coin, margin_usdt):
        """마진 롱 진입: 증거금×레버리지 명목만큼 USDT를 빌려서 시장가 매수.
        가드 통과 시에만 실주문. 반환: {live/dry, qty, ...}. open_short의 방향 반대(빌리는 게 USDT)."""
        cfg = load_config()
        ok, reason = self._gate(margin_usdt)
        if not ok:
            log.info(f"[{self.engine}] 롱진입 차단(dry) {coin} 증거금{margin_usdt} — {reason}")
            self._ledger("open_long", coin, 0, f"DRY:{reason}")
            return {"dry": True, "reason": reason}
        sym = f"{coin}USDT"
        price = _price(sym)
        if price <= 0:
            log.error(f"[{self.engine}] ★롱진입 실패 {sym} — 가격조회 실패(price<=0)")
            return {"error": "price 실패"}
        lev = cfg.get("leverage", 2)
        notional = margin_usdt * lev
        step, minn = _symbol_filters(sym)
        if notional < minn:
            log.error(f"[{self.engine}] ★롱진입 실패 {sym} — 명목 {notional:.1f} < 최소주문 {minn}(증거금{margin_usdt:.2f}×{lev}배)")
            return {"error": f"명목 {notional:.1f} < 최소주문 {minn}"}
        qty = _round_step(notional / price, step)
        if qty <= 0:
            log.error(f"[{self.engine}] ★롱진입 실패 {sym} — 반올림후 수량0(notional={notional:.2f} price={price})")
            return {"error": "수량 0"}
        # 시장가 매수 + 자동 borrow(USDT 부족분)
        try:
            r = _signed("POST", "/sapi/v1/margin/order",
                        {"symbol": sym, "side": "BUY", "type": "MARKET",
                         "quantity": qty, "sideEffectType": "MARGIN_BUY", "isIsolated": "FALSE"})
            res = r.json()
            if r.status_code != 200:
                log.error(f"[{self.engine}] ★롱진입 실패 {sym} {qty} → {res}")
                self._ledger("open_long", coin, qty, f"ERR:{res}")
                return {"error": res}
        except Exception as e:
            self._ledger("open_long", coin, qty, f"ERR:{e}")
            return {"error": str(e)}
        fill_qty = float(res.get("executedQty", qty))
        fill_usdt = float(res.get("cummulativeQuoteQty", qty * price))
        log.warning(f"[{self.engine}] ★마진롱진입 {sym} {fill_qty} (지불 {fill_usdt:.2f} USDT) @~{price:.6g}")
        self._ledger("open_long", coin, fill_qty, res)
        return {"live": True, "qty": fill_qty, "entry_usdt": fill_usdt, "price": price, "result": res}

    def close_long(self, coin):
        """마진 롱 청산: 보유 수량을 시장가 매도 + 자동상환(빌린 USDT). close_short과 반대로
        '보유량 초과 매도'를 막아야 하므로 내림(_round_step)을 씀(청산은 _round_step_up이 아님)."""
        cfg = load_config()
        if not cfg["enabled"] or self.engine not in cfg.get("armed_engines", []):
            self._ledger("close_long", coin, 0, "DRY:미arm")
            return {"dry": True}
        sym = f"{coin}USDT"
        held = get_held(coin)
        if held is None:
            return {"error": "보유조회 실패(API) — 청산 보류, 다음 재시도"}
        if held <= 0:
            return {"error": "보유수량 0(청산할 롱 없음)"}
        step, _ = _symbol_filters(sym)
        qty = _round_step(held, step)
        if qty <= 0:
            return {"error": "수량 0"}
        try:
            r = _signed("POST", "/sapi/v1/margin/order",
                        {"symbol": sym, "side": "SELL", "type": "MARKET",
                         "quantity": qty, "sideEffectType": "AUTO_REPAY", "isIsolated": "FALSE"})
            res = r.json()
            if r.status_code != 200:
                log.error(f"[{self.engine}] ★롱청산 실패 {sym} {qty} → {res}")
                self._ledger("close_long", coin, qty, f"ERR:{res}")
                return {"error": res}
        except Exception as e:
            self._ledger("close_long", coin, qty, f"ERR:{e}")
            return {"error": str(e)}
        fill_usdt = float(res.get("cummulativeQuoteQty", 0))
        log.warning(f"[{self.engine}] ★마진롱청산 {sym} {res.get('executedQty')} (수취 {fill_usdt:.2f} USDT)")
        self._ledger("close_long", coin, qty, res)
        return {"live": True, "close_usdt": fill_usdt, "result": res}

    def record_realized(self, pnl_usdt):
        lock_path = _file_lock(STATE)
        try:
            s = _load_state(); s["realized_pnl_today"] += pnl_usdt; _save_state(s)
            log.info(f"[{self.engine}] 실현손익 {pnl_usdt:+.2f} USDT → 당일 {s['realized_pnl_today']:+.2f}")
        finally:
            _release_lock(lock_path)


if __name__ == "__main__":
    print("=== margin_guard 자가검증 (기본 OFF) ===")
    print("live_status:", json.dumps(live_status(), ensure_ascii=False))
    print("마진 USDT 잔고:", get_margin_usdt())
    g = MarginGuard("mshort")
    print("gate(50):", g._gate(50))
    print("open_short(BTC,10) [OFF라 dry]:", g.open_short("BTC", 10))
