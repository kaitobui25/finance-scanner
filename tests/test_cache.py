import os
import pytest
import pandas as pd
from unittest.mock import patch

from data_provider.base import DataIncompleteError
from data_provider.cache import write_cache, read_cache, _cache_path

@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Override CACHE_DIR to use pytest's temporary directory"""
    with patch("data_provider.cache.CACHE_DIR", tmp_path):
        yield tmp_path


def create_mock_df(dates, val_start=1):
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": range(val_start, val_start + len(dates)),
        "high": range(val_start, val_start + len(dates)),
        "low": range(val_start, val_start + len(dates)),
        "close": range(val_start, val_start + len(dates)),
        "volume": [1000] * len(dates)
    })


def test_write_cache_atomic_crash(tmp_cache_dir):
    """
    Test 3.4: write -> crash simulation -> verify file không corrupt
    Mô phỏng việc mất điện hoặc crash process ngay trong lúc chuẩn bị ghi đè file cuối cùng
    """
    symbol = "TEST_CRASH.T"
    timeframe = "1MO"
    
    # Bước 1: Khởi tạo cache hợp lệ
    df_initial = create_mock_df(["2026-01-01", "2026-02-01"])
    write_cache(symbol, timeframe, df_initial)
    
    cache_file = _cache_path(symbol, timeframe)
    assert cache_file.exists()
    
    # State ban đầu để so sánh
    original_df = read_cache(symbol, timeframe)
    
    # Bước 2: Thử ghi file mới nhưng throw crash giữa chừng
    df_new = create_mock_df(["2026-03-01"])
    
    # Giả lập crash ở hàm `os.replace` (lúc này .tmp đã được tạo nhưng chưa ghi đè parquet thật)
    with patch("os.replace", side_effect=OSError("Mô phỏng crash IO/mất điện")):
        with pytest.raises(OSError, match="Mô phỏng crash IO/mất điện"):
            write_cache(symbol, timeframe, df_new)
            
    # Bước 3: Verify cache gốc vẫn sống khỏe, không bị corrupt và data giữ nguyên
    df_after_crash = read_cache(symbol, timeframe)
    pd.testing.assert_frame_equal(original_df, df_after_crash)


def test_write_cache_gap_protection(tmp_cache_dir):
    """
    Test 3.5: Test gap protection: inject DataFrame thiếu 1 tháng giữa -> phải raise DataIncompleteError
    """
    symbol = "TEST_GAP.T"
    timeframe = "1MO"
    
    # Case 1: Lỗi ngay từ source cung cấp data (thiếu thẳng tháng 2)
    dates_gap1 = ["2026-01-01", "2026-03-01", "2026-04-01"]
    df_gap1 = create_mock_df(dates_gap1)
    
    with pytest.raises(DataIncompleteError, match="Data gap detected"):
        write_cache(symbol, timeframe, df_gap1)
        
    # Case 2: Source provider trả về bị mất các tháng cũ (tạo gap sau khi merge)
    # Lần ghi 1: Dữ liệu bình thường tháng 1
    df_initial = create_mock_df(["2026-01-01"])
    write_cache(symbol, timeframe, df_initial)
    
    # Lần ghi 2 (sau đó vài tháng): Yahoo trả dữ liệu gồm tháng 1 và tháng 4, drop mất tháng 2 và 3 => tạo gap đoạn giữa
    df_new_missing_history = create_mock_df(["2026-01-01", "2026-04-01"])
    with pytest.raises(DataIncompleteError, match="Data gap detected"):
        write_cache(symbol, timeframe, df_new_missing_history)
