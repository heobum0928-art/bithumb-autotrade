"""빗썸 펌핑 눌림목 전략 검증 — train/test OOS 분할 + 그리드 서치 + GO/NO-GO 판정."""

import argparse
import csv as _csv
import itertools
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))   # backtest.py same directory
from bithumb.db import DB_PATH, get_ticks
from backtest import (load_events, simulate_event, compute_metrics,
                      Strategy, _gap_ratio, GAP_EXCLUDE_PCT, MIN_SAMPLE)

TRAIN_RATIO = 0.70


def filter_clean_events(events: list[dict], db_path) -> list[dict]:
    """갭 오염 이벤트를 분할 전에 일괄 제외 (train/test 일관성 보장)."""
    clean = []
    for ev in events:
        ticks = get_ticks(ev["id"])
        if _gap_ratio(ticks) <= GAP_EXCLUDE_PCT:
            clean.append(ev)
    return clean


class EventSplit:
    """detected_at 시간순 train/test 분할 + test 셋 물리 봉인."""

    def __init__(self, events: list[dict], train_ratio: float = TRAIN_RATIO):
        cut = int(len(events) * train_ratio)
        self._train = events[:cut]
        self._test = events[cut:]
        self._test_unlocked = False

    @property
    def train(self) -> list[dict]:
        return self._train

    def unlock_test(self) -> list[dict]:
        self._test_unlocked = True
        return self._test

    @property
    def test(self) -> list[dict]:
        if not self._test_unlocked:
            raise RuntimeError(
                "OOS 위반: 그리드 서치 단계에서 test 셋 접근 시도. "
                "unlock_test()는 최종 검증 직전 1회만 호출돼야 한다 (D-08/D-11)."
            )
        return self._test


ENTRY_GRID    = (0.05, 0.07, 0.10)          # 진입 눌림 -5 / -7 / -10%
TP_GRID       = (0.03, 0.05, 0.07, 0.10)    # 익절 +3 / +5 / +7 / +10%
SL_GRID       = (0.02, 0.03, 0.05)          # 손절 -2 / -3 / -5%
GRID_SLIPPAGE = 0.01                         # D-02: 그리드도 슬리피지 1% 기준선
# 총 3 × 4 × 3 = 36 조합


def grid_search(train_events: list[dict], db_path, slippage: float = GRID_SLIPPAGE) -> list[dict]:
    """train 셋 전용 그리드 서치. D-11: 이 함수에는 split.train만 전달할 것.

    Returns list of 36 result rows sorted by EV desc, win_rate desc, MDD asc.
    """
    results = []
    for entry, tp, sl in itertools.product(ENTRY_GRID, TP_GRID, SL_GRID):
        strat = Strategy(entry_drop_pct=entry, tp_pct=tp, sl_pct=sl)
        trades = []
        for ev in train_events:
            ticks = get_ticks(ev["id"])
            t = simulate_event(ticks, slippage, strat)
            if t is not None:
                t["coin"] = ev["coin"]
                trades.append(t)
        m = compute_metrics(trades)
        results.append({
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "event_count": len(train_events),
            **m,
        })
    results.sort(key=lambda r: (
        -r.get("ev", -1e9),
        -r.get("win_rate", 0.0),
        r.get("mdd", 1e9),
    ))
    return results


# ── 시간대/코인 분해 + 과적합 경고 ───────────────────────────────────────

TIME_BLOCKS = {0: "새벽 0-6", 1: "오전 6-12", 2: "오후 12-18", 3: "저녁 18-24"}

SINGLE_COIN_DOMINANCE = 0.40  # 한 코인이 test 거래수의 40% 초과 시 경고


