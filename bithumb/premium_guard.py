"""
프리미엄 스파이크 가드 — "신선한 김치프리미엄 = 붕괴 예고" 필터.

근거(2026-07-02 리서치 3차): 평소 프리미엄 없던(베이스라인 <5%) 코인이 빗썸 단독
프리미엄 >10%를 돌파하면 이후 +3h 음수율 100%(n=8), +6h 평균 -18.5%.
실증: IN 프리미엄 0→50%(7/1) 후 -72% 붕괴 — lead_ws 64회 진입 손실의 원인.
만성 프리미엄 코인(TAIKO류, 중앙값 33%)은 베이스라인 조건으로 자동 제외.

데이터: crossex_logger가 쌓는 data/crossex_events.csv (premium_up 컬럼).
이벤트 기반 로거라 데이터 없는 코인 = 프리미엄 정상으로 간주.

사용: from bithumb.premium_guard import premium_spiked_coins
      spiked = premium_spiked_coins()   # {"IN", ...} — 진입 금지 목록
캐시 5분 (파일 반복 파싱 방지).
"""
import csv, time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "crossex_events.csv"

SPIKE_PCT = 10.0      # 최근 프리미엄 이 이상 = 스파이크
BASELINE_PCT = 5.0    # 과거 중앙값 이 미만이어야 "신선한" 스파이크 (만성 제외)
RECENT_H = 6          # 스파이크 감시 창
BASELINE_D = 7        # 베이스라인 계산 창

_cache: tuple[float, set] = (0.0, set())


def premium_spiked_coins() -> set[str]:
    """신선 프리미엄 스파이크 상태인 코인 집합. 실패 시 빈 집합(fail-open)."""
    global _cache
    now = time.time()
    if now - _cache[0] < 300:
        return _cache[1]
    out = set()
    try:
        cutoff_recent = datetime.now() - timedelta(hours=RECENT_H)
        cutoff_base = datetime.now() - timedelta(days=BASELINE_D)
        recent: dict[str, list[float]] = {}
        history: dict[str, list[float]] = {}
        with open(CSV_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pu = row.get("premium_up", "")
                if not pu:
                    continue
                try:
                    ts = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
                    val = float(pu)
                except Exception:
                    continue
                if ts < cutoff_base:
                    continue
                coin = row["coin"]
                if ts >= cutoff_recent:
                    recent.setdefault(coin, []).append(val)
                else:
                    history.setdefault(coin, []).append(val)
        for coin, vals in recent.items():
            if max(vals) < SPIKE_PCT:
                continue
            hist = sorted(history.get(coin, []))
            baseline = hist[len(hist) // 2] if hist else 0.0   # 중앙값, 이력 없으면 0(신선)
            if baseline < BASELINE_PCT:
                out.add(coin)
    except Exception:
        out = set()
    _cache = (now, out)
    return out


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    print("현재 프리미엄 스파이크 코인:", premium_spiked_coins() or "(없음)")
