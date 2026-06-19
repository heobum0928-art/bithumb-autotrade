"""[일일 점검] 매일 자동 실행 — 전략 건강검진 + 장세 + 판정 한 장.
'어떤 전략이 좋은지' 매일 확인하고, 행동할 때(게이트 통과/강세 전환)를 포착.
- RT 2% fresh 게이트(실거래 후보) / RT 3%·VB 참고
- 장세(BTC 추세): RT는 추세장 전용일 가능성 → 강세 전환 감지
- 최근 24h 활동량
- 한 줄 판정 + 텔레그램 통보
로컬 실행(봇 DB 접근 필요). 윈도우 작업 스케줄러 일일 등록."""
import sys, sqlite3, statistics as st, urllib.request, json
from pathlib import Path
from datetime import datetime, timedelta
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "trades.db"
RT_2PCT_CUTOVER_ID = 1427   # 이 id 초과 RT 거래만 2% 표본


def _ticker(market):
    try:
        r = urllib.request.urlopen(f"https://api.bithumb.com/v1/ticker?markets={market}", timeout=5)
        return json.load(r)[0]
    except Exception:
        return None


def gate(con, where, label):
    rows = con.execute(
        f"SELECT pnl_pct, exit_reason FROM trades WHERE pnl_pct IS NOT NULL "
        f"AND exit_reason LIKE '[RT%' {where} ORDER BY id").fetchall()
    clean = [r for r in rows if not (("보정" in (r[1] or "")) or ("미수신" in (r[1] or "")))]
    p = [r[0] for r in clean]; n = len(p)
    if not n:
        return f"{label}: 0건", 0, 0.0
    avg = sum(p) / n; sd = st.pstdev(p) if n > 1 else 0; adj = avg - 0.16
    t = adj / (sd / n ** 0.5) if sd else 0
    wr = sum(1 for x in p if x > 0) / n * 100
    go = "GO" if (n >= 30 and t >= 2.0 and adj > 0) else "No-Go"
    return f"{label}: {n}/30 승률{wr:.0f}% 비용후{adj:+.2f}% t{t:.2f} {go}", n, t


def btc_regime():
    """BTC 일봉 8개로 추세 판정: 현재가 vs 7일 단순평균."""
    try:
        r = urllib.request.urlopen(
            "https://api.bithumb.com/v1/candles/days?market=KRW-BTC&count=8", timeout=5)
        d = json.load(r)
        cur = d[0]["trade_price"]
        ma7 = sum(x["trade_price"] for x in d[1:8]) / 7
        chg = (cur - ma7) / ma7 * 100
        if chg > 1.5:
            return f"강세(현재 7일평균 +{chg:.1f}%)", "BULL"
        if chg < -1.5:
            return f"약세(현재 7일평균 {chg:.1f}%)", "BEAR"
        return f"횡보(7일평균 대비 {chg:+.1f}%)", "FLAT"
    except Exception:
        return "장세 조회실패", "?"


def btc_core_signal():
    """코어 엔진: BTC 200일선 타이밍. (신호문구, state, 트리거여부) 반환.
    유지 중인 일봉 파일(750일)로 200일 SMA 계산 — API 200 상한 회피."""
    f = ROOT / "data" / "candles_daily" / "BTC_1d.json"
    try:
        closes = [float(x["trade_price"]) for x in json.loads(f.read_text(encoding="utf-8"))]
        if len(closes) < 201:
            return None, "?", False
        cur = closes[-1]; sma = sum(closes[-200:]) / 200
        dist = (cur / sma - 1) * 100
        if cur >= sma:
            return f"코어: 보유(BULL) — BTC {cur:,.0f} ≥ 200일선 {sma:,.0f} ({dist:+.1f}%)", "BULL", True
        near = " ⚠️트리거 근접" if dist > -5 else ""
        return f"코어: 현금(BEAR) — BTC {cur:,.0f} < 200일선 {sma:,.0f} ({dist:+.1f}%){near}", "BEAR", False
    except Exception:
        return None, "?", False


def main():
    con = sqlite3.connect(str(DB))
    rt2, n2, t2 = gate(con, f"AND id>{RT_2PCT_CUTOVER_ID}", "RT 2%")
    rt3, _, _ = gate(con, "", "RT 전체(참고)")
    # 최근 24h RT 거래
    since = (datetime.now() - timedelta(hours=24)).isoformat()
    r24 = con.execute(
        "SELECT COUNT(*) FROM trades WHERE exit_reason LIKE '[RT%' AND exited_at > ?",
        (since,)).fetchone()[0]
    con.close()

    regime_txt, regime = btc_regime()
    btc = _ticker("KRW-BTC")
    btc_px = f"{btc['trade_price']:,.0f}원" if btc else "?"

    core_txt, core_state, core_trigger = btc_core_signal()

    # 한 줄 판정 (코어 트리거 최우선)
    if core_trigger:
        verdict = "★★ 코어 매수 트리거! BTC 200일선 회복=강세 전환 → BTC 코어 매수 + RT 위성 부활 검토 (사용자 승인)"
    elif n2 >= 30 and t2 >= 2.0:
        verdict = "★ RT 2% 게이트 통과! → 실거래 검토 (사용자 승인 필요)"
    elif regime == "BULL" and n2 < 30:
        verdict = "강세 전환 — RT 신호 재개 예상, 표본 빨리 쌓일 구간 (주목)"
    elif regime == "BEAR":
        verdict = "약세 — 코어=현금 보존, 위성 EM(약세장)만 검증. 단타 엣지 추격 안 함."
    else:
        verdict = "횡보 — 코어 현금 유지, EM 검증 진행"

    lines = [
        f"📊 일일 전략점검 {datetime.now():%Y-%m-%d %H:%M}",
        f"BTC {btc_px} | 장세: {regime_txt}",
    ]
    if core_txt:
        lines.append(core_txt)
    lines += [
        f"{rt2}",
        f"{rt3}",
        f"최근24h RT거래: {r24}건",
        f"→ {verdict}",
    ]
    msg = "\n".join(lines)
    print(msg)
    # 로그 누적
    log = ROOT / "logs" / "daily_strategy.log"
    log.parent.mkdir(exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(msg + "\n\n")
    # 텔레그램 통보
    try:
        from bithumb import notify
        notify.send(msg)
    except Exception as e:
        print(f"(텔레그램 통보 실패: {e})")


if __name__ == "__main__":
    main()
