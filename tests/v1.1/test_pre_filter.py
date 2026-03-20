import pytest
import pandas as pd
import numpy as np

from core.pre_filter import passes_filter
from core.config import MIN_PRICE_JPY, MIN_TURNOVER_JPY, MAX_INACTIVE_BARS

def create_base_mock_df(periods=15, close_price=500, volume=100000):
    """
    Tạo DataFrame hợp lệ chuẩn với DatetimeIndex
    """
    dates = pd.date_range(start="2025-01-01", periods=periods, freq="ME", tz="Asia/Tokyo")
    df = pd.DataFrame({
        "open": [close_price] * periods,
        "high": [close_price] * periods,
        "low": [close_price] * periods,
        "close": [close_price] * periods,
        "volume": [volume] * periods,
    }, index=dates)
    return df

def test_pre_filter_valid_stock():
    """
    Kịch bản: Cổ phiếu xịn, đủ 12 nến, giá lớn hơn MIN_PRICE, thanh khoản lớn hơn MIN_TURNOVER, giao dịch đầy đủ
    -> Phải Pass (True)
    """
    # close=500, volume=100_000 -> Turnover = 50tr > 20tr bèo -> PASS
    df = create_base_mock_df(periods=15, close_price=500, volume=100_000)
    assert passes_filter(df, "VALID.T") == True

def test_pre_filter_insufficient_data():
    """
    Kịch bản: Mã mới lên sàn, chưa đủ 12 tháng dữ liệu.
    -> Phải Fail (False)
    """
    df = create_base_mock_df(periods=5)  # 5 nến < 12
    assert passes_filter(df, "NEW.T") == False

def test_pre_filter_penny_stock():
    """
    Kịch bản: Penny stock, giá dưới MIN_PRICE_JPY (100)
    -> Phải Fail (False)
    """
    # close=50 < 100 JPY
    # Để chắc chắn turnover qua ải (dù logic test tuần tự giá trị giá trước, nhưng cứ set cao)
    df = create_base_mock_df(periods=15, close_price=50, volume=100_000_000)
    assert passes_filter(df, "PENNY.T") == False

def test_pre_filter_thin_turnover():
    """
    Kịch bản: Thanh khoản yếu, median_turnover < MIN_TURNOVER_JPY (20_000_000 JPY)
    -> Phải Fail (False)
    """
    # Giá = 1000 (> 100), nhưng volume = 5000 -> turnover = 5_000_000 < 20tr
    df = create_base_mock_df(periods=15, close_price=1000, volume=5000)
    assert passes_filter(df, "THIN.T") == False

def test_pre_filter_inactive_symbol():
    """
    Kịch bản: Chết thanh khoản ở MAX_INACTIVE_BARS (6 nến gần nhất volume = 0)
    -> Phải Fail (False)
    """
    df = create_base_mock_df(periods=15, close_price=500, volume=100_000)
    # Gán 6 giá trị cuối bằng 0
    df.iloc[-MAX_INACTIVE_BARS:, df.columns.get_loc('volume')] = 0
    
    assert passes_filter(df, "DEAD.T") == False

def test_pre_filter_nan_guard():
    """
    Kịch bản: Source API lỗi quăng NaN cho các nến gần nhất
    -> Nếu drop NaN xong không đủ 12 nến -> Fail (False)
    -> Nếu vẫn đủ 12 nến -> Check logic tiếp -> có thể Pass
    """
    df = create_base_mock_df(periods=14, close_price=500, volume=100_000)
    
    # Cho 3 dòng cuối bị NaN ở giá đóng cửa
    df.iloc[-3:, df.columns.get_loc('close')] = np.nan
    
    # Vậy df thực chất chỉ còn 14 - 3 = 11 nến sạch -> Rớt vòng len < 12
    assert passes_filter(df, "NAN.T") == False
