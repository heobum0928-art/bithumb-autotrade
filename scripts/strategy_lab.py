"""전략 해석엔진 — 전략을 '코드'가 아닌 '구조화 스펙(JSON)'으로 표현하고 백테스트한다.
매일 새 전략 리서치의 안전한 실행기: LLM/사람이 스펙만 주면 엔진이 2년 일봉에 walk-forward 실행.
임의 코드 실행 없음 — 미리 정의된 진입/청산/필터 프리미티브의 파라미터 조합만 해석한다.

스펙 스키마:
{
  "name": "사람이 읽는 이름",
  "entry":  {"type": "breakout|ma_cross|rsi|bb_lower|donchian|vol_spike", "params": {...}},
  "exit":   {"type": "trail|timeout|target_sl|opposite", "params": {...}},
  "filter": {"regime": "BULL|BEAR|any"}        # BTC 200일선 기준 진입일 장세 게이트
}

진입 params 예:
  breakout: {"n": 20}                 # 종가가 직전 n일 신고가 돌파
  ma_cross: {"s": 10, "l": 30}        # 단기SMA가 장기SMA 상향돌파(골든크로스)
  rsi:      {"period": 14, "below": 30}  # RSI 과매도 진입
  bb_lower: {"period": 20, "k": 2.0}  # 볼린저 하단 터치
  donchian: {"n": 20}                 # breakout 별칭
  vol_spike:{"n": 20, "mult": 3.0}    # 거래량이 n일평균의 mult배 + 상승봉

청산 params 예:
  trail:     {"trail": 0.05, "activate": 0.0, "sl": -0.10, "timeout": 30}
  timeout:   {"days": 10, "sl": -0.10}
  target_sl: {"target": 0.10, "sl": -0.05, "timeout": 30}
  opposite:  {"n": 10}                # 직전 n일 신저가 이탈 시 청산(추세추종 짝)

비용 0.16/0.30% 왕복. walk-forward TEST[0.6,1.0). 코인별 독립 비중복 포지션.
"""
import sys, json, glob, statistics as st
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "candles_daily"
TRAIN = 0.6


