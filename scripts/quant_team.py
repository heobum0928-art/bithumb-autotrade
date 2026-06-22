"""
퀀트팀 데일리 브리핑 — 로컬 데이터를 4역할 렌즈로 매일 점검해 한 장으로 보고.

배경(2026-06-22): "에이전트 퀀트팀으로 지속 관리" 합의. 단 클라우드 에이전트는 로컬 봇/
데이터에 접근 불가 → 팀의 로컬 임무(헬스·PM/리스크)는 이 스크립트가, 리서처는 매일 9시
클라우드 루틴이, 레드팀은 후보 발생 시 소집이 담당. 이 파일 = 팀의 '데일리 스탠드업' 출력.

4역할:
  ① 헬스(Data/Ops)  : 봇 생존·일봉 신선도·micro_events 증가·데이터 갭
  ② PM/리스크        : 코어 트리거 거리·ML 게이트·EM/하이브리드 상태·자본 노출
  ③ 리서처(포인터)   : 최신 재학습 성능 + 매일 9시 클라우드 리서치 루틴 참조
  ④ 레드팀(트리거)   : 게이트 근접 엔진 있으면 '소집 권고' 플래그

출력: stdout + logs/quant_team.log + docs/quant_team/YYYY-MM-DD.md + 텔레그램 요약.
실거래 무관·0원. Windows 작업 CoinbaseBot_QuantTeam 일일 등록(MLRetrain 이후).
Run: python scripts/quant_team.py
"""
import sys, os, json, csv, glob, re, statistics as st
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DAILY = ROOT / "data" / "candles_daily"
SUPERVISED = ["tg_bot", "claude_intelligence", "swing_monitor", "vb_trader", "retest_trader",
              "em_trader", "igniter_alert", "ml_trader", "core_trader", "hybrid_trader",
              "crossex_logger", "volume_radar", "accum_trader", "watchdog"]


def _read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _btc():
    cl = _read_json(DAILY / "BTC_1d.json", [])
    if not cl or len(cl) < 200:
        return None
    closes = [float(x["trade_price"]) for x in cl]
    last_date = cl[-1].get("candle_date_time_kst", "")[:10]
    cur = closes[-1]; s50 = sum(closes[-50:]) / 50; s200 = sum(closes[-200:]) / 200
    return {"cur": cur, "s50": s50, "s200": s200,
            "scout_trig": s50 * 1.01, "full_trig": s200 * 1.01, "last_date": last_date}


def role_health():
    lines = ["### ① 헬스 (Data/Ops)"]
    # 봇 생존
    alive = set()
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            if p.info["name"] and "python" in p.info["name"].lower() and p.info["cmdline"]:
                for a in p.info["cmdline"]:
                    if a.endswith(".py"):
                        alive.add(os.path.basename(a)[:-3])
    except Exception as e:
        lines.append(f"- ⚠️ psutil 불가: {e}")
    dead = [b for b in SUPERVISED if b not in alive]
    lines.append(f"- 봇: {len(SUPERVISED)-len(dead)}/{len(SUPERVISED)} 생존" +
                 (f" | 🔴 죽음: {', '.join(dead)}" if dead else " ✅"))
    # 일봉 신선도
    b = _btc()
    today = datetime.now(KST).date().isoformat()
    if b:
        fresh = "✅ 최신" if b["last_date"] >= today else f"⚠️ {b['last_date']}(지연)"
        lines.append(f"- BTC 일봉: 마지막 {b['last_date']} {fresh}")
    else:
        lines.append("- ⚠️ BTC 일봉 부족/없음")
    ncoins = len(glob.glob(str(DAILY / "*_1d.json")))
    lines.append(f"- 일봉 코인 수: {ncoins}개")
    # micro_events 증가
    mp = ROOT / "data" / "micro_events.csv"
    if mp.exists():
        n = sum(1 for _ in open(mp, encoding="utf-8", errors="replace")) - 1
        lines.append(f"- micro_events(호가캡처): {max(n,0)}건 누적")
    else:
        lines.append("- micro_events: 아직 0건 (점화 발생 시 적재)")
    ip = ROOT / "data" / "igniter_events.csv"
    if ip.exists():
        n = sum(1 for _ in open(ip, encoding="utf-8", errors="replace")) - 1
        lines.append(f"- igniter_events(점화감지): {max(n,0)}건 누적")
    # 교차거래소 로거 신선도(하트비트) + 누적
    cs = _read_json(ROOT / "data" / "crossex_state.json")
    cx = ROOT / "data" / "crossex_events.csv"
    nx = (sum(1 for _ in open(cx, encoding="utf-8", errors="replace")) - 1) if cx.exists() else 0
    if cs:
        try:
            last = datetime.fromisoformat(cs["last_cycle"]); age = (datetime.now(KST) - last).total_seconds() / 60
            fresh = "✅" if age < 10 else f"⚠️ {age:.0f}분 지연"
            lines.append(f"- 교차거래소 로거: {fresh} (사이클 {cs.get('cycles','?')}) | crossex_events {max(nx,0)}건")
        except Exception:
            lines.append(f"- 교차거래소 로거: 하트비트 파싱오류 | crossex_events {max(nx,0)}건")
    else:
        lines.append("- 교차거래소 로거: ⚠️ 하트비트 없음(미가동?)")
    # 거래대금 레이더 신선도 + 오늘 급증 상위
    vr = _read_json(ROOT / "data" / "volume_radar_state.json")
    vx = ROOT / "data" / "volume_radar_events.csv"
    nv = (sum(1 for _ in open(vx, encoding="utf-8", errors="replace")) - 1) if vx.exists() else 0
    if vr:
        try:
            last = datetime.fromisoformat(vr["last_cycle"]); age = (datetime.now(KST) - last).total_seconds() / 60
            fresh = "✅" if age < 10 else f"⚠️ {age:.0f}분 지연"
            top = vr.get("top", [])[:5]
            tops = ", ".join(f"{x['coin']} {x['surge']:.0f}배({x['chg']:+.0f}%)" for x in top)
            lines.append(f"- 거래대금 레이더: {fresh} | 캡처 {max(nv,0)}건 | 급증 상위: {tops}")
        except Exception:
            lines.append(f"- 거래대금 레이더: 파싱오류 | 캡처 {max(nv,0)}건")
    else:
        lines.append("- 거래대금 레이더: ⚠️ 하트비트 없음(미가동?)")
    return "\n".join(lines), dead


