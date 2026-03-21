#!/usr/bin/env python3
"""
position_monitor.py — CLI entry point cho IMFVG Position Monitor.

Usage:
    python position_monitor.py --full-scan
    python position_monitor.py --normal
    python position_monitor.py --report
    python position_monitor.py --full-scan --dry-run
    python position_monitor.py --normal   --timeframe 1MO --strategy my_strat
"""

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.config import TZ_MARKET, TELEGRAM_TOKEN, CHAT_ID
from core.position_tracker import (
    PositionConfig,
    PositionState,
    SignalFn,
    _get_db_conn,
    _get_holding_position,
    _process_symbol,
    _resolve_strategy_name,
    check_latest_bar,
    init_positions_db,
    make_imfvg_detector,
    scan_full_history,
)
from data_provider.cache import read_cache

SYMBOLS_CSV = Path("data/symbols.csv")
LOGS_DIR    = Path("logs")

# Guard cho SQL timeframe injection
_VALID_TIMEFRAMES = frozenset({"1MO", "1WK", "1D"})


def _assert_valid_timeframe(tf: str) -> None:
    """Fail fast nếu timeframe không hợp lệ — ngăn SQL injection qua tf."""
    if tf not in _VALID_TIMEFRAMES:
        raise ValueError(f"Invalid timeframe: {tf!r}. Expected: {_VALID_TIMEFRAMES}")


# ══════════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════════

class _JSTFormatter(logging.Formatter):
    """Timestamp luôn JST, bất kể server timezone."""
    _tz = ZoneInfo(TZ_MARKET)

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=self._tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def setup_logging(timeframe: str) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    now_jst  = datetime.now(ZoneInfo(TZ_MARKET))
    log_file = LOGS_DIR / f"position_monitor_{timeframe}_{now_jst.strftime('%Y%m')}.log"

    fmt       = "[%(asctime)s JST] %(levelname)-5s %(name)-18s %(message)s"
    datefmt   = "%Y-%m-%d %H:%M:%S"
    formatter = _JSTFormatter(fmt, datefmt=datefmt)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Xóa handler cũ cùng type để tránh duplicate khi gọi lại (test, reload)
    root.handlers = [h for h in root.handlers
                     if not isinstance(h, (logging.FileHandler, logging.StreamHandler))]
    root.addHandler(fh)
    root.addHandler(sh)

    return logging.getLogger("position_monitor")


# ══════════════════════════════════════════════════════════════════════════════
# Args
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IMFVG Position Monitor")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full-scan", action="store_true",
                      help="Scan toàn bộ lịch sử cache, ghi HOLDING vào DB")
    mode.add_argument("--normal",    action="store_true",
                      help="Check bar mới nhất cho tất cả HOLDING positions")
    mode.add_argument("--report",    action="store_true",
                      help="In HOLDING list ra stdout")

    p.add_argument("--timeframe", default="1MO", choices=["1MO","1WK","1D"])
    p.add_argument("--dry-run",   action="store_true",
                   help="Không ghi DB, không gửi Telegram")
    p.add_argument("--strategy",  default=None,
                   help="Override strategy name (VD: imfvg_fw0.3)")
    return p.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_symbols() -> list[str]:
    """Đọc danh sách symbols từ CSV."""
    if not SYMBOLS_CSV.exists():
        return []
    symbols = []
    with open(SYMBOLS_CSV) as f:
        for line in f:
            s = line.strip()
            if s:
                symbols.append(s)
    return symbols


def _get_all_holding(conn: sqlite3.Connection, tf: str) -> list[dict]:
    """Lấy tất cả HOLDING positions."""
    rows = conn.execute(
        f"SELECT * FROM positions_{tf} WHERE status = 'HOLDING' ORDER BY symbol"
    ).fetchall()
    return [dict(r) for r in rows]


def _format_price(price) -> str:
    if price is None:
        return "N/A"
    try:
        return f"{float(price):,.0f}"
    except (ValueError, TypeError):
        return str(price)


