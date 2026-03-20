#!/usr/bin/env python3
"""
test_e2e_10symbols.py — End-to-end test: 10 mã thanh khoản cao từ Yahoo

Flow:
  1. Init test DB riêng (data/test_e2e.db) → không ảnh hưởng production
  2. Seed 10 symbols
  3. Fetch OHLCV từ Yahoo (1MO)
  4. Write cache
  5. Pre-filter
  6. IMFVG indicator analyze
  7. Write signals vào test DB
  8. Query & report kết quả

Usage:
    python -m tests.test_e2e_10symbols
    IMFVG_DEBUG=1 python -m tests.test_e2e_10symbols   (xem raw bar values)
"""

import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from core.config import TZ_MARKET, get_last_closed_bar
from core.pre_filter import passes_filter
from data_provider.yahoo import get_ohlcv
from data_provider.cache import read_cache, write_cache
from data_provider.base import DataIncompleteError, NoDataError, DataProviderError
from indicators.fvg import analyze as fvg_analyze

# ── Config ────────────────────────────────────────────────────────────────────

TEST_DB_PATH   = PROJECT_ROOT / "data" / "test_e2e.db"
TEST_CSV_PATH  = PROJECT_ROOT / "data" / "test_10.csv"
TIMEFRAME      = "1MO"

SYMBOLS = [
    "7203.T",   # Toyota
    "6758.T",   # Sony
    "8306.T",   # MUFG
    "6861.T",   # Keyence
    "9984.T",   # SoftBank
    "6902.T",   # Denso
    "7741.T",   # HOYA
    "8035.T",   # Tokyo Electron
    "6367.T",   # Daikin
    "4063.T",   # Shin-Etsu
]

SYMBOL_NAMES = {
    "7203.T": "Toyota Motor",
    "6758.T": "Sony Group",
    "8306.T": "MUFG Bank",
    "6861.T": "Keyence",
    "9984.T": "SoftBank Group",
    "6902.T": "Denso",
    "7741.T": "HOYA",
    "8035.T": "Tokyo Electron",
    "6367.T": "Daikin Industries",
    "4063.T": "Shin-Etsu Chemical",
}


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging():
    fmt = "[%(asctime)s] %(levelname)-5s %(name)-14s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Quiet down noisy loggers
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)


# ── Test DB (isolated from production) ────────────────────────────────────────

