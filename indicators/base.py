import pandas as pd
from typing import Any, Dict, Optional
from typing_extensions import TypedDict


class IndicatorResult(TypedDict):
    indicator : str            # "IMFVG"
    version   : str            # "1.1"
    signal    : Optional[str]  # "BULLISH" | "BEARISH" | None
    meta      : Dict[str, Any] # gap_top, gap_bottom, close_price


def analyze(
    df        : pd.DataFrame,
    symbol    : str,
    timeframe : str = "1MO",
) -> IndicatorResult:
    """
    Contract mọi plugin phải implement.

    Args:
        df:        DataFrame với DatetimeIndex tz-aware (TZ_MARKET)
                   columns: open, high, low, close, volume
                   sorted ascending
                   Index mapping (latest last, sorted ascending):
                     df.iloc[-1] = current bar
                     df.iloc[-2] = previous bar (1 bar ago)
                     df.iloc[-3] = 2 bars ago
                     df.iloc[-4] = 3 bars ago   ← cần ít nhất 4 bar
        symbol:    mã chứng khoán, ví dụ "7203.T"
        timeframe: "1MO" | "1WK" | "1D"

    Returns:
        IndicatorResult với:
            signal = "BULLISH" | "BEARISH" | None
            meta   = {} nếu signal là None

    Notes:
        - Plugin phải guard len(df) < 4 → return signal=None
        - Plugin không được raise — catch internally nếu cần
    """
    ...
