"""
알트 비(非)추격 진입법 백테스트 — 빗썸 5분봉 기반.

배경: "펌핑 추격"(신호 보고 시장가 매수)은 음의 기대값으로 확인됨
      (신호 후 5분 -1.85%, 30분 -3.37% → 고점 매수).
      수수료 0.04% 쿠폰 등록으로 왕복 0.08% 가능해짐 (기존 0.5% → 84% 절감).
      추격이 아닌 진입법을 0.08% / 0.5% 두 비용으로 재검증한다.

검증 진입법 (알트 대상, point-in-time 거래대금 상위 N개):
  A. 눌림목 대기 (mean-reversion): 당일 고점 대비 -X% 되돌림 시 지정가 매수
  B. 돌파 후 재테스트 (breakout retest): N봉 신고가 돌파 후 그 레벨로 풀백, 지지 확인 시 진입
  C. 변동성 수축→확장 (BB squeeze): 밴드폭 수축 후 확장 초입 진입

규율:
  - lookahead 금지: 신호는 "완성된 봉" 종가/고저로만 판단, 체결은 "다음 봉 시가".
  - point-in-time 화이트리스트: 매 진입 시점의 직전 24h 거래대금(candle_acc_trade_price 합)
    상위 N개만 후보. "오늘" 거래량이 아니라 그 시점 기준 → 생존편향 완화.
  - walk-forward: 시간순 train(60%)/test(40%) 분할. 파라미터는 train에서만 선택, test로 판정.
  - 자본 배분: 동시 포지션 1개(단일 슬롯) 직렬 — 한 코인 청산 후 다음 진입.
    중복 자본 투입 가정(trend_backtest.py summarize 버그)을 피하기 위해
    단일 슬롯 equity 곡선으로 복리/MDD를 정확히 계산.
  - 비용: case 0.08%(쿠폰) vs case 0.58%(쿠폰 0.08% + 알트 슬리피지 0.5%) 둘 다 시뮬.

데이터: data/candles_cache/{coin}_5m_90d_{date}.json (vb_backtest.py와 동일 캐시 패턴).
        캐시 없으면 API에서 받아 저장.

Run:
  python -X utf8 scripts/alt_entry_backtest.py
  python -X utf8 scripts/alt_entry_backtest.py --topn 30 --detail
"""
import sys
import json
import time
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 비용 케이스 ───────────────────────────────────────────────────────────────
FEE_COUPON   = 0.0008   # 왕복 0.08% (0.04% 쿠폰 × 2)
SLIP_ALT     = 0.005    # 알트 슬리피지 왕복 0.5%
COST_CASES = {
    "0.08%(쿠폰)":        FEE_COUPON,
    "0.58%(쿠폰+슬립0.5%)": FEE_COUPON + SLIP_ALT,
}

# 화이트리스트(거래대금 점검용) — 5분봉 캐시가 있는 코인 풀.
# 스테이블코인은 제외(돌파/되돌림 의미 없음).
EXCLUDE = {"USDT", "USDC", "DAI"}

BARS_PER_DAY = 288          # 5분봉 하루
VOL_LOOKBACK = BARS_PER_DAY # 거래대금 산정 = 직전 24h 합
TOPN_DEFAULT = 30

CACHE_DIR = Path("data/candles_cache")
API = "https://api.bithumb.com/v1"
DAYS = 90
ENTRY_KRW = 400_000


# ── 데이터 수집 ───────────────────────────────────────────────────────────────
def cached_coins(days: int = DAYS) -> list[str]:
    """오늘 날짜 캐시가 존재하는 코인 목록."""
    today = date.today().isoformat()
    out = []
    for p in CACHE_DIR.glob(f"*_5m_{days}d_{today}.json"):
        coin = p.name.split(f"_5m_{days}d_")[0]
        if coin not in EXCLUDE:
            out.append(coin)
    return sorted(out)


