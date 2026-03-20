import pytest
import pandas as pd
from datetime import date
from data_provider.yahoo import get_ohlcv, is_no_data_error
from data_provider.base import NoDataError, DataIncompleteError
from core.config import get_last_closed_bar

def test_yahoo_get_ohlcv_real_data():
    """
    Test 4.7: Test với 7203.T thực -> verify DataFrame shape, dates, columns
    """
    symbol = "7203.T"
    timeframe = "1MO"
    
    try:
        df = get_ohlcv(symbol, timeframe)
    except DataIncompleteError as e:
        # Trong một số ngày đầu tháng, bar trước đó có thể chưa đóng hoàn toàn (hoặc thiếu volume).
        # Ta catch DataIncompleteError để test không fail cứng nếu Yahoo chưa kịp update bar
        pytest.skip(f"Yahoo data incomplete for {symbol} right now: {e}")
        return
        
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    
    # Kiểm tra required columns
    expected_cols = {"open", "high", "low", "close", "volume"}
    assert expected_cols.issubset(set(df.columns))
    
    # Kiểm tra index timezone và loại
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert str(df.index.tz) == "Asia/Tokyo"
    
    # Kiểm tra completeness (bar cuối cùng phải khớp với get_last_closed_bar)
    expected_last_bar = get_last_closed_bar(timeframe)
    assert df.index[-1].date() == expected_last_bar

def test_is_no_data_error():
    assert is_no_data_error(None, None) == True
    assert is_no_data_error(None, pd.DataFrame()) == True
    
    # Missing required cols
    df_missing = pd.DataFrame(columns=["open", "close"])
    assert is_no_data_error(None, df_missing) == True
    
    # Exception string match
    assert is_no_data_error(Exception("No data found for this symbol"), None) == True
