"""빗썸 펌핑 눌림목 전략 백테스트 — 틱 재생 기반 오프라인 시뮬레이션 (읽기 전용)."""

import argparse
import csv
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.db import DB_PATH, get_ticks

# ── 전략 상수 (D-08: Phase 2는 고정 1조합. Phase 3가 그리드 서치로 파라미터화) ──
ENTRY_DROP_PCT   = 0.07    # D-02: 주행 고점 대비 -7% 눌림에서 진입
TP_PCT           = 0.05    # D-06: 익절 +5%
SL_PCT           = 0.03    # D-06: 손절 -3%
TIMEOUT_SEC      = 600     # D-06: 시간초과 청산 (10분 = 이벤트 길이)
MIN_TICKS        = 4       # 이 미만의 틱을 가진 이벤트는 스킵 (Discretion)
GAP_EXCLUDE_PCT  = 0.30    # D-15: 갭 비율이 30% 초과면 이벤트 통째 제외

# ── 비용 모델 (BT-04) ──
ROUND_TRIP_FEE   = 0.005                       # 왕복 수수료 0.5%
SLIPPAGE_SCENARIOS = (0.0, 0.005, 0.01, 0.02)  # D-11: 항상 4행

# ── 지표 (BT-05) ──
Z_95 = 1.96                # D-12: 표준정규 양측 95% z값 (정규근사 CI)
MIN_SAMPLE = 30            # 이 미만의 거래수면 표본 부족 경고 (D-12)


def _apply_slip(price: float, slippage: float, side: str) -> float:
    """슬리피지를 체결가에 반영. 매수는 비싸게(+), 매도는 싸게(-).

    슬리피지는 체결가에만 반영된다 — 손익률에서 또 빼지 않는다 (이중 계산 금지).
    """
    return price * (1 + slippage) if side == "buy" else price * (1 - slippage)


def _net_pnl_pct(entry: float, exit_price: float) -> float:
    """진입가, 청산가(슬리피지 이미 반영됨) -> 왕복 수수료 차감 순손익률.

    수수료(ROUND_TRIP_FEE)는 손익률에서만 차감된다 — 체결가에 또 넣지 않는다.
    """
    gross = (exit_price - entry) / entry
    return gross - ROUND_TRIP_FEE


def load_events(db_path) -> list[dict]:
    """백테스트 대상 펌핑 이벤트 목록. pump_ticks 행이 있는 이벤트만, detected_at 순.

    읽기 전용 — SELECT 외 어떤 쿼리도 실행하지 않는다 (BT-01).
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT p.id, p.coin, p.base_price, p.pump_pct, p.detected_at,
                   COUNT(t.id) AS tick_count
            FROM pump_log p
            JOIN pump_ticks t ON t.pump_id = p.id
            GROUP BY p.id
            HAVING tick_count >= 1
            ORDER BY p.detected_at
            """
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


class DataSlice:
    """현재 커서까지의 틱만 노출. 미래 인덱스 접근 시 IndexError (D-14, BT-03).

    진입·청산 판정 함수는 원본 list[dict]이 아니라 이 객체만 받는다 —
    미래 데이터 접근을 물리적으로 차단한다.
    """
    def __init__(self, ticks: list[dict]):
        self._ticks = ticks      # 원본 (판정 로직에 직접 노출 안 함)
        self._cursor = 0         # 현재 재생 위치 (0-based, 포함)

    def advance(self) -> None:
        self._cursor += 1

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def current(self) -> dict:
        return self._ticks[self._cursor]

    def __len__(self) -> int:
        return self._cursor + 1

    def __getitem__(self, i: int) -> dict:
        idx = self._cursor + 1 + i if i < 0 else i
        if idx > self._cursor or idx < 0:
            raise IndexError(
                f"lookahead 위반: 인덱스 {i} -> 절대 {idx}, 커서는 {self._cursor}"
            )
        return self._ticks[idx]

    def visible(self) -> list[dict]:
        return self._ticks[: self._cursor + 1]


