"""[검증 #21 강건성] 선별 universe LEAD 압박검증.
①클린OOS: TRAIN[0,0.6]만으로 펌핑코인 선별 + 거래대금≥20억 필터 → TEST[0.6,1.0] 측정.
②아웃라이어: TEST에서 상위 1·2건 빼도 t 살아남나(엣지가 대박 몇건 의존인가).
③장세분리: TEST를 BEAR/BULL로 쪼개 — 약세장에서도 되나(우리 실상황).
대조군: 전체 universe TEST. 돌파-재테스트 근사(돌파20+트레일5), 비용0.16%."""
import sys, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategy_lab as lab

COST = 0.0016
SPEC = {"name": "돌파20+트레일5",
        "entry": {"type": "breakout", "params": {"n": 20}},
        "exit": {"type": "trail", "params": {"trail": 0.05, "activate": 0.01, "sl": -0.10, "timeout": 30}},
        "filter": {"regime": "any"}}


def train_pumpfreq(d, thr=0.10):
    n = len(d); b = int(n * 0.6)
    cl = [c["trade_price"] for c in d[:b]]
    rets = [cl[i]/cl[i-1]-1 for i in range(1, len(cl)) if cl[i-1] > 0]
    return sum(1 for r in rets if r > thr)/len(rets)*100 if rets else 0


def turnover(d):
    vals = [(c.get("candle_acc_trade_price") or 0) for c in d[-60:]]
    return st.median(vals)/1e8 if vals else 0


def stat(label, rets):
    n = len(rets)
    if not n:
        print(f"  {label:22} 0건"); return
    p = [r*100 for r in rets]
    avg = sum(p)/n; sd = st.pstdev(p) if n > 1 else 0
    t = avg/(sd/n**0.5) if sd else 0
    wr = sum(1 for x in p if x > 0)/n*100
    print(f"  {label:22} {n:3}건 승률{wr:3.0f}% 거래당{avg:+.2f}% t{t:+.2f}")


def trades_with_date(cc_sel, reg):
    out = []
    for coin, d in cc_sel.items():
        for tr in lab._trades_for_coin(d, SPEC, COST, 0.6, 1.0):
            out.append((tr["ret"], reg.get(tr["date"])))
    return out


def main():
    cc = lab.load_candles(); reg = lab.btc_regime()
    # ① TRAIN으로만 선별 (클린 OOS) + 거래대금 필터
    scored = [(c, train_pumpfreq(d), turnover(d)) for c, d in cc.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    half = len(scored)//2
    pumpy = [c for c, pf, tv in scored[:half]]                       # TRAIN 펌핑 상위 절반
    curated = [c for c, pf, tv in scored[:half] if tv >= 20]          # + 거래대금≥20억
    print(f"전체 {len(cc)}코인 | TRAIN선별 펌핑상위 {len(pumpy)} / 그중 거래대금≥20억 정제 {len(curated)}")
    print(f"정제 리스트: {', '.join(curated)}\n")

    base = trades_with_date(cc, reg)
    pset = trades_with_date({k: cc[k] for k in pumpy}, reg)
    cset = trades_with_date({k: cc[k] for k in curated}, reg)

    print("=== ① 클린 OOS (TRAIN선별 → TEST측정) ===")
    stat("전체 universe", [r for r, _ in base])
    stat("펌핑상위 집중", [r for r, _ in pset])
    stat("정제(펌핑+≥20억)", [r for r, _ in cset])

    print("\n=== ② 아웃라이어 강건성 (정제 리스트) ===")
    cr = sorted([r for r, _ in cset], reverse=True)
    stat("정제 전체", cr)
    stat("최상위 1건 제거", cr[1:])
    stat("최상위 2건 제거", cr[2:])

    print("\n=== ③ 장세분리 (정제 리스트 TEST) — 약세장에서도 되나 ===")
    stat("BEAR 진입", [r for r, g in cset if g == "BEAR"])
    stat("BULL 진입", [r for r, g in cset if g == "BULL"])


if __name__ == "__main__":
    main()