# ══════════════════════════════════════════════════════════════════════════════
# T35 — run_full_scan
# ══════════════════════════════════════════════════════════════════════════════

def run_full_scan(
    timeframe:     str,
    cfg:           PositionConfig,
    dry_run:       bool = False,
    signal_fn:     SignalFn | None = None,
    strategy_name: str | None = None,
) -> dict:
    """
    Scan toàn bộ lịch sử cache cho mỗi symbol.
    Ghi HOLDING position vào DB nếu có signal.

    Returns:
        dict với stats: total, scanned, with_signal, failed
    """
    log = logging.getLogger("position_monitor")

    if signal_fn is None:
        signal_fn = make_imfvg_detector(cfg)
    name = _resolve_strategy_name(signal_fn, strategy_name)

    _assert_valid_timeframe(timeframe)
    symbols = _load_symbols()
    conn    = _get_db_conn()
    init_positions_db(timeframe, conn)

    total       = len(symbols)
    scanned     = 0
    with_signal = 0
    failed      = 0
    t_start     = time.time()

    log.info(
        "full_scan start | tf=%s strategy=%s symbols=%d dry_run=%s",
        timeframe, name, total, dry_run,
    )

    for symbol in symbols:
        t_sym = time.time()
        try:
            df = read_cache(symbol, timeframe)
            if df is None or df.empty:
                log.debug("%s no cache — skip", symbol)
                scanned += 1
                continue

            result = scan_full_history(
                df, cfg,
                signal_fn     = signal_fn,
                strategy_name = name,
            )

            if result is None:
                scanned += 1
                continue

            state    = result
            bar_date = state.last_checked_bar_date or ""

            if state.is_holding:
                with_signal += 1
                log.info(
                    "%s HOLDING %s entry=%.2f tp=%.2f sl=%.2f strategy=%s (%.2fs)",
                    symbol, state.direction, state.entry_close or 0,
                    state.tp_level or 0, state.sl_level or 0,
                    name, time.time() - t_sym,
                )
                if not dry_run:
                    _process_symbol(conn, timeframe, symbol, state, bar_date, name)
            else:
                log.debug("%s no signal (%.2fs)", symbol, time.time() - t_sym)

            scanned += 1

        except Exception as e:
            log.error("%s full_scan error: %s: %s", symbol, type(e).__name__, e)
            failed += 1

    duration = time.time() - t_start
    log.info(
        "full_scan done | scanned=%d with_signal=%d failed=%d duration=%.1fs",
        scanned, with_signal, failed, duration,
    )
    conn.close()
    return {
        "total":       total,
        "scanned":     scanned,
        "with_signal": with_signal,
        "failed":      failed,
        "duration_sec": duration,
    }


# ══════════════════════════════════════════════════════════════════════════════
# T36 — run_normal
# ══════════════════════════════════════════════════════════════════════════════