def _close(entry: dict, exit_tick: dict, slippage: float, reason: str) -> dict:
    """청산 시 Trade dict 생성. 청산 체결가는 exit_tick price에 매도 슬리피지 반영."""
    exit_price = _apply_slip(exit_tick["price"], slippage, "sell")
    return {
        "entry_ts":    entry["ts"],
        "entry_price": entry["price"],
        "exit_ts":     exit_tick["exchange_ts"],
        "exit_price":  exit_price,
        "exit_reason": reason,
        "hold_sec":    int(exit_tick["exchange_ts"] - entry["ts"]),
        "pnl_pct":     _net_pnl_pct(entry["price"], exit_price),
        "slippage":    slippage,
    }


def simulate_event(ticks: list[dict], slippage: float) -> dict | None:
    """한 펌핑 이벤트의 틱을 시간순 재생해 눌림목 진입·청산을 시뮬레이션 (BT-02).

    상태 머신 WAITING_ENTRY -> IN_POSITION. 진입은 주행 고점 대비 -ENTRY_DROP_PCT
    눌림 시 충족되며, 진입·청산 체결가 모두 "다음 틱"(cursor+1) price를 쓴다
    (D-04/D-07 lookahead 방지). 갭 틱(gap_before=1)은 TP/SL 판정에서 제외한다 (D-09).

    Return: 청산된 Trade dict, 진입 못 하거나 틱 부족 시 None.
    """
    if len(ticks) < MIN_TICKS:        # 데이터 부족 스킵 (Discretion)
        return None

    sl = DataSlice(ticks)
    running_peak = ticks[0]["price"]
    state = "WAITING_ENTRY"
    entry = None

    while sl.cursor < len(ticks):
        tick = sl.current
        running_peak = max(running_peak, tick["price"])   # D-02 동적 고점 (미래 아님)

        if state == "WAITING_ENTRY":
            drawdown = (tick["price"] - running_peak) / running_peak
            if drawdown <= -ENTRY_DROP_PCT:               # D-02 -7% 눌림
                # D-04: 다음 틱 체결. 마지막 틱에서 충족 시 다음 틱 없음 -> 무진입
                if sl.cursor + 1 >= len(ticks):
                    return None
                fill = ticks[sl.cursor + 1]["price"]
                entry = {
                    "price":    _apply_slip(fill, slippage, "buy"),
                    "tick_idx": sl.cursor + 1,
                    "ts":       ticks[sl.cursor + 1]["exchange_ts"],
                }
                state = "IN_POSITION"

        elif state == "IN_POSITION":
            # D-09: 갭 틱은 TP/SL 돌파 판정 건너뜀 (running_peak 갱신은 위에서 이미 수행)
            if tick["gap_before"] != 1:
                chg = (tick["price"] - entry["price"]) / entry["price"]
                hit = "익절" if chg >= TP_PCT else ("손절" if chg <= -SL_PCT else None)
                if hit and sl.cursor + 1 < len(ticks):    # D-07 다음 틱 체결
                    return _close(entry, ticks[sl.cursor + 1], slippage, hit)

        sl.advance()

    # D-06 시간초과: 틱 소진. IN_POSITION이면 마지막 틱 price로 강제 청산
    if state == "IN_POSITION":
        return _close(entry, ticks[-1], slippage, "시간초과")
    return None   # 진입 못 함


# ── 지표 계산 (BT-05) ─────────────────────────────────────────────────

def ev_ci(pnls: list[float]) -> tuple[float, float, float]:
    """거래별 손익률 리스트 -> (EV, CI 하한, CI 상한). 정규근사 (D-12)."""
    n = len(pnls)
    ev = statistics.mean(pnls)
    if n < 2:
        return ev, ev, ev      # 표본 1건이면 CI 정의 불가
    se = statistics.stdev(pnls) / (n ** 0.5)   # 표본표준편차(n-1) 사용
    return ev, ev - Z_95 * se, ev + Z_95 * se


def max_drawdown(pnls_ordered: list[float]) -> float:
    """시간순 정렬된 손익률 리스트 -> MDD (양수). 누적손익 단순합 모델."""
    equity = peak = mdd = 0.0
    for r in pnls_ordered:
        equity += r
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    return mdd


