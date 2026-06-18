"""매일 1전략 자동 리서치 — 가드레일 내장.

흐름: Anthropic API가 '안 겹치는 새 전략 스펙(JSON)' 1개 제안
  → 화이트리스트 검증(임의코드 실행 0, 미리 정의된 프리미티브 조합만 허용)
  → strategy_lab 엔진이 2년 일봉 walk-forward 백테스트(비용 0.16/0.30%)
  → 가드레일 판정 → 대장(docs/STRATEGY_RESEARCH.md) 기록 + 텔레그램 보고.

가드레일 (사용자 합의 2026-06-19):
  - 백테 양수 = '합격'이 아니라 '용의자 1명'. 절대 자동 실거래 적용 금지.
  - 다중검정 보정: 매일 여러 개 돌리므로 합격선 t≥2가 아니라 t≥3 + '왜 먹혀야 하는지' 경제적 이유 필수.
  - 후보로 살아남아도 RT와 동일한 dry-run 30건 사전등록 게이트 + 사용자 승인 거쳐야 실거래.
  - 이 루프의 가치 = '먹히는 걸 찾아 적용'이 아니라 '없다를 정직하게 증명 + 진짜 후보만 깔때기로 거름'.
"""
import sys, json, datetime as dt
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import strategy_lab as lab
from bithumb import notify

STATE = ROOT / "data" / "strategy_research_state.json"
LEDGER = ROOT / "docs" / "STRATEGY_RESEARCH.md"
LOG = ROOT / "logs" / "strategy_research.log"
MODEL = "claude-haiku-4-5-20251001"   # 일 1회 호출, 저비용. 필요시 상향 가능.

# 화이트리스트 — 이 외 타입은 거부(임의 실행 차단)
ENTRY_TYPES = {"breakout", "ma_cross", "rsi", "bb_lower", "donchian", "vol_spike"}
EXIT_TYPES = {"trail", "timeout", "target_sl", "opposite"}
REGIMES = {"BULL", "BEAR", "any"}

# 가드레일 합격선
T_CANDIDATE = 3.0      # 다중검정 보정 — 단발 t2 아니라 t3
MIN_N = 20             # 표본 최소


def log(msg: str):
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    print(line)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"tried": []}


def sig(spec: dict) -> str:
    """중복 판별용 정규화 시그니처."""
    e, x, fl = spec["entry"], spec["exit"], spec.get("filter", {})
    return json.dumps({"e": [e["type"], e.get("params", {})],
                       "x": [x["type"], x.get("params", {})],
                       "f": fl.get("regime", "any")}, sort_keys=True, ensure_ascii=False)


def validate(spec: dict) -> str | None:
    """검증 통과 시 None, 실패 시 사유 문자열."""
    if not isinstance(spec, dict):
        return "스펙이 dict 아님"
    for k in ("name", "entry", "exit"):
        if k not in spec:
            return f"필수키 '{k}' 없음"
    if spec["entry"].get("type") not in ENTRY_TYPES:
        return f"진입타입 화이트리스트 밖: {spec['entry'].get('type')}"
    if spec["exit"].get("type") not in EXIT_TYPES:
        return f"청산타입 화이트리스트 밖: {spec['exit'].get('type')}"
    if spec.get("filter", {}).get("regime", "any") not in REGIMES:
        return f"장세필터 화이트리스트 밖: {spec['filter'].get('regime')}"
    for sect in ("entry", "exit"):
        p = spec[sect].get("params", {})
        if not isinstance(p, dict):
            return f"{sect}.params dict 아님"
        for v in p.values():
            if not isinstance(v, (int, float)):
                return f"{sect}.params 값이 숫자 아님: {v}"
    return None


def load_api_key() -> str:
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    key = cfg.get("anthropic_api_key") or cfg.get("anthropic", {}).get("api_key", "")
    if not key:
        raise ValueError("config.yaml에 anthropic_api_key 없음")
    return key


SCHEMA_DOC = """전략 스펙 스키마 (이 프리미티브 조합만 가능):
entry.type: breakout{n} | ma_cross{s,l} | rsi{period,below} | bb_lower{period,k} | donchian{n} | vol_spike{n,mult}
exit.type:  trail{trail,activate,sl,timeout} | timeout{days,sl} | target_sl{target,sl,timeout} | opposite{n}
filter.regime: BULL | BEAR | any   (BTC 200일선 기준 진입일 장세)
파라미터 값은 모두 숫자. 수익률은 소수(예 트레일 0.05=5%, sl -0.10=-10%)."""