def time_block(detected_at: str) -> str:
    """KST detected_at ISO 문자열 -> 6시간 블록 라벨."""
    hour = datetime.fromisoformat(detected_at).hour
    return TIME_BLOCKS[hour // 6]


def decompose_by_time_block(trades: list[dict]) -> dict:
    """거래를 KST 6시간 블록 4구간으로 분해해 각각 compute_metrics 산출."""
    groups: dict[str, list] = {label: [] for label in TIME_BLOCKS.values()}
    for t in trades:
        label = time_block(t["detected_at"])
        groups[label].append(t)
    return {label: compute_metrics(groups[label]) for label in TIME_BLOCKS.values()}


def decompose_by_coin(trades: list[dict]) -> dict:
    """거래를 코인별로 분해 + 단일 코인 지배율 경고."""
    groups: dict[str, list] = {}
    for t in trades:
        groups.setdefault(t["coin"], []).append(t)
    rows = {coin: compute_metrics(ts) for coin, ts in groups.items()}
    top_coin = max(groups, key=lambda c: len(groups[c])) if groups else ""
    dominance = len(groups.get(top_coin, [])) / len(trades) if trades else 0.0
    warning = dominance > SINGLE_COIN_DOMINANCE
    return {"rows": rows, "warning": warning, "top_coin": top_coin, "dominance": dominance}


def overfit_warning(train_ev: float, test_ev: float) -> bool:
    """과적합 의심: test EV 부호가 다르거나 train EV의 절반 미만."""
    if train_ev >= 0 and test_ev < 0:
        return True
    if train_ev < 0 and test_ev >= 0:
        return False  # test가 더 좋으면 경고 불필요
    return test_ev < train_ev * 0.5


# ── GO/NO-GO 판정 + 검증 오케스트레이션 ─────────────────────────────────

def decide_go_nogo(test_trades: list[dict]) -> dict:
    """test 셋 거래로 GO/NO-GO 판정. 표본 게이트 먼저, CI 하한 게이트 후."""
    m = compute_metrics(test_trades)
    # 표본 게이트 (Pitfall 3: CI보다 먼저)
    if m.get("count", 0) < MIN_SAMPLE:
        return {
            "verdict": "NO-GO",
            "reason": f"표본 부족 (거래수 {m.get('count', 0)} < {MIN_SAMPLE})",
            "metrics": m,
        }
    # CI 하한 게이트 (D-01)
    if m["ci_low"] <= 0:
        return {
            "verdict": "NO-GO",
            "reason": f"CI 하한 음수/0 ({m['ci_low'] * 100:+.2f}%) — EV 양수 확신 불가",
            "metrics": m,
        }
    return {
        "verdict": "GO",
        "reason": f"test 셋 CI 하한 양수 ({m['ci_low'] * 100:+.2f}%), 전략 유효",
        "metrics": m,
    }


def run_validation(db_path) -> dict:
    """검증 사이클 전체: 분할 → 그리드 서치 → test 단 1회 OOS → 판정."""
    events = load_events(db_path)
    clean = filter_clean_events(events, db_path)
    split = EventSplit(clean, TRAIN_RATIO)
    grid = grid_search(split.train, db_path, slippage=GRID_SLIPPAGE)
    best = grid[0]
    best_strat = Strategy(best["entry"], best["tp"], best["sl"])

    # test 셋 단 1회 unlock (D-08 — 코드 전체에서 이 한 곳만)
    test_events = split.unlock_test()

    # test 거래 산출 (슬리피지 1% 기준선, D-02)
    test_trades_1pct: list[dict] = []
    for ev in test_events:
        ticks = get_ticks(ev["id"])
        t = simulate_event(ticks, 0.01, best_strat)
        if t is not None:
            t["coin"] = ev["coin"]
            t["detected_at"] = ev["detected_at"]
            test_trades_1pct.append(t)

    # 슬리피지 4행 참고 테이블 (D-02)
    by_slippage: dict[float, list[dict]] = {}
    for slip in (0.0, 0.005, 0.01, 0.02):
        trades: list[dict] = []
        for ev in test_events:
            ticks = get_ticks(ev["id"])
            t = simulate_event(ticks, slip, best_strat)
            if t is not None:
                t2 = dict(t)
                t2["coin"] = ev["coin"]
                t2["detected_at"] = ev["detected_at"]
                trades.append(t2)
        by_slippage[slip] = trades

    verdict = decide_go_nogo(test_trades_1pct)

    return {
        "grid": grid,
        "best": best,
        "verdict": verdict,
        "test_trades": test_trades_1pct,
        "by_slippage": by_slippage,
        "train_event_count": len(split.train),
        "test_event_count": len(test_events),
    }


# ── 출력 + CSV 저장 ──────────────────────────────────────────────────────

def print_validation_report(result: dict) -> None:
    """검증 결과를 stdout에 출력 (그리드/슬리피지/코인별/시간대별/GO-NO-GO)."""
    train_n = result["train_event_count"]
    test_n  = result["test_event_count"]

    print("빗썸 펌핑 눌림목 전략 검증")
    print("=" * 72)

    if train_n == 0 and test_n == 0:
        print("검증 대상 이벤트 없음 — 틱 데이터 축적 필요 (2~3주)")
        return

    print(f"train 이벤트 {train_n}건 / test 이벤트 {test_n}건")
    print()

    # 1. 그리드 서치 결과 상위 10행
    print("── 그리드 서치 결과 (상위 10조합, train 기준) ──")
    print(f"{'진입%':>6s}  {'TP%':>5s}  {'SL%':>5s}  {'거래수':>6s}  {'승률':>6s}  {'EV':>8s}  {'MDD':>6s}")
    print("-" * 55)
    grid = result["grid"]
    for row in grid[:10]:
        if row.get("count", 0) == 0:
            print(f"{row['entry']*100:>5.0f}%  {row['tp']*100:>4.0f}%  {row['sl']*100:>4.0f}%  "
                  f"{'0':>6s}  {'-':>6s}  {'-':>8s}  {'-':>6s}")
        else:
            print(f"{row['entry']*100:>5.0f}%  {row['tp']*100:>4.0f}%  {row['sl']*100:>4.0f}%  "
                  f"{row['count']:>6d}  {row['win_rate']*100:>5.1f}%  "
                  f"{row['ev']*100:>+7.2f}%  {row['mdd']*100:>5.1f}%")
    print()

    # 2. train 최고 조합
    best = result["best"]
    print(f"── 최고 조합 (train): 진입 -{best['entry']*100:.0f}%  "
          f"TP +{best['tp']*100:.0f}%  SL -{best['sl']*100:.0f}%  "
          f"EV {best.get('ev', 0)*100:+.2f}%")
    print()

    # 3. 슬리피지 4행 참고 테이블 (test 셋, best 조합)
    print("── 슬리피지 참고 테이블 (test 셋, best 조합) ──")
    print(f"{'슬리피지':>8s}  {'거래수':>6s}  {'승률':>6s}  {'EV':>8s}  {'95% CI':>22s}  {'MDD':>6s}")
    print("-" * 72)
    for slip, trades in result["by_slippage"].items():
        m = compute_metrics(trades)
        if m.get("count", 0) == 0:
            print(f"{slip*100:>7.1f}%  {'0':>6s}  {'-':>6s}  {'-':>8s}  {'-':>22s}  {'-':>6s}")
        else:
            ci = f"[{m['ci_low']*100:+.2f}%, {m['ci_high']*100:+.2f}%]"
            warn = "  ⚠표본부족" if m.get("sample_warning") else ""
            print(f"{slip*100:>7.1f}%  {m['count']:>6d}  {m['win_rate']*100:>5.1f}%  "
                  f"{m['ev']*100:>+7.2f}%  {ci:>22s}  {m['mdd']*100:>5.1f}%{warn}")
    print()

    # 4. 코인별 분해
    coin_result = decompose_by_coin(result["test_trades"])
    print("── 코인별 분해 (test 셋) ──")
    if coin_result["warning"]:
        print(f"⚠ 단일 코인 지배: {coin_result['top_coin']} {coin_result['dominance']*100:.0f}%")
    print(f"{'코인':>8s}  {'거래수':>6s}  {'승률':>6s}  {'EV':>8s}")
    print("-" * 36)
    for coin, m in coin_result["rows"].items():
        if m.get("count", 0) == 0:
            print(f"{coin:>8s}  {'0':>6s}  {'-':>6s}  {'-':>8s}")
        else:
            print(f"{coin:>8s}  {m['count']:>6d}  {m['win_rate']*100:>5.1f}%  {m['ev']*100:>+7.2f}%")
    print()

    # 5. 시간대별 분해
    time_result = decompose_by_time_block(result["test_trades"])
    print("── 시간대별 분해 (test 셋, KST 6시간 블록) ──")
    print(f"{'시간대':>12s}  {'거래수':>6s}  {'승률':>6s}  {'EV':>8s}")
    print("-" * 38)
    for label, m in time_result.items():
        if m.get("count", 0) == 0:
            print(f"{label:>12s}  {'0':>6s}  {'-':>6s}  {'-':>8s}")
        else:
            print(f"{label:>12s}  {m['count']:>6d}  {m['win_rate']*100:>5.1f}%  {m['ev']*100:>+7.2f}%")
    print()

    # 6. 과적합 경고
    train_ev = best.get("ev", 0.0) or 0.0
    test_m = result["verdict"]["metrics"]
    test_ev = test_m.get("ev", 0.0) or 0.0
    if overfit_warning(train_ev, test_ev):
        print(f"⚠ 과적합 의심: train EV {train_ev*100:+.2f}% vs test EV {test_ev*100:+.2f}%")
        print()

    # 7. 최종 판정
    verdict = result["verdict"]
    print("=" * 72)
    if verdict["verdict"] == "GO":
        print(f"GO: {verdict['reason']}")
    else:
        print(f"NO-GO: {verdict['reason']}")
    print("=" * 72)


def write_grid_csv(grid: list[dict], path: str) -> int:
    """그리드 서치 결과를 CSV로 저장 (data/validate_grid.csv)."""
    cols = ["entry", "tp", "sl", "event_count", "count", "win_rate", "ev",
            "ci_low", "ci_high", "mdd"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in grid:
            w.writerow({k: row.get(k) for k in cols})
    return len(grid)


def write_test_csv(test_trades: list[dict], path: str) -> int:
    """test 셋 거래 상세를 CSV로 저장 (data/validate_test_trades.csv)."""
    cols = ["coin", "detected_at", "entry_ts", "entry_price", "exit_ts",
            "exit_price", "exit_reason", "hold_sec", "pnl_pct", "slippage"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in test_trades:
            w.writerow({k: t.get(k) for k in cols})
    return len(test_trades)


# ── 자가검증 ─────────────────────────────────────────────────────────────

def _self_test() -> int:
    """자가검증: 합성 데이터로 EventSplit·grid_search·decide_go_nogo·분해 동작 확인."""
    # 1. EventSplit: 70/30 분할 + test 봉인
    events_10 = [{"id": i} for i in range(10)]
    sp = EventSplit(events_10, 0.7)
    assert len(sp.train) == 7, f"train size {len(sp.train)}"
    assert len(sp.train) + len(sp._test) == 10
    try:
        _ = sp.test
        assert False, "test 봉인 실패"
    except RuntimeError:
        pass
    unlocked = sp.unlock_test()
    assert len(unlocked) == 3
    assert len(sp.test) == 3

    # 2. grid_search: 빈 train으로도 36행 반환, EV 내림차순
    rows = grid_search([], DB_PATH)
    assert len(rows) == 36, f"grid rows {len(rows)}"
    evs = [r.get("ev", -1e9) for r in rows]
    for i in range(len(evs) - 1):
        assert evs[i] >= evs[i + 1], f"EV sort broken at {i}"

    # 3. decide_go_nogo: 표본 부족 NO-GO
    few = [{"pnl_pct": 0.05, "entry_ts": i} for i in range(5)]
    v = decide_go_nogo(few)
    assert v["verdict"] == "NO-GO" and "표본" in v["reason"], v

    # 4. decide_go_nogo: CI 음수 NO-GO
    neg = [{"pnl_pct": (0.01 if i % 2 == 0 else -0.05), "entry_ts": i} for i in range(40)]
    v2 = decide_go_nogo(neg)
    assert v2["verdict"] == "NO-GO" and "CI" in v2["reason"], v2

    # 5. decide_go_nogo: 양수 GO
    pos = [{"pnl_pct": 0.04, "entry_ts": i} for i in range(40)]
    v3 = decide_go_nogo(pos)
    assert v3["verdict"] == "GO", v3

    # 6. time_block 경계값
    assert time_block("2026-05-16T03:00:00") == "새벽 0-6"
    assert time_block("2026-05-16T16:29:31.454970") == "오후 12-18"
    assert time_block("2026-05-16T20:00:00") == "저녁 18-24"

    # 7. decompose_by_coin 지배율
    tr = [
        {"coin": "A", "pnl_pct": 0.05, "entry_ts": 1},
        {"coin": "A", "pnl_pct": 0.03, "entry_ts": 2},
        {"coin": "A", "pnl_pct": 0.01, "entry_ts": 3},
        {"coin": "B", "pnl_pct": -0.02, "entry_ts": 4},
    ]
    d = decompose_by_coin(tr)
    assert d["warning"] is True and d["top_coin"] == "A", d

    # 8. overfit_warning
    assert overfit_warning(0.08, -0.02) is True
    assert overfit_warning(0.06, 0.05) is False

    print("self-test PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="전략 검증 스크립트")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--grid-csv", default="data/validate_grid.csv")
    parser.add_argument("--test-csv", default="data/validate_test_trades.csv")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    result = run_validation(args.db)
    print_validation_report(result)
    write_grid_csv(result["grid"], args.grid_csv)
    write_test_csv(result["test_trades"], args.test_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
