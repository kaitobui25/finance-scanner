import logging
import pandas as pd

from core.config import MIN_PRICE_JPY, MIN_TURNOVER_JPY, MAX_INACTIVE_BARS

log = logging.getLogger("pre_filter")


def passes_filter(df: pd.DataFrame, symbol: str = "") -> bool:
    """
    Lọc sơ bộ trước khi chạy indicator — loại mã rác, thanh khoản thấp, inactive.

    Input:
        df: DataFrame với DatetimeIndex tz-aware (contract chuẩn)
            columns: open, high, low, close, volume

    Steps:
        1. dropna(close, volume)        — NaN guard trước
        2. len < 12                     — không đủ data để tính TB
        3. close.tail(12).mean() < MIN_PRICE_JPY
        4. (close * volume).tail(12).median() < MIN_TURNOVER_JPY  — median chống outlier
        5. volume.tail(MAX_INACTIVE_BARS).eq(0).all() — inactive

    Returns:
        True  → mã hợp lệ, tiếp tục scan
        False → loại bỏ
    """
    # Step 1: NaN guard
    df = df.dropna(subset=["close", "volume"])

    # Step 2: không đủ data
    if len(df) < 12:
        log.debug(f"{symbol} filtered: insufficient bars ({len(df)} < 12)")
        return False

    # Slice một lần, tái sử dụng
    last_12 = df.tail(12)
    last_n  = df.tail(MAX_INACTIVE_BARS)

    # Step 3: giá quá thấp
    avg_price = last_12["close"].mean()
    if avg_price < MIN_PRICE_JPY:
        log.debug(f"{symbol} filtered: avg_price={avg_price:.1f} < {MIN_PRICE_JPY}")
        return False

    # Step 4: thanh khoản quá mỏng — median chống outlier spike 1 tháng
    median_turnover = (last_12["close"] * last_12["volume"]).median()
    if median_turnover < MIN_TURNOVER_JPY:
        log.debug(
            f"{symbol} filtered: median_turnover={median_turnover:.0f} < {MIN_TURNOVER_JPY}"
        )
        return False

    # Step 5: inactive — volume = 0 toàn bộ N bar gần nhất
    # v2: tighten thành mean < threshold nếu cần
    if last_n["volume"].eq(0).all():
        log.debug(f"{symbol} filtered: inactive (volume=0 for last {MAX_INACTIVE_BARS} bars)")
        return False

    return True
