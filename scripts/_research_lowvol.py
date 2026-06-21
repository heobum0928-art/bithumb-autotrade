"""Low-Volatility anomaly factor backtest - long-only, weekly rebalance.
Walk-forward: select config on first 60% (TRAIN), report on last 40% (TEST).
Run with Python 3.13 (numpy/pandas).
"""
import json, glob, os
import numpy as np
import pandas as pd

DATA = r"C:/code/coinbase/data/candles_daily"
COST = 0.0016            # 0.16% per leg on turnover
REBAL = 5               # rebalance every 5 trading days (~weekly)
MIN_CANDLES = 300

# ---------- load ----------
def load_all():
    series = {}
    for fp in glob.glob(os.path.join(DATA, "*_1d.json")):
        tk = os.path.basename(fp)[:-len("_1d.json")]
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, list) or len(d) < MIN_CANDLES:
            continue
        rows = [(c["candle_date_time_utc"][:10], float(c["trade_price"])) for c in d if c.get("trade_price")]
        if len(rows) < MIN_CANDLES:
            continue
        s = pd.Series({dt: px for dt, px in rows})
        s.index = pd.to_datetime(s.index)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        series[tk] = s
    return series

series = load_all()
print(f"Eligible coins (>= {MIN_CANDLES} candles): {len(series)}")

# common date index = union, then forward axis from BTC range
close = pd.DataFrame(series).sort_index()
print("Date range:", close.index.min().date(), "->", close.index.max().date(), "| rows:", len(close))

logret = np.log(close / close.shift(1))
simret = close.pct_change(fill_method=None)   # simple daily returns for portfolio accounting

dates = close.index
n = len(dates)
train_end = int(n * 0.6)     # index boundary
print(f"Total days {n}; TRAIN [0,{train_end}) TEST [{train_end},{n})")

# ---------- BTC SMA200 regime ----------
btc = close["BTC"]
btc_sma200 = btc.rolling(200).mean()
btc_bull = (btc > btc_sma200)   # True = risk-on

# ---------- backtest engine ----------
def backtest(N, W, gate, start_i, end_i):
    """Returns daily portfolio simple-return series over [start_i, end_i)."""
    weights = pd.Series(0.0, index=close.columns)
    port_rets = []
    turnovers = []
    rebal_days = 0
    hold_counts = pd.Series(0.0, index=close.columns)
    for i in range(start_i, end_i):
        dt = dates[i]
        # apply today's return using yesterday's weights (held overnight)
        if i > start_i:
            day_ret = (weights * simret.iloc[i]).sum(skipna=True)
        else:
            day_ret = 0.0

        # rebalance decision at close of day i (relative to global index for alignment)
        if (i - start_i) % REBAL == 0:
            # eligibility: full lookback W of valid log returns ending at i-1 (no lookahead on today's close used for ranking is fine since we rank on trailing vol then hold)
            window = logret.iloc[i - W:i]   # W returns up to and including day i (uses close[i]); ranking then hold next period - close[i] is known at rebalance
            valid = window.notna().sum() >= W
            vol = window.std()
            elig = vol[valid & vol.notna()]
            # also require price exists today
            elig = elig[close.iloc[i][elig.index].notna()]
            new_w = pd.Series(0.0, index=close.columns)
            if len(elig) >= N:
                picks = elig.nsmallest(N).index
                # regime gate
                risk_on = True
                if gate:
                    bv = btc_bull.iloc[i]
                    risk_on = bool(bv) if not pd.isna(bv) else True
                if risk_on:
                    new_w[picks] = 1.0 / N
                    hold_counts[picks] += 1
            # turnover cost
            turn = (new_w - weights).abs().sum()
            cost = turn * COST
            day_ret -= cost
            turnovers.append(turn)
            rebal_days += 1
            weights = new_w
        port_rets.append(day_ret)
    pr = pd.Series(port_rets, index=dates[start_i:end_i])
    return pr, np.mean(turnovers) if turnovers else 0.0, rebal_days, hold_counts

