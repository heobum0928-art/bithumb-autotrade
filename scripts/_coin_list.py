"""[리스트] 선별 규칙으로 '집중 후보 코인' 랭킹 산출.
규칙: 펌핑빈도(일간 +10%↑ 비율) 기준, 최근창과 과거창 둘 다 높은(지속) 코인 상위.
주의: 생존편향 — 현재 상장·고거래량 코인만 대상. 동적 갱신 전제. dry-run 검증 필수."""
import sys, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategy_lab as lab

RECENT = 120   # 최근 N거래일(약 4개월)
THR = 0.10     # 펌핑 기준 일간 +10%


def metrics(candles, window=None):
    cl = [c["trade_price"] for c in candles]
    if window:
        cl = cl[-window:]
    rets = [cl[i] / cl[i - 1] - 1 for i in range(1, len(cl)) if cl[i - 1] > 0]
    if not rets:
        return 0.0, 0.0, 0.0
    pf = sum(1 for r in rets if r > THR) / len(rets) * 100   # 펌핑빈도%
    mr = st.mean(rets) * 100                                  # 일평균수익%
    mx = max(rets) * 100                                      # 최대 일간상승%
    return pf, mr, mx


def turnover(d):
    vals = []
    for c in d[-60:]:
        tp = c.get("candle_acc_trade_price") or c.get("candle_acc_trade_volume", 0) * c.get("trade_price", 0)
        vals.append(tp)
    return st.median(vals) / 1e8 if vals else 0   # 억원


def main():
    cc = lab.load_candles()
    rows = []
    for coin, d in cc.items():
        pf_all, mr_all, _ = metrics(d)
        pf_rec, mr_rec, mx_rec = metrics(d, RECENT)
        tov = turnover(d)
        # 지속점수: 최근·전체 펌핑빈도 둘 다 반영 (둘 다 높아야 상위)
        persist = (pf_all * pf_rec) ** 0.5
        rows.append((coin, pf_rec, pf_all, persist, mr_rec, tov))
    rows.sort(key=lambda r: r[3], reverse=True)   # 지속점수 내림차순

    print(f"보유 {len(rows)}코인 | 펌핑빈도(일간+{THR*100:.0f}%↑) 최근{RECENT}일·전체 지속점수 랭킹\n")
    print(f"{'순위':>3} {'코인':<9} {'최근펌핑%':>7} {'전체펌핑%':>7} {'지속점수':>6} {'최근일평균%':>9} {'거래대금억':>8}")
    print("-" * 60)
    for i, (coin, pfr, pfa, ps, mrr, tov) in enumerate(rows, 1):
        mark = "  ★집중" if i <= 12 and ps > 0 else ""
        print(f"{i:>3} {coin:<9} {pfr:>7.1f} {pfa:>7.1f} {ps:>6.2f} {mrr:>9.2f} {tov:>8.0f}{mark}")
    print("\n★집중 = 규칙 통과 상위 12 (지속점수>0). 동적 갱신·dry-run 검증 전제.")


if __name__ == "__main__":
    main()
