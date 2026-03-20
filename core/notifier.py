import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import requests

from core.config import TELEGRAM_TOKEN, CHAT_ID
from core.signal_writer import DB_PATH

log = logging.getLogger("notifier")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
CHUNK_SIZE   = 50   # Telegram safe limit: ~50 mã x 30 chars << 4096 chars


# ── DB helper ─────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Query ─────────────────────────────────────────────────────────────────────

def get_unnotified_signals(timeframe: str = "1MO") -> List[dict]:
    """
    Lấy tất cả signal chưa gửi Telegram.

    Returns:
        List[dict] sorted by signal_type, symbol
    """
    tf = timeframe
    with _get_conn() as conn:
        rows = conn.execute(f"""
            SELECT id, symbol, indicator, signal_date, signal_type,
                   gap_top, gap_bottom, close_price
              FROM signals_{tf}
             WHERE notified_at IS NULL
               AND status = 'ACTIVE'
             ORDER BY signal_type, symbol
        """).fetchall()
    return [dict(r) for r in rows]


def _mark_notified(ids: List[int], timeframe: str) -> None:
    """Update notified_at cho danh sách id sau khi gửi thành công."""
    if not ids:
        return
    tf      = timeframe
    now_utc = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" * len(ids))
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE signals_{tf} SET notified_at = ? WHERE id IN ({placeholders})",
            [now_utc] + ids,
        )
        conn.commit()
    log.debug(f"marked notified: {len(ids)} signals tf={tf}")


# ── Format ────────────────────────────────────────────────────────────────────

def _format_price(price) -> str:
    """Format giá JPY với dấu phẩy ngàn."""
    if price is None:
        return "N/A"
    try:
        return f"{int(price):,} JPY"
    except (ValueError, TypeError):
        return str(price)


def format_message(
    signals_chunk : List[dict],
    signal_type   : str,
    signal_date   : str,
    timeframe     : str,
    part          : int,
    total_parts   : int,
    total_signals : int,
) -> str:
    """
    Format 1 chunk thành message Telegram.

    Example:
        [1MO | BULLISH IMFVG — 2026-02-01]  (1/2)
        Tìm thấy 87 tín hiệu:

        7203.T   |  BULLISH  |  3,250 JPY
        6758.T   |  BULLISH  |  12,480 JPY
    """
    header = f"[{timeframe} | {signal_type} IMFVG — {signal_date}]"
    if total_parts > 1:
        header += f"  ({part}/{total_parts})"

    lines = [header]

    # Chỉ hiển thị tổng số ở phần đầu
    if part == 1:
        lines.append(f"Tìm thấy {total_signals} tín hiệu:\n")
    else:
        lines.append("")

    for sig in signals_chunk:
        price_str = _format_price(sig.get("close_price"))
        lines.append(f"{sig['symbol']:<10}|  {signal_type:<8}|  {price_str}")

    return "\n".join(lines)


# ── Send ──────────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    """
    Gửi message qua Telegram Bot API.
    Retry 1 lần nếu gặp lỗi network.

    Returns:
        True nếu gửi thành công, False nếu thất bại
    """
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_TOKEN hoặc CHAT_ID chưa được set trong .env")
        return False

    payload = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }

    for attempt in range(1, 3):   # max 2 lần
        try:
            resp = requests.post(TELEGRAM_API, json=payload, timeout=10)
            resp.raise_for_status()
            log.debug(f"telegram sent ok (attempt={attempt}, chars={len(text)})")
            return True
        except requests.RequestException as e:
            log.warning(f"telegram send failed attempt={attempt}: {e}")
            if attempt < 2:
                time.sleep(3)

    log.error(f"telegram send failed after 2 attempts (chars={len(text)})")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def notify(timeframe: str = "1MO") -> int:
    """
    Query unnotified signals → group by signal_type → chunk → gửi Telegram.

    Edge case: crash sau khi gửi nhưng trước khi update notified_at
    → duplicate message lần sau. v1: accept duplicate (xác suất thấp, không gây hại).

    Returns:
        Số message đã gửi thành công
    """
    # Fail fast — không waste time query DB nếu chưa config
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_TOKEN hoặc CHAT_ID chưa được set trong .env — bỏ qua notify")
        return 0

    signals = get_unnotified_signals(timeframe)

    if not signals:
        log.info(f"notify: no unnotified signals (tf={timeframe})")
        return 0

    # Group by signal_type (đã ORDER BY signal_type, symbol từ query)
    groups: dict[str, List[dict]] = {}
    for sig in signals:
        st = sig["signal_type"]
        groups.setdefault(st, []).append(sig)

    messages_sent = 0

    for signal_type, group in groups.items():
        total_signals = len(group)

        # signal_date từ data — chuẩn hơn get_last_closed_bar() vì reflect DB thực tế
        # 1 group → cùng signal_date (UNIQUE constraint đảm bảo)
        signal_date = group[0]["signal_date"]

        # Chunk 50 mã/message
        chunks = [group[i:i + CHUNK_SIZE] for i in range(0, len(group), CHUNK_SIZE)]
        total_parts = len(chunks)

        chunk_ids_sent: List[int] = []

        for part_idx, chunk in enumerate(chunks, start=1):
            text = format_message(
                signals_chunk = chunk,
                signal_type   = signal_type,
                signal_date   = signal_date,
                timeframe     = timeframe,
                part          = part_idx,
                total_parts   = total_parts,
                total_signals = total_signals,
            )

            ok = send_telegram(text)
            if ok:
                chunk_ids_sent.extend(sig["id"] for sig in chunk)
                messages_sent += 1
                log.info(
                    f"notify sent: {signal_type} ({part_idx}/{total_parts}) "
                    f"{len(chunk)} symbols tf={timeframe}"
                )
                # Rate limit — tránh flood Telegram
                if part_idx < total_parts:
                    time.sleep(1)
            else:
                log.error(
                    f"notify failed: {signal_type} ({part_idx}/{total_parts}) "
                    f"— skipping remaining chunks for this group"
                )
                break   # Chunk fail → dừng group này, không update notified_at

        # Update notified_at chỉ cho các chunk gửi thành công
        # Nếu group chỉ gửi được 1/2 chunk → chỉ mark chunk đó
        # → lần sau retry chunk còn lại (không duplicate chunk đã gửi)
        if chunk_ids_sent:
            _mark_notified(chunk_ids_sent, timeframe)

    log.info(f"notify done: {messages_sent} messages sent tf={timeframe}")
    return messages_sent