def _ml_stats():
    pnls, p07 = [], []
    lp = ROOT / "logs" / "ml_trader.log"
    if not lp.exists():
        return None
    for line in open(lp, encoding="utf-8", errors="replace"):
        m = re.search(r"PnL=([+-][0-9.]+)%", line)
        if m and "청산" in line:
            v = float(m.group(1)); pnls.append(v)
            pm = re.search(r"P(\d+)", line)
            if pm and int(pm.group(1)) >= 70:
                p07.append(v)
    if not pnls:
        return None
    def t(x):
        return st.mean(x) / (st.pstdev(x) or 1e-9) * len(x) ** 0.5 if len(x) > 1 else 0
    return {"n": len(pnls), "avg": st.mean(pnls), "t": t(pnls), "n07": len(p07)}


def role_pm():
    lines = ["### ② PM / 리스크"]
    b = _btc()
    if b:
        gap = (b["full_trig"] / b["cur"] - 1) * 100
        regime = "강세(코어/하이브리드 가동)" if b["cur"] > b["full_trig"] else \
                 ("정찰(50선 위)" if b["cur"] > b["scout_trig"] else "약세=현금")
        lines.append(f"- 레짐: **{regime}** | BTC {b['cur']:,.0f}")
        lines.append(f"- 코어 실거래 트리거: 200선×1.01 = {b['full_trig']:,.0f} (BTC {gap:+.1f}% 필요)")
    ml = _ml_stats()
    if ml:
        lines.append(f"- ML#31: 모의 {ml['n']}건 {ml['avg']:+.2f}%/t{ml['t']:+.2f} | "
                     f"P≥0.7 {ml['n07']}건 (게이트=P≥0.7 CLEAN n≥30·t≥2.5)")
    em = _read_json(ROOT / "data" / "em_pos.json", [])
    if isinstance(em, list):
        lines.append(f"- EM 보유: {', '.join(x['coin'] for x in em) if em else '없음'}")
    hyb = _read_json(ROOT / "data" / "hybrid_state.json")
    if hyb:
        held = list(hyb.get("holdings", {}))
        lines.append(f"- 하이브리드: {hyb.get('state','?')} | 보유 {held if held else '현금'}")
    core = _read_json(ROOT / "data" / "core_state.json")
    if core:
        lines.append(f"- 코어: {core.get('state','?')}")
    try:
        from bithumb.live_guard import live_status
        ls = live_status()
        if ls["enabled"]:
            lines.append(f"- 🔴 **실전 ON** | arm: {ls['armed']} | 전체상한 {ls['global_cap']:,}원 | "
                         f"당일실현 {ls['realized_pnl_today']:+,.0f}원 | 노출 {ls['open_exposure']:,.0f}원")
        else:
            lines.append("- 실전 가드: **OFF(모의)** — 게이트 통과+사용자 승인+arm 전까지 불변")
    except Exception:
        lines.append("- 실거래: 전부 OFF(모의)")
    return "\n".join(lines), b, ml


def role_research():
    lines = ["### ③ 리서처 (포인터)"]
    hp = ROOT / "data" / "ml_model_history.csv"
    if hp.exists():
        rows = list(csv.DictReader(open(hp, encoding="utf-8", errors="replace")))
        if rows:
            r = rows[-1]
            lines.append(f"- 최신 재학습({r.get('date','?')}): 이벤트 {r.get('events','?')} | "
                         f"P≥0.7 OOS {r.get('oos_n','?')}건 {r.get('oos_avg%','?')}%/t{r.get('oos_t','?')} | "
                         f"순열 t{r.get('perm_t','?')}")
    lines.append("- 새 가설 백테스트: 매일 09:00 클라우드 리서치 루틴 담당(전략대장 자동등록)")
    return "\n".join(lines)


