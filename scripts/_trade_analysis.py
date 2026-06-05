"""Trade DB analysis — wins vs losses pattern detection."""
import sqlite3, sys, datetime, statistics
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect(r'c:\code\coinbase\data\trades.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def classify(reason):
    if not reason: return 'unknown'
    c0 = reason[0]
    if c0 == chr(0xD2B8): return 'trailing_stop'    # 트레일링스탑
    if c0 == '[':          return 'trailing_stop'    # [선진입] 트레일링
    if c0 == chr(0xBAA9): return 'timeout'           # 목표타임아웃
    if c0 == chr(0xAC15): return 'force_exit'        # 강제청산
    if c0 == chr(0xCD08): return 'early_stoploss'    # 초기손절
    if c0 == chr(0xC870): return 'early_trail'       # 조기트레일링
    if c0 == chr(0xC120): return 'preentry_timeout'  # 선진입 타임아웃
    if c0 == chr(0xC218): return 'manual_exit'       # 수동청산
    return 'unknown'

LABEL = {
    'trailing_stop':    '트레일링스탑',
    'preentry_timeout': '선진입타임아웃',
    'early_stoploss':   '초기손절',
    'early_trail':      '조기트레일',
    'manual_exit':      '수동청산',
    'timeout':          '목표타임아웃',
    'force_exit':       '강제청산',
    'unknown':          '미분류',
}

BASE = 'WHERE pnl_pct != -100 AND pnl_pct IS NOT NULL AND exit_price IS NOT NULL'
cur.execute(f'SELECT * FROM trades {BASE} ORDER BY id')
rows = [dict(r) for r in cur.fetchall()]

wins   = [r for r in rows if r['pnl_pct'] > 0]
losses = [r for r in rows if r['pnl_pct'] <= 0]
total  = len(rows)

# ─────────────────────────────────────────────
# 1. 전체 통계
# ─────────────────────────────────────────────
all_pnl_pct = [r['pnl_pct'] for r in rows]
all_pnl_krw = [r['pnl_krw'] or 0 for r in rows]
all_hold    = [r['hold_seconds'] or 0 for r in rows]

print('=' * 62)
print('1. 전체 거래 통계')
print('=' * 62)
print(f'  총 거래 건수  : {total}')
print(f'  수익 거래     : {len(wins)} ({len(wins)/total*100:.1f}%)')
print(f'  손실 거래     : {len(losses)} ({len(losses)/total*100:.1f}%)')
print(f'  평균 PnL      : {sum(all_pnl_pct)/total:+.4f}%')
print(f'  누적 PnL      : {sum(all_pnl_krw):+,.0f} 원')
print(f'  평균 보유시간 : {sum(all_hold)/total:.1f}초 ({sum(all_hold)/total/60:.1f}분)')
print(f'  최고 거래     : {max(all_pnl_pct):+.4f}%')
print(f'  최악 거래     : {min(all_pnl_pct):+.4f}%')

# ─────────────────────────────────────────────
# 2. 수익 vs 손실 — 보유시간
# ─────────────────────────────────────────────
print()
print('=' * 62)
print('2. 수익 vs 손실 — 보유시간 비교')
print('=' * 62)
for label, grp in [('수익', wins), ('손실', losses)]:
    hold = [r['hold_seconds'] or 0 for r in grp]
    pnl  = [r['pnl_pct'] for r in grp]
    cost = [r['cost_krw'] or 0 for r in grp]
    print(f'  [{label}] {len(grp)}건')
    print(f'    평균 보유 : {sum(hold)/len(grp):.1f}초 ({sum(hold)/len(grp)/60:.1f}분)')
    print(f'    중앙값    : {statistics.median(hold):.0f}초 ({statistics.median(hold)/60:.1f}분)')
    print(f'    범위      : {min(hold)}초 ~ {max(hold)}초')
    print(f'    평균 PnL  : {sum(pnl)/len(pnl):+.4f}%')
    print(f'    평균 투자 : {sum(cost)/len(cost):,.0f} 원')

# ─────────────────────────────────────────────
# 3. max_pnl_pct 분석
# ─────────────────────────────────────────────
print()
print('=' * 62)
print('3. 수익 vs 손실 — 최고점(max_pnl_pct) 비교')
print('=' * 62)
for label, grp in [('수익', wins), ('손실', losses)]:
    wm = [r for r in grp if r['max_pnl_pct'] is not None]
    no_m = len(grp) - len(wm)
    if not wm:
        print(f'  [{label}] max_pnl_pct 데이터 없음')
        continue
    max_v  = [r['max_pnl_pct'] for r in wm]
    final  = [r['pnl_pct'] for r in wm]
    dd     = [r['max_pnl_pct'] - r['pnl_pct'] for r in wm]
    print(f'  [{label}] {len(wm)}건 (미기록 {no_m}건 제외)')
    print(f'    평균 최고점   : {sum(max_v)/len(max_v):+.4f}%')
    print(f'    평균 최종 PnL : {sum(final)/len(final):+.4f}%')
    print(f'    평균 되돌림   : {sum(dd)/len(dd):+.4f}% (최고점→청산 낙폭)')

# ─────────────────────────────────────────────
# 4. exit_reason 분류별 통계
# ─────────────────────────────────────────────
print()
print('=' * 62)
print('4. exit_reason 분류별 통계')
print('=' * 62)
cs = defaultdict(lambda: {'cnt':0,'wins':0,'pnl_pct':[],'pnl_krw':[],'hold':[]})
for r in rows:
    cat = classify(r['exit_reason'])
    s = cs[cat]
    s['cnt'] += 1
    if r['pnl_pct'] > 0: s['wins'] += 1
    s['pnl_pct'].append(r['pnl_pct'])
    s['pnl_krw'].append(r['pnl_krw'] or 0)
    s['hold'].append(r['hold_seconds'] or 0)

for cat, s in sorted(cs.items(), key=lambda x: sum(x[1]['pnl_krw']), reverse=True):
    cnt = s['cnt']
    wr  = s['wins']/cnt*100
    avg_pnl = sum(s['pnl_pct'])/cnt
    tot_krw = sum(s['pnl_krw'])
    avg_hold = sum(s['hold'])/cnt
    ko = LABEL.get(cat, cat)
    print(f'  [{ko}] {cnt}건')
    print(f'    승률 {wr:.1f}%, 평균PnL {avg_pnl:+.3f}%, 누적PnL {tot_krw:+,.0f}원, 평균보유 {avg_hold:.0f}초')

# ─────────────────────────────────────────────
# 5. 시간대별 승률 (KST)
# ─────────────────────────────────────────────
print()
print('=' * 62)
print('5. 시간대별(KST hour) 승률')
print('=' * 62)
hs = defaultdict(lambda: {'cnt':0,'wins':0,'pnl_sum':0.0})
for r in rows:
    if r['entered_at']:
        dt  = datetime.datetime.fromisoformat(r['entered_at'])
        kst = dt + datetime.timedelta(hours=9)
        h   = kst.hour
        hs[h]['cnt'] += 1
        if r['pnl_pct'] > 0: hs[h]['wins'] += 1
        hs[h]['pnl_sum'] += r['pnl_pct']

print(f"  {'시(KST)':>6} | {'건수':>4} | {'승률':>6} | {'평균PnL':>8} | 막대")
print('  ' + '-' * 55)
for h in sorted(hs.keys()):
    s  = hs[h]
    wr = s['wins']/s['cnt']*100
    ap = s['pnl_sum']/s['cnt']
    bar = '■' * s['wins'] + '□' * (s['cnt'] - s['wins'])
    print(f"  {h:02d}시    | {s['cnt']:4d} | {wr:5.1f}% | {ap:+7.3f}%  | {bar}")

# ─────────────────────────────────────────────
# 6. 코인별 승률 및 총 PnL
# ─────────────────────────────────────────────
print()
print('=' * 62)
print('6. 코인별 승률 및 총 PnL')
print('=' * 62)
coin_s = defaultdict(lambda: {'cnt':0,'wins':0,'pnl_pct':[],'pnl_krw':0})
for r in rows:
    c = r['coin']
    coin_s[c]['cnt'] += 1
    if r['pnl_pct'] > 0: coin_s[c]['wins'] += 1
    coin_s[c]['pnl_pct'].append(r['pnl_pct'])
    coin_s[c]['pnl_krw'] += r['pnl_krw'] or 0

print(f"  {'코인':>8} | {'건수':>4} | {'승률':>6} | {'누적PnL(원)':>12} | {'평균PnL%':>8}")
print('  ' + '-' * 54)
for coin, s in sorted(coin_s.items(), key=lambda x: x[1]['pnl_krw'], reverse=True):
    cnt = s['cnt']
    wr  = s['wins']/cnt*100
    tkw = s['pnl_krw']
    ap  = sum(s['pnl_pct'])/cnt
    print(f"  {coin:>8} | {cnt:4d} | {wr:5.1f}% | {tkw:+12,.0f} | {ap:+7.3f}%")

# ─────────────────────────────────────────────
# 7. 최고점 높았는데 손실로 끝난 거래
# ─────────────────────────────────────────────
print()
print('=' * 62)
print('7. 최고점 +1% 이상 도달 후 손실 청산된 거래 (트레일 실패)')
print('=' * 62)
missed = [r for r in rows if r['max_pnl_pct'] is not None and r['max_pnl_pct'] > 1.0 and r['pnl_pct'] <= 0]
missed.sort(key=lambda x: x['max_pnl_pct'], reverse=True)
print(f'  해당 거래 {len(missed)}건')
print(f"  {'코인':>8} | {'최고점':>8} | {'최종PnL':>8} | {'되돌림':>8} | {'보유(초)':>8} | {'청산사유'}")
print('  ' + '-' * 72)
for r in missed:
    drop = r['max_pnl_pct'] - r['pnl_pct']
    ko   = LABEL.get(classify(r['exit_reason']), '?')
    print(f"  {r['coin']:>8} | {r['max_pnl_pct']:+7.2f}% | {r['pnl_pct']:+7.3f}% | {drop:+7.2f}% | {r['hold_seconds']:8d} | {ko}")

# ─────────────────────────────────────────────
# 8. 핵심 패턴 발견
# ─────────────────────────────────────────────
print()
print('=' * 62)
print('8. 핵심 발견 — 수익 거래 공통 패턴')
print('=' * 62)

# 수익 거래 청산방법 분포
win_cats  = defaultdict(int)
loss_cats = defaultdict(int)
for r in wins:  win_cats[classify(r['exit_reason'])]  += 1
for r in losses: loss_cats[classify(r['exit_reason'])] += 1

print('  수익 거래 청산방법 분포:')
for cat, cnt in sorted(win_cats.items(), key=lambda x: -x[1]):
    print(f'    {LABEL.get(cat, cat)}: {cnt}건 ({cnt/len(wins)*100:.1f}%)')

print('  손실 거래 청산방법 분포:')
for cat, cnt in sorted(loss_cats.items(), key=lambda x: -x[1]):
    print(f'    {LABEL.get(cat, cat)}: {cnt}건 ({cnt/len(losses)*100:.1f}%)')

# +3% 이상 수익 거래
big_wins = sorted([r for r in wins if r['pnl_pct'] >= 3.0], key=lambda x: -x['pnl_pct'])
print(f'\n  +3% 이상 수익 거래 ({len(big_wins)}건):')
for r in big_wins:
    ko  = LABEL.get(classify(r['exit_reason']), '?')
    mp  = r['max_pnl_pct'] if r['max_pnl_pct'] is not None else float('nan')
    print(f"    {r['date']} {r['coin']:8s} {r['pnl_pct']:+7.3f}% | 최고점 {mp:+6.2f}% | 보유 {r['hold_seconds']:4d}초 | {ko}")

# 투자금액 구간별 승률
print('\n  투자금액 구간별 승률:')
buckets = [('~50k', 0, 50000), ('50~100k', 50000, 100000),
           ('100~200k', 100000, 200000), ('200k+', 200000, 9_999_999)]
for lbl, lo, hi in buckets:
    grp = [r for r in rows if lo < (r['cost_krw'] or 0) <= hi]
    if not grp: continue
    w = sum(1 for r in grp if r['pnl_pct'] > 0)
    avg_pnl = sum(r['pnl_pct'] for r in grp) / len(grp)
    print(f'    {lbl}: {len(grp)}건, 승률 {w/len(grp)*100:.1f}%, 평균PnL {avg_pnl:+.3f}%')

# 기간별 성과
print('\n  월별 / 기간별 성과:')
period_s = defaultdict(lambda: {'cnt':0,'wins':0,'pnl_krw':0,'pnl_pct':[]})
for r in rows:
    if r['date']:
        p = r['date'][:7]  # YYYY-MM
        period_s[p]['cnt'] += 1
        if r['pnl_pct'] > 0: period_s[p]['wins'] += 1
        period_s[p]['pnl_krw'] += r['pnl_krw'] or 0
        period_s[p]['pnl_pct'].append(r['pnl_pct'])

for p, s in sorted(period_s.items()):
    cnt = s['cnt']
    wr  = s['wins']/cnt*100
    ap  = sum(s['pnl_pct'])/cnt
    print(f'    {p}: {cnt}건, 승률 {wr:.1f}%, 누적PnL {s["pnl_krw"]:+,.0f}원, 평균PnL {ap:+.3f}%')

conn.close()
print()
print('분석 완료')
