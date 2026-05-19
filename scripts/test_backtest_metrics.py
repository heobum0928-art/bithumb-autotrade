# -*- coding: utf-8 -*-
"""백테스트 지표 함수 회귀 테스트 — ev_ci / max_drawdown / compute_metrics (BT-05)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backtest import ev_ci, max_drawdown, compute_metrics


def test_ev_ci_basic():
    ev, lo, hi = ev_ci([0.05, -0.03, 0.02])
    assert abs(ev - 0.04 / 3) < 1e-9, ev
    assert lo < ev < hi, (lo, ev, hi)


def test_ev_ci_single_sample():
    ev, lo, hi = ev_ci([0.05])
    assert ev == lo == hi == 0.05, (ev, lo, hi)


def test_max_drawdown():
    assert abs(max_drawdown([0.05, 0.02, -0.03]) - 0.03) < 1e-9
    assert max_drawdown([0.05, 0.02, 0.01]) == 0.0          # 낙폭 없음
    assert abs(max_drawdown([-0.02, -0.03, 0.04]) - 0.05) < 1e-9


def test_compute_metrics_empty():
    m = compute_metrics([])
    assert m["count"] == 0


def test_compute_metrics_three():
    t = lambda p, ts: {"pnl_pct": p, "entry_ts": ts}
    m = compute_metrics([t(0.05, 1), t(-0.03, 2), t(0.02, 3)])
    assert m["count"] == 3
    assert m["win_count"] == 2
    assert abs(m["win_rate"] - 2 / 3) < 1e-9, m
    assert m["ci_low"] < m["ev"] < m["ci_high"]
    assert m["mdd"] >= 0.0
    assert m["sample_warning"] is True                      # count < 30


def test_compute_metrics_single_sample_warning():
    m = compute_metrics([{"pnl_pct": 0.05, "entry_ts": 1}])
    assert m["count"] == 1
    assert m["sample_warning"] is True
    assert m["ci_low"] == m["ev"] == m["ci_high"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e!r}")
    print(f"--- {len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