def role_redteam(b, ml):
    lines = ["### ④ 레드팀 (소집 트리거)"]
    flags = []
    if ml and ml["n07"] >= 25:
        flags.append(f"ML P≥0.7 {ml['n07']}건 — 게이트(30) 근접 → 표본 30 도달 시 레드팀 소집")
    if b:
        dist = (b["scout_trig"] / b["cur"] - 1) * 100   # 정찰트리거까지 남은 %
        if b["cur"] > b["scout_trig"]:
            flags.append("🔴 BTC 정찰트리거 돌파 — 즉시 arm 검토(live_config enabled=true → 코어 실전)")
        elif dist <= 3:
            flags.append(f"BTC 정찰트리거 {dist:+.1f}% 근접 — arm 결정 준비(현재 장전됨·enabled=false)")
    if not flags:
        lines.append("- 소집 트리거 없음(게이트 근접 엔진 없음). 약세 지속 시 정상.")
    else:
        for f in flags:
            lines.append(f"- 🔔 {f}")
    return "\n".join(lines)


def main():
    Path("logs").mkdir(exist_ok=True)
    now = datetime.now(KST)
    health, dead = role_health()
    pm, b, ml = role_pm()
    research = role_research()
    redteam = role_redteam(b, ml)
    report = (f"# 🧠 퀀트팀 데일리 브리핑 — {now:%Y-%m-%d %H:%M} KST\n\n"
              f"{health}\n\n{pm}\n\n{research}\n\n{redteam}\n")
    print(report, flush=True)

    with open(ROOT / "logs" / "quant_team.log", "a", encoding="utf-8") as f:
        f.write(report + "\n" + "=" * 60 + "\n")
    outdir = ROOT / "docs" / "quant_team"; outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{now:%Y-%m-%d}.md").write_text(report, encoding="utf-8")

    # 텔레그램 요약 — 쉬운 말로 (전문용어 X)
    try:
        notify.send(build_plain_tg(now, dead, b, ml))
    except Exception as e:
        print(f"(텔레그램 실패: {e})", flush=True)


def build_plain_tg(now, dead, b, ml):
    """누가 봐도 아는 쉬운 말 요약."""
    L = [f"🤖 봇 현황 ({now:%m/%d %H:%M})", ""]
    # 1) 봇 생존
    if dead:
        L.append(f"🔴 봇 {len(dead)}개 멈춤: {', '.join(dead)} (자동복구 시도중)")
    else:
        L.append("✅ 봇 13개 모두 정상")
    # 2) 지금 뭐하고 있나 (현금/보유)
    if b:
        if b["cur"] > b["full_trig"]:
            L.append("💰 지금: 비트+알트 보유 중 (강세장)")
        elif b["cur"] > b["scout_trig"]:
            L.append("💰 지금: 일부 매수 (전환 초입)")
        else:
            L.append("💰 지금: 현금 보유")
            L.append("   (비트 약세 → 안 사는 게 정답)")
    # 3) 실전 매수 준비 상태
    try:
        from bithumb.live_guard import live_status
        from bithumb.live_guard import load_config
        ls = live_status()
        if ls.get("enabled"):
            L.append(f"🔴 실전 ON — 진짜 돈 거래 중 (오늘 {ls.get('realized_pnl_today',0):+,.0f}원)")
        elif "core" in ls.get("armed", []) and b:
            cap = load_config().get("engine_caps_krw", {}).get("core", 0)
            gap = (b["scout_trig"] / b["cur"] - 1) * 100
            L.append(f"📈 실전 매수 준비됨: {cap:,}원 장전 (안전장치 ON)")
            L.append(f"   비트 +{gap:.0f}% 오르면 → 승인 후 자동 매수")
    except Exception:
        pass
    # 4) 단타 실험 (쉬운 말)
    if ml:
        good = ml["avg"] > 0
        L.append(f"🧪 단타 실험(가짜돈): {ml['n']}번 중 평균 {ml['avg']:+.1f}%")
        L.append("   " + ("✅ 수익 중" if good else "아직 손해 — 약세장이라 예상대로, 돈 안 씀"))
    # 5) 한 줄 결론
    L.append("")
    if dead:
        L.append("⚠️ 봇 점검 필요!")
    elif b and b["cur"] > b["scout_trig"]:
        L.append("👉 시장이 살아남 — 실전 진입 검토 시점!")
    else:
        L.append("👉 잘 굴러가는 중. 시장 오를 때까지 안전하게 대기.")
    return "\n".join(L)


if __name__ == "__main__":
    main()
