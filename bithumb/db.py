"""
Trade database — SQLite, one row per completed trade.
Provides write (log_trade) and read (get_trades, get_stats) interfaces.
"""
import sqlite3
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH = Path("data/trades.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT    NOT NULL,
    coin          TEXT    NOT NULL,
    market        TEXT    NOT NULL,
    entry_price   REAL    NOT NULL,
    exit_price    REAL,
    volume        REAL    NOT NULL,
    cost_krw      REAL    NOT NULL,
    received_krw  REAL,
    pnl_krw       REAL,
    pnl_pct       REAL,
    exit_reason   TEXT,
    hold_seconds  INTEGER,
    entered_at    TEXT    NOT NULL,
    exited_at     TEXT,
    max_price     REAL,
    max_pnl_pct   REAL
);
CREATE TABLE IF NOT EXISTS daily_params (
    date          TEXT    PRIMARY KEY,
    entry_delay   INTEGER,
    min_volume    REAL,
    take_profit   REAL,
    stop_loss     REAL,
    entry_ratio   REAL,
    note          TEXT
);
CREATE TABLE IF NOT EXISTS signal_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entered_at    TEXT    NOT NULL,
    coin          TEXT    NOT NULL,
    entry_type    TEXT    NOT NULL,
    price_chg_pct REAL,
    vol_mult      REAL,
    hour_kst      INTEGER,
    strict_mode   INTEGER DEFAULT 0,
    skip_reason   TEXT,
    rsi           REAL,
    bb_pct        REAL,
    macd_bull     INTEGER
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript(CREATE_SQL)
        for col, typ in [("max_price", "REAL"), ("max_pnl_pct", "REAL")]:
            try:
                con.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
            except Exception:
                pass
    log.debug("DB initialised")


def log_trade(
    coin: str,
    market: str,
    entry_price: float,
    exit_price: float,
    volume: float,
    cost_krw: float,
    received_krw: float,
    exit_reason: str,
    entered_at: datetime,
    exited_at: datetime,
    max_price: float = 0.0,
) -> None:
    pnl_krw = received_krw - cost_krw
    pnl_pct = pnl_krw / cost_krw * 100 if cost_krw else 0
    max_pnl_pct = (max_price - entry_price) / entry_price * 100 if entry_price and max_price else 0
    if isinstance(entered_at, str):
        entered_at = datetime.fromisoformat(entered_at)
    if isinstance(exited_at, str):
        exited_at = datetime.fromisoformat(exited_at)
    hold_sec = int((exited_at - entered_at).total_seconds())
    row = (
        exited_at.strftime("%Y-%m-%d"),
        coin, market,
        entry_price, exit_price,
        volume, cost_krw, received_krw,
        pnl_krw, pnl_pct,
        exit_reason, hold_sec,
        entered_at.isoformat(), exited_at.isoformat(),
        max_price, max_pnl_pct,
    )
    with _conn() as con:
        con.execute(
            """INSERT INTO trades
               (date,coin,market,entry_price,exit_price,volume,cost_krw,
                received_krw,pnl_krw,pnl_pct,exit_reason,hold_seconds,
                entered_at,exited_at,max_price,max_pnl_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            row,
        )
    log.info(f"[DB] 거래 저장: {coin} PnL={pnl_krw:+,.0f}원 ({pnl_pct:+.2f}%) 최고={max_pnl_pct:+.1f}% [{exit_reason}]")


def log_signal(coin: str, entered_at: datetime, entry_type: str,
               price_chg_pct: float | None, vol_mult: float | None,
               strict_mode: bool = False, skip_reason: str | None = None,
               rsi: float | None = None, bb_pct: float | None = None,
               macd_bull: int | None = None) -> None:
    hour = entered_at.hour if isinstance(entered_at, datetime) else datetime.now().hour
    with _conn() as con:
        con.execute(
            """INSERT INTO signal_log
               (entered_at, coin, entry_type, price_chg_pct, vol_mult, hour_kst,
                strict_mode, skip_reason, rsi, bb_pct, macd_bull)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (entered_at.isoformat() if isinstance(entered_at, datetime) else str(entered_at),
             coin, entry_type, price_chg_pct, vol_mult, hour,
             int(strict_mode), skip_reason, rsi, bb_pct, macd_bull),
        )


def log_params(params: dict) -> None:
    today = date.today().isoformat()
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO daily_params
               (date,entry_delay,min_volume,take_profit,stop_loss,entry_ratio,note)
               VALUES (:date,:entry_delay,:min_volume,:take_profit,:stop_loss,:entry_ratio,:note)""",
            {"date": today, **params},
        )


def get_trades(days: int = 7) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trades WHERE date >= ? ORDER BY entered_at", (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats(days: int = 7) -> dict:
    trades = get_trades(days)
    if not trades:
        return {"count": 0}
    wins = [t for t in trades if t["pnl_krw"] > 0]
    losses = [t for t in trades if t["pnl_krw"] <= 0]
    total_pnl = sum(t["pnl_krw"] for t in trades)
    avg_hold = sum(t["hold_seconds"] for t in trades) / len(trades)
    sl_hits = [t for t in trades if "손절" in (t["exit_reason"] or "")]
    tp_hits = [t for t in trades if "익절" in (t["exit_reason"] or "")]
    return {
        "count":       len(trades),
        "win_count":   len(wins),
        "loss_count":  len(losses),
        "win_rate":    len(wins) / len(trades),
        "total_pnl":   total_pnl,
        "avg_pnl":     total_pnl / len(trades),
        "avg_hold_sec": avg_hold,
        "sl_count":    len(sl_hits),
        "tp_count":    len(tp_hits),
        "avg_win_pnl": sum(t["pnl_krw"] for t in wins) / max(len(wins), 1),
        "avg_loss_pnl": sum(t["pnl_krw"] for t in losses) / max(len(losses), 1),
    }