def run_normal(
    timeframe:     str,
    cfg:           PositionConfig,
    dry_run:       bool = False,
    signal_fn:     SignalFn | None = None,
    strategy_name: str | None = None,
) -> dict:
    """
    Check bar mới nhất cho tất cả HOLDING positions.
    Update DB: TP/SL/TS exit, TS ratchet, REVERSE, OPEN.

    Returns:
        dict với stats: total_holding, exited, updated, new_opened, failed
    """
    log = logging.getLogger("position_monitor")

    if signal_fn is None:
        signal_fn = make_imfvg_detector(cfg)
    name = _resolve_strategy_name(signal_fn, strategy_name)

    _assert_valid_timeframe(timeframe)
    conn = _get_db_conn()
    init_positions_db(timeframe, conn)

    holdings    = _get_all_holding(conn, timeframe)
    total       = len(holdings)
    exited      = 0
    updated     = 0
    new_opened  = 0
    failed      = 0
    t_start     = time.time()

    log.info(
        "normal start | tf=%s strategy=%s holding=%d dry_run=%s",
        timeframe, name, total, dry_run,
    )

    for pos_row in holdings:
        symbol = pos_row["symbol"]
        t_sym  = time.time()
        try:
            df = read_cache(symbol, timeframe)
            # check_latest_bar guard này rồi nhưng explicit check rõ hơn
            if df is None or df.empty:
                log.debug("%s cache unavailable — skip", symbol)
                continue
            state = check_latest_bar(
                df, pos_row, cfg,
                signal_fn     = signal_fn,
                strategy_name = name,
            )

            bar_date = state.last_checked_bar_date or ""

            # Classify outcome
            if state.close_reason in ("cache_unavailable","no_new_bar","atr_not_ready"):
                log.debug("%s skip: %s", symbol, state.close_reason)
                continue

            if state.close_reason is not None and state.close_reason != "cache_unavailable":
                exited += 1
                log.info(
                    "%s EXIT %s price=%.2f bars=%d (%.2fs)",
                    symbol, state.close_reason,
                    state.close_price_at_exit or 0,
                    state.bars_held,
                    time.time() - t_sym,
                )

            if state.signal_action in ("OPEN", "REVERSE"):
                new_opened += 1
                log.info(
                    "%s %s %s entry=%.2f strategy=%s",
                    symbol, state.signal_action, state.direction,
                    state.entry_close or 0, name,
                )
            elif state.is_holding:
                updated += 1
                log.debug(
                    "%s update bars=%d ts=%.4f (%.2fs)",
                    symbol, state.bars_held,
                    state.trailing_stop or 0,
                    time.time() - t_sym,
                )

            if not dry_run:
                _process_symbol(conn, timeframe, symbol, state, bar_date, name)

        except Exception as e:
            log.error("%s normal error: %s: %s", symbol, type(e).__name__, e)
            failed += 1

    duration = time.time() - t_start
    log.info(
        "normal done | exited=%d updated=%d new_opened=%d failed=%d duration=%.1fs",
        exited, updated, new_opened, failed, duration,
    )
    conn.close()
    return {
        "total_holding": total,
        "exited":        exited,
        "updated":       updated,
        "new_opened":    new_opened,
        "failed":        failed,
        "duration_sec":  duration,
    }


# ══════════════════════════════════════════════════════════════════════════════
# T37 — run_report
# ══════════════════════════════════════════════════════════════════════════════

def run_report(timeframe: str) -> None:
    """In HOLDING positions ra stdout theo format đẹp."""
    _assert_valid_timeframe(timeframe)
    conn     = _get_db_conn()
    holdings = _get_all_holding(conn, timeframe)

    if not holdings:
        print(f"[{timeframe}] No HOLDING positions.")
        return

    print(f"\n[{timeframe}] HOLDING Positions ({len(holdings)} total)")
    print("─" * 80)
    print(f"{'Symbol':<12} {'Dir':<5} {'Entry':>8} {'TP':>8} {'SL':>8} "
          f"{'TS':>8} {'Bars':>5} {'Strategy':<16} {'Entry Date'}")
    print("─" * 80)

    for pos in holdings:
        print(
            f"{pos['symbol']:<12} "
            f"{pos['direction']:<5} "
            f"{_format_price(pos['entry_close']):>8} "
            f"{_format_price(pos['tp_level']):>8} "
            f"{_format_price(pos['sl_level']):>8} "
            f"{_format_price(pos['trailing_stop']):>8} "
            f"{pos['bars_held']:>5} "
            f"{(pos['strategy_name'] or 'N/A'):<16} "
            f"{pos['entry_date']}"
        )

    print("─" * 80)
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# T38 — notify_positions
# ══════════════════════════════════════════════════════════════════════════════

