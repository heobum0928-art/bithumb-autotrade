import sqlite3, sys
from datetime import date
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/trades.db')

# ── 1. 전체 거래 성과 ──────────────────────────────────────────────
trades = conn.execute('SELECT * FROM trades ORDER BY entered_at').fetchall()
cols = [d[0] for d in conn.execute('SELECT * FROM trades LIMIT 0').description]
trades = [dict(zip(cols, r)) for r in trades]

wins = [t for t in trades if (t['pnl_krw'] or 0) > 0]
losses = [t for t in trades if (t['pnl_krw'] or 0) <= 0]
total_pnl = sum(t['pnl_krw'] or 0 for t in trades)
sl_hits = [t for t in losses if '손절' in (t['exit_reason'] or '')]
tp_hits = [t for t in wins if '익절' in (t['exit_reason'] or '') or '트레일' in (t['exit_reason'] or '')]

print("=" * 55)
print("1. 전체 거래 성과")
print("=" * 55)
print(f"  총 거래: {len(trades)}건  승: {len(wins)}건  패: {len(losses)}건")
print(f"  승률: {len(wins)/len(trades)*100:.1f}%")
print(f"  누적 PnL: {total_pnl:+,.0f}원")
print(f"  평균 PnL: {total_pnl/len(trades):+,.0f}원/건")
print(f"  평균 승리: {sum(t['pnl_krw'] for t in wins)/max(len(wins),1):+,.0f}원")
print(f"  평균 손실: {sum(t['pnl_krw'] or 0 for t in losses)/max(len(losses),1):+,.0f}원")
print(f"  손절 횟수: {len(sl_hits)}건  익절 횟수: {len(tp_hits)}건")

# ── 2. max_pnl_pct 분석 (올라갔다가 손절 vs 처음부터 하락) ──────
has_max = [t for t in trades if t.get('max_pnl_pct') is not None]
went_up_then_lost = [t for t in losses if (t.get('max_pnl_pct') or 0) > 0.5]
never_went_up = [t for t in losses if (t.get('max_pnl_pct') or 0) <= 0.5]

print()
print("=" * 55)
print("2. 손실 거래 패턴 (max_pnl_pct 분석)")
print("=" * 55)
print(f"  손실 {len(losses)}건 중:")
print(f"  - 올라갔다가 손절: {len(went_up_then_lost)}건  ({len(went_up_then_lost)/max(len(losses),1)*100:.0f}%)")
print(f"  - 처음부터 하락:   {len(never_went_up)}건  ({len(never_went_up)/max(len(losses),1)*100:.0f}%)")
if went_up_then_lost:
    avg_max = sum(t['max_pnl_pct'] for t in went_up_then_lost) / len(went_up_then_lost)
    print(f"  - 올라갔다가 손절 평균 최고점: +{avg_max:.1f}%")

# ── 3. 진입 유형별 성과 ──────────────────────────────────────────
print()
print("=" * 55)
print("3. 진입 유형별 성과")
print("=" * 55)
by_type = {}
for t in trades:
    et = t.get('exit_reason', 'unknown') or 'unknown'
    # entry_type은 trades에 없으므로 coin으로 구분
    pass

# signal_log에서 실제 진입된 것 가져오기
sigs = conn.execute(
    "SELECT coin, entry_type, entered_at FROM signal_log WHERE entry_type IN ('regular','newlisting','preemptive') ORDER BY entered_at"
).fetchall()

type_trades = {}
for coin, etype, eat in sigs:
    matched = [t for t in trades if t['coin'] == coin and abs(
        (lambda a, b: abs((
            __import__('datetime').datetime.fromisoformat(a) -
            __import__('datetime').datetime.fromisoformat(b)
        ).total_seconds()))(t['entered_at'], eat)
    ) < 120]
    if matched:
        if etype not in type_trades:
            type_trades[etype] = []
        type_trades[etype].append(matched[0])

