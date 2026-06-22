"""
센서→단타 판정 — 호가/거래대금/교차거래소 센서가 단타 방향(이어짐 vs 던짐)을 가르나 데이터로 판정.

배경(2026-06-22 합의): "단타 무한축적 말고 판정일을 박자." 6/25 첫점검·6/29 1차판정.
센서가 잡은 이벤트(고거래대금·점화 순간 호가)에 forward 수익을 붙여,
depth_imb(호가깊이)·buy_ratio(매수체결비)가 그 코인의 직후 방향을 예측하는지 검정.

핵심 질문: "매집신호(depth>0.1 & 매수비>0.55)" 코인이 "던짐신호" 코인보다 실제로 더 오르나?
  → 분리되면 센서가 함정(BICO식 던짐)을 걸러 단타 승률을 올릴 수 있음.

입력: data/volume_radar_events.csv, data/micro_events.csv (센서 캡처)
forward: data/candles_cache/COIN_5m_90d_*.json (최신, +1h/+4h 수익)
출력: stdout + docs/sensor_eval/YYYY-MM-DD.md + 텔레그램.
Windows 작업 CoinbaseBot_SensorEval 6/25부터 매일 09:00. 실거래 무관·0원.
Run: python scripts/sensor_eval.py
"""
import sys, csv, glob, json, os, statistics as st
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "candles_cache"
FWD_1H, FWD_4H = 12, 48   # 5분봉 기준 1시간/4시간


def _cache(coin):
    """코인 최신 5m 캐시 → [(kst_str, close)] 시간오름차순. 없으면 None."""
    fs = glob.glob(str(CACHE / f"{coin}_5m_90d_*.json"))
    if not fs: return None
    f = sorted(fs)[-1]   # 날짜접미사 최신
    try:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        return [(x["candle_date_time_kst"], float(x["trade_price"])) for x in d]
    except Exception:
        return None


def _fwd(series, t_str):
    """이벤트 시각 t_str(>=) 첫 봉 기준 (+1h, +4h) 수익%. 부족하면 None."""
    # series는 kst 문자열 오름차순; t_str = 'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DDTHH:MM'
    key = t_str.replace(" ", "T")[:16]
    idx = None
    for i, (ts, _) in enumerate(series):
        if ts[:16] >= key:
            idx = i; break
    if idx is None or idx + FWD_4H >= len(series): return None
    p0 = series[idx][1]
    if p0 <= 0: return None
    return ((series[idx + FWD_1H][1] / p0 - 1) * 100, (series[idx + FWD_4H][1] / p0 - 1) * 100)


def load_events():
    """센서 이벤트 통합: (coin, time, depth_imb, buy_ratio, surge, chg)."""
    rows = []
    vp = ROOT / "data" / "volume_radar_events.csv"
    if vp.exists():
        for r in csv.DictReader(open(vp, encoding="utf-8", errors="replace")):
            try:
                rows.append((r["coin"], r["time"], float(r["depth_imb"]), float(r["buy_ratio"]),
                             float(r.get("surge", 0) or 0), float(r.get("chg_24h", 0) or 0)))
            except Exception:
                pass
    mp = ROOT / "data" / "micro_events.csv"
    if mp.exists():
        for r in csv.DictReader(open(mp, encoding="utf-8", errors="replace")):
            try:
                rows.append((r["coin"], r["time"] + ":00", float(r["depth_imb"]), float(r["buy_ratio"]), 0.0, 0.0))
            except Exception:
                pass
    return rows


def main():
    now = datetime.now(KST)
    events = load_events()
    labeled = []   # (depth, buy, surge, chg, fwd1, fwd4)
    for coin, t, depth, buy, surge, chg in events:
        s = _cache(coin)
        if not s: continue
        fw = _fwd(s, t)
        if fw is None: continue
        labeled.append((depth, buy, surge, chg, fw[0], fw[1]))

    L = ["# 📈 센서→단타 판정 — " + now.strftime("%Y-%m-%d %H:%M") + " KST\n"]
    L.append(f"- 센서 이벤트 {len(events)}건 중 forward 라벨 가능 **{len(labeled)}건** (캐시가 이벤트 시각 이후까지 있어야 라벨됨)\n")

    if len(labeled) < 30:
        L.append(f"## 판정: 표본부족 (라벨 {len(labeled)} < 30)\n캐시가 따라잡으면 라벨 증가. 데이터 더 축적 필요.")
    else:
        def bucket(name, cond):
            xs = [r[4] for r in labeled if cond(r)]   # fwd1h
            if not xs: return name, 0, 0, 0
            m = st.mean(xs); t = (m / (st.pstdev(xs) or 1e-9) * len(xs) ** 0.5) if len(xs) > 1 else 0
            return name, len(xs), m, t
        accum = bucket("매집신호(깊이>0.1&매수비>0.55)", lambda r: r[0] > 0.1 and r[1] > 0.55)
        dump = bucket("던짐신호(깊이<-0.1 또는 24h<-3%)", lambda r: r[0] < -0.1 or r[3] < -3)
        neut = bucket("중립", lambda r: not (r[0] > 0.1 and r[1] > 0.55) and not (r[0] < -0.1 or r[3] < -3))
        L.append("## 1시간 forward 수익 — 신호 버킷별")
        L.append("| 버킷 | n | 평균 fwd1h | t |")
        L.append("|---|---|---|---|")
        for nm, n, m, t in (accum, dump, neut):
            L.append(f"| {nm} | {n} | {m:+.2f}% | {t:+.2f} |")
        # 분리도
        sep = accum[2] - dump[2]
        # 상관
        bs = [(r[1], r[4]) for r in labeled]   # buy_ratio vs fwd1h
        try:
            import statistics
            corr = statistics.correlation([x for x, _ in bs], [y for _, y in bs]) if len(bs) > 2 else 0
        except Exception:
            corr = 0
        L.append(f"\n- 매집−던짐 분리도: **{sep:+.2f}%p** | 매수비↔fwd1h 상관: {corr:+.2f}")
        if sep > 0.5:
            verdict = "✅ **센서가 방향 가름 (YES)** — 매집신호가 던짐보다 유의하게 나음. microstructure를 ML 피처로 주입 진행."
        elif sep > 0.1:
            verdict = "🟡 약한 신호 — 방향성은 있으나 약함. 더 축적 후 재판정."
        else:
            verdict = "🔴 신호 없음 — 센서가 방향 못 가름. 단타 가망 낮음, 코어 집중 검토."
        L.append(f"\n## 판정: {verdict}")

    report = "\n".join(L)
    print(report, flush=True)
    outdir = ROOT / "docs" / "sensor_eval"; outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{now:%Y-%m-%d}.md").write_text(report, encoding="utf-8")
    try:
        from bithumb import notify
        head = report.split("## 판정:")[-1].strip()[:300] if "## 판정:" in report else ""
        # 텔레그램 HTML 파싱 깨짐 방지(<,> 치환)
        msg = f"📈 센서→단타 판정 {now:%m-%d}\n라벨 {len(labeled)}건\n{head}"
        msg = msg.replace("<", "‹").replace(">", "›")
        notify.send(msg)
    except Exception as e:
        print(f"(텔레그램 실패: {e})", flush=True)


if __name__ == "__main__":
    main()
