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
    macd_bull     INTEGER,
    signal_price  REAL,
    outcome_5m    REAL,
    outcome_30m   REAL
);
CREATE TABLE IF NOT EXISTS pump_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at   TEXT    NOT NULL,
    coin          TEXT    NOT NULL,
    base_price    REAL    NOT NULL,
    pump_pct      REAL,
    vol_mult      REAL,
    price_1m      REAL,
    price_2m      REAL,
    price_3m      REAL,
    price_5m      REAL,
    peak_price    REAL,
    peak_at_sec   INTEGER,
    max_drop_pct  REAL,
    pullback_2pct INTEGER DEFAULT 0,
    bounce_after  INTEGER DEFAULT 0,
    entered       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pump_ticks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pump_id       INTEGER NOT NULL,        -- pump_log.id 참조 (논리적 FK)
    seq           INTEGER NOT NULL,        -- 이벤트 내 절대 순번 (0,1,2,...)
    exchange_ts   REAL,                    -- 거래소 발생 시각 (epoch sec)
    recv_ts       REAL    NOT NULL,        -- 수집기 수신 시각 (time.time())
    price         REAL    NOT NULL,        -- closePrice
    acc_value     REAL,                    -- 누적 거래대금 (value)
    volume_power  REAL,                    -- 체결강도 (volumePower)
    gap_before    INTEGER DEFAULT 0,       -- 직전 틱과 갭이면 1 (REC-04)
    ts_estimated  INTEGER DEFAULT 0        -- exchange_ts가 recv_ts 복사값이면 1 (REC-03)
);
CREATE INDEX IF NOT EXISTS idx_pump_ticks_pump_id ON pump_ticks(pump_id);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript(CREATE_SQL)
        for tbl, col, typ in [
            ("trades",     "max_price",    "REAL"),
            ("trades",     "max_pnl_pct",  "REAL"),
            ("signal_log", "signal_price", "REAL"),
            ("signal_log", "outcome_5m",   "REAL"),
            ("signal_log", "outcome_30m",  "REAL"),
        ]:
            try:
                con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
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
               macd_bull: int | None = None,
               signal_price: float | None = None) -> int:
    hour = entered_at.hour if isinstance(entered_at, datetime) else datetime.now().hour
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO signal_log
               (entered_at, coin, entry_type, price_chg_pct, vol_mult, hour_kst,
                strict_mode, skip_reason, rsi, bb_pct, macd_bull, signal_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (entered_at.isoformat() if isinstance(entered_at, datetime) else str(entered_at),
             coin, entry_type, price_chg_pct, vol_mult, hour,
             int(strict_mode), skip_reason, rsi, bb_pct, macd_bull, signal_price),
        )
        return cur.lastrowid


def log_pump(coin: str, detected_at: datetime, base_price: float,
             pump_pct: float, vol_mult: float) -> int:
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO pump_log (detected_at, coin, base_price, pump_pct, vol_mult)
               VALUES (?,?,?,?,?)""",
            (detected_at.isoformat() if isinstance(detected_at, datetime) else str(detected_at),
             coin, base_price, pump_pct, vol_mult),
        )
        return cur.lastrowid


def update_pump_path(pump_id: int, **kwargs) -> None:
    allowed = {"price_1m", "price_2m", "price_3m", "price_5m",
               "peak_price", "peak_at_sec", "max_drop_pct",
               "pullback_2pct", "bounce_after", "entered"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [pump_id]
    with _conn() as con:
        con.execute(f"UPDATE pump_log SET {cols} WHERE id = ?", vals)


def log_tick(pump_id: int, seq: int, recv_ts: float, price: float,
             exchange_ts: float | None = None, acc_value: float | None = None,
             volume_power: float | None = None, gap_before: bool = False,
             ts_estimated: bool = False) -> None:
    """펌핑 이벤트 1틱을 pump_ticks 에 기록. pump_id 는 pump_log.id 참조.

    exchange_ts 가 None 이면 recv_ts 를 복사하고 ts_estimated 를 True 로 강제한다.
    [Phase 2 의존 계약] 이 시그니처는 백테스트 엔진이 import 한다 — 변경 금지.
    위치 인자 4개(pump_id, seq, recv_ts, price) 고정, 나머지는 키워드 인자.
    """
    if exchange_ts is None:
        exchange_ts = recv_ts
        ts_estimated = True
    with _conn() as con:
        con.execute(
            """INSERT INTO pump_ticks
               (pump_id, seq, exchange_ts, recv_ts, price, acc_value,
                volume_power, gap_before, ts_estimated)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (pump_id, seq, exchange_ts, recv_ts, price, acc_value,
             volume_power, int(gap_before), int(ts_estimated)),
        )


def get_ticks(pump_id: int) -> list[dict]:
    """특정 펌핑 이벤트의 모든 틱을 seq 순으로 반환 (Phase 2 백테스트용).

    [Phase 2 의존 계약] get_ticks(pump_id) -> list[dict]. 변경 금지.
    존재하지 않는 pump_id 는 빈 리스트를 반환한다.
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM pump_ticks WHERE pump_id = ? ORDER BY seq", (pump_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_signal_outcome(signal_id: int, outcome_5m: float = None, outcome_30m: float = None) -> None:
    updates, vals = [], []
    if outcome_5m is not None:
        updates.append("outcome_5m = ?"); vals.append(outcome_5m)
    if outcome_30m is not None:
        updates.append("outcome_30m = ?"); vals.append(outcome_30m)
    if not updates:
        return
    vals.append(signal_id)
    with _conn() as con:
        con.execute(f"UPDATE signal_log SET {', '.join(updates)} WHERE id = ?", vals)


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
