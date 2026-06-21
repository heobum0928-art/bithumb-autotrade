"""
하이브리드 트레이더 — 장세전환 모의(BEAR=현금 / BULL=BTC50%+알트Top3 모멘텀50%).

배경(2026-06-22 리서치):
  - 검증된 코어 = "BTC>200선×1.01이면 BTC, 아니면 현금"이 약세장 43%를 피하고 강세장 57%를 타서 +30%.
  - 사용자 요구: 강세장에 BTC만 들지 말고 알트 모멘텀을 얹어 그 +30%를 키우자(하이브리드).
  - 백테스트(walk-forward TEST, 비용0.16%): 강세구간만 분해 시 알트Top3 +27% vs BTC -3%(점추정).
    단 t=0.75(유의X)·강세구간 N=1·생존편향 스트레스시 -36%로 뒤집힘 → 백테스트로 진위판별 불가.
  - 결론: "강세장 forward 데이터(생존편향 없는)"를 모의로 모으는 게 유일한 검증법. 그래서 이 봇.
  - 블렌드(BTC50%+알트50%) 채택 이유: 알트순수보다 MDD 절반(-18.5%)·단일종목 생존리스크 분산 = 최저후회.

전략:
  - BEAR (BTC < 200선×1.01)        → 전량 현금
  - BULL (BTC > 200선×1.01)        → BTC 50% + 알트 Top3(20일 모멘텀, 유동성 플로어) 각 1/6
  - 일1회 리랭크(강세 시) + 장세전환 시 즉시 리밸런싱. 비용 0.16%/leg.

⚠️ 모의(노셔널 100만원). 실거래는 게이트 통과 + 사용자 승인 후. --live 차단.
상태: data/hybrid_state.json | 로그: logs/hybrid_trader.log
Run: python scripts/hybrid_trader.py --dry-run
"""
import sys, os, atexit, time, json, glob, socket, logging, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
KST = timezone(timedelta(hours=9))

_sock = None
def _single():
    global _sock
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM); _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try: _sock.bind(("127.0.0.1", 47225))   # hybrid 전용 (rt=47221,em=47222,ml=47223,core=47224)
    except OSError: print("[ERROR] hybrid_trader 이미 실행 중 (포트 47225)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb import notify

p = argparse.ArgumentParser(add_help=False); p.add_argument("--dry-run", action="store_true"); p.add_argument("--live", action="store_true")
_DRY = not p.parse_known_args()[0].live
_TAG = "HYB-DRY" if _DRY else "HYB"

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{_TAG}] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/hybrid_trader.log", encoding="utf-8")])
log = logging.getLogger(__name__)

# ── 파라미터 ─────────────────────────────────────────────
HYB_KRW = 1_000_000          # 노셔널 모의 자본
BTC_WEIGHT = 0.50            # 강세장 BTC 비중 (나머지 50%를 알트 Top-N 균등)
ALT_N = 3                    # 알트 모멘텀 Top-N
MOM_LB = 20                  # 모멘텀 룩백(일)
LIQ_FLOOR = 100_000_000      # 유동성 플로어: 20일 평균 거래대금(원)
SMA_SLOW = 200
BAND = 0.01                  # 1% 확인밴드
COST = 0.0016
CHECK_SEC = 21600            # 6시간마다 (일봉 신호라 충분)
DRIFT_SKIP = 0.02            # 자산대비 2% 미만 변화는 리밸런싱 스킵

DAILY_DIR = ROOT / "data" / "candles_daily"
STATE = ROOT / "data" / "hybrid_state.json"


def _closes(ticker: str) -> list[float] | None:
    f = DAILY_DIR / f"{ticker}_1d.json"
    if not f.exists(): return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return [float(x["trade_price"]) for x in d]
    except Exception:
        return None


def _liq_ok(ticker: str) -> bool:
    f = DAILY_DIR / f"{ticker}_1d.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        vols = [float(x.get("candle_acc_trade_price", 0)) for x in d[-MOM_LB:]]
        return len(vols) >= MOM_LB and sum(vols) / len(vols) >= LIQ_FLOOR
    except Exception:
        return False


def regime():
    """(is_bull, btc_price, sma200) — 실패 시 None."""
    cl = _closes("BTC")
    if not cl or len(cl) < SMA_SLOW: return None
    cur = cl[-1]; s200 = sum(cl[-SMA_SLOW:]) / SMA_SLOW
    return cur > s200 * (1 + BAND), cur, s200


def rank_alts() -> list[str]:
    """20일 모멘텀 상위 ALT_N 알트(유동성 플로어 통과, BTC 제외)."""
    scored = []
    for f in glob.glob(str(DAILY_DIR / "*_1d.json")):
        t = os.path.basename(f).replace("_1d.json", "")
        if t == "BTC": continue
        cl = _closes(t)
        if not cl or len(cl) < MOM_LB + 1: continue
        if not _liq_ok(t): continue
        mom = cl[-1] / cl[-1 - MOM_LB] - 1
        scored.append((mom, t))
    scored.sort(reverse=True)
    return [t for _, t in scored[:ALT_N]]


