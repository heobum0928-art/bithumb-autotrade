"""Regime-switched hybrid research: can a BULL-day ALT engine beat BTC-in-bull?
Walk-forward. TRAIN=first 60%, TEST=last 40%. Read-only research, no bot changes.
"""
import json, glob, os, sys
import numpy as np
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

DDIR = r"c:\code\coinbase\data\candles_daily"
COST = 0.0016  # per leg on turnover

# ---------- load ----------
def load_all():
    closes, vols, highs, lows = {}, {}, {}, {}
    for fp in glob.glob(os.path.join(DDIR, "*_1d.json")):
        tk = os.path.basename(fp)[:-len("_1d.json")]
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if not d:
            continue
        rows = []
        for c in d:
            # use KST date as the index key (date string)
            dt = c.get("candle_date_time_utc") or c.get("candle_date_time_kst")
            rows.append((dt[:10], c["trade_price"], c.get("candle_acc_trade_price", 0.0),
                         c.get("high_price", c["trade_price"]), c.get("low_price", c["trade_price"])))
        df = pd.DataFrame(rows, columns=["date", "close", "vol", "high", "low"])
        df = df.drop_duplicates("date").set_index("date").sort_index()
        closes[tk] = df["close"]; vols[tk] = df["vol"]; highs[tk] = df["high"]; lows[tk] = df["low"]
    return closes, vols, highs, lows

closes, vols, highs, lows = load_all()
print("coins loaded:", len(closes), " BTC days:", len(closes["BTC"]))

# common date index = union (BTC is the spine)
all_dates = sorted(set().union(*[set(s.index) for s in closes.values()]))
idx = pd.Index(all_dates)

def frame(d):
    return pd.DataFrame({tk: s.reindex(idx) for tk, s in d.items()})

C = frame(closes)   # close
V = frame(vols)
H = frame(highs)
L = frame(lows)

# forward-fill PRICES only (for return continuity); a coin is "eligible" only where it had real history
listed = C.notna()                      # real data present that day
Cff = C.ffill()                         # ffill prices for return calc
# liquidity 20d avg KRW volume (use real vols, ffill 0 for missing handled via listed mask)
Vff = V.ffill()

# daily simple returns per coin from ffilled close
R = Cff.pct_change()
# a coin's return on day t is only valid (tradeable) if it was listed on t-1 and t
tradeable = listed & listed.shift(1, fill_value=False)
R = R.where(tradeable)

n = len(idx)
print("total days:", n)

# ---------- regime ----------
btc = Cff["BTC"]
sma200 = btc.rolling(200).mean()
bull_raw = btc > sma200 * 1.01          # decided at close[t]
# act on t+1: shift regime forward 1 day
bull = bull_raw.shift(1, fill_value=False)   # bull[t] => hold during day t's return
btc_ret = R["BTC"]

# split
train_end = int(n * 0.6)
is_test = np.zeros(n, dtype=bool); is_test[train_end:] = True
seg = pd.Series(np.where(is_test, "TEST", "TRAIN"), index=idx)

# ---------- engine return builders ----------
LIQ_FLOOR = 1e8  # 100M KRW 20d-avg volume floor (realism)
liq20 = Vff.rolling(20).mean()
elig_liq = (liq20 > LIQ_FLOOR) & tradeable   # eligible: liquid + tradeable today
# exclude BTC from alt universe
alt_cols = [c for c in C.columns if c != "BTC"]

mom20 = Cff.pct_change(20)               # trailing 20d return (signal at close, use shift)

def topn_engine(N, rebal=5):
    """Top-N momentum, weekly (5d) rebalance, equal weight, BULL only."""
    weights = pd.DataFrame(0.0, index=idx, columns=alt_cols)
    cur = []  # current holdings
    last_rebal = -10**9
    # precompute signals shifted by 1 (decide on yesterday's close)
    mom_s = mom20.shift(1)
    elig_s = elig_liq.shift(1, fill_value=False)
    for i in range(n):
        if not bull.iloc[i]:
            cur = []
            continue
        if i - last_rebal >= rebal or not cur:
            # pick top-N by trailing mom among eligible alts (signal from t-1)
            row_mom = mom_s.iloc[i][alt_cols]
            row_elig = elig_s.iloc[i][alt_cols]
            cand = row_mom[row_elig.fillna(False) & row_mom.notna()]
            cand = cand.sort_values(ascending=False)
            cur = list(cand.index[:N])
            last_rebal = i
        if cur:
            w = 1.0 / len(cur)
            for tk in cur:
                weights.iloc[i, weights.columns.get_loc(tk)] = w
    return weights

