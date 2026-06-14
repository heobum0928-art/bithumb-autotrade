"""[일회성] EDEN PC다운 청산지연 보정 — 봇이 청산했어야 할 트레일 청산가로 기록.
고점 63.26(+1.66%) → 트레일3% 청산선 61.3622(-1.39%). PC 다운(16:43~20:00)으로
실제 청산 못 함 → 현재가가 아닌 '봇 정상 시 청산가'로 검증 표본 무결성 유지."""
import sys, json
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from bithumb.db import log_trade

POS = ROOT / "data" / "retest_pos.json"
pos = json.loads(POS.read_text())
assert pos.get("coin") == "EDEN", f"포지션이 EDEN 아님: {pos.get('coin')}"

EXIT_PX = 63.26 * 0.97        # 트레일 청산선 = 고점 × (1-0.03)
vol = pos["volume"]
recv = EXIT_PX * vol
pnl_pct = (recv - pos["cost_krw"]) / pos["cost_krw"] * 100

log_trade(
    coin=pos["coin"], market=pos["market"],
    entry_price=pos["entry_price"], exit_price=EXIT_PX,
    volume=vol, cost_krw=pos["cost_krw"], received_krw=recv,
    exit_reason="[RT-DRY] 트레일3% (PC다운 청산지연 보정)",
    entered_at=datetime.fromisoformat(pos["entered_at"]).replace(tzinfo=None),
    exited_at=datetime.now(),
    max_price=63.26,
)
# 포지션 비움 (봇 재시작 시 포지션 없이 시작)
POS.write_text("{}")
print(f"EDEN 보정 청산 기록 완료: 청산가 {EXIT_PX:.4f}, PnL {pnl_pct:+.2f}%, 포지션 비움")
