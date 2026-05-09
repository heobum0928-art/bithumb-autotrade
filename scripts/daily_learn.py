"""
Daily Learning Engine
  - Reads last 7 days of trades from DB
  - Analyzes win rate, PnL patterns, hold times
  - Auto-tunes config.yaml parameters
  - Saves tuned params to DB for tracking

Run manually: python scripts/daily_learn.py
Auto-run:     scheduler.py calls this at midnight
"""
import sys
import yaml
import logging
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bithumb.db import init_db, get_stats, get_trades, log_params

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/learn.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")

# ── tuning bounds ──────────────────────────────────────────────────────────────
BOUNDS = {
    "entry_delay_sec":  (10,  120),
    "min_volume_krw":   (0,   30_000_000),
    "take_profit_pct":  (0.03, 0.20),
    "stop_loss_pct":    (0.01, 0.06),
    "entry_ratio":      (0.10, 0.40),
}

MIN_SAMPLES = 3   # 최소 거래 수 미달 시 파라미터 변경 안 함


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def analyze(stats: dict, trades: list[dict]) -> dict:
    """Return dict of recommended adjustments."""
    adj = {}
    count = stats["count"]

    if count < MIN_SAMPLES:
        log.info(f"거래 수 부족 ({count}건 < {MIN_SAMPLES}건) -파라미터 유지")
        return adj

    win_rate   = stats["win_rate"]
    avg_hold   = stats["avg_hold_sec"]
    sl_count   = stats["sl_count"]
    tp_count   = stats["tp_count"]
    avg_pnl    = stats["avg_pnl"]

    log.info(
        f"분석 결과 | 거래={count}건 승률={win_rate*100:.1f}% "
        f"평균PnL={avg_pnl:+,.0f}원 평균보유={avg_hold:.0f}초 "
        f"손절={sl_count}건 익절={tp_count}건"
    )

    # ── 1. 승률 기반 진입 조건 조정 ───────────────────────────────────────────
    if win_rate < 0.35:
        # 진입이 너무 공격적 → 쿨다운 늘리고, 최소 거래량 높임
        adj["entry_delay_delta"] = +15
        adj["min_volume_delta"]  = +2_000_000
        log.info("승률 낮음 → 쿨다운 +15s, 최소거래량 +200만원")

    elif win_rate > 0.70:
        # 진입 조건 너무 보수적 → 약간 완화
        adj["entry_delay_delta"] = -5
        log.info("승률 높음 → 쿨다운 -5s (완화)")

    # ── 2. 평균 보유시간 기반 트레일링 조정 ──────────────────────────────────
    if sl_count > 0:
        sl_trades = [t for t in trades if "손절" in (t["exit_reason"] or "")]
        avg_sl_hold = sum(t["hold_seconds"] for t in sl_trades) / len(sl_trades)
        if avg_sl_hold < 60:
            # 손절이 너무 빨리 → 트레일링 폭 넓히기
            adj["stop_loss_delta"] = +0.005
            log.info(f"손절 평균보유 {avg_sl_hold:.0f}초 → 트레일폭 +0.5%")

    # ── 3. 평균 PnL 기반 진입 비율 조정 ─────────────────────────────────────
    if avg_pnl < -5000 and count >= 5:
        # 계속 손실 → 포지션 크기 줄이기
        adj["entry_ratio_delta"] = -0.05
        log.info("평균 손실 → 진입비율 -5%")
    elif avg_pnl > 10000 and win_rate > 0.55 and count >= 5:
        # 안정적 수익 → 포지션 크기 늘리기
        adj["entry_ratio_delta"] = +0.05
        log.info("안정적 수익 → 진입비율 +5%")

    # ── 4. 익절 최적화 ────────────────────────────────────────────────────────
    if tp_count > 0:
        tp_trades = [t for t in trades if "익절" in (t["exit_reason"] or "")]
        avg_tp_hold = sum(t["hold_seconds"] for t in tp_trades) / len(tp_trades)
        if avg_tp_hold < 120 and win_rate > 0.6:
            # 빠르게 TP 도달 → TP 목표 올려볼 여지
            adj["take_profit_delta"] = +0.01
            log.info(f"익절 빠름 ({avg_tp_hold:.0f}초) → 익절목표 +1%")

    return adj


