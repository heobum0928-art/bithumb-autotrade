"""
탐험 트레이더 에이전트 — Claude가 '사람 트레이더'로서 모의 매매 (실주문 0, 순수 가상).

목적(2026-06-27 사용자): 룰봇(임계값 기계)이 못 보는 패턴을 '사람처럼' 판단해 시도 →
다양한 진입 데이터 + 에이전트 추론 적중률 평가. 룰로 코드화 불가한 맥락(뉴스·분위기·
포지션 관리 재량)을 탐험. **검증된 전략이 목적 아님**(재량은 t값 불가) — 탐험·학습 데이터용.

설계(과거 CI 실패 교훈 반영): CI는 실시간 매신호 판단하다 1894/1902 무응답으로 멈춤 →
여기는 **1시간 배치**로 호출(무응답 문제 회피). 사람도 24시간 안 봄 = 능동 스윙 트레이더.

작동: 1시간마다 시장 스냅샷(시세·급등락·선물OI/펀딩·보유포지션) → Claude에게 사람
트레이더 페르소나로 결정 요청(JSON) → 모의 체결 → 매매일지 기록. 가상자본 100만원.
★ 실거래 절대 안 함: 빗썸 주문 API 미호출(get_ticker 읽기만). LiveGuard 미사용.

상태 data/explorer_pos.json | 일지 docs/explorer/journal_YYYY-MM-DD.md | 포트 47234
Run: python scripts/explorer_agent.py
"""
import sys, os, atexit, time, json, socket, logging, re, yaml
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
    try: _sock.bind(("127.0.0.1", 47234))
    except OSError: print("[ERROR] explorer_agent 이미 실행 중 (포트 47234)."); sys.exit(1)
    atexit.register(_sock.close)
_single()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.client import BithumbClient

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [EXPL] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("logs/explorer_agent.log", encoding="utf-8")])
log = logging.getLogger(__name__)

STABLE = {"USDT","USDC","DAI","TUSD","BUSD","FDUSD","PYUSD","USDS","KRW"}
POS = ROOT / "data" / "explorer_pos.json"
JOURNAL_DIR = ROOT / "docs" / "explorer"
CYCLE_SEC = 3600           # 1시간마다 (능동 스윙 주기)
START_CASH = 1_000_000     # 가상자본 100만원
TOPN = 25                  # 시장 스냅샷 코인 수
MODEL = "claude-sonnet-4-6"

# ── 트레이더 페르소나 (1호) ──
PERSONA = """너는 빗썸 한국 거래소에서 알트코인을 단타·스윙하는 개인 트레이더다.
성향: 공격적으로 기회를 노리되, 손절은 칼같이 지키는 타입. "많이 먹고 빠르게 빠진다"가 신조.
너는 룰 기계가 아니라 사람이다 — 차트 숫자뿐 아니라 시장 분위기, 거래소 선물 동향(청산·펀딩),
급등락의 '맥락'을 종합해 직관적으로 판단한다. 손실은 짧게, 수익은 길게.
지금은 모의(가상돈)지만 진짜 내 돈처럼 진지하게 굴린다. 무리한 풀베팅 금지, 한 종목 30% 이내."""


def load_api_key():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return cfg.get("anthropic_api_key") or cfg.get("anthropic", {}).get("api_key", "")


def load_state():
    if POS.exists():
        try: return json.loads(POS.read_text(encoding="utf-8"))
        except Exception: pass
    return {"cash": START_CASH, "positions": {}, "realized_pnl": 0.0, "n_trades": 0, "started": datetime.now(KST).isoformat()}


def save_state(s):
    tmp = POS.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, POS)


def all_prices(c):
    try:
        t = c.get_ticker("ALL")
        return {k: v for k, v in t.items() if k != "date" and k not in STABLE and isinstance(v, dict) and v.get("closing_price")}
    except Exception as e:
        log.warning(f"시세 실패: {e}"); return {}


def market_snapshot(c, t):
    """TOP 거래대금 코인 + 급등/급락 요약."""
    rows = []
    for coin, d in t.items():
        try:
            p = float(d["closing_price"]); prev = float(d.get("prev_closing_price", p) or p)
            v = float(d.get("acc_trade_value_24H", 0))
            chg = (p/prev - 1)*100 if prev > 0 else 0
            rows.append({"coin": coin, "price": p, "chg24h": round(chg, 1), "value_eok": round(v/1e8, 1)})
        except Exception: continue
    rows.sort(key=lambda x: -x["value_eok"])
    top = rows[:TOPN]
    gainers = sorted(rows, key=lambda x: -x["chg24h"])[:8]
    losers = sorted(rows, key=lambda x: x["chg24h"])[:8]
    return {"top_by_value": top, "top_gainers": gainers, "top_losers": losers}


