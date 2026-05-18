"""pump_ticks log_tick / get_ticks 검증용 일회성 테스트 스크립트.

behavior Test 1~3 (01-01-PLAN.md)을 검증한다. 임시 pump_id=99999 사용 후 정리한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.db import init_db, log_tick, get_ticks, _conn

TEST_PID = 99999


def _cleanup():
    with _conn() as con:
        con.execute("DELETE FROM pump_ticks WHERE pump_id = ?", (TEST_PID,))


def main():
    init_db()
    _cleanup()  # 이전 잔여 행 제거
    try:
        # Test 1: exchange_ts 미지정 → recv_ts 복사 + ts_estimated=1
        log_tick(pump_id=TEST_PID, seq=0, recv_ts=1000.0, price=100.0)
        ticks = get_ticks(TEST_PID)
        assert len(ticks) == 1, f"Test 1 len: {len(ticks)}"
        r0 = ticks[0]
        assert r0["exchange_ts"] == 1000.0, f"Test 1 exchange_ts: {r0['exchange_ts']}"
        assert r0["ts_estimated"] == 1, f"Test 1 ts_estimated: {r0['ts_estimated']}"
        print("Test 1 OK")

        # Test 2: 모든 인자 지정
        log_tick(pump_id=TEST_PID, seq=1, recv_ts=1010.0, price=101.0,
                 exchange_ts=1009.5, acc_value=5000.0, volume_power=120.0,
                 gap_before=True, ts_estimated=False)
        ticks = get_ticks(TEST_PID)
        assert len(ticks) == 2, f"Test 2 len: {len(ticks)}"
        r1 = ticks[1]
        assert r1["exchange_ts"] == 1009.5, f"Test 2 exchange_ts: {r1['exchange_ts']}"
        assert r1["ts_estimated"] == 0, f"Test 2 ts_estimated: {r1['ts_estimated']}"
        assert r1["gap_before"] == 1, f"Test 2 gap_before: {r1['gap_before']}"
        assert r1["acc_value"] == 5000.0, f"Test 2 acc_value: {r1['acc_value']}"
        assert r1["volume_power"] == 120.0, f"Test 2 volume_power: {r1['volume_power']}"
        print("Test 2 OK")

        # Test 3: seq 오름차순 정렬 + 존재하지 않는 pump_id
        seqs = [t["seq"] for t in ticks]
        assert seqs == [0, 1], f"Test 3 seq order: {seqs}"
        empty = get_ticks(888888)
        assert empty == [], f"Test 3 empty: {empty}"
        print("Test 3 OK")

        print("ALL TESTS OK")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