def breakout_engine(rebal=5):
    """Donchian 20d high entry, exit close<20d-low or weekly rebal. BULL only."""
    hh20 = Cff.rolling(20).max().shift(1)   # prior 20d high (exclude today)
    ll20 = Cff.rolling(20).min().shift(1)
    new_high = (Cff[alt_cols] >= hh20[alt_cols])   # made new high today (signal at close -> shift)
    new_high_s = new_high.shift(1, fill_value=False)
    below_low = (Cff[alt_cols] <= ll20[alt_cols])
    below_low_s = below_low.shift(1, fill_value=False)
    elig_s = elig_liq.shift(1, fill_value=False)
    weights = pd.DataFrame(0.0, index=idx, columns=alt_cols)
    held = set()
    last_rebal = -10**9
    for i in range(n):
        if not bull.iloc[i]:
            held = set(); continue
        # exits
        for tk in list(held):
            if bool(below_low_s.iloc[i].get(tk, False)):
                held.discard(tk)
        # weekly rebalance: refresh on breakout names
        if i - last_rebal >= rebal:
            nh = new_high_s.iloc[i]
            el = elig_s.iloc[i]
            entrants = [tk for tk in alt_cols if bool(nh.get(tk, False)) and bool(el.get(tk, False))]
            held = set(entrants)  # rebalance to current breakouts
            last_rebal = i
        else:
            # intra-week new entries
            nh = new_high_s.iloc[i]; el = elig_s.iloc[i]
            for tk in alt_cols:
                if bool(nh.get(tk, False)) and bool(el.get(tk, False)):
                    held.add(tk)
        if held:
            w = 1.0 / len(held)
            for tk in held:
                weights.iloc[i, weights.columns.get_loc(tk)] = w
    return weights

# ---------- portfolio return from weights ----------
def port_returns(weights):
    """Given target weights per day (for that day's return), compute net return after cost.
    Cost = COST * turnover, turnover = sum|w_t - w_{t-1}| (legs). Also BTC cash on non-weight days = 0."""
    Ralt = R[alt_cols].reindex_like(weights)
    gross = (weights * Ralt).sum(axis=1)
    # turnover: weights change day-to-day; treat NaN ret coins as held weight 0 issue minimal
    wprev = weights.shift(1, fill_value=0.0)
    turn = (weights - wprev).abs().sum(axis=1)
    cost = COST * turn
    net = gross - cost
    return net.fillna(0.0), turn.fillna(0.0)

def btc_only_returns():
    """E0 core: hold BTC on bull days else cash. Cost on regime switch."""
    w = pd.Series(np.where(bull.values, 1.0, 0.0), index=idx)
    gross = w * btc_ret.fillna(0.0)
    turn = (w - w.shift(1, fill_value=0.0)).abs()
    net = gross - COST * turn
    return net.fillna(0.0), turn.fillna(0.0)

def blend_returns(topn_w):
    """E3: 50% BTC + 50% E1 on bull days, else cash."""
    alt_net, alt_turn = port_returns(topn_w)
    bw = pd.Series(np.where(bull.values, 0.5, 0.0), index=idx)
    btc_gross = bw * btc_ret.fillna(0.0)
    btc_turn = (bw - bw.shift(1, fill_value=0.0)).abs()
    btc_net = btc_gross - COST * btc_turn
    net = btc_net + 0.5 * alt_net   # alt_net already net of its cost; scale alt sleeve to 50%
    # NOTE alt sleeve weights already sum to 1 on bull days; scale to 0.5
    # recompute properly: alt sleeve at half weight
    alt_half_net, alt_half_turn = port_returns(topn_w * 0.5)
    net = btc_net + alt_half_net
    turn = btc_turn + alt_half_turn
    return net.fillna(0.0), turn.fillna(0.0)

# ---------- metrics ----------
def tstat(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    if len(x) < 3 or x.std(ddof=1) == 0: return np.nan
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))

def metrics(net, turn, mask):
    """mask selects the days (e.g. TEST). Returns decomposed stats."""
    r = net[mask]
    b = bull[mask]
    eq = (1 + r).cumprod()
    total = eq.iloc[-1] - 1
    # bull-only contribution: product of (1+r) on bull days only
    bull_r = r.where(b, 0.0)
    bear_r = r.where(~b, 0.0)
    bull_total = (1 + bull_r).prod() - 1
    bear_total = (1 + bear_r).prod() - 1
    sharpe = (r.mean() / r.std(ddof=1) * np.sqrt(365)) if r.std(ddof=1) > 0 else np.nan
    peak = eq.cummax(); dd = (eq / peak - 1).min()
    avg_turn = turn[mask].mean()
    t_all = tstat(r.values)
    t_bull = tstat(r[b].values)
    return dict(total=total, bull=bull_total, bear=bear_total, sharpe=sharpe,
                mdd=dd, turn=avg_turn, t_all=t_all, t_bull=t_bull,
                bull_daily=r[b])

