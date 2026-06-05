"""
TP/SL 최적화 분석 스크립트
현재 설정: TP=+3%, SL=-3%
trades 테이블 + signal_log outcome 기반 분석
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import sqlite3
import statistics
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"

def banner(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# ── 0. 스키마 확인 ──────────────────────────────────────────
banner("0. 테이블 컬럼 확인")
for tbl in ("trades", "signal_log"):
    cur.execute(f"PRAGMA table_info({tbl})")
    cols = [r["name"] for r in cur.fetchall()]
    print(f"  {tbl}: {cols}")

# ── 1. trades 전체 조회 ────────────────────────────────────
banner("1. trades 실거래 결과 전체")
cur.execute("""
    SELECT coin, pnl_pct, hold_sec, exit_reason, entered_at, exited_at
    FROM trades
    ORDER BY entered_at DESC
""")
trades = [dict(r) for r in cur.fetchall()]

if not trades:
    print("  ⚠️  trades 데이터 없음")
else:
    print(f"  총 거래 수: {len(trades)}")
    pnl_list = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
    hold_list = [t["hold_sec"] for t in trades if t["hold_sec"] is not None]

    wins   = [p for p in pnl_list if p >= 0]
    losses = [p for p in pnl_list if p < 0]
    print(f"  승: {len(wins)} / 패: {len(losses)} / 승률: {len(wins)/len(pnl_list)*100:.1f}%")
    print(f"  평균 PnL: {statistics.mean(pnl_list):+.2f}%")
    print(f"  중앙값 PnL: {statistics.median(pnl_list):+.2f}%")
    if len(pnl_list) >= 2:
        print(f"  표준편차: {statistics.stdev(pnl_list):.2f}%")
    print(f"  최대 익절: {max(pnl_list):+.2f}%  최대 손절: {min(pnl_list):+.2f}%")
    if hold_list:
        print(f"  평균 보유 시간: {statistics.mean(hold_list):.0f}초 ({statistics.mean(hold_list)/60:.1f}분)")

    print("\n  exit_reason 분포:")
    reason_cnt = defaultdict(int)
    for t in trades:
        reason_cnt[t.get("exit_reason") or "None"] += 1
    for r, c in sorted(reason_cnt.items(), key=lambda x: -x[1]):
        print(f"    {r}: {c}건")

    print("\n  개별 거래 목록:")
    print(f"  {'코인':<8} {'pnl_pct':>8} {'hold_sec':>9} {'exit_reason':<15} {'entered_at'}")
    for t in trades:
        print(f"  {t['coin']:<8} {(t['pnl_pct'] or 0):>+8.2f}% {(t['hold_sec'] or 0):>8.0f}s "
              f"  {(t['exit_reason'] or '-'):<15} {t['entered_at'] or '-'}")

# ── 2. signal_log outcome 분포 ─────────────────────────────
banner("2. signal_log outcome_5m / outcome_30m 분포")
try:
    cur.execute("""
        SELECT coin, outcome_5m, outcome_30m, detected_at
        FROM signal_log
        WHERE outcome_5m IS NOT NULL OR outcome_30m IS NOT NULL
        ORDER BY detected_at DESC
    """)
    signals = [dict(r) for r in cur.fetchall()]
except Exception as e:
    print(f"  signal_log 조회 오류: {e}")
    signals = []

if not signals:
    print("  ⚠️  signal_log outcome 데이터 없음")
else:
    o5  = [s["outcome_5m"]  for s in signals if s["outcome_5m"]  is not None]
    o30 = [s["outcome_30m"] for s in signals if s["outcome_30m"] is not None]
    print(f"  outcome_5m 유효 샘플: {len(o5)}  |  outcome_30m 유효 샘플: {len(o30)}")

    if o5:
        print(f"\n  [outcome_5m]")
        print(f"    평균: {statistics.mean(o5):+.2f}%  중앙값: {statistics.median(o5):+.2f}%")
        print(f"    최대: {max(o5):+.2f}%  최소: {min(o5):+.2f}%")
        buckets = [(-99,-5), (-5,-3), (-3,-1), (-1,0), (0,1), (1,3), (3,5), (5,99)]
        print("    구간별 분포:")
        for lo, hi in buckets:
            cnt = sum(1 for v in o5 if lo <= v < hi)
            pct = cnt / len(o5) * 100
            print(f"      [{lo:>+3}% ~ {hi:>+3}%): {cnt:3d}건 ({pct:.1f}%)")

    if o30:
        print(f"\n  [outcome_30m]")
        print(f"    평균: {statistics.mean(o30):+.2f}%  중앙값: {statistics.median(o30):+.2f}%")
        print(f"    최대: {max(o30):+.2f}%  최소: {min(o30):+.2f}%")

# ── 3. TP 달성률 분석 ──────────────────────────────────────
banner("3. outcome_5m 기준 목표가 달성률")
if signals and o5:
    total = len(o5)
    thresholds = [3.0, 2.0, 1.5, 1.0, 0.5]
    sl_levels  = [-1.5, -2.0, -3.0, -5.0]

    print(f"\n  TP 달성률 (n={total}):")
    for tp in thresholds:
        cnt = sum(1 for v in o5 if v >= tp)
        print(f"    +{tp:.1f}% 이상: {cnt}건 ({cnt/total*100:.1f}%)")

    print(f"\n  SL 손실률 (n={total}):")
    for sl in sl_levels:
        cnt = sum(1 for v in o5 if v <= sl)
        print(f"    {sl:.1f}% 이하: {cnt}건 ({cnt/total*100:.1f}%)")

    # EV 계산 (매수 수수료 0.25% + 매도 0.25% = 0.5% 왕복)
    FEE = 0.5
    print(f"\n  --- EV 시뮬레이션 (수수료 {FEE}% 왕복 차감) ---")
    for tp in [1.5, 2.0, 3.0]:
        for sl in [-1.5, -2.0, -3.0]:
            tp_hits  = sum(1 for v in o5 if v >= tp)
            sl_hits  = sum(1 for v in o5 if v <= sl)
            neither  = total - tp_hits - sl_hits
            # 단순 EV: TP에선 tp-fee, SL에선 sl-fee, 나머지는 0-fee(중립 청산)
            ev = (tp_hits * (tp - FEE) + sl_hits * (sl - FEE) + neither * (0 - FEE)) / total
            print(f"    TP={tp:+.1f}% / SL={sl:.1f}%  →  EV={ev:+.3f}%  "
                  f"(TP달성{tp_hits}, SL{sl_hits}, 기타{neither})")
else:
    print("  ⚠️  outcome_5m 데이터 부족")

# ── 4. hold_sec vs pnl_pct ────────────────────────────────
banner("4. 보유 시간 구간별 평균 PnL")
if trades and pnl_list:
    buckets_sec = [(0,60), (60,180), (180,300), (300,600), (600,1200), (1200,9999)]
    print(f"  {'hold 구간':<20} {'건수':>5} {'평균PnL':>10} {'승률':>8}")
    for lo, hi in buckets_sec:
        grp = [t["pnl_pct"] for t in trades
               if t["hold_sec"] is not None and t["pnl_pct"] is not None
               and lo <= t["hold_sec"] < hi]
        if grp:
            wins_g = sum(1 for v in grp if v >= 0)
            label = f"{lo//60}분~{hi//60}분" if hi < 9999 else f"{lo//60}분~"
            print(f"  {label:<20} {len(grp):>5} {statistics.mean(grp):>+10.2f}% {wins_g/len(grp)*100:>7.1f}%")

# ── 5. 코인별 분석 ────────────────────────────────────────
banner("5. 코인별 성과 (trades + signal_log)")
focus_coins = {"CUDIS", "SPURS", "NEIRO", "MOCA", "SUI", "CGPT"}

print("\n  [trades 코인별]")
if trades:
    coin_trades = defaultdict(list)
    for t in trades:
        if t["pnl_pct"] is not None:
            coin_trades[t["coin"]].append(t["pnl_pct"])
    for coin, pnls in sorted(coin_trades.items(), key=lambda x: -len(x[1])):
        flag = " ★" if coin in focus_coins else ""
        print(f"  {coin:<8}{flag}  n={len(pnls):2d}  평균={statistics.mean(pnls):+.2f}%  "
              f"최대={max(pnls):+.2f}%  최소={min(pnls):+.2f}%")

print("\n  [signal_log 코인별 outcome_5m]")
if signals:
    coin_signals = defaultdict(list)
    for s in signals:
        if s["outcome_5m"] is not None:
            coin_signals[s["coin"]].append(s["outcome_5m"])
    overall_mean = statistics.mean(o5) if o5 else 0

    for coin, vals in sorted(coin_signals.items(), key=lambda x: -len(x[1])):
        flag = " ★" if coin in focus_coins else ""
        diff = statistics.mean(vals) - overall_mean
        print(f"  {coin:<8}{flag}  n={len(vals):2d}  평균={statistics.mean(vals):+.2f}%  "
              f"(전체대비 {diff:+.2f}%)  최대={max(vals):+.2f}%  최소={min(vals):+.2f}%")

# ── 6. 최종 권고 ──────────────────────────────────────────
banner("6. 종합 권고")
if o5:
    tp3_rate   = sum(1 for v in o5 if v >= 3.0) / len(o5)
    tp15_rate  = sum(1 for v in o5 if v >= 1.5) / len(o5)
    sl3_rate   = sum(1 for v in o5 if v <= -3.0) / len(o5)
    sl15_rate  = sum(1 for v in o5 if v <= -1.5) / len(o5)

    print(f"""
  현재 설정: TP=+3%, SL=-3%
    5분 내 TP(+3%) 달성률:  {tp3_rate*100:.1f}%
    5분 내 SL(-3%) 손실률:  {sl3_rate*100:.1f}%

  대안 설정: TP=+1.5%, SL=-1.5%
    5분 내 TP(+1.5%) 달성률: {tp15_rate*100:.1f}%
    5분 내 SL(-1.5%) 손실률: {sl15_rate*100:.1f}%

  → 데이터 기반 판단:
    - TP 달성률이 낮고 SL 손실이 잦다면: TP 낮추거나 SL 좁히는 전략 유리
    - 반등 코인(CUDIS/SPURS)이 평균 이상이면: 코인별 TP 차별화 검토
    - EV > 0 인 파라미터 조합을 위 시뮬레이션에서 확인할 것
    """)

conn.close()
print("\n분석 완료.")