# ── 데이터 로드 ────────────────────────────────────────────────
def load_candles(min_len: int = 250) -> dict:
    cc = {}
    for f in glob.glob(str(DAILY / "*_1d.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if len(d) >= min_len:
            cc[Path(f).stem.replace("_1d", "")] = d
    return cc


def btc_regime() -> dict:
    """date(YYYY-MM-DD) -> 'BULL'/'BEAR' (BTC 200일 SMA 기준)."""
    f = DAILY / "BTC_1d.json"
    if not f.exists():
        return {}
    d = json.loads(f.read_text(encoding="utf-8"))
    closes = [x["trade_price"] for x in d]
    reg = {}
    for i in range(len(d)):
        if i < 200:
            continue
        sma = sum(closes[i - 200:i]) / 200
        reg[d[i]["candle_date_time_kst"][:10]] = "BULL" if closes[i] > sma else "BEAR"
    return reg


# ── 보조 계산 ──────────────────────────────────────────────────
def _sma(seq, i, n):
    return sum(seq[i - n:i]) / n


def _rsi(closes, i, period):
    if i < period:
        return None
    gains = losses = 0.0
    for j in range(i - period + 1, i + 1):
        ch = closes[j] - closes[j - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


# ── 진입 신호: index i에서 진입하면 True ───────────────────────
def _entry_signal(etype, p, candles, closes, highs, lows, vols, i):
    if etype in ("breakout", "donchian"):
        n = p.get("n", 20)
        if i < n:
            return False
        return closes[i] > max(highs[i - n:i])
    if etype == "ma_cross":
        s, l = p.get("s", 10), p.get("l", 30)
        if i < l + 1:
            return False
        return _sma(closes, i, s) > _sma(closes, i, l) and _sma(closes, i - 1, s) <= _sma(closes, i - 1, l)
    if etype == "rsi":
        period, below = p.get("period", 14), p.get("below", 30)
        r = _rsi(closes, i, period)
        return r is not None and r <= below
    if etype == "bb_lower":
        period, k = p.get("period", 20), p.get("k", 2.0)
        if i < period:
            return False
        win = closes[i - period:i]
        sma = sum(win) / period
        sd = st.pstdev(win)
        return sd > 0 and closes[i] <= sma - k * sd
    if etype == "vol_spike":
        n, mult = p.get("n", 20), p.get("mult", 3.0)
        if i < n:
            return False
        avg = sum(vols[i - n:i]) / n
        return avg > 0 and vols[i] >= avg * mult and closes[i] > closes[i - 1]
    raise ValueError(f"unknown entry type: {etype}")


# ── 청산: 진입 후 경로를 따라가며 (exit_price, exit_index) 반환 ──
def _run_exit(xtype, p, candles, closes, highs, lows, entry, ei, cost):
    nlen = len(candles)
    if xtype == "trail":
        trail = p.get("trail", 0.05); activate = p.get("activate", 0.0)
        sl = p.get("sl", -0.10); timeout = p.get("timeout", 30)
        peak = entry; armed = (activate <= 0)
        end = min(ei + 1 + timeout, nlen)
        for j in range(ei + 1, end):
            hi, lo, cl = highs[j], lows[j], closes[j]
            if lo <= entry * (1 + sl):
                return entry * (1 + sl), j
            peak = max(peak, hi)
            if not armed and peak >= entry * (1 + activate):
                armed = True
            if armed and lo <= peak * (1 - trail):
                return peak * (1 - trail), j
        return closes[min(end, nlen) - 1], min(end, nlen) - 1
    if xtype == "timeout":
        days = p.get("days", 10); sl = p.get("sl", -0.10)
        end = min(ei + 1 + days, nlen)
        for j in range(ei + 1, end):
            if lows[j] <= entry * (1 + sl):
                return entry * (1 + sl), j
        return closes[min(end, nlen) - 1], min(end, nlen) - 1
    if xtype == "target_sl":
        target = p.get("target", 0.10); sl = p.get("sl", -0.05); timeout = p.get("timeout", 30)
        end = min(ei + 1 + timeout, nlen)
        for j in range(ei + 1, end):
            if lows[j] <= entry * (1 + sl):
                return entry * (1 + sl), j
            if highs[j] >= entry * (1 + target):
                return entry * (1 + target), j
        return closes[min(end, nlen) - 1], min(end, nlen) - 1
    if xtype == "opposite":
        n = p.get("n", 10)
        j = ei + 1
        while j < nlen:
            if j >= n and closes[j] < min(lows[j - n:j]):
                return closes[j], j
            j += 1
        return closes[-1], nlen - 1
    raise ValueError(f"unknown exit type: {xtype}")


# ── 한 코인 백테스트 ───────────────────────────────────────────
def _trades_for_coin(candles, spec, cost, lo, hi):
    nlen = len(candles)
    a, b = int(nlen * lo), int(nlen * hi)
    closes = [c["trade_price"] for c in candles]
    highs = [c["high_price"] for c in candles]
    lows = [c["low_price"] for c in candles]
    vols = [c.get("candle_acc_trade_volume", c.get("candle_acc_trade_price", 0.0)) for c in candles]
    et, ep = spec["entry"]["type"], spec["entry"].get("params", {})
    xt, xp = spec["exit"]["type"], spec["exit"].get("params", {})
    out = []
    i = 1
    while i < nlen - 1:
        if not (a <= i < b):
            i += 1; continue
        if _entry_signal(et, ep, candles, closes, highs, lows, vols, i):
            entry = closes[i]; edate = candles[i]["candle_date_time_kst"][:10]
            ex_px, ex_i = _run_exit(xt, xp, candles, closes, highs, lows, entry, i, cost)
            out.append({"date": edate, "ret": (ex_px - entry) / entry - cost})
            i = max(ex_i, i + 1)
        else:
            i += 1
    return out


# ── 스펙 전체 실행 + 통계 ──────────────────────────────────────
def backtest(spec, cost=0.0016, lo=TRAIN, hi=1.0, candles_by_coin=None, regime=None):
    cc = candles_by_coin if candles_by_coin is not None else load_candles()
    reg = regime if regime is not None else btc_regime()
    rfilter = spec.get("filter", {}).get("regime", "any")
    all_trades = []
    for coin, d in cc.items():
        for t in _trades_for_coin(d, spec, cost, lo, hi):
            if rfilter != "any" and reg.get(t["date"]) != rfilter:
                continue
            all_trades.append(t)
    rets = [t["ret"] for t in all_trades]
    n = len(rets)
    if n == 0:
        return {"n": 0, "avg": 0, "sd": 0, "t": 0, "wr": 0, "sum": 0}
    p = [r * 100 for r in rets]
    avg = sum(p) / n
    sd = st.pstdev(p) if n > 1 else 0
    t = avg / (sd / n ** 0.5) if sd else 0
    wr = sum(1 for x in p if x > 0) / n * 100
    return {"n": n, "avg": avg, "sd": sd, "t": t, "wr": wr, "sum": sum(p)}


def evaluate(spec, verbose=True):
    """비용 0.16/0.30% 두 가지로 평가해 dict 반환."""
    cc = load_candles(); reg = btc_regime()
    res = {}
    for cost, tag in ((0.0016, "0.16%"), (0.0030, "0.30%")):
        r = backtest(spec, cost=cost, candles_by_coin=cc, regime=reg)
        res[tag] = r
        if verbose:
            print(f"  비용{tag}: {r['n']:3}건 승률{r['wr']:3.0f}% 거래당{r['avg']:+.2f}% t{r['t']:+.2f} 표본합{r['sum']:+.0f}%")
    return res


if __name__ == "__main__":
    # 시연: 새 조합 — '돌파 + 장세BULL 필터 + 트레일 청산' (기존 19개 미시도 조합)
    demo = {
        "name": "돌파20일 + BULL장세필터 + 트레일5%",
        "entry": {"type": "breakout", "params": {"n": 20}},
        "exit": {"type": "trail", "params": {"trail": 0.05, "activate": 0.01, "sl": -0.10, "timeout": 30}},
        "filter": {"regime": "BULL"},
    }
    print(f"[시연] {demo['name']}")
    evaluate(demo)