def latest_price(ticker: str) -> float:
    cl = _closes(ticker)
    return cl[-1] if cl else 0.0


def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"state": "CASH", "cash": float(HYB_KRW), "holdings": {}, "last_rebalance_date": None}


def save_state(s):
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=2), encoding="utf-8"); os.replace(tmp, STATE)


def equity_of(s, prices) -> float:
    return s["cash"] + sum(u * prices.get(t, 0) for t, u in s["holdings"].items())


def rebalance(s, targets: dict[str, float], prices: dict[str, float], reason: str) -> bool:
    """targets = {ticker: weight}. 자산 마크투마켓 후 비중 맞춰 매수/매도(비용 적용)."""
    eq = equity_of(s, prices)
    if eq <= 0: return False
    allt = set(s["holdings"]) | set(targets)
    changed = False
    for t in allt:
        price = prices.get(t, 0) or latest_price(t)
        if price <= 0: continue
        cur_val = s["holdings"].get(t, 0.0) * price
        tgt_val = eq * targets.get(t, 0.0)
        delta = tgt_val - cur_val
        if abs(delta) < eq * DRIFT_SKIP: continue
        if delta > 0:   # 매수
            s["cash"] -= delta
            s["holdings"][t] = s["holdings"].get(t, 0.0) + delta * (1 - COST) / price
        else:           # 매도
            s["holdings"][t] = s["holdings"].get(t, 0.0) - (-delta) / price
            s["cash"] += (-delta) * (1 - COST)
        if s["holdings"].get(t, 0.0) <= 1e-12:
            s["holdings"].pop(t, None)
        changed = True
    if changed:
        neq = equity_of(s, prices)
        held = ", ".join(f"{t} {targets.get(t,0)*100:.0f}%" for t in targets) or "현금"
        log.warning(f"리밸런싱 → {reason}: [{held}] | 모의자산 {neq:,.0f}원 ({(neq/HYB_KRW-1)*100:+.1f}%)")
        try: notify.send(f"[{_TAG}] 하이브리드 {reason} — {held} | 모의자산 {neq:,.0f}원({(neq/HYB_KRW-1)*100:+.1f}%)")
        except Exception: pass
    return changed


def build_targets(bull: bool) -> tuple[dict[str, float], list[str]]:
    if not bull:
        return {}, []
    alts = rank_alts()
    tw = {"BTC": BTC_WEIGHT}
    if alts:
        each = (1 - BTC_WEIGHT) / len(alts)
        for a in alts: tw[a] = each
    else:
        tw = {"BTC": 1.0}   # 알트 후보 없으면 전량 BTC (코어로 폴백)
    return tw, alts


def main():
    if not _DRY:
        log.error("LIVE는 게이트 통과 + 사용자 승인 필요."); sys.exit(1)
    s = load_state()
    log.info(f"하이브리드 시작 — 노셔널 {HYB_KRW:,}원 | BEAR=현금 / BULL=BTC{BTC_WEIGHT*100:.0f}%+알트Top{ALT_N}(모멘텀{MOM_LB}일) | 상태={s['state']}")
    try: notify.send(f"[{_TAG}] hybrid_trader 시작 — 약세=현금 / 강세=BTC50%+알트Top3 모멘텀50% 모의")
    except Exception: pass
    while True:
        try:
            reg = regime()
            if reg is None:
                log.warning("BTC 일봉 부족 — 대기"); time.sleep(CHECK_SEC); continue
            bull, btc, s200 = reg
            new_state = "BULL" if bull else "CASH"
            today = datetime.now(KST).date().isoformat()
            regime_flip = new_state != s["state"]
            new_day = s.get("last_rebalance_date") != today

            # 장세전환 즉시, 또는 강세장에서 하루 1회 리랭크
            if regime_flip or (bull and new_day):
                targets, alts = build_targets(bull)
                prices = {t: latest_price(t) for t in (set(targets) | set(s["holdings"]))}
                reason = f"{s['state']}→{new_state}" if regime_flip else f"{new_state} 일일리랭크"
                if rebalance(s, targets, prices, reason):
                    s["state"] = new_state
                    s["last_rebalance_date"] = today
                    save_state(s)
                elif regime_flip:   # 변화 작아 스킵돼도 상태/날짜는 갱신
                    s["state"] = new_state; s["last_rebalance_date"] = today; save_state(s)
            else:
                prices = {t: latest_price(t) for t in s["holdings"]}
                eq = equity_of(s, prices)
                log.info(f"유지 {s['state']} | BTC {btc:,.0f}(200선 {s200:,.0f}) | 모의자산 {eq:,.0f}원({(eq/HYB_KRW-1)*100:+.1f}%) | 보유 {list(s['holdings'])}")
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CHECK_SEC)


if __name__ == "__main__":
    main()
