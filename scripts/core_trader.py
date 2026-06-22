"""
코어 트레이더 — BTC 사이클 타이밍 (검증된 엔진, 모의 추적).

전략 (검증: SMA200 +30%/SMA50 +34% vs HODL -4%, MDD 절반):
  - BTC > 200일선×1.01  → FULL (BTC 100%)
  - BTC > 50일선×1.01   → SCOUT (BTC 30%, 전환 초입 정찰)
  - 둘 다 아래          → CASH (현금)
  3단계 자동 리밸런싱. 일봉 SMA(유지 파일), 현재가는 ticker. 비용 0.16%.

⚠️ 모의(노셔널 100만원). 실거래는 사용자 승인 후. 상태: data/core_state.json | 로그: logs/core_trader.log
Run: python scripts/core_trader.py --dry-run
"""
import sys, os, atexit, time, json, socket, logging, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
KST = timezone(timedelta(hours=9))

_sock=None
def _single():
    global _sock
    _sock=socket.socket(socket.AF_INET, socket.SOCK_STREAM); _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try: _sock.bind(("127.0.0.1", 47224))   # core 전용 (rt=47221,em=47222,ml=47223)
    except OSError: print("[ERROR] core_trader 이미 실행 중 (포트 47224)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient
from bithumb import notify
from bithumb.live_guard import LiveGuard, live_status, load_config

p=argparse.ArgumentParser(add_help=False); p.add_argument("--dry-run",action="store_true"); p.add_argument("--live",action="store_true")
_DRY = not p.parse_known_args()[0].live
_TAG = "CORE-DRY" if _DRY else "CORE"

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{_TAG}] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/core_trader.log", encoding="utf-8")])
log=logging.getLogger(__name__)

CORE_KRW=1_000_000        # 노셔널 모의 자본
SCOUT_FRAC, FULL_FRAC=0.30, 1.0
SMA_FAST, SMA_SLOW=50, 200
BAND=0.01                 # 1% 확인밴드
COST=0.0016
CHECK_SEC=1800            # 30분마다 (일봉 신호라 충분)
BTC_FILE=ROOT/"data"/"candles_daily"/"BTC_1d.json"
STATE=ROOT/"data"/"core_state.json"


def signals():
    """현재 BTC가, (SMA50 위?, SMA200 위?, 현재가, sma50, sma200) — 실패 시 None."""
    try:
        cl=[float(x["trade_price"]) for x in json.loads(BTC_FILE.read_text(encoding="utf-8"))]
        if len(cl)<SMA_SLOW: return None
        cur=cl[-1]; s50=sum(cl[-SMA_FAST:])/SMA_FAST; s200=sum(cl[-SMA_SLOW:])/SMA_SLOW
        return cur>s50*(1+BAND), cur>s200*(1+BAND), cur, s50, s200
    except Exception as e:
        log.warning(f"신호 조회 실패: {e}"); return None


def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"state":"CASH","btc_units":0.0,"cash":float(CORE_KRW),"last_price":0.0}


def save_state(s):
    tmp=STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s,indent=2),encoding="utf-8"); os.replace(tmp,STATE)


def rebalance(s, target_frac, price, reason):
    equity=s["cash"]+s["btc_units"]*price
    target_val=equity*target_frac; cur_val=s["btc_units"]*price; delta=target_val-cur_val
    if abs(delta)<equity*0.02: return False   # 2% 미만 변화는 스킵
    if delta>0:   # BTC 매수
        s["cash"]-=delta; s["btc_units"]+=delta*(1-COST)/price
    else:         # BTC 매도
        s["btc_units"]-=(-delta)/price; s["cash"]+=(-delta)*(1-COST)
    s["last_price"]=price
    eq=s["cash"]+s["btc_units"]*price
    log.warning(f"리밸런싱 → {reason}: BTC {target_frac*100:.0f}% (delta {delta:+,.0f}원) | 자산 {eq:,.0f}원 ({(eq/CORE_KRW-1)*100:+.1f}%)")
    try: notify.send(f"[{_TAG}] 코어 {reason} — BTC {target_frac*100:.0f}% 비중 @{price:,.0f} | 모의자산 {eq:,.0f}원({(eq/CORE_KRW-1)*100:+.1f}%)")
    except Exception: pass
    return True