# HODL BTC
hodl = btc_ret.fillna(0.0)

# ---------- TRAIN selection ----------
train_mask = (seg == "TRAIN").values
test_mask = (seg == "TEST").values

e0_net, e0_turn = btc_only_returns()

engines = {}
engines["E0_BTCcore"] = (e0_net, e0_turn)
for N in (3, 5, 8):
    w = topn_engine(N)
    net, turn = port_returns(w)
    engines[f"E1_top{N}"] = (net, turn)
engines["E1_topW"] = None  # placeholder
bw = breakout_engine()
bnet, bturn = port_returns(bw)
engines["E2_breakout"] = (bnet, bturn)
# blend uses top-5 default (decided after train pick below, but compute for table)

# choose best E1 N on TRAIN by bull-only contribution
train_scores = {}
for N in (3, 5, 8):
    net, turn = engines[f"E1_top{N}"]
    m = metrics(net, turn, train_mask)
    train_scores[N] = m["bull"]
bestN = max(train_scores, key=train_scores.get)
del engines["E1_topW"]

# blend with best N
blend_w = topn_engine(bestN)
blnet, blturn = blend_returns(blend_w)
engines["E3_blend"] = (blnet, blturn)

# TRAIN pick across all engine families (E0..E3) by bull contribution
train_all = {}
for name, (net, turn) in engines.items():
    train_all[name] = metrics(net, turn, train_mask)["bull"]
# pick best among E1/E2/E3 alt-engines (the question: does an alt engine beat core)
alt_engine_names = [k for k in engines if k != "E0_BTCcore" and k != "HODL"]
train_pick = max([k for k in engines], key=lambda k: train_all[k])

print("\n=== TRAIN bull-contribution scores ===")
for k, v in sorted(train_all.items(), key=lambda x: -x[1]):
    print(f"  {k:14s} bull={v:+.3f}")
print(f"  best E1 N (train) = top{bestN}")
print(f"  TRAIN-picked engine (max bull contrib) = {train_pick}")

# ---------- TEST table ----------
print("\n=== TEST (last 40%) decomposed — all engines ===")
test_bull_days = int(bull[test_mask].sum())
# count distinct bull segments in TEST
bt = bull[test_mask].astype(int).values
segs = int(((bt[1:] == 1) & (bt[:-1] == 0)).sum() + (1 if len(bt) and bt[0] == 1 else 0))
print(f"TEST days={int(test_mask.sum())}  bull days={test_bull_days}  distinct bull segments={segs}")
print(f"{'engine':14s} {'total':>8s} {'BULLcap':>8s} {'BEARcap':>8s} {'Sharpe':>7s} {'MaxDD':>7s} {'turn':>6s} {'t_all':>6s} {'t_bull':>6s}")

# HODL row
hm = metrics(hodl, pd.Series(0.0, index=idx), test_mask)
def prow(name, m):
    print(f"{name:14s} {m['total']*100:7.1f}% {m['bull']*100:7.1f}% {m['bear']*100:7.1f}% "
          f"{m['sharpe']:7.2f} {m['mdd']*100:6.1f}% {m['turn']:6.3f} {m['t_all']:6.2f} {m['t_bull']:6.2f}")

prow("HODL_BTC", hm)
test_metrics = {}
order = ["E0_BTCcore", f"E1_top3", f"E1_top5", f"E1_top8", "E2_breakout", "E3_blend"]
for name in order:
    net, turn = engines[name]
    m = metrics(net, turn, test_mask)
    test_metrics[name] = m
    prow(name, m)

# ---------- RED TEAM ----------
print("\n=== RED-TEAM ===")
# (b) per-day excess of best alt engine minus BTC over BULL days (TEST)
best_alt = train_pick if train_pick != "E0_BTCcore" else f"E1_top{bestN}"
print(f"Best alt engine for excess test: {best_alt}")
e0m = test_metrics["E0_BTCcore"]
am = test_metrics[best_alt]
# align bull-day daily returns
bull_test = bull[test_mask]
alt_net_test = engines[best_alt][0][test_mask][bull_test]
btc_net_test = engines["E0_BTCcore"][0][test_mask][bull_test]
excess = (alt_net_test - btc_net_test)
print(f"  BULL-capture: {best_alt}={am['bull']*100:.1f}%  vs E0_BTCcore={e0m['bull']*100:.1f}%")
print(f"  per-day excess (alt-BTC) over {len(excess)} bull days: mean={excess.mean()*100:.3f}%/day  t={tstat(excess.values):.2f}")

