"""Technical indicator calculations from candle data."""
import time
import requests as _requests

# ── 바이낸스 펀딩율 ────────────────────────────────────────────────────────────

FUNDING_RATE_MAX  = 0.001   # +0.1% 초과 시 롱 과열 — 진입 차단
FUNDING_CACHE_TTL = 300     # 5분 캐시 (펀딩율은 8시간 주기로 갱신)
_funding_cache: dict[str, tuple[float, float]] = {}  # coin → (rate, ts)


def get_binance_funding_rate(coin: str, timeout: float = 2.0) -> float | None:
    """바이낸스 선물 펀딩율 조회. 미상장·오류 시 None 반환 (진입 허용).

    None = 필터 스킵 (차단 아님). 봇 장애 방지 우선.
    """
    now = time.time()
    cached = _funding_cache.get(coin)
    if cached and now - cached[1] < FUNDING_CACHE_TTL:
        return cached[0]
    symbol = f"{coin.upper()}USDT"
    try:
        resp = _requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=timeout,
        )
        if resp.status_code == 400:   # 바이낸스 미상장
            return None
        resp.raise_for_status()
        rate = float(resp.json()["lastFundingRate"])
        _funding_cache[coin] = (rate, now)
        return rate
    except Exception:
        return None


def is_funding_ok(coin: str) -> bool:
    """펀딩율이 과열(+0.1% 초과)이 아니면 True. 조회 실패 시 True(통과)."""
    rate = get_binance_funding_rate(coin)
    if rate is None:
        return True   # 미상장·오류 → 차단하지 않음
    return rate <= FUNDING_RATE_MAX


def _closes(candles: list[dict]) -> list[float]:
    """Extract close prices in chronological order (candles are newest-first)."""
    return [float(c["trade_price"]) for c in reversed(candles)]


def calc_rsi(candles: list[dict], period: int = 14) -> float | None:
    closes = _closes(candles)
    if len(closes) < period + 1:
        return None
    closes = closes[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calc_bb_pct(candles: list[dict], period: int = 20, std_mult: float = 2.0) -> float | None:
    """Bollinger Band %B: 0=lower band, 1=upper band, >1=above upper."""
    closes = _closes(candles)
    if len(closes) < period:
        return None
    closes = closes[-period:]
    mean = sum(closes) / period
    std = (sum((c - mean) ** 2 for c in closes) / period) ** 0.5
    if std == 0:
        return None
    upper = mean + std_mult * std
    lower = mean - std_mult * std
    current = closes[-1]
    return round((current - lower) / (upper - lower), 3)


def calc_macd_bull(candles: list[dict],
                   fast: int = 12, slow: int = 26, signal: int = 9) -> bool | None:
    """Return True if MACD line > Signal line (bullish momentum)."""
    closes = _closes(candles)
    if len(closes) < slow + signal:
        return None

    def ema(data: list[float], n: int) -> list[float]:
        k = 2 / (n + 1)
        result = [data[0]]
        for price in data[1:]:
            result.append(price * k + result[-1] * (1 - k))
        return result

    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line = [f - s for f, s in zip(fast_ema[slow - fast:], slow_ema)]
    if len(macd_line) < signal:
        return None
    signal_line = ema(macd_line, signal)
    return macd_line[-1] > signal_line[-1]


def calc_ema(candles: list[dict], period: int = 9) -> float | None:
    """Return latest EMA value (exponential moving average)."""
    closes = _closes(candles)
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 8)


def is_ema_bouncing(candles: list[dict], period: int = 9) -> bool:
    """EMA 반등 확인: 현재가 > EMA9 이고 최근 캔들이 양봉(close > open).
    van de Poppe 방식: 눌림목 후 EMA 위로 회복 = 반등 시작 신호."""
    closes = _closes(candles)
    ema = calc_ema(candles, period)
    if ema is None or len(candles) < 2:
        return False
    latest_close = closes[-1]
    latest_open = float(candles[0]["opening_price"])  # candles newest-first
    green_candle = latest_close >= latest_open        # 최근 캔들 양봉
    above_ema = latest_close > ema                    # EMA 위로 회복
    return green_candle and above_ema


def snapshot(client, market: str) -> dict:
    """Fetch 35 1-min candles and return indicator dict. Never raises."""
    result = {"rsi": None, "bb_pct": None, "macd_bull": None}
    try:
        candles = client.get_candles(market, unit=1, count=35)
        result["rsi"] = calc_rsi(candles)
        result["bb_pct"] = calc_bb_pct(candles)
        mb = calc_macd_bull(candles)
        result["macd_bull"] = (1 if mb else 0) if mb is not None else None
    except Exception:
        pass
    return result
