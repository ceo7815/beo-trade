"""SQLite file for the SPY engine only. The options database is never opened."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS spy_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spy_bars (
  symbol TEXT NOT NULL,
  start TEXT NOT NULL,
  open TEXT NOT NULL,
  high TEXT NOT NULL,
  low TEXT NOT NULL,
  close TEXT NOT NULL,
  volume TEXT NOT NULL,
  PRIMARY KEY (symbol, start)
);
CREATE TABLE IF NOT EXISTS spy_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  shadow INTEGER NOT NULL DEFAULT 0,
  session_date TEXT NOT NULL,
  decision_at TEXT NOT NULL,
  minute_end INTEGER,
  price TEXT,
  open_px TEXT,
  prior_close TEXT,
  sigma TEXT,
  upper_px TEXT,
  lower_px TEXT,
  vwap TEXT,
  stop_px TEXT,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  qty INTEGER NOT NULL DEFAULT 0,
  detail TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (symbol, session_date, decision_at)
);
CREATE TABLE IF NOT EXISTS spy_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_order_id TEXT NOT NULL UNIQUE,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  purpose TEXT NOT NULL,
  status TEXT NOT NULL,
  broker_order_id TEXT,
  filled_qty TEXT,
  filled_avg_price TEXT,
  reject_reason TEXT,
  session_date TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spy_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  session_date TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  entry_at TEXT,
  entry_price TEXT,
  exit_at TEXT,
  exit_price TEXT,
  exit_reason TEXT,
  pnl TEXT,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spy_shadow_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  session_date TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  entry_at TEXT,
  entry_price TEXT,
  exit_at TEXT,
  exit_price TEXT,
  exit_reason TEXT,
  pnl TEXT,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spy_days (
  session_date TEXT PRIMARY KEY,
  equity_start TEXT,
  equity_last TEXT,
  pnl TEXT,
  entries INTEGER NOT NULL DEFAULT 0,
  loss_brake INTEGER NOT NULL DEFAULT 0,
  flattened INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
"""


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
        row = self._conn.execute("SELECT value FROM spy_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO spy_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def upsert_bars(self, symbol: str, rows: list[tuple]) -> None:
        self._conn.executemany(
            """
            INSERT INTO spy_bars(symbol, start, open, high, low, close, volume)
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
                "SELECT symbol, start, open, high, low, close, volume FROM spy_bars WHERE symbol = ? ORDER BY start",
                (symbol,),
            )
        )

    def latest_bar_start(self, symbol: str) -> str | None:
        row = self._conn.execute("SELECT MAX(start) AS start FROM spy_bars WHERE symbol = ?", (symbol,)).fetchone()
        if row is None or row["start"] is None:
            return None
        return str(row["start"])

    def signal_for(self, symbol: str, session_date: str, decision_at: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM spy_signals WHERE symbol = ? AND session_date = ? AND decision_at = ?",
            (symbol, session_date, decision_at),
        ).fetchone()

    def insert_signal(self, row: dict) -> bool:
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO spy_signals(
              symbol, shadow, session_date, decision_at, minute_end, price, open_px, prior_close,
              sigma, upper_px, lower_px, vwap, stop_px, action, reason, qty, detail, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["symbol"],
                1 if row.get("shadow") else 0,
                row["session_date"],
                row["decision_at"],
                row.get("minute_end"),
                row.get("price"),
                row.get("open_px"),
                row.get("prior_close"),
                row.get("sigma"),
                row.get("upper_px"),
                row.get("lower_px"),
                row.get("vwap"),
                row.get("stop_px"),
                row["action"],
                row["reason"],
                int(row.get("qty") or 0),
                row.get("detail"),
                _now(),
            ),
        )
        return cur.rowcount == 1

    def order(self, client_order_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM spy_orders WHERE client_order_id = ?", (client_order_id,)).fetchone()

    def save_order(self, row: dict) -> None:
        current = _now()
        self._conn.execute(
            """
            INSERT INTO spy_orders(
              client_order_id, symbol, side, qty, purpose, status, broker_order_id,
              filled_qty, filled_avg_price, reject_reason, session_date, submitted_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
              status = excluded.status,
              broker_order_id = COALESCE(excluded.broker_order_id, spy_orders.broker_order_id),
              filled_qty = COALESCE(excluded.filled_qty, spy_orders.filled_qty),
              filled_avg_price = COALESCE(excluded.filled_avg_price, spy_orders.filled_avg_price),
              reject_reason = COALESCE(excluded.reject_reason, spy_orders.reject_reason),
              updated_at = excluded.updated_at
            """,
            (
                row["client_order_id"],
                row["symbol"],
                row["side"],
                int(row["qty"]),
                row["purpose"],
                row["status"],
                row.get("broker_order_id"),
                row.get("filled_qty"),
                row.get("filled_avg_price"),
                row.get("reject_reason"),
                row["session_date"],
                row.get("submitted_at") or current,
                current,
            ),
        )

    def pdt_rejected(self, session_date: str, purpose: str | None = None) -> bool:
        if purpose is None:
            row = self._conn.execute(
                "SELECT 1 FROM spy_orders WHERE session_date = ? AND status = 'pdt_rejected' LIMIT 1",
                (session_date,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT 1 FROM spy_orders WHERE session_date = ? AND purpose = ? AND status = 'pdt_rejected' LIMIT 1",
                (session_date, purpose),
            ).fetchone()
        return row is not None

    def entry_count(self, session_date: str) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS n FROM spy_orders
            WHERE session_date = ? AND purpose = 'entry'
              AND status IN ('new', 'accepted', 'pending_new', 'partially_filled', 'filled')
            """,
            (session_date,),
        ).fetchone()
        return int(row["n"] if row else 0)

    def open_trade(self, table: str = "spy_trades", symbol: str = "SPY") -> sqlite3.Row | None:
        if table not in {"spy_trades", "spy_shadow_trades"}:
            raise ValueError(table)
        return self._conn.execute(
            f"SELECT * FROM {table} WHERE symbol = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()

    def insert_trade(self, table: str, row: dict) -> int:
        if table not in {"spy_trades", "spy_shadow_trades"}:
            raise ValueError(table)
        cur = self._conn.execute(
            f"""
            INSERT INTO {table}(symbol, session_date, side, qty, entry_at, entry_price, status)
            VALUES(?, ?, ?, ?, ?, ?, 'open')
            """,
            (row["symbol"], row["session_date"], row["side"], int(row["qty"]), row.get("entry_at"), row.get("entry_price")),
        )
        return int(cur.lastrowid)

    def close_trade(self, table: str, trade_id: int, exit_at: str, exit_price: str, reason: str, pnl: str) -> None:
        if table not in {"spy_trades", "spy_shadow_trades"}:
            raise ValueError(table)
        self._conn.execute(
            f"""
            UPDATE {table}
            SET exit_at = ?, exit_price = ?, exit_reason = ?, pnl = ?, status = 'closed'
            WHERE id = ?
            """,
            (exit_at, exit_price, reason, pnl, trade_id),
        )

    def day(self, session_date: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM spy_days WHERE session_date = ?", (session_date,)).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO spy_days(session_date, entries, loss_brake, flattened, updated_at) VALUES(?, 0, 0, 0, ?)",
                (session_date, _now()),
            )
            row = self._conn.execute("SELECT * FROM spy_days WHERE session_date = ?", (session_date,)).fetchone()
        return row

    def update_day(self, session_date: str, **fields: str | int | None) -> None:
        self.day(session_date)
        allowed = {"equity_start", "equity_last", "pnl", "entries", "loss_brake", "flattened"}
        sets = []
        values: list[object] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            values.append(value)
        sets.append("updated_at = ?")
        values.append(_now())
        values.append(session_date)
        self._conn.execute(f"UPDATE spy_days SET {', '.join(sets)} WHERE session_date = ?", values)

    def recent_signals(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM spy_signals ORDER BY id DESC LIMIT ?", (limit,)))

    def recent_trades(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM spy_trades ORDER BY id DESC LIMIT ?", (limit,)))

    def shadow_trades(self, symbol: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        if symbol:
            return list(
                self._conn.execute(
                    "SELECT * FROM spy_shadow_trades WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                    (symbol, limit),
                )
            )
        return list(self._conn.execute("SELECT * FROM spy_shadow_trades ORDER BY id DESC LIMIT ?", (limit,)))

    def days(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM spy_days ORDER BY session_date"))


def connect_read(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn
