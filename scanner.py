#!/usr/bin/env python3
"""
scanner.py — CLI entry point cho Japan Stock Scanner v1

Usage:
    python scanner.py --timeframe 1MO
    python scanner.py --timeframe 1MO --resume
    python scanner.py --timeframe 1MO --retry-failed
    python scanner.py --timeframe 1MO --dry-run
"""

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from core.config import (
    MAX_BATCH_TIME_SEC,
    TZ_MARKET,
    get_last_closed_bar,
)
from core.plugin_manager import run_all
from core.pre_filter import passes_filter
from core.signal_writer import (
    DB_PATH,
    expire_old_signals,
    init_db,
    seed_symbols,
    write_signal,
)
import data_provider.cache as cm
from data_provider.cache import read_cache, write_cache
from data_provider.yahoo import get_ohlcv
from data_provider.base import DataIncompleteError, NoDataError, DataProviderError

SYMBOLS_CSV = Path("data/symbols.csv")
LOGS_DIR    = Path("logs")


# ── JST Formatter ─────────────────────────────────────────────────────────────

class JSTFormatter(logging.Formatter):
    """
    Custom formatter: asctime luôn in theo Asia/Tokyo (JST),
    bất kể timezone của server (UTC hay khác).

    Lý do cần custom:
        logging.Formatter mặc định dùng time.localtime() → phụ thuộc OS timezone.
        Nếu server chạy UTC, %(asctime)s sẽ in giờ UTC nhưng dán nhãn "JST" → sai 9h.
        Override formatTime() → dùng datetime.fromtimestamp(..., tz=JST) → luôn đúng.
    """
    _tz = ZoneInfo(TZ_MARKET)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=self._tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(timeframe: str) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    now_jst  = datetime.now(ZoneInfo(TZ_MARKET))
    log_file = LOGS_DIR / f"batch_{timeframe}_{now_jst.strftime('%Y%m')}.log"

    fmt       = "[%(asctime)s JST] %(levelname)-5s %(name)-14s %(message)s"
    datefmt   = "%Y-%m-%d %H:%M:%S"
    formatter = JSTFormatter(fmt, datefmt=datefmt)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)

    # Stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.DEBUG)
        root.addHandler(fh)
        root.addHandler(sh)

    return logging.getLogger("scanner")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _reset_batch(timeframe: str) -> None:
    """Normal run: SCANNED → PENDING, reset retry_count=0."""
    tf = timeframe
    with _get_conn() as conn:
        conn.execute(f"""
            UPDATE scan_state_{tf}
               SET status = 'PENDING', retry_count = 0
             WHERE status = 'SCANNED'
        """)


def _load_symbols(timeframe: str, mode: str) -> list[str]:
    """
    Load symbols cần quét theo mode:
      normal        : PENDING + FAILED retry_count < 3
      --resume      : chỉ PENDING
      --retry-failed: tất cả FAILED (force, không giới hạn retry_count)
    """
    tf = timeframe
    with _get_conn() as conn:
        if mode == "resume":
            rows = conn.execute(f"""
                SELECT symbol FROM scan_state_{tf}
                 WHERE is_active = 1 AND status = 'PENDING'
            """).fetchall()
        elif mode == "retry-failed":
            rows = conn.execute(f"""
                SELECT symbol FROM scan_state_{tf}
                 WHERE is_active = 1 AND status = 'FAILED'
            """).fetchall()
        else:  # normal
            rows = conn.execute(f"""
                SELECT symbol FROM scan_state_{tf}
                 WHERE is_active = 1
                   AND (
                         status = 'PENDING'
                      OR (status = 'FAILED' AND retry_count < 3)
                   )
            """).fetchall()
    return [r["symbol"] for r in rows]


def _mark_scanned(timeframe: str, symbol: str) -> None:
    tf      = timeframe
    now_utc = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(f"""
            UPDATE scan_state_{tf}
               SET status = 'SCANNED', last_scanned_at = ?, fail_reason = NULL
             WHERE symbol = ?
        """, (now_utc, symbol))


def _mark_failed(timeframe: str, symbol: str, reason: str) -> None:
    tf      = timeframe
    now_utc = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(f"""
            UPDATE scan_state_{tf}
               SET status = 'FAILED',
                   last_scanned_at = ?,
                   fail_reason = ?,
                   retry_count = retry_count + 1
             WHERE symbol = ?
        """, (now_utc, reason[:500], symbol))


def _set_inactive(timeframe: str, symbol: str, reason: str) -> None:
    tf      = timeframe
    now_utc = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(f"""
            UPDATE scan_state_{tf}
               SET is_active = 0,
                   last_scanned_at = ?,
                   fail_reason = ?
             WHERE symbol = ?
        """, (now_utc, reason[:500], symbol))


def _get_retry_count(timeframe: str, symbol: str) -> int:
    tf = timeframe
    with _get_conn() as conn:
        row = conn.execute(
            f"SELECT retry_count FROM scan_state_{tf} WHERE symbol = ?",
            (symbol,)
        ).fetchone()
    return row["retry_count"] if row else 0


# ── Main scan loop ────────────────────────────────────────────────────────────