def init_test_db():
    """Tạo schema giống production nhưng trong file riêng."""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
        print(f"  Deleted old test DB: {TEST_DB_PATH}")

    TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS scan_state_{TIMEFRAME} (
            symbol          TEXT PRIMARY KEY,
            status          TEXT    NOT NULL DEFAULT 'PENDING',
            last_scanned_at DATETIME,
            fail_reason     TEXT,
            retry_count     INTEGER NOT NULL DEFAULT 0,
            is_active       INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS signals_{TIMEFRAME} (
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

        CREATE INDEX IF NOT EXISTS idx_scan_state_{TIMEFRAME}_status_retry
            ON scan_state_{TIMEFRAME}(status, retry_count);

        CREATE INDEX IF NOT EXISTS idx_signals_{TIMEFRAME}_active_notify
            ON signals_{TIMEFRAME}(status, notified_at);
    """)
    conn.close()
    print(f"  Test DB created: {TEST_DB_PATH}")


def seed_test_symbols():
    """Insert 10 symbols vào scan_state."""
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.executemany(
        f"INSERT OR IGNORE INTO scan_state_{TIMEFRAME} (symbol) VALUES (?)",
        [(s,) for s in SYMBOLS],
    )
    conn.commit()
    count = conn.execute(
        f"SELECT COUNT(*) FROM scan_state_{TIMEFRAME}"
    ).fetchone()[0]
    conn.close()
    print(f"  Seeded {count} symbols into scan_state_{TIMEFRAME}")


def write_signal_to_test_db(symbol, result, signal_date):
    """Ghi signal vào test DB (tương tự signal_writer nhưng dùng test DB)."""
    if result.get("signal") is None:
        return False

    now_utc = datetime.now(timezone.utc).isoformat()
    meta = result.get("meta", {})
    gap_top = float(meta["gap_top"]) if meta.get("gap_top") is not None else None
    gap_bottom = float(meta["gap_bottom"]) if meta.get("gap_bottom") is not None else None
    close_price = float(meta["close_price"]) if meta.get("close_price") is not None else None

    conn = sqlite3.connect(TEST_DB_PATH)
    conn.execute(
        f"""INSERT OR IGNORE INTO signals_{TIMEFRAME}
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
    conn.commit()
    conn.close()
    return inserted == 1


def mark_scanned(symbol):
    """Mark symbol as SCANNED in test DB."""
    now_utc = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.execute(
        f"""UPDATE scan_state_{TIMEFRAME}
               SET status = 'SCANNED', last_scanned_at = ?, fail_reason = NULL
             WHERE symbol = ?""",
        (now_utc, symbol),
    )
    conn.commit()
    conn.close()


def mark_failed(symbol, reason):
    """Mark symbol as FAILED in test DB."""
    now_utc = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.execute(
        f"""UPDATE scan_state_{TIMEFRAME}
               SET status = 'FAILED', last_scanned_at = ?,
                   fail_reason = ?, retry_count = retry_count + 1
             WHERE symbol = ?""",
        (now_utc, reason[:500], symbol),
    )
    conn.commit()
    conn.close()


# ── Pretty print helpers ─────────────────────────────────────────────────────

def fmt_price(v):
    """Format price: 3250.0 → '3,250'"""
    if v is None:
        return "-"
    return f"{v:,.0f}"


def print_header():
    print()
    print("=" * 82)
    print(f"  E2E Test — 10 Blue-Chip Symbols × IMFVG Indicator")
    print(f"  Timeframe: {TIMEFRAME} | last_closed_bar: {get_last_closed_bar(TIMEFRAME)}")
    print("=" * 82)
    print()


def print_results_table(results):
    """In bảng kết quả cuối cùng."""
    print()
    print("─" * 82)
    print(f"  {'Symbol':<10} {'Name':<20} {'Signal':<10} {'Gap Top':>10} "
          f"{'Gap Bot':>10} {'Close':>10} {'Bars':>6}")
    print("─" * 82)

    signals_count = 0
    for r in results:
        sym = r["symbol"]
        name = SYMBOL_NAMES.get(sym, "")[:18]
        signal = r.get("signal") or "None"
        meta = r.get("meta", {})
        gap_top = fmt_price(meta.get("gap_top"))
        gap_bot = fmt_price(meta.get("gap_bottom"))
        close = fmt_price(meta.get("close_price"))
        bars = r.get("bars", "-")

        if signal != "None":
            signals_count += 1
            marker = "→"
        else:
            marker = " "

        print(f"{marker} {sym:<10} {name:<20} {signal:<10} {gap_top:>10} "
              f"{gap_bot:>10} {close:>10} {bars:>6}")

    print("─" * 82)
    print(f"  Signals found: {signals_count}/{len(results)}")
    print("─" * 82)
    return signals_count


def print_db_summary():
    """Query test DB và in summary."""
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.row_factory = sqlite3.Row

    print()
    print("── scan_state_1MO ──")
    rows = conn.execute(
        f"SELECT symbol, status, last_scanned_at FROM scan_state_{TIMEFRAME}"
    ).fetchall()
    for r in rows:
        print(f"  {r['symbol']:<10} {r['status']:<10} {r['last_scanned_at'] or '-'}")

    print()
    print("── signals_1MO (ACTIVE) ──")
    rows = conn.execute(
        f"""SELECT symbol, indicator, signal_type, signal_date,
                   gap_top, gap_bottom, close_price, indicator_version
              FROM signals_{TIMEFRAME}
             WHERE status = 'ACTIVE'
             ORDER BY symbol"""
    ).fetchall()

    if not rows:
        print("  (no active signals)")
    else:
        for r in rows:
            print(f"  {r['symbol']:<10} {r['indicator']:<8} {r['signal_type']:<10} "
                  f"date={r['signal_date']}  gap={fmt_price(r['gap_bottom'])}"
                  f"-{fmt_price(r['gap_top'])}  close={fmt_price(r['close_price'])}  "
                  f"v{r['indicator_version']}")

    conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    setup_logging()
    log = logging.getLogger("e2e_test")

    print_header()

    # Step 1: Init test DB
    print("Step 1 — Init test DB")
    init_test_db()
    seed_test_symbols()
    print()

    # Step 2: Fetch + Cache + Filter + Analyze
    print("Step 2 — Fetch → Cache → Pre-filter → IMFVG Analyze")
    print("-" * 60)

    last_closed = get_last_closed_bar(TIMEFRAME)
    results = []
    total_start = time.time()

    for i, symbol in enumerate(SYMBOLS, 1):
        sym_start = time.time()
        entry = {"symbol": symbol, "signal": None, "meta": {}, "bars": "-", "error": None}

        try:
            # Fetch
            print(f"  [{i:2d}/10] {symbol:<10} fetching...", end="", flush=True)
            df = get_ohlcv(symbol, TIMEFRAME)

            if df.empty:
                raise NoDataError(f"{symbol}: empty DataFrame")

            # Cache
            try:
                write_cache(symbol, TIMEFRAME, df)
                df_cached = read_cache(symbol, TIMEFRAME)
                if df_cached is not None:
                    df = df_cached
            except DataIncompleteError as e:
                log.warning(f"{symbol} cache gap: {e} — using fresh data")

            entry["bars"] = len(df)

            # Pre-filter
            if not passes_filter(df, symbol):
                print(f" filtered out ({len(df)} bars, {time.time() - sym_start:.1f}s)")
                mark_scanned(symbol)
                results.append(entry)
                continue

            # IMFVG Analyze
            result = fvg_analyze(df, symbol, TIMEFRAME)
            entry["signal"] = result.get("signal")
            entry["meta"] = result.get("meta", {})

            # Write signal to test DB
            if result.get("signal"):
                written = write_signal_to_test_db(symbol, result, last_closed)
                status_str = "DB ✓" if written else "dup"
            else:
                status_str = ""

            # Mark scanned
            mark_scanned(symbol)

            latency = time.time() - sym_start
            sig_display = result.get("signal") or "no signal"
            print(f" {sig_display:<12} {len(df)} bars  ({latency:.1f}s) {status_str}")

        except (NoDataError, DataIncompleteError, DataProviderError) as e:
            latency = time.time() - sym_start
            print(f" ERROR: {e} ({latency:.1f}s)")
            entry["error"] = str(e)
            mark_failed(symbol, str(e))

        except Exception as e:
            latency = time.time() - sym_start
            print(f" UNEXPECTED: {type(e).__name__}: {e} ({latency:.1f}s)")
            entry["error"] = f"{type(e).__name__}: {e}"
            mark_failed(symbol, f"{type(e).__name__}: {e}")

        results.append(entry)

    total_duration = time.time() - total_start
    print()
    print(f"  Total fetch time: {total_duration:.1f}s")

    # Step 3: Results table
    print()
    print("Step 3 — Results")
    signals_count = print_results_table(results)

    # Step 4: DB summary
    print()
    print("Step 4 — DB Verification")
    print_db_summary()

    # Step 5: Analysis hints
    print()
    print("=" * 82)
    if signals_count == 0:
        print("  ⓘ  0 signals found — IMFVG monthly rất selective, đây là behavior bình thường.")
        print("  ⓘ  Để xem raw bar values, chạy lại với: IMFVG_DEBUG=1")
        print("  ⓘ  Cross-check: mở TradingView → nến tháng → verify pattern thủ công")
    else:
        print(f"  ✓  {signals_count} signal(s) found — cross-check trên TradingView:")
        for r in results:
            if r.get("signal"):
                meta = r["meta"]
                print(f"     {r['symbol']} {r['signal']}: "
                      f"gap {fmt_price(meta.get('gap_bottom'))}"
                      f"-{fmt_price(meta.get('gap_top'))}, "
                      f"close {fmt_price(meta.get('close_price'))}")
    print("=" * 82)
    print()


if __name__ == "__main__":
    main()