def futures_recent():
    """선물신호 최근 1사이클 요약 (OI급감·펀딩 극단)."""
    csv_f = ROOT / "data" / "futures_signals.csv"
    if not csv_f.exists(): return []
    try:
        import csv as _csv
        with open(csv_f, encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
        if not rows: return []
        last_time = rows[-1]["time"]
        recent = [r for r in rows if r["time"] == last_time]
        out = []
        for r in recent:
            try:
                out.append({"coin": r["coin"], "funding": r["funding"],
                            "oi_chg_5m": r["oi_chg_5m"], "ls_ratio": r["ls_ratio"],
                            "taker": r["taker_ratio"]})
            except Exception: pass
        return out
    except Exception: return []


def equity(c, prices, s):
    val = s["cash"]
    for coin, p in s["positions"].items():
        cur = prices.get(coin, {})
        px = float(cur.get("closing_price", 0)) if cur else 0
        val += p["vol"] * px
    return val


def ask_trader(snapshot, futures, s, prices):
    import anthropic
    client = anthropic.Anthropic(api_key=load_api_key())
    # 보유 포지션 현재가/손익 첨부
    holdings = []
    for coin, p in s["positions"].items():
        cur = prices.get(coin, {})
        px = float(cur.get("closing_price", 0)) if cur else 0
        pnl = (px/p["entry"] - 1)*100 if p["entry"] > 0 else 0
        holdings.append({"coin": coin, "entry": p["entry"], "now": px,
                         "pnl_pct": round(pnl, 1), "vol": p["vol"],
                         "value_krw": round(px*p["vol"]), "since": p.get("entered","")[:16]})
    eq = round(equity(c_global, prices, s))
    prompt = f"""{PERSONA}

지금 시각: {datetime.now(KST):%Y-%m-%d %H:%M} KST

## 내 계좌
- 현금: {s['cash']:,.0f}원 / 총 평가액: {eq:,.0f}원 / 누적실현손익: {s['realized_pnl']:+,.0f}원 / 거래수: {s['n_trades']}

## 보유 포지션
{json.dumps(holdings, ensure_ascii=False) if holdings else "없음 (전액 현금)"}

## 시장 — 거래대금 상위
{json.dumps(snapshot['top_by_value'], ensure_ascii=False)}

## 급등 TOP
{json.dumps(snapshot['top_gainers'], ensure_ascii=False)}

## 급락 TOP
{json.dumps(snapshot['top_losers'], ensure_ascii=False)}

## Binance 선물 동향 (최근, OI 5분급감=청산발생, 펀딩 양수=롱과열)
{json.dumps(futures, ensure_ascii=False) if futures else "데이터 없음"}

---
지금 트레이더로서 결정해라. 반드시 아래 JSON 형식만 출력(설명 텍스트 금지):
{{
  "market_view": "지금 시장을 한 문장으로",
  "mood": "지금 심정 한 문장 (사람처럼)",
  "actions": [
    {{"action": "buy|add|sell_partial|sell_all|hold", "coin": "심볼", "krw": 매수금액(buy/add만, 정수), "sell_ratio": 청산비율0~1(sell_partial만), "reason": "이 결정의 근거"}}
  ]
}}
- 살 게 없으면 actions를 빈 배열로.
- buy/add는 현금 한도 내에서. 한 종목 평가액이 총자산 30% 넘기지 마라.
- 보유 종목은 손익 보고 hold/추가/청산 판단.
- 근거(reason)는 차트 숫자가 아니라 '왜 그렇게 보는지' 너의 판단을 적어라."""
    msg = client.messages.create(model=MODEL, max_tokens=1500,
                                 messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m: return None
    try: return json.loads(m.group(0))
    except Exception:
        # ```json 블록 시도
        m2 = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m2:
            try: return json.loads(m2.group(1))
            except Exception: return None
    return None


def execute(decision, s, prices):
    """모의 체결 — 빗썸 주문 호출 0, 가상 장부만."""
    acts = decision.get("actions", []) or []
    done = []
    for a in acts:
        act = a.get("action"); coin = (a.get("coin") or "").upper()
        cur = prices.get(coin, {})
        px = float(cur.get("closing_price", 0)) if cur else 0
        if act in ("buy", "add"):
            if px <= 0: continue
            krw = float(a.get("krw", 0) or 0)
            krw = min(krw, s["cash"])              # 현금 한도
            if krw < 5000: continue
            vol = krw / px
            if coin in s["positions"]:
                p = s["positions"][coin]
                tot_cost = p["entry"]*p["vol"] + krw
                p["vol"] += vol; p["entry"] = tot_cost / p["vol"]
            else:
                s["positions"][coin] = {"entry": px, "vol": vol, "entered": datetime.now(KST).isoformat()}
            s["cash"] -= krw
            done.append(f"{'추가' if act=='add' else '매수'} {coin} {krw:,.0f}원 @{px:,.4f}")
        elif act in ("sell_partial", "sell_all"):
            if coin not in s["positions"]: continue
            p = s["positions"][coin]
            ratio = 1.0 if act == "sell_all" else max(0.0, min(1.0, float(a.get("sell_ratio", 1) or 1)))
            sell_vol = p["vol"] * ratio
            proceeds = sell_vol * px
            pnl = (px - p["entry"]) * sell_vol
            s["cash"] += proceeds; s["realized_pnl"] += pnl; s["n_trades"] += 1
            p["vol"] -= sell_vol
            pnl_pct = (px/p["entry"]-1)*100 if p["entry"]>0 else 0
            done.append(f"{'전량' if ratio>=1 else f'{ratio*100:.0f}%'}청산 {coin} @{px:,.4f} PnL={pnl_pct:+.1f}%({pnl:+,.0f}원)")
            if p["vol"] * px < 1000:  # 잔량 먼지 정리
                del s["positions"][coin]
    return done


def journal(decision, done, s, eq):
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    f = JOURNAL_DIR / f"journal_{datetime.now(KST):%Y-%m-%d}.md"
    new = not f.exists()
    with open(f, "a", encoding="utf-8") as fh:
        if new:
            fh.write(f"# 탐험 트레이더 1호 매매일지 — {datetime.now(KST):%Y-%m-%d}\n\n")
            fh.write(f"가상자본 {START_CASH:,}원 · 성향: 공격적 단타(손절 철저)\n\n")
        fh.write(f"## {datetime.now(KST):%H:%M}\n")
        fh.write(f"- **시장뷰**: {decision.get('market_view','')}\n")
        fh.write(f"- **심정**: {decision.get('mood','')}\n")
        if done:
            fh.write(f"- **체결**: {' / '.join(done)}\n")
        else:
            fh.write(f"- **체결**: 관망\n")
        # 결정 근거
        for a in decision.get("actions", []) or []:
            if a.get("reason"):
                fh.write(f"  - {a.get('action')} {a.get('coin','')}: {a['reason']}\n")
        fh.write(f"- **평가액**: {eq:,.0f}원 (현금 {s['cash']:,.0f} / 실현 {s['realized_pnl']:+,.0f})\n\n")


c_global = None
def main():
    global c_global
    c_global = BithumbClient()
    if not load_api_key():
        log.error("config.yaml에 anthropic_api_key 없음 — 종료"); return
    s = load_state()
    log.info(f"탐험 트레이더 1호 시작 — 가상자본 {s['cash']:,.0f}원 보유{list(s['positions'])} | {CYCLE_SEC//60}분 주기 | 모의(실주문0)")
    try:
        from bithumb import notify
        notify.send(f"🧑‍💼 탐험 트레이더 1호 가동 — Claude가 사람처럼 모의매매(실주문0). 룰밖 패턴 탐험·근거기록")
    except Exception: pass
    while True:
        try:
            t = c_global.get_ticker("ALL")
            prices = {k: v for k, v in t.items() if k != "date" and isinstance(v, dict) and v.get("closing_price")}
            snap = market_snapshot(c_global, t)
            fut = futures_recent()
            raw = ask_trader(snap, fut, s, prices)
            decision = parse_json(raw)
            if not decision:
                log.warning(f"JSON 파싱 실패 — 응답앞부분: {raw[:200]}")
            else:
                done = execute(decision, s, prices)
                save_state(s)
                eq = round(equity(c_global, prices, s))
                journal(decision, done, s, eq)
                mv = decision.get("market_view","")[:50]
                if done:
                    log.info(f"[모의] {' / '.join(done)} | 평가{eq:,.0f}원 | {mv}")
                    try:
                        from bithumb import notify
                        notify.send(f"🧑‍💼 트레이더1호: {' / '.join(done)}\n평가 {eq:,.0f}원 (실현 {s['realized_pnl']:+,.0f})\n\"{decision.get('mood','')}\"")
                    except Exception: pass
                else:
                    log.info(f"[모의] 관망 | 평가{eq:,.0f}원 | {mv}")
        except KeyboardInterrupt:
            log.info("종료"); break
        except Exception as e:
            log.error(f"루프오류: {e}")
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main()
