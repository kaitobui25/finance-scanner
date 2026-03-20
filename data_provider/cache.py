import logging
import os
import pandas as pd
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from data_provider.base import DataIncompleteError
from core.config import CACHE_MERGE_WINDOW, MAX_DATE_GAP, TZ_MARKET

CACHE_DIR = Path("cache")

log = logging.getLogger("cache")


def _cache_path(symbol: str, timeframe: str) -> Path:
    return CACHE_DIR / f"{symbol}_{timeframe}.parquet"


def _to_market_dates(series: pd.Series) -> pd.Series:
    """
    Convert một Series datetime (tz-aware hoặc tz-naive) về date object
    theo TZ_MARKET (Asia/Tokyo).

    - tz-aware  → tz_convert(TZ_MARKET) trước, rồi strip tz
    - tz-naive  → assume TZ_MARKET, tz_localize rồi strip
    """
    dates = pd.to_datetime(series)
    if dates.dt.tz is None:
        dates = dates.dt.tz_localize(TZ_MARKET)
    else:
        dates = dates.dt.tz_convert(TZ_MARKET)
    return dates.dt.tz_localize(None).dt.date


def _normalize_dates(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Normalize cột 'date' (date object) về anchor date của timeframe:
      1MO → YYYY-MM-01
      1WK → thứ Hai của tuần (ISO weekday=0)
      1D  → YYYY-MM-DD (giữ nguyên)
    Internal only.
    """
    df = df.copy()
    dates = pd.to_datetime(df["date"])

    if timeframe == "1MO":
        df["date"] = dates.values.astype("datetime64[M]").astype("datetime64[D]")
    elif timeframe == "1WK":
        df["date"] = dates - pd.to_timedelta(dates.dt.dayofweek, unit="D")
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    else:  # 1D
        df["date"] = pd.to_datetime(dates).dt.normalize()

    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def _check_gaps(df: pd.DataFrame, timeframe: str) -> None:
    """
    Kiểm tra continuity sau merge — calendar-aware.

    1MO / 1WK: so sánh với expected date_range → chính xác 100%
    1D:        fixed threshold 7 ngày (tránh false positive Golden Week v1)
               v2: thêm japanese_holiday_calendar

    Raises:
        DataIncompleteError: nếu phát hiện gap
    """
    if len(df) < 2:
        return

    dates = pd.to_datetime(df["date"]).sort_values().reset_index(drop=True)

    if timeframe == "1MO":
        expected = pd.date_range(start=dates.iloc[0], end=dates.iloc[-1], freq="MS")
        missing = expected.difference(dates)
        if not missing.empty:
            raise DataIncompleteError(
                f"Data gap detected (1MO): missing months "
                f"{[d.strftime('%Y-%m') for d in missing[:3]]}"
                f"{'...' if len(missing) > 3 else ''}"
            )

    elif timeframe == "1WK":
        expected = pd.date_range(start=dates.iloc[0], end=dates.iloc[-1], freq="W-MON")
        missing = expected.difference(dates)
        if not missing.empty:
            raise DataIncompleteError(
                f"Data gap detected (1WK): missing weeks "
                f"{[d.strftime('%Y-%m-%d') for d in missing[:3]]}"
                f"{'...' if len(missing) > 3 else ''}"
            )

    else:  # 1D — fixed threshold, v2 upgrade với holiday calendar
        diffs = dates.diff().dropna()
        max_gap = timedelta(days=MAX_DATE_GAP[timeframe])  # default 4, đủ cho weekend
        bad = diffs[diffs > max_gap]
        if not bad.empty:
            idx = bad.index[0]
            raise DataIncompleteError(
                f"Data gap detected (1D): "
                f"{dates[idx - 1].date()} → {dates[idx].date()} "
                f"({bad.iloc[0].days} days)"
            )


def _extract_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Chuẩn hóa input từ contract mới (DatetimeIndex tz-aware)
    hoặc cũ (date column) về internal format: date column (date object).
    Xử lý đúng timezone — luôn convert về TZ_MARKET trước khi lấy date.
    """
    df = df.copy()
    if "date" not in df.columns:
        # Contract mới: DatetimeIndex → reset thành column
        df = df.reset_index()
        idx_col = df.columns[0]
        df = df.rename(columns={idx_col: "date"})
    df["date"] = _to_market_dates(df["date"])
    return df


def _to_tz_aware_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert internal format (date column, tz-naive) → DatetimeIndex tz-aware (TZ_MARKET).
    Safe: handle cả tz-aware và tz-naive input, không crash với NaT.
    """
    df = df.copy()
    index = pd.to_datetime(df["date"])
    if index.dt.tz is None:
        index = index.dt.tz_localize(TZ_MARKET)
    else:
        index = index.dt.tz_convert(TZ_MARKET)
    df = df.drop(columns=["date"])
    df.index = index
    df.index.name = "date"
    return df.sort_index()


def read_cache(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """
    Đọc cache từ parquet.

    Returns:
        DataFrame với DatetimeIndex tz-aware (TZ_MARKET), sorted ascending.
        None nếu file chưa tồn tại hoặc file bị corrupt.

    Notes:
        Nếu file parquet bị corrupt (ArrowInvalid, OSError) — ví dụ do process
        bị kill giữa lúc ghi — file sẽ bị xóa và hàm trả None.
        Scanner sẽ fallback về df_fresh từ Yahoo và rebuild cache từ đầu.
        Không raise exception để 1 file corrupt không làm crash cả batch.
    """
    path = _cache_path(symbol, timeframe)
    if not path.exists():
        return None

    try:
        df = pd.read_parquet(path)
    except Exception as e:
        # Bắt tất cả exception từ parquet (ArrowInvalid, OSError, thư viện khác)
        # vì pyarrow / fastparquet có thể raise các type khác nhau tùy phiên bản
        log.warning(
            f"{symbol} corrupt cache ({type(e).__name__}: {e}) "
            f"— deleting {path.name} and rebuilding from Yahoo"
        )
        path.unlink(missing_ok=True)
        return None

    df["date"] = pd.to_datetime(df["date"]).dt.date
    return _to_tz_aware_index(df)


def write_cache(symbol: str, timeframe: str, df_new: pd.DataFrame) -> None:
    """
    Merge df_new vào cache hiện có và ghi lại (atomic write).

    Input:
        df_new: DataFrame với DatetimeIndex tz-aware (contract mới)
                hoặc date column (backward compat)

    Merge rules:
    - Bar trong window (CACHE_MERGE_WINDOW[timeframe] bar gần nhất) → re-syncable
    - Bar cũ hơn window → immutable (không ghi đè)
    - Normalize date theo timeframe
    - drop_duplicates + sort
    - Atomic write: .tmp → os.replace()

    Raises:
        DataIncompleteError: nếu có gap sau merge
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol, timeframe)
    path_tmp = Path(str(path) + ".tmp")

    # Flatten về internal format (date column, tz-naive date object)
    df_new = _extract_date_column(df_new)
    df_new = _normalize_dates(df_new, timeframe)

    # Đọc existing — internal format
    existing_raw = None
    if path.exists():
        try:
            existing_raw = pd.read_parquet(path)
            existing_raw["date"] = pd.to_datetime(existing_raw["date"]).dt.date
        except Exception as e:
            log.warning(
                f"{symbol} corrupt cache during write ({type(e).__name__}: {e}) "
                f"— deleting and rebuilding cache"
            )
            path.unlink(missing_ok=True)
            existing_raw = None

    if existing_raw is None:
        merged = df_new
    else:
        window = CACHE_MERGE_WINDOW[timeframe]
        existing_sorted = existing_raw.sort_values("date")

        if len(existing_sorted) > window:
            cutoff_date = existing_sorted.iloc[-(window + 1)]["date"]
            old_part = existing_sorted[existing_sorted["date"] <= cutoff_date]
            new_part = df_new[df_new["date"] > cutoff_date]
            merged = pd.concat([old_part, new_part], ignore_index=True)
        else:
            merged = df_new

    # Normalize + deduplicate + sort
    merged = _normalize_dates(merged, timeframe)
    merged = (merged
              .drop_duplicates(subset=["date"])
              .sort_values("date")
              .reset_index(drop=True))

    # Gap check — calendar-aware
    _check_gaps(merged, timeframe)

    # Atomic write — cleanup .tmp nếu lỗi giữa chừng
    try:
        merged.to_parquet(path_tmp, index=False)
        os.replace(path_tmp, path)
    except Exception:
        path_tmp.unlink(missing_ok=True)
        raise