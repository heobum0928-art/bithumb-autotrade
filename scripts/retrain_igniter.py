"""[재학습 파이프라인] ML 점화 모델을 새 데이터로 매일 재학습 + 성능 추적.
1) _rebuild_cache.py — 오늘자 5분봉 수집 (학습 재료 갱신)
2) _train_igniter_model.py — 갱신 데이터로 모델 재학습 → data/igniter_model.pkl
3) 워크포워드 검증(P>=0.7 OOS t) + 순열검정 → 진짜 학습됐나
4) data/ml_model_history.csv 에 날짜별 성능 기록 → "나아지는지" 데이터로 확인
5) 텔레그램 1줄 요약
Windows 작업 스케줄러 일일 등록 (collect_daily 이후). 실거래 무관, 0원.
"""
import sys, subprocess, csv, glob, statistics as st
from datetime import datetime
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
HIST = ROOT / "data" / "ml_model_history.csv"


def run(script, timeout):
    try:
        r = subprocess.run([PY, str(ROOT/"scripts"/script)], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode == 0, (r.stdout or "")[-200:]
    except Exception as e:
        return False, str(e)


def main():
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== ML 재학습 {today} ===", flush=True)

    ok1, o1 = run("_rebuild_cache.py", 600)
    print(f"[1] 5분봉 수집: {'OK' if ok1 else '실패'} {o1.strip()[-80:]}", flush=True)
    ok2, o2 = run("_train_igniter_model.py", 300)
    print(f"[2] 재학습: {'OK' if ok2 else '실패'} {o2.strip()[-80:]}", flush=True)

    # 3) 검증 — 재학습 검증 모듈 함수 재사용
    sys.path.insert(0, str(ROOT/"scripts"))
    import importlib
    V = importlib.import_module("_igniter_ml_validate")
    coins = sorted(set(Path(f).name.split("_5m")[0] for f in glob.glob(str(ROOT/"data"/"candles_cache"/"*_5m_90d_*.json"))) - {"BTC"})
    rows = V.build(coins, V.btc_absmove_map())
    real = V.walkforward(rows)
    sh = V.walkforward(rows, shuffle=True)
    rm, rt, rn = V.tstat([x*100 for x in real])
    sm, stt, sn = V.tstat([x*100 for x in sh])
    base_m = (sum(r[2] for r in rows)/len(rows)*100) if rows else 0
    print(f"[3] 검증: 이벤트{len(rows)} | P>=0.7 OOS {rn}건 {rm:+.2f}%/t{rt:+.2f} | 순열 {sm:+.2f}%/t{stt:+.2f} | 베이스{base_m:+.2f}%", flush=True)

    # 4) 이력 기록
    new = not HIST.exists()
    with open(HIST, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(["date","events","baseline%","oos_n","oos_avg%","oos_t","perm_avg%","perm_t"])
        w.writerow([today, len(rows), f"{base_m:.2f}", rn, f"{rm:.2f}", f"{rt:.2f}", f"{sm:.2f}", f"{stt:.2f}"])

    verdict = "진짜 학습(실제>>순열)" if (rm>0 and rm>sm+0.3) else "신호 약함/우연 가능"
    msg = (f"🤖 ML 재학습 {today}\n이벤트 {len(rows)} | P≥0.7 OOS {rn}건 {rm:+.2f}%/t{rt:+.2f}\n"
           f"순열(가짜) {sm:+.2f}%/t{stt:+.2f} | 베이스 {base_m:+.2f}%\n→ {verdict}")
    print(msg, flush=True)
    try:
        from bithumb import notify; notify.send(msg)
    except Exception as e:
        print(f"(텔레그램 실패: {e})", flush=True)


if __name__ == "__main__":
    main()