# (a) survivorship: how many alt coins exist full history vs listed late
full_hist = sum(1 for tk in alt_cols if listed[tk].iloc[train_end:].all())
late = len(alt_cols) - full_hist
print(f"  SURVIVORSHIP: {len(alt_cols)} alts; {full_hist} present entire TEST, {late} listed/missing partway.")
# crude impact: top-N momentum tends to chase recent winners -> survivor flatter
print("    -> momentum top-N preferentially holds recent big movers; dataset only contains coins that")
print("       still exist today (delisted pumps absent), so realised alt returns are upward-biased.")

# (c) sample adequacy
print(f"  SAMPLE: TEST bull days={test_bull_days}, segments={segs}. ", end="")
print("ADEQUATE" if segs >= 4 else "WEAK (few segments)")

# (d) lookahead/liquidity
print(f"  LIQUIDITY FLOOR={LIQ_FLOOR:,.0f} KRW 20d-avg. All signals shifted +1 day (no lookahead).")
print(f"    avg eligible alts/day in TEST bull = {elig_liq.shift(1)[test_mask][bull_test].sum(axis=1).mean():.1f}")

# verdict helper
print("\n=== VERDICT INPUTS ===")
for name in order:
    m = test_metrics[name]
    print(f"  {name:14s} TEST total={m['total']*100:6.1f}%  BULLcap={m['bull']*100:6.1f}%  t_bull={m['t_bull']:.2f}")
print(f"  HODL_BTC      TEST total={hm['total']*100:6.1f}%")

# ---------- ROBUSTNESS: bull segment detail + survivorship stress ----------
print("\n=== ROBUSTNESS ===")
# describe the single TEST bull segment span
bt_series = bull[test_mask]
dates_test = idx[test_mask]
in_bull = bt_series.values
# find contiguous runs
runs = []
i = 0
while i < len(in_bull):
    if in_bull[i]:
        j = i
        while j < len(in_bull) and in_bull[j]:
            j += 1
        runs.append((dates_test[i], dates_test[j-1], j-i))
        i = j
    else:
        i += 1
print("TEST bull runs (start, end, len):")
for r in runs:
    print(f"   {r[0]} -> {r[1]}  ({r[2]} days)")

# Survivorship stress: drop coins NOT present for the entire TEST window from the alt universe,
# i.e. only trade coins that existed before TEST began (proxy for 'no late-listed survivors').
survivor_safe = [tk for tk in alt_cols if listed[tk].iloc[:train_end].sum() > 100]
print(f"\nSurvivorship-stress universe: {len(survivor_safe)}/{len(alt_cols)} alts existed (>100d) pre-TEST")

def topn_engine_universe(N, universe, rebal=5):
    cols = universe
    weights = pd.DataFrame(0.0, index=idx, columns=alt_cols)
    cur = []; last_rebal = -10**9
    mom_s = mom20.shift(1); elig_s = elig_liq.shift(1, fill_value=False)
    for i2 in range(n):
        if not bull.iloc[i2]:
            cur = []; continue
        if i2 - last_rebal >= rebal or not cur:
            row_mom = mom_s.iloc[i2][cols]; row_elig = elig_s.iloc[i2][cols]
            cand = row_mom[row_elig.fillna(False) & row_mom.notna()].sort_values(ascending=False)
            cur = list(cand.index[:N]); last_rebal = i2
        if cur:
            w = 1.0/len(cur)
            for tk in cur:
                weights.iloc[i2, weights.columns.get_loc(tk)] = w
    return weights

w_ss = topn_engine_universe(bestN, survivor_safe)
net_ss, turn_ss = port_returns(w_ss)
m_ss = metrics(net_ss, turn_ss, test_mask)
print(f"E1_top{bestN} on survivor-safe universe: TEST total={m_ss['total']*100:.1f}%  BULLcap={m_ss['bull']*100:.1f}%  t_bull={m_ss['t_bull']:.2f}")

# Alt split robustness: use 50% split instead of 60% to see if pick & sign hold
for sp in (0.5, 0.7):
    te = int(n*sp)
    tm = np.zeros(n, bool); tm[te:] = True
    e1 = engines[f"E1_top3"][0]; e1t = engines[f"E1_top3"][1]
    m1 = metrics(e1, e1t, tm)
    e0m2 = metrics(e0_net, e0_turn, tm)
    bd = int(bull[tm].sum())
    print(f"split={sp}: TEST bull days={bd}  E1_top3 BULLcap={m1['bull']*100:.1f}% (t={m1['t_bull']:.2f})  E0 BULLcap={e0m2['bull']*100:.1f}%")