def live_rebalance(c, g, target_frac, price, reason, core_live_krw):
    """실전 모드 — 실제 잔고 읽어 목표비중까지 가드 통해 실주문. 실패 시 거래 안 함(눈감고 매매 금지)."""
    try:
        bal = c.get_balance("BTC"); btc_units = krw_avail = 0.0
        for a in bal:
            if a.get("currency") == "BTC": btc_units = float(a.get("balance", 0) or 0)
            if a.get("currency") == "KRW": krw_avail = float(a.get("balance", 0) or 0)
    except Exception as e:
        log.error(f"[LIVE] 잔고조회 실패 — 거래 보류: {e}"); return
    cur_btc_krw = btc_units * price
    target_krw = core_live_krw * target_frac
    delta = target_krw - cur_btc_krw
    if abs(delta) < core_live_krw * 0.1:   # 10% 미만 변화 스킵
        log.info(f"[LIVE] 코어 {reason} — 변화작아 스킵(목표 {target_frac*100:.0f}%, delta {delta:+,.0f}원)"); return
    if delta > 0:
        res = g.execute_buy(c, "KRW-BTC", min(delta, krw_avail))
    else:
        vol = min((-delta) / price, btc_units)
        res = g.execute_sell(c, "KRW-BTC", vol, krw_hint=-delta)
    log.warning(f"[LIVE] 코어 {reason} — 목표 BTC {target_frac*100:.0f}%(@{price:,.0f}) → {res}")
    try: notify.send(f"[CORE-LIVE] {reason} — BTC {target_frac*100:.0f}% 목표 @{price:,.0f} | {('실매수' if delta>0 else '실매도')} {res.get('live') and '체결' or res.get('reason','')}")
    except Exception: pass


def main():
    if not _DRY: log.error("--live 플래그는 차단됨. 실전은 live_guard config로만 제어."); sys.exit(1)
    c=BithumbClient(); s=load_state()
    _ls = live_status(); _live = bool(_ls.get("enabled")) and "core" in _ls.get("armed", [])
    _mode = f"🔴실전(코어자본 {load_config().get('engine_caps_krw',{}).get('core',50_000):,}원)" if _live else "모의(노셔널)"
    log.info(f"코어 시작 [{_mode}] — SMA{SMA_FAST}>SCOUT30% / SMA{SMA_SLOW}>FULL100% / 밴드{BAND*100:.0f}% | 상태={s['state']} | 가드 enabled={_ls.get('enabled')} armed={_ls.get('armed')}")
    try: notify.send(f"[{_TAG}] core_trader 시작 — {_mode} (SMA50→정찰30%, SMA200→풀100%, 아래→현금)")
    except Exception: pass
    while True:
        try:
            sig=signals()
            if sig is None: time.sleep(CHECK_SEC); continue
            above50, above200, price, s50, s200 = sig
            target_frac = FULL_FRAC if above200 else (SCOUT_FRAC if above50 else 0.0)
            new_state = "FULL" if above200 else ("SCOUT" if above50 else "CASH")
            ls = live_status()
            live = bool(ls.get("enabled")) and "core" in ls.get("armed", [])
            if live:   # ★ 실전 모드 (가드 armed) — 실잔고 기준 실주문
                g = LiveGuard("core")
                core_live = load_config().get("engine_caps_krw", {}).get("core", 50_000)
                if new_state != s["state"]:
                    live_rebalance(c, g, target_frac, price, f"{s['state']}→{new_state}", core_live)
                    s["state"] = new_state; save_state(s)
                else:
                    log.info(f"[LIVE] 유지 {s['state']} | BTC {price:,.0f}(50선 {s50:,.0f}/200선 {s200:,.0f}) | 코어자본 {core_live:,}원")
            elif new_state != s["state"]:
                if rebalance(s, target_frac, price, f"{s['state']}→{new_state}"):
                    s["state"]=new_state; save_state(s)
            else:
                # 동일 상태: 자산 평가 로그만 (1시간마다)
                eq=s["cash"]+s["btc_units"]*price
                log.info(f"유지 {s['state']} | BTC {price:,.0f}(50선 {s50:,.0f}/200선 {s200:,.0f}) | 모의자산 {eq:,.0f}원({(eq/CORE_KRW-1)*100:+.1f}%)")
        except KeyboardInterrupt: break
        except Exception as e: log.error(f"루프오류: {e}")
        time.sleep(CHECK_SEC)


if __name__=="__main__":
    main()