# ---------- metrics ----------
def metrics(daily_ret):
    daily_ret = daily_ret.fillna(0.0)
    eq = (1 + daily_ret).cumprod()
    total = eq.iloc[-1] - 1
    days = len(daily_ret)
    years = days / 365.0
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 and eq.iloc[-1] > 0 else float("nan")
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(365)) if daily_ret.std() > 0 else float("nan")
    roll_max = eq.cummax()
    mdd = ((eq - roll_max) / roll_max).min()
    # weekly returns for t-stat
    wk = (1 + daily_ret).groupby(np.arange(len(daily_ret)) // REBAL).prod() - 1
    tstat = (wk.mean() / wk.std() * np.sqrt(len(wk))) if wk.std() > 0 and len(wk) > 1 else float("nan")
    return dict(total=total, cagr=cagr, sharpe=sharpe, mdd=mdd, tstat=tstat, nweeks=len(wk))

# ---------- TRAIN: select config by Sharpe ----------
print("\n=== TRAIN (first 60%) - config search ===")
results = []
for N in (5, 10, 15):
    for W in (20, 40):
        for gate in (False, True):
            pr, turn, rb, _ = backtest(N, W, gate, W, train_end)  # start at W to have lookback
            m = metrics(pr)
            results.append((N, W, gate, m, turn))
            print(f"N={N:2d} W={W:2d} gate={str(gate):5s} | Sharpe={m['sharpe']:.2f} CAGR={m['cagr']*100:6.1f}% MDD={m['mdd']*100:6.1f}% turn={turn:.2f} ret={m['total']*100:7.1f}%")

best = max(results, key=lambda r: (r[3]["sharpe"] if not np.isnan(r[3]["sharpe"]) else -99))
N, W, gate, mtr, turn = best
print(f"\n>>> CHOSEN (max TRAIN Sharpe): N={N} W={W} gate={gate} (TRAIN Sharpe={mtr['sharpe']:.2f})")

# ---------- TEST: report chosen config ----------
print("\n=== TEST (last 40%) - chosen config ===")
pr_test, turn_test, rb_test, holds = backtest(N, W, gate, train_end, n)
m_test = metrics(pr_test)
print(f"Strategy N={N} W={W} gate={gate}: total={m_test['total']*100:.1f}% CAGR={m_test['cagr']*100:.1f}% Sharpe={m_test['sharpe']:.2f} MDD={m_test['mdd']*100:.1f}% turn={turn_test:.2f} t={m_test['tstat']:.2f} nweeks={m_test['nweeks']}")

# ---------- benchmarks on TEST ----------
# HODL BTC
btc_test_ret = simret["BTC"].iloc[train_end:n]
m_btc = metrics(btc_test_ret)
# EW all eligible (rebalanced weekly to equal weight among coins with price)
def ew_market(start_i, end_i):
    port = []
    for i in range(start_i, end_i):
        if i == start_i:
            port.append(0.0); continue
        row = simret.iloc[i]
        avail = close.iloc[i-1].notna() & close.iloc[i].notna()
        r = row[avail].mean()
        port.append(r if not pd.isna(r) else 0.0)
    return pd.Series(port, index=dates[start_i:end_i])
m_ew = metrics(ew_market(train_end, n))

print("\n--- TEST comparison ---")
hdr = f"{'strategy':12s} {'total%':>8s} {'CAGR%':>7s} {'Sharpe':>7s} {'MDD%':>7s} {'turn':>5s} {'t':>5s}"
print(hdr)
def line(name, m, t):
    print(f"{name:12s} {m['total']*100:8.1f} {m['cagr']*100:7.1f} {m['sharpe']:7.2f} {m['mdd']*100:7.1f} {t:5.2f} {m['tstat']:5.2f}")
line(f"LowVol", m_test, turn_test)
line("HODL-BTC", m_btc, 0.0)
line("EW-market", m_ew, 1.0)

# ---------- red team ----------
print("\n=== RED TEAM ===")
print(f"TEST weekly rebalances: {m_test['nweeks']} (>=20 needed for confidence)")
held = holds[holds > 0].sort_values(ascending=False)
totrb = rb_test
print(f"Most-held coins in TEST (out of {totrb} rebalances):")
for tk, c in held.head(15).items():
    print(f"  {tk:8s} held {int(c)} rebalances ({c/totrb*100:.0f}% of time)")
print(f"Turnover cost drag/rebalance ~= turn*cost = {turn_test*COST*100:.3f}% ; annualized ~ {turn_test*COST*(252/REBAL)*100:.1f}%")

# ---------- relative factor edge (LowVol - EW market), TEST ----------
ew_daily = ew_market(train_end, n).fillna(0.0)
lv_daily = pr_test.fillna(0.0)
# align
rel = (lv_daily - ew_daily)
wk_rel = rel.groupby(np.arange(len(rel))//REBAL).sum()
mean_rel = wk_rel.mean(); sd_rel = wk_rel.std(); t_rel = mean_rel/sd_rel*np.sqrt(len(wk_rel))
print("\n=== RELATIVE EDGE (LowVol minus EW-market, weekly) ===")
print(f"weeks={len(wk_rel)} mean_wk_excess={mean_rel*100:.3f}% t-stat={t_rel:.2f}")
# also vs BTC
rel_b = (lv_daily - btc_test_ret.fillna(0.0))
wk_relb = rel_b.groupby(np.arange(len(rel_b))//REBAL).sum()
t_relb = wk_relb.mean()/wk_relb.std()*np.sqrt(len(wk_relb))
print(f"vs BTC: mean_wk_excess={wk_relb.mean()*100:.3f}% t-stat={t_relb:.2f}")
