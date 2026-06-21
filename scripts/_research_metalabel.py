"""
Meta-labeling overlay on BTC SMA cycle-timing core.
Walk-forward: TRAIN=first 60%, TEST=last 40%. Fit only on TRAIN, report only on TEST.
Cost 0.16% per rebalance leg. Deterministic (fixed random_state).
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier

np.random.seed(42)
DATA = Path("data/candles_daily/BTC_1d.json")
COST_LEG = 0.0016          # per rebalance leg
FWD = 20                   # forward horizon for meta-label
RT_COST = 0.0032           # round-trip cost threshold for label

# ---------- load ----------
candles = json.loads(DATA.read_text(encoding="utf-8"))
close = np.array([c["trade_price"] for c in candles], dtype=float)
high = np.array([c["high_price"] for c in candles], dtype=float)
low = np.array([c["low_price"] for c in candles], dtype=float)
n = len(close)
df = pd.DataFrame({"close": close, "high": high, "low": low})

# ---------- indicators (all causal: use only past/current close) ----------
df["sma50"] = df["close"].rolling(50).mean()
df["sma200"] = df["close"].rolling(200).mean()
df["ret1"] = df["close"].pct_change()

def rsi(s, period=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(period).mean()
    dn = (-d.clip(upper=0)).rolling(period).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

df["rsi14"] = rsi(df["close"], 14)
df["vol20"] = df["ret1"].rolling(20).std()
df["sma50_slope"] = df["sma50"].pct_change(5)        # slope over 5d
df["ret5"] = df["close"].pct_change(5)
df["ret20"] = df["close"].pct_change(20)
df["ret60"] = df["close"].pct_change(60)
df["dist50"] = df["close"] / df["sma50"] - 1
df["dist200"] = df["close"] / df["sma200"] - 1
roll_hi = df["high"].rolling(20).max()
roll_lo = df["low"].rolling(20).min()
df["hl_pos"] = (df["close"] - roll_lo) / (roll_hi - roll_lo).replace(0, np.nan)
df["pct_green10"] = (df["ret1"] > 0).rolling(10).mean()

# ---------- core signal: target allocation fraction ----------
# scout (0.5) when close>sma50*1.01 ; full (1.0) when close>sma200*1.01 ; else cash
def core_target(row):
    full = (not np.isnan(row.sma200)) and row.close > row.sma200 * 1.01
    scout = (not np.isnan(row.sma50)) and row.close > row.sma50 * 1.01
    if full:
        return 1.0
    if scout:
        return 0.5
    return 0.0

df["core_frac"] = df.apply(core_target, axis=1)
df["core_frac_prev"] = df["core_frac"].shift(1).fillna(0.0)

# BUY trigger = core fraction increases (a fresh "go long" signal)
df["is_buy"] = df["core_frac"] > df["core_frac_prev"]

# ---------- meta labels: forward FWD-day return after entry > RT_COST ----------
df["fwd_ret"] = df["close"].shift(-FWD) / df["close"] - 1
df["meta_label"] = (df["fwd_ret"] > RT_COST).astype(int)

FEATS = ["dist50", "dist200", "sma50_slope", "vol20", "ret5", "ret20", "ret60",
         "rsi14", "hl_pos", "pct_green10"]

# ---------- split ----------
train_end = int(n * 0.6)
idx = np.arange(n)
is_train = idx < train_end
is_test = idx >= train_end

# Buy signal rows usable for training the meta-model: must have valid feats AND
# a valid forward label (forward window must not run off the data end, and must
# fall fully inside the train region to avoid using test info).
valid_feat = df[FEATS].notna().all(axis=1) & df["sma200"].notna()
has_fwd = idx < (n - FWD)

train_buy_mask = df["is_buy"].values & is_train & valid_feat.values & has_fwd
# restrict train labels to entries whose 20d forward window stays within TRAIN
train_buy_mask &= (idx + FWD) < train_end

Xtr = df.loc[train_buy_mask, FEATS].values
ytr = df.loc[train_buy_mask, "meta_label"].values

print(f"Total days: {n}, train_end idx: {train_end}")
print(f"Train BUY signals (labelable): {train_buy_mask.sum()}, label balance: {ytr.mean():.2f}")

clf = GradientBoostingClassifier(random_state=42)
if len(np.unique(ytr)) < 2:
    raise SystemExit("Train labels single-class; cannot fit meta-model.")
clf.fit(Xtr, ytr)

# meta prediction for ALL days (only used on buy days)
Xall = df[FEATS].copy()
pred_p = np.full(n, np.nan)
ok = valid_feat.values
pred_p[ok] = clf.predict_proba(Xall.loc[ok].values)[:, 1]
df["meta_p"] = pred_p

# ---------- build daily allocation series ----------
# raw core: hold core_frac each day (decided from prev close -> applied next day)
# We trade based on signal known at close of day t, applied to day t+1 returns.
def simulate(target_frac):
    """target_frac: array len n, desired exposure decided at close[t], earns ret[t+1]."""
    ret = df["ret1"].values  # ret[t] = close[t]/close[t-1]-1
    pos = np.nan_to_num(target_frac)
    # exposure on day t (earning ret[t]) was decided at close[t-1] => shift
    exp = np.roll(pos, 1)
    exp[0] = 0.0
    strat_ret = exp * ret
    # costs: when exposure changes between days
    turn = np.abs(np.diff(np.concatenate([[0.0], exp])))
    cost = turn * COST_LEG
    net = strat_ret - cost
    return net, exp

# core target each day = core_frac (carry exposure while condition holds)
core_target_series = df["core_frac"].values.copy()

# meta-filtered: on a BUY day, only allow the increase if meta_p>=0.5.
# Implement as: maintain exposure; recompute desired exposure but veto fresh buys
# whose meta prob < 0.5. Once vetoed, that increment is skipped until next buy.
meta_target = np.zeros(n)
cur = 0.0
for t in range(n):
    desired = df["core_frac"].values[t]
    if desired > cur:  # a buy / increase
        p = df["meta_p"].values[t]
        if not np.isnan(p) and p >= 0.5:
            cur = desired
        # else: veto increase, keep cur (could be 0 or 0.5)
    else:
        cur = desired  # sells / reductions always honored (risk-off)
    meta_target[t] = cur

# ---------- metrics on TEST ----------
def metrics(net, exp, label):
    seg = net[is_test]
    e = exp[is_test]
    eq = np.cumprod(1 + seg)
    total = eq[-1] - 1
    days = len(seg)
    cagr = (eq[-1]) ** (252 / days) - 1
    mu, sd = seg.mean(), seg.std(ddof=1)
    sharpe = mu / sd * np.sqrt(252) if sd > 0 else np.nan
    run_max = np.maximum.accumulate(eq)
    mdd = (eq / run_max - 1).min()
    # turnover / trades within test
    turn = np.abs(np.diff(np.concatenate([[e[0]], e])))
    n_trades = int((turn > 1e-9).sum())
    turnover = turn.sum()
    tstat = mu / (sd / np.sqrt(days)) if sd > 0 else np.nan
    return dict(label=label, total=total, cagr=cagr, sharpe=sharpe, mdd=mdd,
                trades=n_trades, turnover=turnover, tstat=tstat)

# HODL
hodl_net = df["ret1"].values.copy()
hodl_exp = np.ones(n)
m_hodl = metrics(hodl_net, hodl_exp, "HODL BTC")

core_net, core_exp = simulate(core_target_series)
m_core = metrics(core_net, core_exp, "Raw core SMA")

meta_net, meta_exp = simulate(meta_target)
m_meta = metrics(meta_net, meta_exp, "Core + meta filter")

# ---------- red team ----------
# (b) test BUY signals count
test_buy = df["is_buy"].values & is_test & valid_feat.values
n_test_buy = int(test_buy.sum())

# (d) permutation: shuffle train labels, refit, rebuild meta filter
rng = np.random.RandomState(7)
ytr_shuf = ytr.copy()
rng.shuffle(ytr_shuf)
clf_s = GradientBoostingClassifier(random_state=42)
clf_s.fit(Xtr, ytr_shuf)
pred_s = np.full(n, np.nan)
pred_s[ok] = clf_s.predict_proba(Xall.loc[ok].values)[:, 1]
meta_target_s = np.zeros(n)
cur = 0.0
for t in range(n):
    desired = df["core_frac"].values[t]
    if desired > cur:
        p = pred_s[t]
        if not np.isnan(p) and p >= 0.5:
            cur = desired
    else:
        cur = desired
    meta_target_s[t] = cur
meta_net_s, meta_exp_s = simulate(meta_target_s)
m_meta_s = metrics(meta_net_s, meta_exp_s, "Core + SHUFFLED meta")

# ---------- report ----------
def row(m):
    return (f"{m['label']:<22} {m['total']*100:>8.1f}% {m['cagr']*100:>8.1f}% "
            f"{m['sharpe']:>7.2f} {m['mdd']*100:>8.1f}% {m['trades']:>6d} "
            f"{m['turnover']:>7.2f} {m['tstat']:>7.2f}")

print("\n=== TEST METRICS (last 40% of days) ===")
print(f"{'Strategy':<22} {'TotRet':>9} {'CAGR':>9} {'Sharpe':>7} {'MaxDD':>9} {'Trades':>6} {'Turnov':>7} {'t-stat':>7}")
for m in (m_hodl, m_core, m_meta):
    print(row(m))
print("\n--- permutation control ---")
print(row(m_meta_s))

print("\n=== RED TEAM ===")
print(f"(b) BUY signals landing in TEST: {n_test_buy}  -> "
      f"{'TRUSTWORTHY' if n_test_buy>=15 else 'NOT TRUSTWORTHY (<15)'}")
print(f"(c) meta total ret {m_meta['total']*100:.1f}% vs core {m_core['total']*100:.1f}% ; "
      f"meta Sharpe {m_meta['sharpe']:.2f} vs core {m_core['sharpe']:.2f} ; "
      f"meta trades {m_meta['trades']} vs core {m_core['trades']}")
print(f"(d) shuffled-label meta total ret {m_meta_s['total']*100:.1f}% (should NOT beat core {m_core['total']*100:.1f}%)")
print(f"    test days: {is_test.sum()}, train days: {is_train.sum()}")