for etype, ts in type_trades.items():
    w = [t for t in ts if (t['pnl_krw'] or 0) > 0]
    pnl = sum(t['pnl_krw'] or 0 for t in ts)
    print(f"  [{etype}] {len(ts)}건 | 승률 {len(w)/len(ts)*100:.0f}% | PnL {pnl:+,.0f}원")

# ── 4. 시간대별 성과 ──────────────────────────────────────────────
print()
print("=" * 55)
print("4. 시간대별 성과")
print("=" * 55)
hour_data = {}
for t in trades:
    h = int(t['entered_at'][11:13])
    if h not in hour_data:
        hour_data[h] = []
    hour_data[h].append(t)

print(f"  {'시간':>4}  {'건수':>4}  {'승률':>6}  {'PnL':>10}")
for h in sorted(hour_data.keys()):
    ts = hour_data[h]
    w = [t for t in ts if (t['pnl_krw'] or 0) > 0]
    pnl = sum(t['pnl_krw'] or 0 for t in ts)
    print(f"  {h:02d}시  {len(ts):>4}건  {len(w)/len(ts)*100:>5.0f}%  {pnl:>+10,.0f}원")

# ── 5. 차단 신호 outcome 분석 (핵심) ────────────────────────────
print()
print("=" * 55)
print("5. 차단 신호 outcome 분석 (필터 품질)")
print("=" * 55)

blocked = conn.execute(
    "SELECT skip_reason, outcome_5m, outcome_30m FROM signal_log WHERE skip_reason IS NOT NULL AND outcome_5m IS NOT NULL"
).fetchall()

# 차단 사유 카테고리화
cats = {'RSI과열': [], 'BB과열': [], 'MACD하락': [], '호가불균형': [], '거래량과다': [], '체결강도미달': [], '기타': []}
for reason, o5, o30 in blocked:
    if 'RSI' in reason: cats['RSI과열'].append((o5, o30))
    elif 'BB' in reason: cats['BB과열'].append((o5, o30))
    elif 'MACD' in reason: cats['MACD하락'].append((o5, o30))
    elif '호가' in reason: cats['호가불균형'].append((o5, o30))
    elif '거래량과다' in reason: cats['거래량과다'].append((o5, o30))
    elif '체결강도' in reason: cats['체결강도미달'].append((o5, o30))
    else: cats['기타'].append((o5, o30))

print(f"  {'필터':12}  {'건수':>4}  {'5분후평균':>9}  {'올라간비율':>9}  판정")
for cat, data in cats.items():
    if not data: continue
    avg5 = sum(d[0] for d in data if d[0] is not None) / len(data)
    up_ratio = sum(1 for d in data if (d[0] or 0) > 0.3) / len(data)
    verdict = "✓ 올바른차단" if avg5 < 0 else ("△ 애매" if avg5 < 1.0 else "✗ 잘못된차단")
    print(f"  {cat:12}  {len(data):>4}건  {avg5:>+8.2f}%  {up_ratio*100:>8.0f}%  {verdict}")

# ── 6. 코인별 성과 ────────────────────────────────────────────────
print()
print("=" * 55)
print("6. 코인별 성과")
print("=" * 55)
coin_data = {}
for t in trades:
    c = t['coin']
    if c not in coin_data: coin_data[c] = []
    coin_data[c].append(t)

print(f"  {'코인':8}  {'건수':>4}  {'승률':>6}  {'PnL':>10}")
for coin, ts in sorted(coin_data.items(), key=lambda x: sum(t['pnl_krw'] or 0 for t in x[1])):
    w = [t for t in ts if (t['pnl_krw'] or 0) > 0]
    pnl = sum(t['pnl_krw'] or 0 for t in ts)
    print(f"  {coin:8}  {len(ts):>4}건  {len(w)/len(ts)*100:>5.0f}%  {pnl:>+10,.0f}원")

conn.close()