def compute_metrics(trades: list[dict]) -> dict:
    """청산 Trade dict 리스트 -> 승률·EV·95% CI·MDD·표본경고 지표 dict (BT-05).

    MDD는 거래 순서에 의존하므로 entry_ts 기준 정렬 후 누적손익을 쌓는다.
    """
    if not trades:
        return {"count": 0}
    ordered = sorted(trades, key=lambda t: t["entry_ts"])
    pnls = [t["pnl_pct"] for t in trades]
    pnls_ordered = [t["pnl_pct"] for t in ordered]
    wins = [t for t in trades if t["pnl_pct"] > 0]
    ev, ci_low, ci_high = ev_ci(pnls)
    count = len(trades)
    return {
        "count":          count,
        "win_count":      len(wins),
        "win_rate":       len(wins) / count,
        "ev":             ev,
        "ci_low":         ci_low,
        "ci_high":        ci_high,
        "mdd":            max_drawdown(pnls_ordered),
        "sample_warning": count < MIN_SAMPLE,
    }


def _self_test() -> bool:
    """DataSlice의 lookahead 차단 동작을 코드로 검증한다 (BT-03)."""
    ticks = [{"price": p} for p in range(10)]

    # 커서 0: len==1, current==ticks[0], [0] 정상
    sl = DataSlice(ticks)
    if len(sl) != 1:
        print(f"self-test FAIL: 커서 0에서 len()=={len(sl)} (기대 1)")
        return False
    if sl.current["price"] != 0:
        print(f"self-test FAIL: 커서 0에서 current price=={sl.current['price']} (기대 0)")
        return False
    if sl[0]["price"] != 0:
        print(f"self-test FAIL: 커서 0에서 [0] price=={sl[0]['price']} (기대 0)")
        return False

    # 커서 5까지 advance: len==6, [5] 정상
    for _ in range(5):
        sl.advance()
    if len(sl) != 6:
        print(f"self-test FAIL: 커서 5에서 len()=={len(sl)} (기대 6)")
        return False
    if sl[5]["price"] != 5:
        print(f"self-test FAIL: 커서 5에서 [5] price=={sl[5]['price']} (기대 5)")
        return False

    # [6] — 커서 초과 미래 인덱스는 IndexError
    try:
        sl[6]
        print("self-test FAIL: [6] (미래 인덱스)가 IndexError를 던지지 않음")
        return False
    except IndexError:
        pass

    # [10] — 먼 미래도 IndexError
    try:
        sl[10]
        print("self-test FAIL: [10] (먼 미래)가 IndexError를 던지지 않음")
        return False
    except IndexError:
        pass

    # [-1] — 현재 커서 틱 반환
    if sl[-1]["price"] != 5:
        print(f"self-test FAIL: [-1] price=={sl[-1]['price']} (기대 5)")
        return False

    print("self-test PASS")
    return True


# ── 백테스트 오케스트레이션 (BT-05) ───────────────────────────────────

def _gap_ratio(ticks: list[dict]) -> float:
    """이벤트 틱 중 갭 틱(gap_before=1) 비율. 빈 리스트면 0.0 (D-15)."""
    if not ticks:
        return 0.0
    return sum(t["gap_before"] for t in ticks) / len(ticks)


def _est_ratio(ticks: list[dict]) -> float:
    """이벤트 틱 중 추정 시각 틱(ts_estimated=1) 비율. 빈 리스트면 0.0 (D-16)."""
    if not ticks:
        return 0.0
    return sum(t["ts_estimated"] for t in ticks) / len(ticks)


def run_backtest(db_path) -> dict:
    """모든 펌핑 이벤트를 슬리피지 4시나리오로 시뮬레이션 (BT-05).

    D-15: 갭 비율이 GAP_EXCLUDE_PCT 초과인 이벤트는 통째 제외.
    제외 안 된 이벤트만 SLIPPAGE_SCENARIOS 각각으로 simulate_event 호출.

    Return dict: by_slippage(slippage -> list[trade]), total_events,
                 excluded, avg_est_ratio.
    """
    events = load_events(db_path)
    by_slippage: dict[float, list[dict]] = {s: [] for s in SLIPPAGE_SCENARIOS}
    excluded = 0
    est_ratios: list[float] = []

    for event in events:
        ticks = get_ticks(event["id"])
        if _gap_ratio(ticks) > GAP_EXCLUDE_PCT:    # D-15: 갭 오염 이벤트 제외
            excluded += 1
            continue
        est_ratios.append(_est_ratio(ticks))
        for slip in SLIPPAGE_SCENARIOS:
            trade = simulate_event(ticks, slip)
            if trade is None:                       # 무진입 이벤트는 거래 없음
                continue
            trade["pump_id"] = event["id"]
            trade["coin"] = event["coin"]
            by_slippage[slip].append(trade)

    avg_est_ratio = sum(est_ratios) / len(est_ratios) if est_ratios else 0.0
    return {
        "by_slippage":   by_slippage,
        "total_events":  len(events) - excluded,
        "excluded":      excluded,
        "avg_est_ratio": avg_est_ratio,
    }


