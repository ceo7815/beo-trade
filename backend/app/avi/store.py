"""SQLite file for the Avi engine only. The options database and spy.sqlite are never opened."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS avi_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS avi_bars (
  symbol TEXT NOT NULL,
  start TEXT NOT NULL,
  open TEXT NOT NULL,
  high TEXT NOT NULL,
  low TEXT NOT NULL,
  close TEXT NOT NULL,
  volume TEXT NOT NULL,
  PRIMARY KEY (symbol, start)
);
CREATE TABLE IF NOT EXISTS avi_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_date TEXT NOT NULL,
  decision_at TEXT NOT NULL,
  state TEXT NOT NULL,
  reason TEXT NOT NULL,
  price TEXT,
  open_px TEXT,
  sigma TEXT,
  upper_px TEXT,
  lower_px TEXT,
  vwap TEXT,
  vix TEXT,
  detail TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (session_date, decision_at)
);
CREATE TABLE IF NOT EXISTS avi_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_order_id TEXT NOT NULL UNIQUE,
  purpose TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  limit_price TEXT,
  natural_price TEXT,
  status TEXT NOT NULL,
  broker_order_id TEXT,
  filled_avg_price TEXT,
  long_symbol TEXT,
  short_symbol TEXT,
  session_date TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS avi_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_date TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  long_symbol TEXT NOT NULL,
  short_symbol TEXT NOT NULL,
  long_strike TEXT,
  short_strike TEXT,
  expiry TEXT,
  width TEXT,
  debit TEXT,
  exit_credit TEXT,
  entry_at TEXT,
  exit_at TEXT,
  exit_reason TEXT,
  pnl TEXT,
  current_value TEXT,
  status TEXT NOT NULL
);
"""

WORKING = (
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "accepted_for_bidding",
    "pending_cancel",
    "pending_replace",
    "cancel_requested",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM avi_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO avi_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def upsert_bars(self, rows: list[tuple]) -> None:
        self._conn.executemany(
            """
            INSERT INTO avi_bars(symbol, start, open, high, low, close, volume)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, start) DO UPDATE SET
              open = excluded.open, high = excluded.high, low = excluded.low,
              close = excluded.close, volume = excluded.volume
            """,
            rows,
        )

    def bars(self, symbol: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT symbol, start, open, high, low, close, volume FROM avi_bars WHERE symbol = ? ORDER BY start",
                (symbol,),
            )
        )

    def latest_bar_start(self, symbol: str) -> str | None:
        row = self._conn.execute("SELECT MAX(start) AS start FROM avi_bars WHERE symbol = ?", (symbol,)).fetchone()
        if row is None or row["start"] is None:
            return None
        return str(row["start"])

    def insert_signal(self, row: dict) -> bool:
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO avi_signals(
              session_date, decision_at, state, reason, price, open_px, sigma,
              upper_px, lower_px, vwap, vix, detail, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["session_date"],
                row["decision_at"],
                row["state"],
                row["reason"],
                row.get("price"),
                row.get("open_px"),
                row.get("sigma"),
                row.get("upper_px"),
                row.get("lower_px"),
                row.get("vwap"),
                row.get("vix"),
                row.get("detail"),
                _now(),
            ),
        )
        return cur.rowcount == 1

    def signal_keys(self, session_date: str) -> set[str]:
        rows = self._conn.execute("SELECT decision_at FROM avi_signals WHERE session_date = ?", (session_date,)).fetchall()
        return {str(row["decision_at"]) for row in rows}

    def latest_signal(self, session_date: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM avi_signals WHERE session_date = ? ORDER BY decision_at DESC LIMIT 1",
            (session_date,),
        ).fetchone()

    def signals(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM avi_signals ORDER BY id DESC LIMIT ?", (limit,)))

    def save_order(self, row: dict) -> None:
        current = _now()
        self._conn.execute(
            """
            INSERT INTO avi_orders(
              client_order_id, purpose, side, qty, limit_price, natural_price, status,
              broker_order_id, filled_avg_price, long_symbol, short_symbol, session_date,
              submitted_at, updated_at, detail
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
              status = excluded.status,
              broker_order_id = COALESCE(excluded.broker_order_id, avi_orders.broker_order_id),
              filled_avg_price = COALESCE(excluded.filled_avg_price, avi_orders.filled_avg_price),
              limit_price = COALESCE(excluded.limit_price, avi_orders.limit_price),
              updated_at = excluded.updated_at,
              detail = COALESCE(excluded.detail, avi_orders.detail)
            """,
            (
                row["client_order_id"],
                row["purpose"],
                row["side"],
                int(row["qty"]),
                row.get("limit_price"),
                row.get("natural_price"),
                row["status"],
                row.get("broker_order_id"),
                row.get("filled_avg_price"),
                row.get("long_symbol"),
                row.get("short_symbol"),
                row["session_date"],
                row.get("submitted_at") or current,
                current,
                row.get("detail"),
            ),
        )

    def working(self, purpose: str) -> sqlite3.Row | None:
        marks = ",".join("?" for _ in WORKING)
        return self._conn.execute(
            f"SELECT * FROM avi_orders WHERE purpose = ? AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
            (purpose, *WORKING),
        ).fetchone()

    def open_trade(self) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM avi_trades WHERE status = 'open' ORDER BY id DESC LIMIT 1").fetchone()

    def insert_trade(self, row: dict) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO avi_trades(
              session_date, side, qty, long_symbol, short_symbol, long_strike, short_strike,
              expiry, width, debit, entry_at, current_value, status
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                row["session_date"],
                row["side"],
                int(row["qty"]),
                row["long_symbol"],
                row["short_symbol"],
                row.get("long_strike"),
                row.get("short_strike"),
                row.get("expiry"),
                row.get("width"),
                row.get("debit"),
                row.get("entry_at"),
                row.get("current_value"),
            ),
        )
        return int(cur.lastrowid)

    def close_trade(self, trade_id: int, exit_at: str, exit_credit: str, reason: str, pnl: str) -> None:
        self._conn.execute(
            """
            UPDATE avi_trades
            SET exit_at = ?, exit_credit = ?, exit_reason = ?, pnl = ?, status = 'closed', current_value = ?
            WHERE id = ?
            """,
            (exit_at, exit_credit, reason, pnl, exit_credit, trade_id),
        )

    def update_mark(self, trade_id: int, value: str) -> None:
        self._conn.execute("UPDATE avi_trades SET current_value = ? WHERE id = ?", (value, trade_id))

    def trade_count(self, session_date: str) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM avi_trades WHERE session_date = ?", (session_date,)).fetchone()
        return int(row["n"] if row else 0)

    def trades(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM avi_trades ORDER BY id DESC LIMIT ?", (limit,)))


def connect_read(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn
