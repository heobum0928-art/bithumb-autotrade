"""Dual Momentum (Antonacci) backtest on Bithumb daily candles. Research only."""
import json, glob, os
import numpy as np
import pandas as pd

DATA = r'c:/code/coinbase/data/candles_daily/*.json'
COST = 0.0016          # 0.16% per rebalance leg when held asset changes
REBAL = 21             # trading days per month
MIN_HIST = 400         # min candles to be in universe
CORE = ['BTC','ETH','SOL','BNB','XRP','ADA']

# ---------- load ----------
series = {}
for f in glob.glob(DATA):
    tk = os.path.basename(f).replace('_1d.json','')
    d = json.load(open(f, encoding='utf-8'))
    if len(d) < MIN_HIST:
        continue
    s = pd.Series({c['candle_date_time_utc'][:10]: c['trade_price'] for c in d})
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    series[tk] = s

print(f'Universe (>= {MIN_HIST} candles): {len(series)} coins')

# common date index (union, forward-fill within each coin only where it exists)
prices = pd.DataFrame(series).sort_index()
# Build trading-day grid = union of all dates
prices = prices.ffill()   # forward fill gaps; a coin still NaN before its first listing
N = len(prices)
print(f'Date range: {prices.index[0].date()} -> {prices.index[-1].date()}, {N} rows')

dates = prices.index
core_present = [c for c in CORE if c in prices.columns]
print(f'Core present: {core_present}')

split = int(N * 0.6)
print(f'TRAIN rows [0,{split}), TEST rows [{split},{N})')
print(f'TRAIN: {dates[0].date()} -> {dates[split-1].date()}')
print(f'TEST:  {dates[split].date()} -> {dates[-1].date()}')


def backtest(L_days, use_core, gate=True, start=0, end=None):
    """Returns dict of metrics + daily equity series over [start,end)."""
    if end is None:
        end = N
    cols = core_present if use_core else list(prices.columns)
    P = prices[cols]
    # rebalance days: every REBAL within [start,end), need L_days history before
    rebal_idx = list(range(max(start, L_days), end, REBAL))
    held = None  # currently held ticker or 'CASH'
    eq = 1.0
    equity = []         # (date, equity)
    eq_dates = []
    n_rebal = 0
    n_changes = 0
    months_cash = 0
    n_months = 0
    monthly_rets = []

    for k, ri in enumerate(rebal_idx):
        # decide allocation at ri based on trailing L return (no lookahead: uses data up to ri)
        cur = P.iloc[ri]
        past = P.iloc[ri - L_days]
        mom = (cur / past) - 1.0
        # eligible: both prices valid (full lookback history)
        mom = mom.dropna()
        mom = mom[past[mom.index].notna()]
        if len(mom) == 0:
            winner = 'CASH'
        else:
            winner = mom.idxmax()
            if gate and mom[winner] <= 0:
                winner = 'CASH'
        n_months += 1
        if winner == 'CASH':
            months_cash += 1
        # cost if asset changes
        change = (winner != held)
        if change:
            n_changes += 1
        n_rebal += 1
        # hold this allocation until next rebal (or end)
        nxt = rebal_idx[k+1] if k+1 < len(rebal_idx) else end - 1
        # compute period return
        if winner == 'CASH':
            pr = 0.0
        else:
            p0 = P[winner].iloc[ri]
            p1 = P[winner].iloc[nxt]
            if np.isnan(p0) or np.isnan(p1) or p0 == 0:
                pr = 0.0
            else:
                pr = (p1 / p0) - 1.0
        # apply cost on change (one leg out of old + one leg into new ~ treat as one leg per spec)
        if change and (held not in (None,)):
            pr -= COST  # leg cost on switching
        if change and held is None:
            pr -= COST  # initial entry cost (unless going to cash)
            if winner == 'CASH':
                pr += COST  # no cost to sit in cash
        eq *= (1 + pr)
        monthly_rets.append(pr)
        equity.append(eq)
        eq_dates.append(dates[nxt])
        held = winner

    monthly_rets = np.array(monthly_rets)
    return _metrics(monthly_rets, n_rebal, n_changes, months_cash, n_months), monthly_rets, (eq_dates, equity)


def _metrics(mr, n_rebal, n_changes, months_cash, n_months):
    if len(mr) == 0:
        return {}
    total = np.prod(1+mr) - 1
    # CAGR: months -> years
    yrs = len(mr) * REBAL / 252
    cagr = (1+total)**(1/yrs) - 1 if yrs > 0 else np.nan
    sharpe = (mr.mean()/mr.std() * np.sqrt(12)) if mr.std() > 0 else np.nan
    # max drawdown on monthly equity
    eq = np.cumprod(1+mr)
    peak = np.maximum.accumulate(eq)
    mdd = ((eq - peak)/peak).min()
    tstat = mr.mean()/(mr.std()/np.sqrt(len(mr))) if mr.std() > 0 else np.nan
    return dict(total=total, cagr=cagr, sharpe=sharpe, mdd=mdd,
                n_rebal=n_rebal, n_changes=n_changes, months_cash=months_cash,
                n_months=n_months, pct_cash=months_cash/n_months if n_months else 0,
                tstat=tstat)


def bench_hodl(ticker, start, end):
    P = prices[ticker]
    # monthly returns to match
    rebal_idx = list(range(start, end, REBAL))
    mr = []
    for k in range(len(rebal_idx)-1):
        p0 = P.iloc[rebal_idx[k]]; p1 = P.iloc[rebal_idx[k+1]]
        if np.isnan(p0) or np.isnan(p1) or p0==0:
            mr.append(0.0)
        else:
            mr.append(p1/p0-1)
    return _metrics(np.array(mr), len(mr), 0, 0, len(mr))


def bench_ew(start, end):
    P = prices
    rebal_idx = list(range(start, end, REBAL))
    mr = []
    for k in range(len(rebal_idx)-1):
        r0 = P.iloc[rebal_idx[k]]; r1 = P.iloc[rebal_idx[k+1]]
        rr = (r1/r0 - 1)
        rr = rr.replace([np.inf,-np.inf], np.nan).dropna()
        mr.append(rr.mean() if len(rr) else 0.0)
    return _metrics(np.array(mr), len(mr), 0, 0, len(mr))


# ---------- TRAIN: pick L and universe ----------
print('\n=== TRAIN (first 60%) — selecting config ===')
configs = []
for L_m, L_d in [(3,63),(6,126),(12,252)]:
    for use_core in [True, False]:
        m, _, _ = backtest(L_d, use_core, gate=True, start=0, end=split)
        configs.append((L_m, use_core, m))
        print(f'L={L_m:2d}m core={use_core!s:5} | total={m["total"]:+.2%} CAGR={m["cagr"]:+.2%} '
              f'Sharpe={m["sharpe"]:.2f} MDD={m["mdd"]:.2%} cash={m["pct_cash"]:.0%} n={m["n_months"]}')

# choose best by Sharpe on TRAIN
best = max(configs, key=lambda c: (c[2]['sharpe'] if not np.isnan(c[2]['sharpe']) else -9))
L_m, use_core, _ = best
L_d = {3:63,6:126,12:252}[L_m]
print(f'\n>> CHOSEN on TRAIN by Sharpe: L={L_m}m, universe={"CORE" if use_core else "FULL"}')

# ---------- TEST ----------
print('\n=== TEST (last 40%) — chosen config only ===')
m_test, mr_test, _ = backtest(L_d, use_core, gate=True, start=split, end=N)
m_btc = bench_hodl('BTC', split, N)
m_ew = bench_ew(split, N)

def row(name, m):
    return (f'{name:18} total={m["total"]:+8.2%} CAGR={m["cagr"]:+8.2%} Sharpe={m["sharpe"]:6.2f} '
            f'MDD={m["mdd"]:7.2%} cash={m.get("pct_cash",0):5.0%} t={m["tstat"]:5.2f} n={m["n_months"]}')

print(row(f'DualMom L{L_m} {"CORE" if use_core else "FULL"}', m_test))
print(row('HODL BTC', m_btc))
print(row('EqualWeight univ', m_ew))

# red-team (d): gate vs no-gate on TEST
m_nogate, mr_ng, _ = backtest(L_d, use_core, gate=False, start=split, end=N)
print('\n--- Red-team (d): absolute-momentum cash gate vs without (TEST) ---')
print(row('WITH gate', m_test))
print(row('WITHOUT gate', m_nogate))

print(f'\nTEST monthly rebalances: {m_test["n_months"]}')
print(f'TEST asset changes (turnover events): {m_test["n_changes"]}')