def propose(tried_sigs: list, dae_jang: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=load_api_key())
    prompt = f"""너는 알트코인 단타 퀀트다. 빗썸 2년 일봉으로 백테스트할 '새 전략 스펙' 1개를 제안하라.

{SCHEMA_DOC}

[이미 시도해 폐기된 전략군 — 같은 메커니즘 재제안 금지]
{dae_jang}

[이미 백테스트 돌린 스펙 시그니처 — 정확히 겹치면 안 됨]
{json.dumps(tried_sigs[-40:], ensure_ascii=False)}

요구사항:
1. 위와 메커니즘이 겹치지 않는 새 조합. 특히 장세필터(BULL/BEAR)·청산방식 조합을 적극 활용.
2. "왜 이 전략이 이 시장(2024~2026 알트 약세장)에서 먹혀야 하는가"를 한 문장 경제적 근거로.
3. 백테 숫자가 좋아도 그게 우연일 수 있음을 알고, 논리가 있는 가설만 제안.

JSON만 출력(코드블록·설명 금지):
{{"name":"...","entry":{{"type":"...","params":{{...}}}},"exit":{{"type":"...","params":{{...}}}},"filter":{{"regime":"..."}},"rationale":"한 문장 경제적 근거"}}"""
    msg = client.messages.create(model=MODEL, max_tokens=600,
                                 messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].replace("json", "", 1).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# ── 무료 폴백: 정의된 조합공간을 체계적으로 훑어 미시도 스펙 1개 반환 ──
GRID_ENTRY = [
    ("breakout", {"n": 10}), ("breakout", {"n": 20}), ("breakout", {"n": 55}),
    ("ma_cross", {"s": 5, "l": 20}), ("ma_cross", {"s": 10, "l": 30}), ("ma_cross", {"s": 20, "l": 60}),
    ("rsi", {"period": 14, "below": 30}), ("rsi", {"period": 14, "below": 25}),
    ("bb_lower", {"period": 20, "k": 2.0}), ("bb_lower", {"period": 20, "k": 2.5}),
    ("vol_spike", {"n": 20, "mult": 3.0}), ("vol_spike", {"n": 20, "mult": 5.0}),
]
GRID_EXIT = [
    ("trail", {"trail": 0.05, "activate": 0.01, "sl": -0.10, "timeout": 30}),
    ("trail", {"trail": 0.03, "activate": 0.01, "sl": -0.05, "timeout": 20}),
    ("trail", {"trail": 0.08, "activate": 0.02, "sl": -0.10, "timeout": 30}),
    ("timeout", {"days": 10, "sl": -0.10}),
    ("timeout", {"days": 5, "sl": -0.05}),
    ("target_sl", {"target": 0.10, "sl": -0.05, "timeout": 30}),
    ("target_sl", {"target": 0.15, "sl": -0.07, "timeout": 30}),
    ("opposite", {"n": 10}),
    ("opposite", {"n": 20}),
]
GRID_REGIME = ["BULL", "BEAR", "any"]


def next_grid_spec(tried_sigs: list) -> dict | None:
    """결정적 순서로 조합공간을 훑어 아직 안 해본 스펙 1개 반환. 고갈 시 None."""
    seen = set(tried_sigs)
    for rgm in GRID_REGIME:
        for et, ep in GRID_ENTRY:
            for xt, xp in GRID_EXIT:
                spec = {"name": f"[그리드] {et}+{rgm}+{xt}",
                        "entry": {"type": et, "params": ep},
                        "exit": {"type": xt, "params": xp},
                        "filter": {"regime": rgm},
                        "rationale": f"체계적 그리드 탐색 — {et} 진입 / {rgm}장세 / {xt} 청산 미시도 조합 (경제적 근거는 후보 승격 시 별도 검토)"}
                if sig(spec) not in seen:
                    return spec
    return None


def verdict(res16: dict, has_rationale: bool) -> tuple[str, str]:
    n, t = res16["n"], res16["t"]
    if n < MIN_N:
        return "표본부족", f"거래 {n}건 < {MIN_N} — 판정 보류"
    if t >= T_CANDIDATE and res16["avg"] > 0:
        return "후보", f"t{t:+.2f}≥{T_CANDIDATE} → dry-run 30건 게이트 대상(자동적용 금지, 경제적근거 확인+사용자 승인 필요)"
    if t >= 2.0 and res16["avg"] > 0:
        return "약신호", f"t{t:+.2f}는 단발 기준 양수지만 다중검정 보정상 t≥{T_CANDIDATE} 미달 → 관찰만"
    return "기각", f"t{t:+.2f} 비용후평균{res16['avg']:+.2f}% — 엣지 없음"


