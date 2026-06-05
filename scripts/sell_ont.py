import sys; sys.path.insert(0, ".")
import json
from pathlib import Path
from datetime import datetime
from bithumb.client import BithumbClient
from bithumb.db import log_trade
from bithumb import notify

STATE_FILE = Path("data/active_pos.json")

saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
coin   = saved["coin"]
market = saved["market"]
volume = float(saved["volume"]) - float(saved.get("sold_vol", 0))
cost   = float(saved["cost"])

print(f"[{coin}] 매도 시작 | 수량={volume:.8f}")

client = BithumbClient()

# 잔고 확인
bal = 0.0
for a in client.get_accounts():
    if a["currency"] == coin:
        bal = float(a["balance"])
        break
print(f"[{coin}] 실제 잔고={bal:.8f}")
volume = min(volume, bal)

# 시장가 매도
r = client.market_sell(market, volume)
uuid = r.get("uuid")
print(f"[{coin}] 주문 UUID={uuid}")

import time
received = 0.0
for _ in range(30):
    time.sleep(1)
    order = client.get_order(uuid)
    if order.get("state") == "done":
        received = float(order.get("executed_funds", 0)) - float(order.get("paid_fee", 0))
        break

pnl     = received - cost
pnl_pct = pnl / cost * 100
print(f"[{coin}] 매도 완료 | 수령={received:,.0f}원 | PnL={pnl:+,.0f}원 ({pnl_pct:+.2f}%)")

cur_price = float(client.get_ticker(coin)["closing_price"])
log_trade(
    coin=coin, market=market,
    entry_price=float(saved["entry_price"]),
    exit_price=cur_price,
    volume=float(saved["volume"]),
    cost_krw=cost, received_krw=received,
    exit_reason="수동청산 (파라미터 변경)",
    entered_at=saved["entered_at"], exited_at=datetime.now(),
)
notify.notify_sell(coin, pnl, pnl_pct, "수동청산 (파라미터 변경)")
STATE_FILE.unlink(missing_ok=True)
print("완료")