def notify_positions(timeframe: str) -> int:
    """
    Gửi Telegram notification cho HOLDING positions chưa notify.
    Chunk 20 symbols/message để tránh Telegram 4096 char limit.

    Returns:
        Số messages đã gửi thành công.
    """
    import requests

    log = logging.getLogger("position_monitor")

    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.warning("TELEGRAM_TOKEN hoặc CHAT_ID chưa set — skip notify")
        return 0

    _assert_valid_timeframe(timeframe)
    conn     = _get_db_conn()
    holdings = conn.execute(
        f"""SELECT * FROM positions_{timeframe}
             WHERE status = 'HOLDING'
               AND (notified_at IS NULL OR notified_at = '')
             ORDER BY symbol""",
    ).fetchall()
    holdings = [dict(r) for r in holdings]

    if not holdings:
        log.info("notify: no unnotified HOLDING positions")
        conn.close()
        return 0

    api_url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    CHUNK_SIZE = 20      # ~20 rows × ~80 chars << 4096 char Telegram limit
    now_jst    = datetime.now(ZoneInfo(TZ_MARKET))
    date_str   = now_jst.strftime("%Y-%m-%d")

    chunks       = [holdings[i:i+CHUNK_SIZE] for i in range(0, len(holdings), CHUNK_SIZE)]
    total_parts  = len(chunks)
    messages_sent = 0
    notified_ids  = []

    for part_idx, chunk in enumerate(chunks, start=1):
        header = f"[{timeframe} | HOLDING IMFVG — {date_str}]"
        if total_parts > 1:
            header += f"  ({part_idx}/{total_parts})"

        lines = [header]
        if part_idx == 1:
            lines.append(f"Đang theo dõi {len(holdings)} vị thế:\n")
        else:
            lines.append("")

        for pos in chunk:
            lines.append(
                f"{pos['symbol']:<10}| {pos['direction']:<5}| "
                f"Entry: {_format_price(pos['entry_close'])} JPY | "
                f"TP: {_format_price(pos['tp_level'])} | "
                f"SL: {_format_price(pos['sl_level'])}"
            )

        text = "\n".join(lines)
        try:
            resp = requests.post(api_url, json={
                "chat_id":    CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
            }, timeout=10)
            resp.raise_for_status()
            notified_ids.extend(pos["id"] for pos in chunk)
            messages_sent += 1
            log.info("notify: sent chunk %d/%d (%d positions)",
                     part_idx, total_parts, len(chunk))
            if part_idx < total_parts:
                time.sleep(1)   # rate limit
        except Exception as e:
            log.error("notify: chunk %d/%d failed: %s", part_idx, total_parts, e)
            break   # stop; mark only successfully sent chunks

    # Mark notified_at chỉ cho các positions đã gửi thành công
    if notified_ids:
        now_utc      = datetime.now(ZoneInfo("UTC")).isoformat()
        placeholders = ",".join("?" * len(notified_ids))
        conn.execute(
            f"UPDATE positions_{timeframe} SET notified_at = ? "
            f"WHERE id IN ({placeholders})",
            [now_utc] + notified_ids,
        )
        conn.commit()
        log.info("notify: marked %d positions as notified", len(notified_ids))

    conn.close()
    return messages_sent

# ══════════════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args      = parse_args()
    timeframe = args.timeframe
    dry_run   = args.dry_run
    log       = setup_logging(timeframe)

    cfg = PositionConfig()

    # Resolve signal_fn từ --strategy nếu có
    signal_fn     = make_imfvg_detector(cfg)
    strategy_name = args.strategy or _resolve_strategy_name(signal_fn, None)

    if args.full_scan:
        stats = run_full_scan(timeframe, cfg, dry_run, signal_fn, strategy_name)
        if not dry_run and stats["with_signal"] > 0:
            notify_positions(timeframe)

    elif args.normal:
        stats = run_normal(timeframe, cfg, dry_run, signal_fn, strategy_name)
        if not dry_run and (stats["exited"] > 0 or stats["new_opened"] > 0):
            notify_positions(timeframe)

    elif args.report:
        run_report(timeframe)


if __name__ == "__main__":
    main()