def append_ledger(date: str, spec: dict, res16: dict, res30: dict, vd: str, why: str, n_total: int):
    if not LEDGER.exists():
        LEDGER.write_text(
            "# 일일 전략 리서치 대장 (자동)\n\n"
            "> 백테 양수 = '용의자'일 뿐. 적용은 무조건 dry-run 30건 게이트 + 사용자 승인. (가드레일 2026-06-19)\n\n"
            "| 일자 | # | 전략 | 진입 | 청산 | 장세 | 0.16% t | 거래당 | 판정 | 근거 |\n"
            "|---|---|------|------|------|------|---------|--------|------|------|\n",
            encoding="utf-8")
    e, x = spec["entry"], spec["exit"]
    row = (f"| {date} | {n_total} | {spec['name'][:24]} "
           f"| {e['type']}{e.get('params',{})} | {x['type']}{x.get('params',{})} "
           f"| {spec.get('filter',{}).get('regime','any')} "
           f"| {res16['t']:+.2f} | {res16['avg']:+.2f}% | **{vd}** | {spec.get('rationale','')[:40]} |\n")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(row)


def main():
    state = load_state()
    tried_sigs = [t["sig"] for t in state["tried"]]
    dae_jang = ("추세추종(돈치안·MA골든크로스), 평균회귀(BB하단·RSI), 펌프추격, 눌림목, "
                "횡단면모멘텀, 거래량이벤트, ORB, 시즌성, 숏-돈치안, 바구니보유 — 모두 기각됨")
    log(f"=== 일일 전략 리서치 시작 (누적 {len(tried_sigs)}개 시도) ===")

    # 1) 새 스펙 제안 (중복/검증 실패 시 최대 3회 재시도)
    spec = None
    for attempt in range(3):
        try:
            cand = propose(tried_sigs, dae_jang)
        except Exception as e:
            log(f"[API 미사용 → 그리드 폴백] {str(e)[:120]}")
            break
        err = validate(cand)
        if err:
            log(f"[검증 실패 {attempt+1}/3] {err}")
            continue
        if sig(cand) in tried_sigs:
            log(f"[중복 {attempt+1}/3] 이미 시도한 스펙 — 재요청")
            continue
        spec = cand
        break
    # API 실패/고갈 시 무료 그리드 폴백
    if spec is None:
        log("[폴백] API 미사용 — 그리드 enumeration으로 미시도 조합 선택")
        spec = next_grid_spec(tried_sigs)
    if spec is None:
        notify.send("📉 일일 전략 리서치: API 실패 + 그리드 고갈 — 더 시도할 조합 없음.")
        log("종료 — 유효 스펙 없음 (그리드까지 고갈)")
        return

    # 2) 백테스트
    log(f"제안: {spec['name']} | {sig(spec)}")
    res = lab.evaluate(spec, verbose=False)
    res16, res30 = res["0.16%"], res["0.30%"]
    has_rat = bool(spec.get("rationale", "").strip())
    vd, why = verdict(res16, has_rat)

    # 3) 기록
    date = dt.date.today().isoformat()
    n_total = len(tried_sigs) + 1
    state["tried"].append({"date": date, "sig": sig(spec), "name": spec["name"],
                           "rationale": spec.get("rationale", ""),
                           "res16": res16, "res30": res30, "verdict": vd})
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    append_ledger(date, spec, res16, res30, vd, why, n_total)

    line = (f"📊 일일 전략 리서치 #{n_total} ({date})\n"
            f"전략: {spec['name']}\n"
            f"  진입 {spec['entry']['type']} / 청산 {spec['exit']['type']} / 장세 {spec.get('filter',{}).get('regime','any')}\n"
            f"  0.16%: {res16['n']}건 승률{res16['wr']:.0f}% 거래당{res16['avg']:+.2f}% t{res16['t']:+.2f}\n"
            f"  판정: 【{vd}】 {why}\n"
            f"  근거: {spec.get('rationale','(없음)')}")
    log(line.replace("\n", " | "))
    notify.send(line)
    if vd == "후보":
        notify.send("⚠️ '후보' 발생 — 자동 적용 안 함. dry-run 게이트 설계는 사용자 검토 필요.")


if __name__ == "__main__":
    main()
