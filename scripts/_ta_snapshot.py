"""바이낸스 코인 기술지표 스냅샷 — 재량매매 후보 판단 보조용 (2026-07-15).

DEXE 손절 사건(RSI 미확인하고 진입) 이후 사용자 요청: "RSI뿐만 아니라 다른 지표도 참고".
bithumb/indicators.py와 동일 계산식(RSI14/BB%B/MACD)을 바이낸스 klines 배열 형식에 맞춰 재구현.
자동매매 신호로 쓰는 게 아니라(이 프로젝트는 TA단독 신호 106조합 전멸 확인됨), 재량 진입 전
"이미 반전 조짐 있는지" 마지막 점검용.

Run: python scripts/_ta_snapshot.py <SYMBOL예: DEXE>
"""
import sys, requests

BASE = "https://api.binance.com"


def klines(symbol, interval="5m", limit=100):
    r = requests.get(f"{BASE}/api/v3/klines", params={"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}, timeout=10)
    r.raise_for_status()
    return r.json()


def wilder_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag/al)


def rsi_series(closes, period=14, tail=6):
    """최근 tail개 RSI 값 — 방향(오르는중/내리는중) 확인용."""
    out = []
    for i in range(len(closes) - tail, len(closes)):
        if i < period + 1: continue
        out.append(wilder_rsi(closes[:i+1], period))
    return out


def bb_pct(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x-mean)**2 for x in window) / period
    std = var ** 0.5
    upper = mean + mult*std; lower = mean - mult*std
    if upper == lower:
        return None
    return (closes[-1] - lower) / (upper - lower)


def ema(vals, period):
    k = 2 / (period + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1-k)
        out.append(e)
    return out


def macd_hist(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line[-(slow+signal):], signal)
    hist = macd_line[-1] - signal_line[-1]
    hist_prev = macd_line[-2] - signal_line[-2] if len(macd_line) > 1 and len(signal_line) > 1 else None
    return hist, hist_prev


def snapshot(symbol):
    k = klines(symbol, "5m", 100)
    closes = [float(x[4]) for x in k]
    vols = [float(x[7]) for x in k]
    price = closes[-1]

    rsi_now = wilder_rsi(closes)
    rseries = rsi_series(closes, tail=6)
    rsi_trend = "상승중" if len(rseries) >= 2 and rseries[-1] > rseries[0] else ("하락중" if len(rseries) >= 2 and rseries[-1] < rseries[0] else "?")

    bb = bb_pct(closes)
    hist, hist_prev = macd_hist(closes)
    macd_trend = None
    if hist is not None and hist_prev is not None:
        macd_trend = "히스토그램 커지는중(하락가속)" if hist < hist_prev < 0 else \
                     "히스토그램 줄어드는중(하락둔화·반전조짐)" if hist_prev < hist < 0 else \
                     "0선 위(상승국면)" if hist > 0 else "?"

    avg_vol20 = sum(vols[-21:-1]) / 20
    vratio = vols[-1] / avg_vol20 if avg_vol20 > 0 else 0

    c1h = (closes[-1]/closes[-13]-1)*100 if len(closes) > 13 else None
    c3h = (closes[-1]/closes[-37]-1)*100 if len(closes) > 37 else None

    print(f"=== {symbol} 기술지표 스냅샷 ===")
    print(f"  현재가: {price:.6g}")
    if c1h is not None: print(f"  1h: {c1h:+.2f}% | 3h: {c3h:+.2f}%" if c3h is not None else f"  1h: {c1h:+.2f}%")
    print(f"  RSI(14): {rsi_now:.1f} (최근추세: {rsi_trend}) — 최근6봉: {[round(x,1) for x in rseries]}")
    if bb is not None:
        bb_note = "하단이탈(과매도)" if bb < 0 else "상단이탈(과매수)" if bb > 1 else f"밴드내 {bb*100:.0f}%위치"
        print(f"  볼린저%B: {bb:.2f} ({bb_note})")
    if hist is not None:
        print(f"  MACD 히스토그램: {hist:+.5f} ({macd_trend})")
    print(f"  거래량배수(직전20봉평균 대비): {vratio:.1f}배")
    print()
    warn = []
    # ★ 2026-07-16 일반화: UNI·SNX·DEXE·ONDO 재량숏 손실 4건 전부 공통점 발견 —
    #   "RSI가 낮을 때만"이 아니라 절대 RSI 구간과 무관하게 "최근 몇 봉 내 저점 찍고 반등 중"이면
    #   전부 위험(ONDO는 RSI55~60대에서 반등해서 기존 <35 조건에 안 걸렸었음). 트로프 대비 회복폭으로 판정.
    rsi_recovery = rsi_now - min(rseries) if rseries else 0
    if rsi_recovery >= 8 and rsi_trend == "상승중":
        warn.append(f"RSI가 최근저점({min(rseries):.1f})에서 이미 {rsi_recovery:.1f}p 반등 중 — 숏 진입 시 늦었을 가능성 (절대RSI값 무관)")
    rsi_pullback = max(rseries) - rsi_now if rseries else 0
    if rsi_pullback >= 8 and rsi_trend == "하락중":
        warn.append(f"RSI가 최근고점({max(rseries):.1f})에서 이미 {rsi_pullback:.1f}p 꺾이는 중 — 롱 진입 시 늦었을 가능성 (절대RSI값 무관)")
    if bb is not None and bb < 0 and rsi_trend == "상승중":
        warn.append("볼린저 하단 이탈 + RSI 반등 = 과매도 반전 초기 신호(숏에 불리)")
    if warn:
        print("⚠️ 주의:")
        for w in warn: print(f"   - {w}")
    else:
        print("특별한 반전 경고 신호 없음")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python scripts/_ta_snapshot.py <SYMBOL>")
        sys.exit(1)
    snapshot(sys.argv[1].upper())
