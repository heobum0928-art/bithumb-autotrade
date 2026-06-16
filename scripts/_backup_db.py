"""[유틸] 거래 DB + 상태파일 일자별 백업. 디스크 사고 대비 off-folder 사본.
- trades.db는 sqlite backup API로 일관성 있는 스냅샷(쓰는 중에도 안전).
- 상태 json(포지션/쿨다운 등)도 함께 복사.
- backups/ 에 날짜별 보관, N일 지난 건 자동 삭제.
실거래 무관 — 데이터 보존만. 매일 1회 스케줄러로 실행 권장."""
import sys, shutil, sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BACKUP = ROOT / "backups"
KEEP_DAYS = 30          # 최근 N일은 매일 스냅샷 전부 보관
KEEP_MONTHLY = True     # N일 넘으면 매월 1일 스냅샷만 영구 보관(나머지 정리)
STATE_FILES = ["retest_pos.json", "active_pos.json", "loss_coins.json",
               "retest_state.json"]


def backup_db(stamp: str) -> bool:
    src = DATA / "trades.db"
    if not src.exists():
        print(f"[DB] {src} 없음 — 건너뜀"); return False
    dst_dir = BACKUP / "db"; dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"trades_{stamp}.db"
    # sqlite backup API: 쓰는 중에도 일관된 스냅샷
    src_con = sqlite3.connect(str(src))
    dst_con = sqlite3.connect(str(dst))
    with dst_con:
        src_con.backup(dst_con)
    src_con.close(); dst_con.close()
    print(f"[DB] {dst.name} ({dst.stat().st_size:,} bytes)")
    return True


def backup_state(stamp: str) -> int:
    dst_dir = BACKUP / "state" / stamp
    n = 0
    for name in STATE_FILES:
        src = DATA / name
        if src.exists():
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst_dir / name)
            n += 1
    if n:
        print(f"[상태] {n}개 파일 → state/{stamp}/")
    return n


def _expired(d: date, cutoff: date) -> bool:
    """삭제 대상? 최근 KEEP_DAYS 안이면 보존. 넘으면 매월 1일만 영구 보존."""
    if d >= cutoff:
        return False
    if KEEP_MONTHLY and d.day == 1:
        return False
    return True


def prune():
    cutoff = date.today() - timedelta(days=KEEP_DAYS)
    removed = 0
    # db 스냅샷
    for f in (BACKUP / "db").glob("trades_*.db"):
        try:
            d = datetime.strptime(f.stem.replace("trades_", ""), "%Y%m%d").date()
            if _expired(d, cutoff):
                f.unlink(); removed += 1
        except Exception:
            pass
    # 상태 폴더
    sdir = BACKUP / "state"
    if sdir.exists():
        for f in sdir.iterdir():
            try:
                d = datetime.strptime(f.name, "%Y%m%d").date()
                if _expired(d, cutoff):
                    shutil.rmtree(f); removed += 1
            except Exception:
                pass
    if removed:
        print(f"[정리] 일별 {KEEP_DAYS}일 초과 {removed}개 삭제 (매월 1일·최신은 보존)")


if __name__ == "__main__":
    stamp = date.today().strftime("%Y%m%d")
    print(f"=== 백업 {datetime.now():%Y-%m-%d %H:%M} (보관 {KEEP_DAYS}일) ===")
    backup_db(stamp)
    backup_state(stamp)
    prune()
    print("완료.")
