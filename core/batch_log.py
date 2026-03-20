import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from core.config import TZ_MARKET
from core.signal_writer import DB_PATH

log = logging.getLogger("batch_log")

VALID_TIMEFRAMES = {"1MO", "1WK", "1D"}


def _validate_timeframe(timeframe: str) -> None:
    if timeframe not in VALID_TIMEFRAMES:
        raise ValueError(f"Invalid timeframe: {timeframe!r}. Use: {VALID_TIMEFRAMES}")


# ── DB helper ─────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Write ─────────────────────────────────────────────────────────────────────

def log_batch_run(timeframe: str, stats: dict) -> int:
    """
    Ghi kết quả batch vào batch_runs_{timeframe}.

    Args:
        timeframe: "1MO" | "1WK" | "1D"
        stats: dict từ scanner.run_scan() —
               keys: total_symbols, scanned, failed, signals_found, duration_sec

    Returns:
        run_id (INTEGER PK) của row vừa insert
    """
    _validate_timeframe(timeframe)
    tf       = timeframe
    run_date = datetime.now(ZoneInfo(TZ_MARKET)).date().isoformat()

    with _get_conn() as conn:
        cur = conn.execute(
            f"""INSERT INTO batch_runs_{tf}
                (run_date, total_symbols, scanned, failed, signals_found, duration_sec)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (
                run_date,
                stats.get("total_symbols", 0),
                stats.get("scanned",        0),
                stats.get("failed",         0),
                stats.get("signals_found",  0),
                round(stats.get("duration_sec", 0.0), 2),
            ),
        )
        conn.commit()
        run_id = cur.lastrowid

    log.info(
        f"batch_run logged: id={run_id} tf={tf} date={run_date} "
        f"scanned={stats.get('scanned')} failed={stats.get('failed')} "
        f"signals={stats.get('signals_found')} "
        f"duration={stats.get('duration_sec', 0):.1f}s"
    )
    return run_id


# ── Export ────────────────────────────────────────────────────────────────────

def export_json(timeframe: str, run_id: Optional[int] = None) -> str:
    """
    Export kết quả batch ra JSON string cho AI agent đọc.

    Nếu run_id=None → export batch run mới nhất.

    Returns:
        JSON string với keys:
            run_id, timeframe, run_date,
            total_symbols, scanned, failed, signals_found, duration_sec,
            exported_at
    """
    _validate_timeframe(timeframe)
    tf = timeframe
    with _get_conn() as conn:
        if run_id is not None:
            row = conn.execute(
                f"SELECT * FROM batch_runs_{tf} WHERE id = ?",
                (run_id,),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT * FROM batch_runs_{tf} ORDER BY id DESC LIMIT 1"
            ).fetchone()

    if row is None:
        target = f"id={run_id}" if run_id is not None else "latest"
        log.warning(f"export_json: no batch_run found ({target}) tf={tf}")
        return json.dumps({"error": "no_batch_run", "timeframe": tf, "run_id": run_id})

    payload = {
        "run_id":         row["id"],
        "timeframe":      tf,
        "run_date":       row["run_date"],
        "total_symbols":  row["total_symbols"],
        "scanned":        row["scanned"],
        "failed":         row["failed"],
        "signals_found":  row["signals_found"],
        "duration_sec":   row["duration_sec"],
        "exported_at":    datetime.now(timezone.utc).isoformat(),
    }

    return json.dumps(payload, ensure_ascii=False, indent=2)
