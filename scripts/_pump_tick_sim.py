"""펌핑 틱 기록 경로 + start_pump_tracker 루프 wiring 검증 (일회성).

봇 프로세스·실제 펌핑·실제 WebSocket 없이 다음을 검증한다:
  Part A — log_tick / get_ticks 계약 + 갭 판정(gap_before) + exchange_ts/acc_value 폴백
  Part B — start_pump_tracker 루프 1사이클 wiring (queue_pump → log_tick → seq 채번)

실제 펌핑 이벤트는 즉시 발생하지 않으므로(01-RESEARCH Environment Availability),
INSERT 경로와 루프 배선을 stub PriceTracker로 직접 검증한다.

Run: python scripts/_pump_tick_sim.py   (성공 시 "OK" 출력, exit 0)
"""
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows 콘솔(cp949)에서 유니코드 출력이 깨지지 않도록 UTF-8 강제
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.db import init_db, log_pump, log_tick, get_ticks, _conn

GAP_THRESHOLD_SEC = 30   # alt_monitor.GAP_THRESHOLD_SEC 와 동일 값 — 갭 판정 재현용

_test_pump_ids: list[int] = []


def _cleanup() -> None:
    """검증에 사용한 모든 test_pump_id 의 틱·펌핑 행 삭제 — 실제 분석 데이터 오염 방지."""
    if not _test_pump_ids:
        return
    placeholders = ",".join("?" for _ in _test_pump_ids)
    with _conn() as con:
        con.execute(f"DELETE FROM pump_ticks WHERE pump_id IN ({placeholders})", _test_pump_ids)
        con.execute(f"DELETE FROM pump_log WHERE id IN ({placeholders})", _test_pump_ids)


def _new_test_pump() -> int:
    """테스트용 pump_log 행 생성 후 그 id 반환."""
    log_pump("TICKSIM", datetime.now(), 100.0, 5.0, 6.0)
    with _conn() as con:
        pid = con.execute("SELECT MAX(id) FROM pump_log").fetchone()[0]
    _test_pump_ids.append(pid)
    return pid


# ── Part A — log_tick / get_ticks 계약 + 갭 판정 직접 검증 ───────────────────
def part_a() -> None:
    pid = _new_test_pump()

    # 정상 간격 틱 3개 (seq 0,1,2; recv_ts 1000/1010/1020 — 10초 간격, 갭 아님)
    recv = [1000.0, 1010.0, 1020.0]
    last_recv = 0.0
    for seq, rt in enumerate(recv):
        gap = bool(last_recv) and (rt - last_recv) >= GAP_THRESHOLD_SEC
        # seq 0: exchange_ts 전달 + acc_value 전달, seq 1: exchange_ts None(폴백), seq 2: 전달
        ex_ts = rt + 0.5 if seq != 1 else None
        acc_v = 5000.0 if seq == 0 else None
        log_tick(pid, seq, recv_ts=rt, price=100.0 + seq,
                 exchange_ts=ex_ts, acc_value=acc_v, volume_power=120.0,
                 gap_before=gap)
        last_recv = rt

    # 갭 틱 1개 (seq 3; recv_ts 1060 → 직전 1020 과 40초 간격 >= 30 → gap_before=True)
    rt = 1060.0
    gap = bool(last_recv) and (rt - last_recv) >= GAP_THRESHOLD_SEC
    assert gap is True, "Part A: 40초 간격이 갭으로 판정돼야 한다"
    log_tick(pid, 3, recv_ts=rt, price=103.0,
             exchange_ts=rt + 0.5, acc_value=None, volume_power=120.0,
             gap_before=gap)

    ticks = get_ticks(pid)
    assert len(ticks) == 4, f"Part A: 틱 4개 기대, 실제 {len(ticks)}"
    assert [t["seq"] for t in ticks] == [0, 1, 2, 3], "Part A: seq 오름차순 정렬"
    assert ticks[3]["gap_before"] == 1, "Part A: seq=3 행 gap_before=1"
    assert ticks[0]["gap_before"] == 0, "Part A: seq=0 행은 갭 아님"
    # exchange_ts None 으로 넣은 seq=1 행은 ts_estimated=1, recv_ts 복사
    assert ticks[1]["ts_estimated"] == 1, "Part A: exchange_ts None 행은 ts_estimated=1"
    assert ticks[1]["exchange_ts"] == ticks[1]["recv_ts"], "Part A: 폴백 시 exchange_ts==recv_ts"
    assert ticks[0]["ts_estimated"] == 0, "Part A: exchange_ts 전달 행은 ts_estimated=0"
    # acc_value 전달한 seq=0 행 값 일치
    assert ticks[0]["acc_value"] == 5000.0, "Part A: seq=0 acc_value=5000.0"
    assert ticks[1]["acc_value"] is None, "Part A: acc_value 미전달 행은 NULL"
    print("Part A OK — log_tick/get_ticks 계약 + 갭 판정 + exchange_ts/acc_value 폴백")


# ── Part B — start_pump_tracker 루프 wiring 검증 ────────────────────────────
class StubPriceTracker:
    """start_pump_tracker 루프가 호출하는 getter 만 최소 구현한 stub."""
    EX_TS = 1700000000.0
    ACC_V = 5000.0

    def get_latest_price(self, coin: str) -> float:
        return 150.0

    def get_vol_power(self, coin: str) -> float:
        return 120.0

    def get_latest_exchange_ts(self, coin: str) -> float | None:
        return self.EX_TS

    def get_latest_acc_value(self, coin: str) -> float | None:
        return self.ACC_V


def part_b() -> None:
    # alt_monitor 는 모듈 레벨 부작용(_ensure_single_instance)이 있으나
    # 클린 환경에서는 pidfile 작성만 수행하므로 import 가능.
    from scripts import alt_monitor

    pid = _new_test_pump()

    stub = StubPriceTracker()
    alt_monitor.start_pump_tracker(stub)        # daemon 스레드로 루프 시작
    alt_monitor.queue_pump(pid, "TICKSIM", 100.0)

    # 큐에 들어간 item 이 15원소인지 간접 확인 (queue_pump 직후, 루프가 소비하기 전 가능성도 있어 별도 확인)
    # 루프 1사이클(sleep 10) 후 틱이 INSERT 됐는지 검증 — 약 12초 대기
    time.sleep(12)

    ticks = get_ticks(pid)
    assert len(ticks) >= 1, f"Part B: 루프 1사이클 후 틱 1개 이상 기대, 실제 {len(ticks)}"
    first = ticks[0]
    assert first["seq"] == 0, f"Part B: seq 0부터 채번 기대, 실제 {first['seq']}"
    assert first["acc_value"] == StubPriceTracker.ACC_V, \
        f"Part B: acc_value 가 stub 반환값({StubPriceTracker.ACC_V})과 일치해야 한다"
    assert first["exchange_ts"] == StubPriceTracker.EX_TS, \
        f"Part B: exchange_ts 가 stub 반환값({StubPriceTracker.EX_TS})과 일치해야 한다"
    assert first["ts_estimated"] == 0, "Part B: exchange_ts 가 stub 값이므로 ts_estimated=0"
    assert first["price"] == 150.0, "Part B: price 가 stub get_latest_price 값"
    print(f"Part B OK — start_pump_tracker 루프 wiring 검증 (틱 {len(ticks)}개 INSERT, "
          f"seq 채번/acc_value/exchange_ts 전달 확인)")


def main() -> None:
    init_db()
    try:
        part_a()
        part_b()
    finally:
        _cleanup()
    print("OK")


if __name__ == "__main__":
    main()
