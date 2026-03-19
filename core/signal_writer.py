import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List

from core.config import get_last_closed_bar
from indicators.base import IndicatorResult

log = logging.getLogger("signal_writer")

DB_PATH = Path("data/state.db")


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(timeframe: str = "1MO") -> None:
    """
    Tạo các bảng cần thiết nếu chưa tồn tại.
    Idempotent — gọi lại không ảnh hưởng.
    """
    tf = timeframe
    with _get_conn() as conn:
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS scan_state_{tf} (
                symbol          TEXT PRIMARY KEY,
                status          TEXT    NOT NULL DEFAULT 'PENDING',
                last_scanned_at DATETIME,
                fail_reason     TEXT,
                retry_count     INTEGER NOT NULL DEFAULT 0,
                is_active       INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS signals_{tf} (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol             TEXT    NOT NULL,
                indicator          TEXT    NOT NULL,
                signal_date        DATE    NOT NULL,
                signal_type        TEXT    NOT NULL,
                status             TEXT    NOT NULL DEFAULT 'ACTIVE',
                gap_top            REAL,
                gap_bottom         REAL,
                close_price        REAL,
                indicator_version  TEXT,
                notified_at        DATETIME,
                created_at         DATETIME NOT NULL,
                UNIQUE(symbol, indicator, signal_date)
            );

            CREATE TABLE IF NOT EXISTS batch_runs_{tf} (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date       DATE    NOT NULL,
                total_symbols  INTEGER,
                scanned        INTEGER,
                failed         INTEGER,
                signals_found  INTEGER,
                duration_sec   REAL
            );

            CREATE INDEX IF NOT EXISTS idx_scan_state_{tf}_status_retry
                ON scan_state_{tf}(status, retry_count);

            CREATE INDEX IF NOT EXISTS idx_signals_{tf}_active_notify
                ON signals_{tf}(status, notified_at);
        """)
    log.debug(f"init_db done for timeframe={tf}")


def seed_symbols(symbols_csv_path: str, timeframe: str = "1MO") -> int:
    """
    Populate scan_state_{timeframe} từ CSV.
    INSERT OR IGNORE — idempotent, không mất state khi chạy lại.

    Returns:
        số symbol được insert mới (không tính existing)
    """
    tf = timeframe
    symbols = []
    with open(symbols_csv_path) as f:
        for line in f:
            sym = line.strip()
            if sym:
                symbols.append((sym,))

    with _get_conn() as conn:
        before   = conn.execute("SELECT total_changes()").fetchone()[0]
        conn.executemany(
            f"INSERT OR IGNORE INTO scan_state_{tf} (symbol) VALUES (?)",
            symbols,
        )
        inserted = conn.execute("SELECT total_changes()").fetchone()[0] - before
        total    = conn.execute(f"SELECT COUNT(*) FROM scan_state_{tf}").fetchone()[0]

    log.info(f"seed_symbols: {inserted} new symbols inserted ({total} total) tf={tf}")
    return inserted


def expire_old_signals(timeframe: str = "1MO") -> int:
    """
    ACTIVE → EXPIRED cho signal cũ hơn 3 tháng so với last_closed_bar.

    Returns:
        số signal bị expire
    """
    tf = timeframe
    if tf != "1MO":
        raise NotImplementedError(
            f"expire_old_signals only supports 1MO in v1, got {tf!r}"
        )
    last_bar = get_last_closed_bar(tf)
    # 3 tháng tính theo relativedelta-like: lùi 3 tháng từ last_bar
    # Dùng date arithmetic đơn giản: lùi về ngày 1 của tháng - 3
    y, m = last_bar.year, last_bar.month
    m -= 3
    if m <= 0:
        m += 12
        y -= 1
    cutoff = date(y, m, 1)

    with _get_conn() as conn:
        cur = conn.execute(
            f"""UPDATE signals_{tf}
                   SET status = 'EXPIRED'
                 WHERE signal_date < ?
                   AND status = 'ACTIVE'""",
            (cutoff.isoformat(),),
        )
        expired = cur.rowcount

    if expired:
        log.info(f"expire_old_signals: {expired} signals expired (cutoff={cutoff}) tf={tf}")
    return expired


def write_signal(
    symbol    : str,
    result    : IndicatorResult,
    timeframe : str = "1MO",
) -> bool:
    """
    Ghi signal vào signals_{timeframe}.

    - signal_date = get_last_closed_bar(timeframe)  — không dùng date.today()
    - INSERT OR IGNORE — chống duplicate
    - Log DEBUG nếu duplicate skipped, INFO nếu inserted

    Returns:
        True  = inserted
        False = duplicate skipped
    """
    required_keys = ("indicator", "signal", "version")
    if not all(k in result for k in required_keys):
        log.error(f"write_signal: invalid result format from {symbol}: {result}")
        return False

    if result.get("signal") is None:
        return False

    tf          = timeframe
    signal_date = get_last_closed_bar(tf)
    now_utc     = datetime.now(timezone.utc).isoformat()
    meta        = result.get("meta", {})

    gap_top     = float(meta["gap_top"])     if meta.get("gap_top")     is not None else None
    gap_bottom  = float(meta["gap_bottom"])  if meta.get("gap_bottom")  is not None else None
    close_price = float(meta["close_price"]) if meta.get("close_price") is not None else None

    with _get_conn() as conn:
        conn.execute(
            f"""INSERT OR IGNORE INTO signals_{tf}
                (symbol, indicator, signal_date, signal_type,
                 gap_top, gap_bottom, close_price,
                 indicator_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol,
                result["indicator"],
                signal_date.isoformat(),
                result["signal"],
                gap_top,
                gap_bottom,
                close_price,
                result["version"],
                now_utc,
            ),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0]

    if inserted == 1:
        log.info(
            f"signal inserted: {symbol} {result['indicator']} "
            f"{result['signal']} {signal_date}"
        )
        return True
    else:
        log.debug(
            f"duplicate skipped: {symbol} {result['indicator']} {signal_date}"
        )
        return False
