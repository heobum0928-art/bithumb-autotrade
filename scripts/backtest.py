"""빗썸 펌핑 눌림목 전략 백테스트 — 틱 재생 기반 오프라인 시뮬레이션 (읽기 전용)."""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from bithumb.db import DB_PATH

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="빗썸 펌핑 눌림목 전략 백테스트 (읽기 전용)"
    )
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite DB 경로")
    parser.add_argument(
        "--self-test", action="store_true", help="DataSlice lookahead 차단 자가 검증"
    )
    args = parser.parse_args()

    if args.self_test:
        ok = _self_test()
        sys.exit(0 if ok else 1)

    print("백테스트 시뮬레이션 루프는 Plan 02·03에서 구현 예정.")
    sys.exit(0)
