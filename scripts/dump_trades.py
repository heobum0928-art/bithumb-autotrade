import sqlite3
con = sqlite3.connect("data/trades.db")
con.row_factory = sqlite3.Row
cols = [d[0] for d in con.execute("PRAGMA table_info(trades)").fetchall()]
print("columns:", cols)
rows = con.execute("SELECT * FROM trades ORDER BY exited_at ASC").fetchall()
for r in rows:
    d = dict(r)
    cost = d.get("cost_krw", d.get("cost", "?"))
    recv = d.get("recv_krw", "?")
    print(f"coin={d['coin']}, cost={cost}, recv={recv}, pnl_krw={d['pnl_krw']:.0f}, pnl_pct={d['pnl_pct']:.1f}%, reason={d['exit_reason']}")
con.close()