def print_report(result: dict) -> None:
    """백테스트 결과를 stdout에 슬리피지 4행 비교 테이블로 출력 (D-10/D-11/D-16)."""
    print("빗썸 펌핑 눌림목 전략 백테스트")
    print("=" * 68)

    if result["total_events"] == 0 and result["excluded"] == 0:
        print("백테스트 대상 이벤트 없음 — 틱 데이터 축적 필요")
        return

    print(f"대상 이벤트: {result['total_events']}건  "
          f"(제외: {result['excluded']}건 — 갭 오염 임계 초과)")
    print(f"추정 틱 경고: 평균 ts_estimated 비율 {result['avg_est_ratio'] * 100:.1f}%")
    print(f"전략 상수: 진입 -{ENTRY_DROP_PCT * 100:.0f}%  "
          f"TP +{TP_PCT * 100:.0f}%  SL -{SL_PCT * 100:.0f}%  "
          f"시간초과 {TIMEOUT_SEC}초")
    print(f"수수료(왕복): {ROUND_TRIP_FEE * 100:.2f}%")
    print()
    print(f"{'슬리피지':>8s}  {'거래수':>6s}  {'승률':>6s}  {'EV':>8s}  "
          f"{'95% CI':>20s}  {'MDD':>6s}")
    print("-" * 68)

    warned = False
    for slip in SLIPPAGE_SCENARIOS:
        trades = result["by_slippage"][slip]
        m = compute_metrics(trades)
        if m["count"] == 0:
            print(f"{slip * 100:>7.1f}%  {'0':>6s}  {'-':>6s}  {'-':>8s}  "
                  f"{'-':>20s}  {'-':>6s}  (거래 없음)")
            continue
        ci = f"[{m['ci_low'] * 100:+.2f}%, {m['ci_high'] * 100:+.2f}%]"
        warn = "  ⚠표본부족" if m["sample_warning"] else ""
        if m["sample_warning"]:
            warned = True
        print(f"{slip * 100:>7.1f}%  {m['count']:>6d}  "
              f"{m['win_rate'] * 100:>5.1f}%  {m['ev'] * 100:>+7.2f}%  "
              f"{ci:>20s}  {m['mdd'] * 100:>5.1f}%{warn}")

    print("-" * 68)
    if warned:
        print(f"⚠ 표본 부족 (거래수 < {MIN_SAMPLE}) — EV/CI 결론 신뢰 제한, "
              f"Phase 3 추가 검증 필요 (D-12)")


def write_csv(result: dict, path: str) -> int:
    """4개 슬리피지 시나리오의 거래 상세를 한 CSV 파일로 출력 (D-10).

    encoding=utf-8-sig — 한글 코인명·헤더 Excel 깨짐 방지.
    Return: 기록한 거래 행 수.
    """
    cols = ["pump_id", "coin", "entry_ts", "entry_price", "exit_ts",
            "exit_price", "exit_reason", "hold_sec", "pnl_pct", "slippage"]
    rows = [t for slip in SLIPPAGE_SCENARIOS for t in result["by_slippage"][slip]]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in rows:
            w.writerow({k: t.get(k) for k in cols})
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="빗썸 펌핑 눌림목 전략 백테스트 (읽기 전용)"
    )
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite DB 경로")
    parser.add_argument("--csv", default="data/backtest_trades.csv",
                        help="거래 상세 CSV 출력 경로")
    parser.add_argument(
        "--self-test", action="store_true", help="DataSlice lookahead 차단 자가 검증"
    )
    args = parser.parse_args()

    if args.self_test:
        return 0 if _self_test() else 1

    result = run_backtest(args.db)
    print_report(result)
    n = write_csv(result, args.csv)
    print(f"상세: {args.csv} ({n}행)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
