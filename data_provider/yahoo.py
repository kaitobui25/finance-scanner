import time
import logging
import pandas as pd
import yfinance as yf

from core.config import (
    TZ_MARKET, TIMEFRAMES, MAX_RETRY_TIME_SEC,
    get_last_closed_bar,
)
from data_provider.base import DataIncompleteError, NoDataError

log = logging.getLogger("yahoo")

REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}

# Số năm lịch sử fetch theo timeframe — đủ dùng, không "max"
HISTORY_PERIOD = {
    "1MO": "10y",
    "1WK": "5y",
    "1D":  "2y",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_no_data_error(e: Exception | None, df) -> bool:
    """
    Normalize mọi case "no data" từ Yahoo:
    - df là None / rỗng / thiếu required columns
    - exception message chứa "no data"
    """
    if df is None:
        return True
    if hasattr(df, "empty") and df.empty:
        return True
    if hasattr(df, "columns"):
        cols = {c.lower() for c in df.columns}
        if not REQUIRED_COLUMNS.issubset(cols):
            return True
    if e is not None:
        msg = str(e).lower()
        if "no data found" in msg or "no data" in msg or "no timezone" in msg:
            return True
    return False


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Chuẩn hóa DataFrame từ yfinance:
    - flatten MultiIndex columns (yfinance v0.2+)
    - lowercase columns
    - guard: raise NoDataError nếu thiếu required columns
    - tz: tz-naive → assume UTC → convert TZ_MARKET
           tz-aware → convert TZ_MARKET
    - drop duplicate index
    - dropna OHLCV
    - sort ascending
    - chỉ giữ 5 cột OHLCV
    """
    # Flatten MultiIndex columns: ("Close", "7203.T") → "Close"
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.columns = [c.lower() for c in df.columns]

    # Guard: phải có đủ 5 cột trước khi tiếp tục
    cols = set(df.columns)
    if not REQUIRED_COLUMNS.issubset(cols):
        raise NoDataError(
            f"Missing required OHLCV columns. "
            f"Got: {sorted(cols)}, need: {sorted(REQUIRED_COLUMNS)}"
        )

    df = df[list(REQUIRED_COLUMNS)].copy()

    # Timezone: Yahoo trả UTC (tz-naive hoặc UTC tz-aware)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    if df.index.tz is None:
        # tz-naive từ Yahoo → assume UTC trước, rồi convert
        df.index = df.index.tz_localize("UTC").tz_convert(TZ_MARKET)
    else:
        df.index = df.index.tz_convert(TZ_MARKET)

    df.index.name = "date"

    # Drop duplicate index (Yahoo đôi khi trả duplicate bar)
    df = df[~df.index.duplicated()]

    # Drop NaN (Yahoo đôi khi trả NaN ở đầu hoặc giữa series)
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])

    return df.sort_index()


# ── Main fetch ────────────────────────────────────────────────────────────────

def get_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Fetch OHLCV từ Yahoo Finance với retry + exponential backoff.

    Args:
        symbol:    mã chứng khoán, ví dụ "7203.T"
        timeframe: "1MO" | "1WK" | "1D"

    Returns:
        DataFrame với DatetimeIndex tz-aware (TZ_MARKET), sorted ascending.
        columns: open, high, low, close, volume

    Raises:
        NoDataError:          symbol không có data (delisted, sai mã...)
        DataIncompleteError:  data chưa đủ / bar cuối chưa đúng / stale
    """
    interval = TIMEFRAMES.get(timeframe)
    if interval is None:
        raise ValueError(f"Timeframe không hỗ trợ: {timeframe!r}")

    period   = HISTORY_PERIOD[timeframe]
    last_exc: Exception | None = None
    no_data_count = 0
    attempt  = 0
    t_start  = time.time()
    base_wait = 2.0

    while True:
        elapsed   = time.time() - t_start
        remaining = MAX_RETRY_TIME_SEC - elapsed

        if attempt > 0:
            wait = min(base_wait * (2 ** (attempt - 1)), max(remaining, 0))
            if wait <= 0:
                break
            log.warning(
                f"{symbol} retry={attempt} waiting {wait:.1f}s "
                f"(elapsed {elapsed:.1f}s)"
            )
            time.sleep(wait)

        if time.time() - t_start >= MAX_RETRY_TIME_SEC:
            break

        attempt += 1
        df  = None
        exc = None          # ← scope per-attempt, không leak state
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=True)
        except Exception as e:
            exc      = e
            last_exc = e
            log.warning(f"{symbol} fetch error attempt={attempt}: {e}")

        if is_no_data_error(exc, df):
            no_data_count += 1
            log.warning(
                f"{symbol} no_data attempt={attempt} (count={no_data_count})"
            )
            if no_data_count >= 3:
                raise NoDataError(
                    f"{symbol}: no data after {no_data_count} attempts"
                )
            continue

        # Normalize — có thể raise NoDataError nếu thiếu columns
        try:
            df = _normalize_df(df)
        except NoDataError:
            raise
        except Exception as e:
            last_exc = e
            log.warning(f"{symbol} normalize error attempt={attempt}: {e}")
            continue

        # Drop bar chưa đóng (current open bar) — Yahoo luôn include bar đang chạy
        # Dùng Timestamp tz-aware để so sánh, không dùng .index.date (phá tz contract)
        # cutoff = end-of-day của expected_last để không drop bar hợp lệ
        # (Yahoo trả bar tháng 2 lúc 09:00 JST, không phải 00:00 JST)
        expected_last = get_last_closed_bar(timeframe)
        cutoff = pd.Timestamp(expected_last, tz=TZ_MARKET) + pd.offsets.Day(1) - pd.Timedelta(seconds=1)
        df = df[df.index <= cutoff]

        if df.empty:
            raise DataIncompleteError(
                f"{symbol}: no bars remaining after dropping future bars "
                f"(expected last={expected_last})"
            )

        # Completeness check: bar cuối phải khớp expected
        actual_last = df.index[-1].normalize().date()
        if actual_last != expected_last:
            raise DataIncompleteError(
                f"{symbol}: last bar {actual_last} != expected {expected_last}"
            )

        # Freshness: volume > 0
        if df.iloc[-1]["volume"] <= 0:
            raise DataIncompleteError(
                f"{symbol}: last bar volume=0 (stale data)"
            )

        # Freshness: OHLC không identical với bar trước
        if len(df) >= 2:
            last = df.iloc[-1][["open", "high", "low", "close"]]
            prev = df.iloc[-2][["open", "high", "low", "close"]]
            if last.equals(prev):
                raise DataIncompleteError(
                    f"{symbol}: last bar OHLC identical to previous "
                    f"(Yahoo not yet updated)"
                )

        log.info(f"{symbol} fetched ok ({time.time() - t_start:.2f}s)")
        return df

    # Hết retry
    if last_exc is not None:
        raise last_exc
    raise NoDataError(f"{symbol}: exhausted retries ({attempt} attempts)")