def apply_adjustments(cfg: dict, adj: dict) -> dict:
    t = cfg["trading"]
    m = cfg["monitor"]

    if "entry_delay_delta" in adj:
        cur = m.get("entry_delay_sec", 30)
        new = clamp(cur + adj["entry_delay_delta"], *BOUNDS["entry_delay_sec"])
        log.info(f"  entry_delay_sec: {cur} → {new}")
        m["entry_delay_sec"] = new

    if "min_volume_delta" in adj:
        cur = m.get("min_volume_krw", 5_000_000)
        new = clamp(cur + adj["min_volume_delta"], *BOUNDS["min_volume_krw"])
        log.info(f"  min_volume_krw: {cur:,.0f} → {new:,.0f}")
        m["min_volume_krw"] = new

    if "stop_loss_delta" in adj:
        cur = abs(t.get("stop_loss_pct", 0.03))
        new = clamp(cur + adj["stop_loss_delta"], *BOUNDS["stop_loss_pct"])
        log.info(f"  stop_loss_pct: -{cur*100:.1f}% → -{new*100:.1f}%")
        t["stop_loss_pct"] = -new

    if "take_profit_delta" in adj:
        cur = t.get("take_profit_pct", 0.07)
        new = clamp(cur + adj["take_profit_delta"], *BOUNDS["take_profit_pct"])
        log.info(f"  take_profit_pct: {cur*100:.1f}% → {new*100:.1f}%")
        t["take_profit_pct"] = new

    if "entry_ratio_delta" in adj:
        cur = t.get("entry_ratio", 0.25)
        new = clamp(cur + adj["entry_ratio_delta"], *BOUNDS["entry_ratio"])
        log.info(f"  entry_ratio: {cur*100:.0f}% → {new*100:.0f}%")
        t["entry_ratio"] = round(new, 2)

    cfg["trading"] = t
    cfg["monitor"] = m
    return cfg


def print_report(stats: dict, trades: list[dict]) -> None:
    log.info("=" * 55)
    log.info(f"  일별 학습 리포트 ({date.today()})")
    log.info("=" * 55)
    if not stats.get("count"):
        log.info("  최근 7일 거래 없음")
        return

    log.info(f"  총 거래:    {stats['count']}건")
    log.info(f"  승률:       {stats['win_rate']*100:.1f}%  (익절 {stats['tp_count']} / 손절 {stats['sl_count']})")
    log.info(f"  총 PnL:     {stats['total_pnl']:+,.0f}원")
    log.info(f"  평균 PnL:   {stats['avg_pnl']:+,.0f}원")
    log.info(f"  평균 수익:  {stats['avg_win_pnl']:+,.0f}원")
    log.info(f"  평균 손실:  {stats['avg_loss_pnl']:+,.0f}원")
    log.info(f"  평균 보유:  {stats['avg_hold_sec']:.0f}초")

    # 코인별 성과
    from collections import defaultdict
    by_coin = defaultdict(list)
    for t in trades:
        by_coin[t["coin"]].append(t["pnl_krw"])
    log.info("  --- 코인별 ---")
    for coin, pnls in sorted(by_coin.items(), key=lambda x: sum(x[1]), reverse=True):
        log.info(f"    {coin}: {len(pnls)}건  합계={sum(pnls):+,.0f}원")
    log.info("=" * 55)


def run():
    init_db()
    log.info(f"=== 일별 학습 엔진 실행 ({date.today()}) ===")

    stats = get_stats(days=7)
    trades = get_trades(days=7)

    print_report(stats, trades)

    adj = analyze(stats, trades)

    if adj:
        cfg = load_config()
        cfg = apply_adjustments(cfg, adj)
        save_config(cfg)
        log.info("config.yaml 업데이트 완료")

        # DB에 적용된 파라미터 기록
        t = cfg["trading"]
        m = cfg["monitor"]
        log_params({
            "entry_delay":  m.get("entry_delay_sec", 30),
            "min_volume":   m.get("min_volume_krw", 5_000_000),
            "take_profit":  t.get("take_profit_pct", 0.07),
            "stop_loss":    abs(t.get("stop_loss_pct", 0.03)),
            "entry_ratio":  t.get("entry_ratio", 0.25),
            "note":         str(adj),
        })
    else:
        log.info("파라미터 변경 없음")

    log.info("학습 완료")


if __name__ == "__main__":
    run()
