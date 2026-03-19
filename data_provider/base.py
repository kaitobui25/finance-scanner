from typing import Protocol, runtime_checkable
import pandas as pd


# --- Base Exception ---

class DataProviderError(Exception):
    """Base class cho mọi lỗi từ data provider."""
    pass


class NoDataError(DataProviderError):
    """
    Yahoo không có data cho symbol này.
    Ví dụ: delisted, sai mã, DataFrame rỗng.
    """
    pass


class DataIncompleteError(DataProviderError):
    """
    Fetch thành công nhưng data chưa đủ điều kiện để dùng.
    Ví dụ:
    - last bar chưa khớp get_last_closed_bar()
    - volume = 0
    - OHLC identical với bar trước
    - data bị gap sau khi merge
    """
    pass


# --- Protocol ---

@runtime_checkable
class DataProviderProtocol(Protocol):
    def get_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """
        Fetch OHLCV data cho symbol theo timeframe.

        Args:
            symbol:    mã chứng khoán, ví dụ "7203.T"
            timeframe: "1MO" | "1WK" | "1D"

        Returns:
            DataFrame:
                columns: open, high, low, close, volume
                index:   DatetimeIndex (tz-aware, Asia/Tokyo)
                sorted ascending theo date

        Raises:
            NoDataError:          symbol không có data
            DataIncompleteError:  data chưa đủ / chưa update
            Exception:            network / unexpected error
        """
        ...