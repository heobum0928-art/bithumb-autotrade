import sys; sys.path.insert(0, ".")
import sqlite3
from bithumb.client import BithumbClient
from bithumb.db import DB_PATH

# ── DB 기록 ──────────────────────────────────────────────────────────────────
con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT coin, exited_at, cost_krw, received_krw, pnl_krw, pnl_pct, exit_reason
    FROM trades WHERE date >= '2026-05-10' ORDER BY exited_at
""").fetchall()
con.close()

print(f"{'시간':16s}  {'코인':8s}  {'비용':>10s}  {'수령':>10s}  {'손익':>12s}")
print("-" * 72)
db_total = 0
for r in rows:
    recv = r["received_krw"] or 0
    pnl  = r["pnl_krw"]
    db_total += pnl
    sign = "+" if pnl >= 0 else ""
    bug  = "  *** 수령0 버그" if recv == 0 and pnl < -10000 else ""
    ts   = str(r["exited_at"])[:16]
    print(f"{ts}  {r['coin']:8s}  {r['cost_krw']:>10,.0f}  {recv:>10,.0f}  {sign}{pnl:>8,.0f}원 ({r['pnl_pct']:+.1f}%){bug}")

print("-" * 72)
print(f"DB 합계: {db_total:+,.0f}원  ({len(rows)}건)")

# XION 미기록 여부 확인
xion_in_db = any(r["coin"] == "XION" for r in rows)
if not xion_in_db:
    print("\n[!] XION 거래 DB 미기록 (datetime 버그로 저장 실패)")
    print("    → XION PnL +13,483원 (+8.97%) 누락됨")

# ── 현재 잔고 ─────────────────────────────────────────────────────────────────
print("\n── 현재 빗썸 잔고 ──────────────────────────────────────────────────────")
client = BithumbClient()
for a in client.get_accounts():
    bal = float(a.get("balance", 0))
    if bal <= 0:
        continue
    cur = a["currency"]
    if cur == "KRW":
        print(f"  KRW: {bal:,.0f}원")
    elif cur not in ("P",):
        try:
            price = float(client.get_ticker(cur)["closing_price"])
            val   = bal * price
            if val >= 1000:
                print(f"  {cur}: {bal:.4f}개 = {val:,.0f}원")
        except Exception:
            pass
