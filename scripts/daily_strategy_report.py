"""[일일 점검] 매일 자동 실행 — 전략 건강검진 + 장세 + 판정 한 장.
'어떤 전략이 좋은지' 매일 확인하고, 행동할 때(게이트 통과/강세 전환)를 포착.
- RT 2% fresh 게이트(실거래 후보) / RT 3%·VB 참고
- 장세(BTC 추세): RT는 추세장 전용일 가능성 → 강세 전환 감지
- 최근 24h 활동량
- 한 줄 판정 + 텔레그램 통보
로컬 실행(봇 DB 접근 필요). 윈도우 작업 스케줄러 일일 등록."""
import sys, sqlite3, statistics as st, urllib.request, json, re
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


def cascade_status():
    """cascade 모의 실측 게이트 추적 — 로그 파싱(DB 미기록).
    현재 파라미터(가장 최근 '시작' 라인) 이후 청산만 fresh 표본으로 집계.
    게이트: n≥30 AND 비용0.30%후 평균>0 AND t≥2.0 → 실거래 검토(승인필요).
    반환: (요약문, n, t, GO여부)."""
    log = ROOT / "logs" / "cascade_trader.log"
    if not log.exists():
        return "캐스케이드: 로그없음", 0, 0.0, False
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return "캐스케이드: 로그읽기실패", 0, 0.0, False
    # 가장 최근 파라미터 시작 시각 + 파라미터 문구
    cutover = None; param = ""
    for ln in lines:
        if "캐스케이드-반등 시작" in ln:
            cutover = ln[:19]
            m = re.search(r"(드롭[-\d.]+%\+거래량[\d.]+배)", ln)
            param = m.group(1) if m else ""
    pnls = []; started = cutover is None
    for ln in lines:
        ts = ln[:19]
        if cutover and ts >= cutover:
            started = True
        if not started:
            continue
        if "청산" in ln:
            m = re.search(r"PnL=([+-][\d.]+)%", ln)
            if m:
                pnls.append(float(m.group(1)))
    n = len(pnls)
    if n == 0:
        return f"캐스케이드({param}): 0/30 — fresh 표본 대기", 0, 0.0, False
    avg = sum(pnls) / n
    sd = st.pstdev(pnls) if n > 1 else 0
    adj = avg - 0.30                       # 왕복 비용 0.30%(슬리피지 스트레스)
    t = adj / (sd / n ** 0.5) if sd else 0
    wr = sum(1 for x in pnls if x > 0) / n * 100
    go = (n >= 30 and t >= 2.0 and adj > 0)
    tag = "GO" if go else "No-Go"
    return (f"캐스케이드({param}): {n}/30 승률{wr:.0f}% 비용후{adj:+.2f}% t{t:.2f} {tag}",
            n, t, go)


def futures_data_status():
    """선물 외부신호(futures_logger) 축적 진행도 — cascade 하이브리드 필터용."""
    csv_f = ROOT / "data" / "futures_signals.csv"
    state = ROOT / "data" / "futures_state.json"
    if not csv_f.exists():
        return "선물신호: 수집전"
    try:
        rows = sum(1 for _ in open(csv_f, encoding="utf-8")) - 1
    except Exception:
        rows = 0
    last = ""
    try:
        s = json.loads(state.read_text(encoding="utf-8"))
        last = s.get("last_cycle", "")[:16]
    except Exception:
        pass
    # 상관분석 착수 가능선: 대략 cascade 신호와 겹치려면 며칠치(행 2000+) 필요
    ready = "✅상관분석 가능" if rows >= 2000 else f"축적중(상관분석 ~2000행 목표)"
    return f"선물신호: {rows}행 {ready} (최근 {last})"


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
        trig = sma * 1.01   # 1% 확인밴드 (에이전트 검증: 밴드 없으면 휘프소로 엣지 절반 손실)
        if cur >= trig:
            return f"코어: 보유(BULL) — BTC {cur:,.0f} ≥ 200일선+1% {trig:,.0f} ({dist:+.1f}%)", "BULL", True
        near = " ⚠️트리거 근접(확인밴드 대기)" if dist > -5 else ""
        return f"코어: 현금(BEAR) — BTC {cur:,.0f} < 200일선 {sma:,.0f} (트리거 {trig:,.0f}, {dist:+.1f}%){near}", "BEAR", False
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
    casc_txt, casc_n, casc_t, casc_go = cascade_status()
    fut_txt = futures_data_status()

    # 한 줄 판정 (실거래 전환 신호 최우선 — 코어 > cascade > RT)
    if core_trigger:
        verdict = "★★ 코어 매수 트리거! BTC 200일선 회복=강세 전환 → BTC 코어 매수 + RT 위성 부활 검토 (사용자 승인)"
    elif casc_go:
        verdict = f"★ 캐스케이드 게이트 통과! ({casc_n}건 t{casc_t:.2f}) → 소액 실거래 검토 (사용자 승인 필요)"
    elif n2 >= 30 and t2 >= 2.0:
        verdict = "★ RT 2% 게이트 통과! → 실거래 검토 (사용자 승인 필요)"
    elif casc_n >= 30 and not casc_go:
        verdict = f"캐스케이드 30건 도달했으나 미달(t{casc_t:.2f}) — 슬리피지/엣지 재검토 필요"
    elif regime == "BEAR":
        verdict = f"약세 — 코어=현금. 단타는 캐스케이드 모의 표본({casc_n}/30) 축적 + 선물신호 하이브리드 검증 중."
    else:
        verdict = f"횡보/강세 — 코어 신호 대기. 캐스케이드 {casc_n}/30 축적 중."

    lines = [
        f"📊 일일 전략점검 {datetime.now():%Y-%m-%d %H:%M}",
        f"BTC {btc_px} | 장세: {regime_txt}",
    ]
    if core_txt:
        lines.append(core_txt)
    lines += [
        f"{casc_txt}",
        f"{fut_txt}",
        f"{rt2}",
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
    # 텔레그램 통보 (부등호는 HTML 파서가 태그로 오인 → 안전치환)
    try:
        from bithumb import notify
        notify.send(msg.replace("<", "‹").replace(">", "›"))
    except Exception as e:
        print(f"(텔레그램 통보 실패: {e})")


if __name__ == "__main__":
    main()