def run_scan(timeframe: str, mode: str, dry_run: bool) -> dict:
    """
    Chạy batch scan.

    Returns:
        dict với stats: total, scanned, failed, signals_found, duration_sec
    """
    log = logging.getLogger("scanner")

    # Init DB + seed symbols
    init_db(timeframe)
    if SYMBOLS_CSV.exists():
        seed_symbols(str(SYMBOLS_CSV), timeframe)

    # Reset batch (chỉ normal run)
    if mode == "normal":
        _reset_batch(timeframe)

    # Expire old signals trước khi insert mới
    if not dry_run:
        try:
            expire_old_signals(timeframe)
        except NotImplementedError:
            log.debug(f"expire_old_signals not implemented for tf={timeframe}, skipping")

    symbols       = _load_symbols(timeframe, mode)
    total         = len(symbols)
    scanned       = 0
    failed        = 0
    signals_found = 0
    batch_start   = time.time()
    last_closed   = get_last_closed_bar(timeframe)

    log.info(f"batch start | tf={timeframe} mode={mode} dry_run={dry_run} "
             f"symbols={total} last_closed={last_closed}")

    for symbol in symbols:

        # MAX_BATCH_TIME guard
        if time.time() - batch_start > MAX_BATCH_TIME_SEC:
            log.warning(f"MAX_BATCH_TIME reached ({MAX_BATCH_TIME_SEC}s) — stopping gracefully")
            break

        symbol_start = time.time()

        try:
            # ── 1. Fetch ──────────────────────────────────────────────────────
            df_fresh = get_ohlcv(symbol, timeframe)

            if df_fresh.empty:
                raise NoDataError(f"{symbol}: no data fetched (empty DataFrame)")

            # ── 2. Soft delisting check ───────────────────────────────────────
            soft_cutoff = last_closed - relativedelta(months=3)

            if df_fresh.index[-1].date() < soft_cutoff:
                reason = (f"soft delisted: last_bar={df_fresh.index[-1].date()} "
                          f"< cutoff={soft_cutoff}")
                log.warning(f"{symbol} {reason}, setting is_active=0")
                if not dry_run:
                    _set_inactive(timeframe, symbol, reason)
                continue

            # ── 3. Cache merge ────────────────────────────────────────────────
            try:
                write_cache(symbol, timeframe, df_fresh)
                df = read_cache(symbol, timeframe)
            except DataIncompleteError as e:
                log.warning(f"{symbol} cache gap: {e} — using fresh data only")
                df = df_fresh

            if df is None:
                cache_path = cm.CACHE_DIR / f"{symbol}_{timeframe}.parquet"
                raise RuntimeError(
                    f"{symbol}: cache write succeeded but read returned None "
                    f"(path={cache_path}, exists={cache_path.exists()})"
                )

            # ── 4. Pre-filter ─────────────────────────────────────────────────
            if not passes_filter(df, symbol):
                log.debug(f"{symbol} filtered out by pre_filter")
                if not dry_run:
                    _mark_scanned(timeframe, symbol)
                scanned += 1
                continue

            # ── 5. Plugin analysis ────────────────────────────────────────────
            results = run_all(df, symbol, timeframe)

            # ── 6. Write signals ──────────────────────────────────────────────
            for result in results:
                if not dry_run:
                    inserted = write_signal(symbol, result, timeframe)
                    if inserted:
                        signals_found += 1
                else:
                    log.info(f"[DRY-RUN] {symbol} {result['indicator']} "
                             f"{result['signal']} gap={result['meta'].get('gap_bottom')}"
                             f"-{result['meta'].get('gap_top')}")

            if dry_run and results:
                signals_found += len(results)

            # ── 7. Mark scanned ───────────────────────────────────────────────
            if not dry_run:
                _mark_scanned(timeframe, symbol)
            scanned += 1

            latency = time.time() - symbol_start
            log.info(f"{symbol} done ({latency:.2f}s)")

        except NoDataError as e:
            retry_count = _get_retry_count(timeframe, symbol) + 1
            log.error(f"{symbol} NoDataError retry={retry_count}: {e}")
            if not dry_run:
                if retry_count >= 3:
                    _set_inactive(timeframe, symbol, str(e))
                    log.warning(f"{symbol} hard delisted: setting is_active=0")
                else:
                    _mark_failed(timeframe, symbol, str(e))
            failed += 1

        except DataProviderError as e:
            log.warning(f"{symbol} DataProviderError: {e}")
            if not dry_run:
                _mark_failed(timeframe, symbol, str(e))
            failed += 1

        except Exception as e:
            log.exception(f"{symbol} unexpected error: {type(e).__name__}: {e}")
            if not dry_run:
                _mark_failed(timeframe, symbol, f"{type(e).__name__}: {e}")
            failed += 1

    duration = time.time() - batch_start
    log.info(
        f"batch done | scanned={scanned} failed={failed} "
        f"signals={signals_found} duration={duration:.1f}s"
    )

    return {
        "total":         total,
        "scanned":       scanned,
        "failed":        failed,
        "signals_found": signals_found,
        "duration_sec":  duration,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Japan Stock Scanner")
    parser.add_argument("--timeframe",    default="1MO",
                        choices=["1MO", "1WK", "1D"])
    parser.add_argument("--resume",       action="store_true",
                        help="Chỉ quét PENDING (tiếp tục batch dang dở)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Force retry tất cả FAILED")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Chạy full pipeline nhưng không ghi DB, không gửi Telegram")
    args = parser.parse_args()

    timeframe = args.timeframe
    dry_run   = args.dry_run

    if args.resume and args.retry_failed:
        print("ERROR: --resume và --retry-failed không dùng cùng nhau")
        sys.exit(1)

    mode = "normal"
    if args.resume:
        mode = "resume"
    elif args.retry_failed:
        mode = "retry-failed"

    log = setup_logging(timeframe)
    run_scan(timeframe, mode, dry_run)


if __name__ == "__main__":
    main()