def fetch_5m(coin: str, days: int = DAYS) -> list[dict]:
    """최근 N일 5분봉 (오래된 순). 디스크 캐시 (vb_backtest.py와 동일)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{coin}_5m_{days}d_{date.today().isoformat()}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    need_from = datetime.now() - timedelta(days=days + 1)
    out: list[dict] = []
    to: str | None = None
    while True:
        params = {"market": f"KRW-{coin}", "count": 200}
        if to:
            params["to"] = to
        r = requests.get(f"{API}/candles/minutes/5", params=params, timeout=10)
        if r.status_code != 200:
            break
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk:
            break
        out.extend(chunk)
        oldest = datetime.fromisoformat(chunk[-1]["candle_date_time_kst"])
        if oldest <= need_from or len(chunk) < 200:
            break
        to = oldest.strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(0.05)
    out.sort(key=lambda c: c["candle_date_time_kst"])
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


# ── point-in-time 거래대금 화이트리스트 ────────────────────────────────────────
def build_pit_volume(coin_candles: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
    """
    각 코인의 각 시각(kst 문자열)에 대해 직전 24h 거래대금 합을 미리 계산.
    반환: {coin: {kst: trailing_24h_turnover}}
    lookahead 없음 — i번째 봉 시점의 값은 [i-288, i) 봉 합 (현재 봉 미포함).
    """
    pit: dict[str, dict[str, float]] = {}
    for coin, candles in coin_candles.items():
        vols = [c.get("candle_acc_trade_price", 0.0) for c in candles]
        # prefix sum
        pre = [0.0] * (len(vols) + 1)
        for i, v in enumerate(vols):
            pre[i + 1] = pre[i] + v
        m: dict[str, float] = {}
        for i, c in enumerate(candles):
            lo = max(0, i - VOL_LOOKBACK)
            m[c["candle_date_time_kst"]] = pre[i] - pre[lo]  # [lo, i) — 현재봉 미포함
        pit[coin] = m
    return pit


def is_topn(coin: str, kst: str, pit: dict, topn: int) -> bool:
    """kst 시점에 coin이 거래대금 상위 topn 안인가 (point-in-time)."""
    cur = pit.get(coin, {}).get(kst)
    if cur is None or cur <= 0:
        return False
    rank = 1
    for other, m in pit.items():
        if other == coin:
            continue
        v = m.get(kst)
        if v is not None and v > cur:
            rank += 1
            if rank > topn:
                return False
    return True


# ── 단일 코인 진입 신호 생성 (전략별) ──────────────────────────────────────────
# 각 신호는 (signal_bar_index, entry_ref_price) 를 만들고, 체결은 i+1 시가.
# day 경계: 당일 고점 등은 KST 일자 기준으로 리셋.

def _day_index(candles: list[dict]) -> list[str]:
    return [c["candle_date_time_kst"][:10] for c in candles]


def signals_pullback(candles: list[dict], drop_pct: float,
                     min_run_pct: float) -> list[int]:
    """
    A. 눌림목: 당일 고점 대비 -drop_pct 되돌림.
       추가 필터: 당일 시가→당일고점 상승폭 >= min_run_pct (급등이 선행돼야 '눌림목').
       신호봉 i: 완성된 봉 종가가 (당일고점 × (1-drop_pct)) 이하로 처음 내려온 봉.
       하루 1회만.
    """
    days = _day_index(candles)
    sig: list[int] = []
    day_open = None
    day_high = None
    cur_day = None
    fired = False
    for i, c in enumerate(candles):
        d = days[i]
        if d != cur_day:
            cur_day = d
            day_open = c["opening_price"]
            day_high = c["high_price"]
            fired = False
        # 현재 봉까지 반영된 당일 고점 (이 봉 포함 — 종가 시점엔 이미 알고 있음)
        day_high = max(day_high, c["high_price"])
        if fired:
            continue
        run = (day_high - day_open) / day_open if day_open else 0
        if run < min_run_pct:
            continue
        trigger = day_high * (1 - drop_pct)
        if c["trade_price"] <= trigger:
            sig.append(i)   # 체결은 i+1 시가
            fired = True
    return sig


def signals_retest(candles: list[dict], breakout_n: int, retest_pct: float,
                   confirm_pct: float) -> list[int]:
    """
    B. 돌파 후 재테스트:
       1) 종가가 직전 breakout_n봉 신고가 돌파 → 돌파 레벨 = 그 신고가
       2) 이후 가격이 돌파 레벨의 (1+retest_pct) 이내로 풀백(지지 근접)
       3) 풀백 봉의 종가가 그 봉 시가보다 높음(반등 확인, confirm)이면 신호.
       돌파~재테스트는 같은 봉이 아니라 이후 봉에서 일어나야 함(lookahead 방지).
       돌파 후 24h(288봉) 내 재테스트 없으면 무효.
    """
    sig: list[int] = []
    n = breakout_n
    i = n
    while i < len(candles) - 1:
        prior_high = max(c["high_price"] for c in candles[i - n:i])
        if candles[i]["trade_price"] > prior_high:
            level = prior_high
            # 재테스트 탐색
            for j in range(i + 1, min(i + 1 + BARS_PER_DAY, len(candles))):
                cj = candles[j]
                # 풀백: 저가가 레벨의 (1+retest_pct) 이내로 접근
                if cj["low_price"] <= level * (1 + retest_pct):
                    # 지지 확인: 종가 >= 시가 (반등) and 종가 > 레벨(이탈 안함)
                    if cj["trade_price"] >= cj["opening_price"] and cj["trade_price"] > level:
                        sig.append(j)
                    i = j  # 다음 탐색은 재테스트 이후부터
                    break
            else:
                i = i  # 재테스트 못 찾음
        i += 1
    return sorted(set(sig))


def signals_squeeze(candles: list[dict], bb_n: int, squeeze_pct: float,
                    expand_mult: float) -> list[int]:
    """
    C. 변동성 수축→확장 (볼린저 스퀴즈):
       bb_n봉 이동표준편차 기반 밴드폭 = 2*std/ma (정규화).
       조건: 직전 봉 밴드폭이 최근 bb_n*2 구간의 squeeze_pct 분위 이하(수축) AND
             현재 봉 종가가 상단밴드(ma+2std) 상향 돌파(확장 초입, 상방).
       모두 완성된 봉으로 계산, 체결 i+1 시가.
    """
    import statistics
    closes = [c["trade_price"] for c in candles]
    sig: list[int] = []
    win = bb_n
    look = bb_n * 2
    for i in range(win + look, len(candles) - 1):
        seg = closes[i - win + 1:i + 1]
        ma = sum(seg) / win
        sd = statistics.pstdev(seg)
        if ma <= 0 or sd <= 0:
            continue
        bw = 2 * sd / ma
        # 최근 look 구간의 밴드폭 분포로 수축 판정 (직전 봉까지)
        bws = []
        for k in range(i - look, i):
            s2 = closes[k - win + 1:k + 1]
            if len(s2) < win:
                continue
            m2 = sum(s2) / win
            d2 = statistics.pstdev(s2)
            if m2 > 0:
                bws.append(2 * d2 / m2)
        if len(bws) < look // 2:
            continue
        bws.sort()
        thresh = bws[int(len(bws) * squeeze_pct)]
        prev_bw_low = bw <= thresh  # 현재 밴드폭이 여전히 좁음 → 막 확장 시작
        upper = ma + 2 * sd
        # 확장 초입: 종가가 상단 돌파 + 밴드폭이 직전 평균 대비 확장 시작
        prev_seg = closes[i - win:i]
        prev_sd = statistics.pstdev(prev_seg) if len(prev_seg) == win else sd
        expanding = sd >= prev_sd * expand_mult
        if prev_bw_low and expanding and closes[i] > upper:
            sig.append(i)
    return sig


# ── 청산 시뮬 (공통): 진입 후 TP/SL/타임아웃 ──────────────────────────────────
def simulate_exit(candles: list[dict], entry_idx: int, entry_px: float,
                  tp: float, sl: float, timeout_bars: int) -> dict:
    """
    진입 후 봉을 순회하며 TP/SL/타임아웃 청산.
    보수적: 같은 봉에서 SL 먼저 체크. 체결가는 트리거 레벨(지정가 가정).
    timeout 시 마지막 봉 종가.
    """
    tp_px = entry_px * (1 + tp)
    sl_px = entry_px * (1 + sl)
    end = min(entry_idx + 1 + timeout_bars, len(candles))
    for j in range(entry_idx + 1, end):
        cj = candles[j]
        if cj["low_price"] <= sl_px:
            return {"exit": sl_px, "reason": "SL", "bars": j - entry_idx}
        if cj["high_price"] >= tp_px:
            return {"exit": tp_px, "reason": "TP", "bars": j - entry_idx}
    # 타임아웃
    last = candles[min(end, len(candles)) - 1]
    return {"exit": last["trade_price"], "reason": "TIMEOUT", "bars": end - entry_idx}


# ── 단일 슬롯 직렬 백테스트 (정확한 복리/MDD) ─────────────────────────────────
def run_serial(coin_candles: dict[str, list[dict]], pit: dict, topn: int,
               sig_fn, sig_kwargs: dict, tp: float, sl: float,
               timeout_bars: int, cost: float, lo_frac: float, hi_frac: float):
    """
    모든 코인에서 신호를 모아 (시각순) 단일 슬롯으로 직렬 체결.
    동시에 한 포지션만 — 포지션 보유 중 발생한 다른 신호는 무시.
    자본 100% 단일 슬롯 → equity 곡선으로 복리/MDD 정확 계산.
    구간은 전체 타임라인의 [lo_frac, hi_frac) (walk-forward).
    """
    # 모든 신호 수집: (kst, coin, entry_idx)
    events = []
    for coin, candles in coin_candles.items():
        n = len(candles)
        a, b = int(n * lo_frac), int(n * hi_frac)
        idxs = sig_fn(candles, **sig_kwargs)
        for si in idxs:
            if si + 1 >= n:
                continue
            if not (a <= si < b):
                continue
            kst = candles[si]["candle_date_time_kst"]
            events.append((kst, coin, si))
    events.sort()

    trades = []
    busy_until_kst = ""  # 이 시각 이전엔 새 진입 금지
    for kst, coin, si in events:
        if kst < busy_until_kst:
            continue
        # point-in-time 거래대금 상위 필터
        if not is_topn(coin, kst, pit, topn):
            continue
        candles = coin_candles[coin]
        entry_px = candles[si + 1]["opening_price"]  # 다음 봉 시가 체결
        ex = simulate_exit(candles, si + 1, entry_px, tp, sl, timeout_bars)
        gross = (ex["exit"] - entry_px) / entry_px
        pnl = gross - cost
        exit_kst = candles[min(si + 1 + ex["bars"], len(candles) - 1)]["candle_date_time_kst"]
        trades.append({
            "coin": coin, "entry_kst": candles[si + 1]["candle_date_time_kst"],
            "exit_kst": exit_kst, "entry": entry_px, "exit": ex["exit"],
            "pnl_pct": pnl, "reason": ex["reason"], "bars": ex["bars"],
        })
        busy_until_kst = exit_kst  # 청산까지 슬롯 점유
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": 0, "avg_pct": 0, "total_pct": 0, "mdd_pct": 0}
    wins = [t for t in trades if t["pnl_pct"] > 0]
    ts = sorted(trades, key=lambda t: t["entry_kst"])
    equity, peak, mdd = 1.0, 1.0, 0.0
    for t in ts:
        equity *= 1 + t["pnl_pct"]
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak)
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "avg_pct": sum(t["pnl_pct"] for t in trades) / len(trades) * 100,
        "total_pct": (equity - 1) * 100,
        "mdd_pct": mdd * 100,
    }


# ── 전략 정의: 그리드 + 고정 청산 ─────────────────────────────────────────────
STRATEGIES = {
    "A_눌림목": {
        "fn": signals_pullback,
        "grid": [
            {"drop_pct": d, "min_run_pct": r}
            for d in (0.03, 0.05, 0.08)
            for r in (0.05, 0.10)
        ],
        "exits": [
            {"tp": 0.03, "sl": -0.02, "timeout_bars": 288},
            {"tp": 0.05, "sl": -0.03, "timeout_bars": 288},
        ],
    },
    "B_재테스트": {
        "fn": signals_retest,
        "grid": [
            {"breakout_n": bn, "retest_pct": rt, "confirm_pct": 0.0}
            for bn in (48, 96, 288)     # 4h / 8h / 24h 신고가
            for rt in (0.005, 0.015)
        ],
        "exits": [
            {"tp": 0.04, "sl": -0.025, "timeout_bars": 288},
            {"tp": 0.06, "sl": -0.03, "timeout_bars": 288},
        ],
    },
    "C_스퀴즈": {
        "fn": signals_squeeze,
        "grid": [
            {"bb_n": n, "squeeze_pct": sq, "expand_mult": em}
            for n in (20, 40)
            for sq in (0.2, 0.35)
            for em in (1.1, 1.3)
        ],
        "exits": [
            {"tp": 0.04, "sl": -0.025, "timeout_bars": 144},
            {"tp": 0.06, "sl": -0.03, "timeout_bars": 288},
        ],
    },
}

TRAIN_FRAC = 0.60   # 앞 60% train, 뒤 40% test


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", type=int, default=TOPN_DEFAULT)
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--only", type=str, default="", help="A/B/C 중 하나만")
    args = ap.parse_args()

    coins = cached_coins()
    print(f"=== 알트 비추격 진입법 백테스트 ===")
    print(f"유니버스: 캐시된 {len(coins)}개 코인 (스테이블 제외)")
    print(f"point-in-time 거래대금 상위 {args.topn}개만 진입 후보 (직전 24h 합 기준)")
    print(f"walk-forward: 앞 {TRAIN_FRAC*100:.0f}% train(파라미터 선택) / 뒤 {(1-TRAIN_FRAC)*100:.0f}% test(판정)")
    print(f"비용 케이스: {' | '.join(COST_CASES.keys())}")
    print(f"단일 슬롯 직렬 체결 (동시 1포지션, 정확한 복리/MDD)\n")

    coin_candles: dict[str, list[dict]] = {}
    for c in coins:
        cc = fetch_5m(c)
        if len(cc) >= BARS_PER_DAY * 5:
            coin_candles[c] = cc
    print(f"데이터 로드 완료: {len(coin_candles)}개 코인\n")

    print("point-in-time 거래대금 인덱스 구축 중...")
    pit = build_pit_volume(coin_candles)
    print("완료.\n")

    results = {}
    for name, spec in STRATEGIES.items():
        if args.only and not name.startswith(args.only):
            continue
        print(f"\n{'='*70}\n전략 {name}\n{'='*70}")

        # train에서 (그리드 × 청산) 최적 선택 — 비용은 보수적으로 0.58% 케이스로 선택
        sel_cost = COST_CASES["0.58%(쿠폰+슬립0.5%)"]
        best = None
        best_score = -1e9
        print(f"--- TRAIN (파라미터 선택, 비용 0.58% 기준) ---")
        for params in spec["grid"]:
            for ex in spec["exits"]:
                tr = run_serial(coin_candles, pit, args.topn, spec["fn"], params,
                                ex["tp"], ex["sl"], ex["timeout_bars"], sel_cost,
                                0.0, TRAIN_FRAC)
                s = summarize(tr)
                # 표본 부족하면 후보 제외
                if s["n"] < 10:
                    continue
                score = s["total_pct"]
                if score > best_score:
                    best_score = score
                    best = (params, ex)
        if best is None:
            print("  train 표본 부족 — 유효 후보 없음. 판정 보류.")
            results[name] = None
            continue
        params, ex = best
        ts = summarize(run_serial(coin_candles, pit, args.topn, spec["fn"], params,
                                  ex["tp"], ex["sl"], ex["timeout_bars"], sel_cost, 0.0, TRAIN_FRAC))
        print(f"  선택 파라미터: {params} | 청산 TP{ex['tp']*100:+.0f}% SL{ex['sl']*100:+.0f}% "
              f"타임아웃{ex['timeout_bars']}봉")
        print(f"  train 성과: {ts['n']}건 승률{ts['win_rate']:.0f}% 평균{ts['avg_pct']:+.2f}% "
              f"누적{ts['total_pct']:+.1f}% MDD{ts['mdd_pct']:.1f}%")

        # test: 두 비용 케이스
        print(f"--- TEST (out-of-sample, 진짜 성적) ---")
        test_rows = {}
        detail_trades = None
        for cost_name, cost in COST_CASES.items():
            tr = run_serial(coin_candles, pit, args.topn, spec["fn"], params,
                            ex["tp"], ex["sl"], ex["timeout_bars"], cost, TRAIN_FRAC, 1.0)
            s = summarize(tr)
            test_rows[cost_name] = s
            if detail_trades is None:
                detail_trades = tr
            verdict = ""
            if s["n"] < 30:
                verdict = " [표본<30 판정보류]"
            elif s["total_pct"] > 0:
                verdict = " GO후보"
            else:
                verdict = " NO-GO"
            print(f"  [{cost_name:20s}] {s['n']:3}건 승률{s['win_rate']:5.0f}% "
                  f"평균{s['avg_pct']:+.2f}% 누적{s['total_pct']:+7.1f}% MDD{s['mdd_pct']:5.1f}%{verdict}")
        results[name] = {"params": params, "exit": ex, "test": test_rows}

        if args.detail and detail_trades:
            print("  개별 거래 (test, 0.08% 케이스):")
            for t in sorted(detail_trades, key=lambda x: x["entry_kst"])[:40]:
                print(f"    {t['entry_kst'][:16]} {t['coin']:7s} {t['pnl_pct']*100:+5.1f}% "
                      f"{t['reason']:7s} {t['bars']}봉")

    # ── 종합 판정 ──
    print(f"\n\n{'='*70}\n종합 판정\n{'='*70}")
    for name, r in results.items():
        if r is None:
            print(f"  {name}: 표본 부족 — 판정 보류")
            continue
        c08 = r["test"]["0.08%(쿠폰)"]
        c58 = r["test"]["0.58%(쿠폰+슬립0.5%)"]
        if c08["n"] < 30:
            tag = "표본 부족(판정 보류)"
        elif c08["total_pct"] > 0 and c58["total_pct"] > 0:
            tag = "GO 후보 (두 비용 모두 +)"
        elif c08["total_pct"] > 0:
            tag = "조건부: 0.08%에선 +, 슬립0.5%에선 -  (쿠폰으로 살아남)"
        else:
            tag = "NO-GO (0.08%에서도 -)"
        print(f"  {name}: 0.08%={c08['total_pct']:+.1f}%({c08['n']}건) "
              f"0.58%={c58['total_pct']:+.1f}% → {tag}")


if __name__ == "__main__":
    main()
