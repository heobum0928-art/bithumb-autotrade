"""
AI 분석기 — 수집된 거래/신호/pump_log 데이터를 Claude API로 분석.
매일 또는 수동 실행: python scripts/ai_analyze.py
"""
import sys
import sqlite3
import yaml
import json
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT    = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "trades.db"


def load_api_key() -> str:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    key = cfg.get("anthropic_api_key") or cfg.get("anthropic", {}).get("api_key", "")
    if not key:
        raise ValueError("config.yaml에 anthropic_api_key 없음")
    return key


def gather_data() -> dict:
    conn = sqlite3.connect(DB_PATH)

    # 최근 7일 거래
    since = (date.today() - timedelta(days=7)).isoformat()
    trades = conn.execute(
        "SELECT coin, entered_at, pnl_krw, pnl_pct, exit_reason, hold_seconds, max_pnl_pct "
        "FROM trades WHERE date >= ? ORDER BY entered_at",
        (since,),
    ).fetchall()

    # 전체 거래 통계
    all_trades = conn.execute(
        "SELECT COUNT(*), SUM(pnl_krw), "
        "SUM(CASE WHEN pnl_krw>0 THEN 1 ELSE 0 END) FROM trades"
    ).fetchone()

    # 차단 신호 outcome (최근 7일)
    outcomes = conn.execute(
        """SELECT skip_reason,
           COUNT(*) as cnt,
           AVG(outcome_5m) as avg5m,
           AVG(outcome_30m) as avg30m,
           SUM(CASE WHEN outcome_5m > 0.5 THEN 1 ELSE 0 END) as up_cnt
           FROM signal_log
           WHERE skip_reason IS NOT NULL AND outcome_5m IS NOT NULL
           AND date(entered_at) >= ?
           GROUP BY substr(skip_reason,1,10)
           ORDER BY cnt DESC LIMIT 8""",
        (since,),
    ).fetchall()

    # pump_log 요약
    pump_summary = conn.execute(
        """SELECT
           COUNT(*) as total,
           AVG(pump_pct) as avg_pump,
           AVG(vol_mult) as avg_vol,
           SUM(pullback_2pct) as pullback_cnt,
           SUM(bounce_after) as bounce_cnt,
           AVG(CASE WHEN price_5m IS NOT NULL AND base_price > 0
               THEN (price_5m - base_price) / base_price * 100 END) as avg_5m_return
           FROM pump_log"""
    ).fetchone()

    # 시간대별 성과
    hour_stats = conn.execute(
        """SELECT strftime('%H', entered_at) as h,
           COUNT(*) as cnt,
           SUM(CASE WHEN pnl_krw>0 THEN 1 ELSE 0 END) as wins,
           SUM(pnl_krw) as pnl
           FROM trades GROUP BY h ORDER BY pnl DESC"""
    ).fetchall()

    conn.close()

    return {
        "trades_7d": [
            {"coin": t[0], "time": t[1][11:16], "pnl": round(t[2] or 0),
             "pnl_pct": round(t[3] or 0, 2), "reason": t[4],
             "hold_sec": t[5], "max_pnl_pct": round(t[6] or 0, 1)}
            for t in trades
        ],
        "overall": {
            "total": all_trades[0],
            "total_pnl": round(all_trades[1] or 0),
            "wins": all_trades[2],
            "win_rate": round(all_trades[2] / max(all_trades[0], 1) * 100, 1),
        },
        "filter_outcomes": [
            {"filter": o[0], "count": o[1],
             "avg_5m": round(o[2] or 0, 2),
             "avg_30m": round(o[3] or 0, 2),
             "up_ratio": round((o[4] or 0) / max(o[1], 1) * 100)}
            for o in outcomes
        ],
        "pump_log": {
            "total": pump_summary[0],
            "avg_pump_pct": round(pump_summary[1] or 0, 2),
            "avg_vol_mult": round(pump_summary[2] or 0, 1),
            "pullback_2pct_cnt": pump_summary[3],
            "bounce_cnt": pump_summary[4],
            "pullback_rate": round((pump_summary[3] or 0) / max(pump_summary[0], 1) * 100),
            "bounce_rate": round((pump_summary[4] or 0) / max(pump_summary[3] or 1, 1) * 100),
            "avg_5m_return": round(pump_summary[5] or 0, 2),
        },
        "hour_stats": [
            {"hour": h[0], "count": h[1], "wins": h[2],
             "win_rate": round(h[2] / max(h[1], 1) * 100),
             "pnl": round(h[3] or 0)}
            for h in hour_stats
        ],
        "current_params": {
            "entry_krw": 50000,
            "stop_pct": -1.5,
            "tp_trail": 1.5,
            "rsi_range": "45~90",
            "bb_limit": 1.3,
            "vol_ceiling": 15,
            "confirm_delay_sec": 30,
        }
    }


def analyze(data: dict) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=load_api_key())

    prompt = f"""당신은 알트코인 단타 자동매매 퀀트 전문가입니다.
아래는 빗썸 거래소에서 운영 중인 자동매매 봇의 실제 데이터입니다.
데이터를 분석하고 실용적인 인사이트와 개선 방향을 제시해주세요.

## 전체 성과
{json.dumps(data['overall'], ensure_ascii=False, indent=2)}

## 현재 파라미터
{json.dumps(data['current_params'], ensure_ascii=False, indent=2)}

## 최근 7일 거래 내역
{json.dumps(data['trades_7d'], ensure_ascii=False, indent=2)}

## 필터별 차단 신호 결과 (차단 후 실제 가격 변화)
{json.dumps(data['filter_outcomes'], ensure_ascii=False, indent=2)}

## 펌핑 이벤트 분석 (pump_log)
{json.dumps(data['pump_log'], ensure_ascii=False, indent=2)}

## 시간대별 성과
{json.dumps(data['hour_stats'], ensure_ascii=False, indent=2)}

---

다음 항목을 분석해주세요:

1. **필터 품질 평가**: 각 필터가 올바른 신호를 차단하고 있는지
2. **펌핑 패턴 인사이트**: pump_log 데이터에서 발견되는 패턴
3. **눌림목 전략 실현 가능성**: 현재 데이터 기준 눌림목 진입 가능성
4. **즉시 개선 가능한 것**: 파라미터 조정 제안 (수치 포함)
5. **주의할 리스크**: 현재 시스템의 위험 요소

한국어로 답변하고, 수치 근거를 포함해주세요."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


def main():
    print("데이터 수집 중...")
    data = gather_data()

    print(f"분석 요청 중... (거래 {data['overall']['total']}건, pump_log {data['pump_log']['total']}건)")
    result = analyze(data)

    print("\n" + "="*60)
    print("AI 분석 결과")
    print("="*60)
    print(result)

    # 결과 저장
    out = ROOT / "docs" / f"ai_analysis_{date.today().isoformat()}.md"
    out.write_text(f"# AI 분석 {date.today().isoformat()}\n\n{result}", encoding="utf-8")
    print(f"\n→ 저장: {out.name}")


if __name__ == "__main__":
    main()
